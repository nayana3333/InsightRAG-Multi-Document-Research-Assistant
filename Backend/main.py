import asyncio
import hashlib
import logging
import json
import os
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from io import BytesIO
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pypdf import PdfReader
from pydantic import BaseModel, Field, field_validator


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE, override=False)

import database
from auth import (
    AuthenticationError,
    create_access_token,
    hash_password,
    verify_access_token,
    verify_password,
)
from charbot import RAGChatbot, delete_vector_index, vector_backend
from evaluation import evaluate_retrieval
from security import SlidingWindowRateLimiter


UPLOAD_DIR = BASE_DIR / "uploads"
VECTOR_DIR = BASE_DIR / "vector_stores"
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE_MB", "15")) * 1024 * 1024
MAX_PDF_PAGES = int(os.environ.get("MAX_PDF_PAGES", "500"))
MAX_WORKSPACE_DOCUMENTS = int(os.environ.get("MAX_WORKSPACE_DOCUMENTS", "20"))
CONFIG_CACHE_TTL_SECONDS = int(os.environ.get("CONFIG_CACHE_TTL_SECONDS", "60"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
VECTOR_DIR.mkdir(parents=True, exist_ok=True)
database.initialize_database()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("insight_rag.api")

app = FastAPI(
    title="InsightRAG API",
    version="3.1.0",
    description="Production-oriented document ingestion and grounded question answering.",
)
security = HTTPBearer(auto_error=False)
rate_limiter = SlidingWindowRateLimiter()
workspace_locks = defaultdict(asyncio.Lock)
configuration_lock = asyncio.Lock()
configuration_cache = {"expiresAt": 0.0, "value": None}

allowed_origins = os.environ.get(
    "CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Process-Time-Ms", "Retry-After"],
)


class AuthRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("Enter a valid email address.")
        return normalized


class RegisterRequest(AuthRequest):
    name: str = Field(min_length=2, max_length=80)


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class EvaluationCase(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    relevantPages: list[int] = Field(min_length=1, max_length=20)
    relevantFiles: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("relevantPages")
    @classmethod
    def positive_pages(cls, pages: list[int]) -> list[int]:
        if any(page < 1 for page in pages):
            raise ValueError("Page numbers must be positive.")
        return sorted(set(pages))


class EvaluationRequest(BaseModel):
    cases: list[EvaluationCase] = Field(min_length=1, max_length=50)
    k: int = Field(default=4, ge=1, le=10)


def public_user(user: dict) -> dict:
    return {key: user[key] for key in ("id", "email", "name", "created_at")}


def auth_response(user: dict) -> dict:
    token, expires_at = create_access_token(user["id"], user["email"])
    return {
        "accessToken": token,
        "tokenType": "bearer",
        "expiresAt": expires_at,
        "user": public_user(user),
    }


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        claims = verify_access_token(credentials.credentials)
    except (AuthenticationError, RuntimeError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    user = database.get_user_by_id(claims["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    return user


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request_id = (
        supplied_request_id
        if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_request_id)
        else uuid.uuid4().hex
    )
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    logger.info(
        "%s %s status=%s duration_ms=%s request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request_id,
    )
    return response


def enforce_rate_limit(
    request: Request, bucket: str, identity: str, limit: int, window_seconds: int
) -> None:
    if os.environ.get("RATE_LIMIT_ENABLED", "true").lower() != "true":
        return
    client_host = request.client.host if request.client else "unknown"
    retry_after = rate_limiter.check(
        f"{bucket}:{identity}:{client_host}", limit, window_seconds
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait and retry.",
            headers={"Retry-After": str(retry_after)},
        )


def get_api_key() -> str:
    load_dotenv(ENV_FILE, override=False)
    return os.environ.get("OPENROUTER_API_KEY", "")


async def get_ai_configuration(force: bool = False) -> dict:
    now = time.monotonic()
    if not force and configuration_cache["value"] and now < configuration_cache["expiresAt"]:
        return configuration_cache["value"]
    async with configuration_lock:
        now = time.monotonic()
        if not force and configuration_cache["value"] and now < configuration_cache["expiresAt"]:
            return configuration_cache["value"]
        result = await _fetch_ai_configuration()
        configuration_cache["value"] = result
        configuration_cache["expiresAt"] = now + CONFIG_CACHE_TTL_SECONDS
        return result


async def _fetch_ai_configuration() -> dict:
    api_key = get_api_key()
    if not api_key:
        return {
            "aiConfigured": False,
            "provider": "OpenRouter",
            "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
            "configurationError": "OpenRouter API key is not configured. Add it to Backend/.env.",
        }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.RequestError:
        return {
            "aiConfigured": False,
            "provider": "OpenRouter",
            "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
            "configurationError": "Could not connect to OpenRouter.",
        }

    if response.status_code == 200:
        return {
            "aiConfigured": True,
            "provider": "OpenRouter",
            "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
            "configurationError": None,
        }
    if response.status_code == 401:
        message = "OpenRouter rejected the API key. Replace it in Backend/.env."
    elif response.status_code == 403:
        message = "The API key does not have permission to use OpenRouter."
    else:
        message = f"OpenRouter configuration check failed with HTTP {response.status_code}."
    return {
        "aiConfigured": False,
        "provider": "OpenRouter",
        "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
        "configurationError": message,
    }


async def require_ai_configuration() -> None:
    configuration = await get_ai_configuration()
    if not configuration["aiConfigured"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=configuration["configurationError"],
        )


async def read_and_validate_pdf(file: UploadFile) -> tuple[str, bytes, str, int]:
    safe_filename = Path(file.filename or "upload.pdf").name
    if Path(safe_filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Only PDF files are supported.")
    contents = await file.read(MAX_FILE_SIZE + 1)
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds the {MAX_FILE_SIZE // (1024 * 1024)} MB upload limit.",
        )
    if not contents.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="The file is not a valid PDF.")
    try:
        reader = PdfReader(BytesIO(contents))
        if reader.is_encrypted:
            raise HTTPException(status_code=422, detail="Password-protected PDFs are not supported.")
        page_count = len(reader.pages)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=422, detail="The PDF is damaged or unreadable.") from error
    if page_count == 0:
        raise HTTPException(status_code=422, detail="The PDF does not contain any pages.")
    if page_count > MAX_PDF_PAGES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds the {MAX_PDF_PAGES}-page processing limit.",
        )
    return safe_filename, contents, hashlib.sha256(contents).hexdigest(), page_count


async def create_chat_from_upload(file: UploadFile, user: dict) -> dict:
    await require_ai_configuration()
    file_name, contents, content_hash, page_count = await read_and_validate_pdf(file)
    chat_id = f"chat_{uuid.uuid4().hex}"
    document_id = f"doc_{uuid.uuid4().hex}"
    stored_path = UPLOAD_DIR / f"{chat_id}_{document_id}.pdf"
    try:
        stored_path.write_bytes(contents)
        await run_in_threadpool(RAGChatbot, [str(stored_path)], chat_id, [file_name])
        database.create_conversation(user["id"], chat_id, file_name, str(stored_path))
        database.create_document(
            user["id"], chat_id, document_id, file_name, str(stored_path),
            content_hash, len(contents), page_count
        )
        database.append_message(
            user["id"], chat_id, "ai", "Document indexed. Ask me anything about it."
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Document indexing failed chat_id=%s", chat_id)
        stored_path.unlink(missing_ok=True)
        try:
            delete_vector_index(chat_id)
        except Exception:
            logger.exception("Vector cleanup failed chat_id=%s", chat_id)
        raise HTTPException(
            status_code=500,
            detail="Document indexing failed. Verify that the PDF contains extractable text.",
        ) from error

    return {
        "message": "Document uploaded and indexed successfully.",
        "chatId": chat_id,
        "chat_id": chat_id,
        "fileName": file_name,
        "documentId": document_id,
        "byteSize": len(contents),
        "pageCount": page_count,
        "status": "ready",
    }


async def answer_question(chat_id: str, question: str, user: dict) -> dict:
    await require_ai_configuration()
    conversation = database.get_conversation(user["id"], chat_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    history = []
    for message in database.get_messages(user["id"], chat_id):
        message_class = HumanMessage if message["type"] == "human" else AIMessage
        history.append(message_class(content=message["text"]))

    started = time.perf_counter()
    try:
        documents = database.list_documents(user["id"], chat_id)
        document_paths = [item["filePath"] for item in documents]
        chatbot = await run_in_threadpool(
            RAGChatbot, document_paths, chat_id, [item["fileName"] for item in documents]
        )
        result = await run_in_threadpool(chatbot.invoke, question, history)
    except Exception as error:
        logger.exception("Question answering failed chat_id=%s", chat_id)
        raise HTTPException(
            status_code=502,
            detail="The AI provider could not complete this request. Please retry.",
        ) from error

    database.append_message(user["id"], chat_id, "human", question)
    database.append_message(user["id"], chat_id, "ai", result["answer"], result["sources"])
    return {
        "answer": result["answer"],
        "sources": result["sources"],
        "latencyMs": round((time.perf_counter() - started) * 1000, 2),
        "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
    }


async def add_document_to_chat(chat_id: str, file: UploadFile, user: dict) -> dict:
    await require_ai_configuration()
    conversation = database.get_conversation(user["id"], chat_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    file_name, contents, content_hash, page_count = await read_and_validate_pdf(file)
    existing_documents = database.list_documents(user["id"], chat_id)
    if len(existing_documents) >= MAX_WORKSPACE_DOCUMENTS:
        raise HTTPException(
            status_code=409,
            detail=f"A workspace can contain at most {MAX_WORKSPACE_DOCUMENTS} documents.",
        )
    if database.document_hash_exists(user["id"], chat_id, content_hash):
        raise HTTPException(status_code=409, detail="This PDF is already in the workspace.")
    document_id = f"doc_{uuid.uuid4().hex}"
    stored_path = UPLOAD_DIR / f"{chat_id}_{document_id}.pdf"
    previous_paths = database.get_document_paths(user["id"], chat_id)
    try:
        stored_path.write_bytes(contents)
        await run_in_threadpool(delete_vector_index, chat_id)
        await run_in_threadpool(
            RAGChatbot,
            [*previous_paths, str(stored_path)],
            chat_id,
            [*[item["fileName"] for item in existing_documents], file_name],
        )
        database.create_document(
            user["id"], chat_id, document_id, file_name, str(stored_path),
            content_hash, len(contents), page_count
        )
    except Exception as error:
        logger.exception("Document addition failed chat_id=%s", chat_id)
        stored_path.unlink(missing_ok=True)
        if previous_paths:
            try:
                await run_in_threadpool(delete_vector_index, chat_id)
                await run_in_threadpool(
                    RAGChatbot,
                    previous_paths,
                    chat_id,
                    [item["fileName"] for item in existing_documents],
                )
            except Exception:
                logger.exception("Previous index restoration failed chat_id=%s", chat_id)
        raise HTTPException(status_code=500, detail="The document could not be added.") from error
    return {
        "message": "Document added and workspace index rebuilt.",
        "chatId": chat_id,
        "documentId": document_id,
        "fileName": file_name,
        "byteSize": len(contents),
        "pageCount": page_count,
        "status": "ready",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": app.version,
        "database": "sqlite",
        "vectorBackend": vector_backend(),
    }


@app.get("/health/live")
async def liveness():
    return {"status": "alive", "version": app.version}


@app.get("/health/ready")
async def readiness():
    checks = {"database": database.database_is_ready(), "vectorStore": True}
    if vector_backend() == "qdrant":
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(
                    f"{os.environ.get('QDRANT_URL', 'http://127.0.0.1:6333').rstrip('/')}/readyz"
                )
            checks["vectorStore"] = response.status_code == 200
        except httpx.RequestError:
            checks["vectorStore"] = False
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request):
    enforce_rate_limit(request, "register", payload.email, 5, 900)
    try:
        user = await run_in_threadpool(
            database.create_user,
            payload.email,
            payload.name.strip(),
            hash_password(payload.password),
        )
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="An account with this email already exists.") from error
    return auth_response(user)


@app.post("/auth/login")
async def login(payload: AuthRequest, request: Request):
    enforce_rate_limit(request, "login", payload.email, 10, 900)
    user_with_password = database.get_user_by_email(payload.email)
    if not user_with_password or not verify_password(
        payload.password, user_with_password["password_hash"]
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return auth_response(user_with_password)


@app.get("/auth/me")
async def me(user: dict = Depends(current_user)):
    return {"user": public_user(user)}


@app.get("/configuration")
async def configuration(request: Request, refresh: bool = False):
    enforce_rate_limit(request, "configuration", "public", 30, 300)
    return await get_ai_configuration(force=refresh)


@app.post("/chats", status_code=status.HTTP_201_CREATED)
async def create_chat(
    request: Request, file: UploadFile = File(...), user: dict = Depends(current_user)
):
    enforce_rate_limit(request, "upload", user["id"], 20, 3600)
    return await create_chat_from_upload(file, user)


@app.get("/chats")
async def list_chats(user: dict = Depends(current_user)):
    return {"conversations": database.list_conversations(user["id"])}


@app.get("/chats/{chat_id}/messages")
async def list_chat_messages(chat_id: str, user: dict = Depends(current_user)):
    if not database.get_conversation(user["id"], chat_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"messages": database.get_messages(user["id"], chat_id)}


@app.post("/chats/{chat_id}/messages")
async def create_message(
    chat_id: str, payload: QuestionRequest, request: Request,
    user: dict = Depends(current_user)
):
    enforce_rate_limit(request, "message", user["id"], 60, 3600)
    return await answer_question(chat_id, payload.question.strip(), user)


@app.post("/chats/{chat_id}/messages/stream")
async def stream_message(
    chat_id: str, payload: QuestionRequest, request: Request,
    user: dict = Depends(current_user)
):
    enforce_rate_limit(request, "message", user["id"], 60, 3600)
    await require_ai_configuration()
    if not database.get_conversation(user["id"], chat_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    history = [
        (HumanMessage if message["type"] == "human" else AIMessage)(
            content=message["text"]
        )
        for message in database.get_messages(user["id"], chat_id)
    ]
    documents = database.list_documents(user["id"], chat_id)
    document_paths = [item["filePath"] for item in documents]
    chatbot = await run_in_threadpool(
        RAGChatbot, document_paths, chat_id, [item["fileName"] for item in documents]
    )
    question = payload.question.strip()

    def events():
        started = time.perf_counter()
        answer_parts = []
        sources = []
        try:
            for event in chatbot.stream(question, history):
                if event["type"] == "sources":
                    sources = event["sources"]
                elif event["type"] == "token":
                    answer_parts.append(event["token"])
                yield f"data: {json.dumps(event)}\n\n"
            answer = "".join(answer_parts).strip()
            if not answer:
                raise RuntimeError("The provider returned an empty stream.")
            database.append_message(user["id"], chat_id, "human", question)
            database.append_message(user["id"], chat_id, "ai", answer, sources)
            done = {
                "type": "done",
                "latencyMs": round((time.perf_counter() - started) * 1000, 2),
                "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
            }
            yield f"data: {json.dumps(done)}\n\n"
        except Exception:
            logger.exception("Streaming failed chat_id=%s", chat_id)
            yield f"data: {json.dumps({'type': 'error', 'message': 'Streaming failed. Please retry.'})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/chats/{chat_id}/documents")
async def documents(chat_id: str, user: dict = Depends(current_user)):
    if not database.get_conversation(user["id"], chat_id):
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {"documents": database.list_documents(user["id"], chat_id)}


@app.post("/chats/{chat_id}/documents", status_code=status.HTTP_201_CREATED)
async def add_document(
    chat_id: str, request: Request, file: UploadFile = File(...),
    user: dict = Depends(current_user)
):
    enforce_rate_limit(request, "upload", user["id"], 20, 3600)
    async with workspace_locks[chat_id]:
        return await add_document_to_chat(chat_id, file, user)


@app.delete("/chats/{chat_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_document(
    chat_id: str, document_id: str, user: dict = Depends(current_user)
):
    async with workspace_locks[chat_id]:
        items = database.list_documents(user["id"], chat_id)
        target = next((item for item in items if item["id"] == document_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Document not found.")
        if len(items) == 1:
            raise HTTPException(status_code=409, detail="A workspace must contain at least one document.")
        remaining_items = [item for item in items if item["id"] != document_id]
        remaining_paths = [item["filePath"] for item in remaining_items]
        try:
            await run_in_threadpool(delete_vector_index, chat_id)
            await run_in_threadpool(
                RAGChatbot,
                remaining_paths,
                chat_id,
                [item["fileName"] for item in remaining_items],
            )
        except Exception as error:
            logger.exception("Document removal reindex failed chat_id=%s", chat_id)
            try:
                await run_in_threadpool(delete_vector_index, chat_id)
                await run_in_threadpool(
                    RAGChatbot,
                    [item["filePath"] for item in items],
                    chat_id,
                    [item["fileName"] for item in items],
                )
            except Exception:
                logger.exception("Original index restoration failed chat_id=%s", chat_id)
            raise HTTPException(
                status_code=500,
                detail="The document could not be removed; the workspace was preserved.",
            ) from error
        database.delete_document(user["id"], chat_id, document_id)
        Path(target["filePath"]).unlink(missing_ok=True)


@app.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_chat(chat_id: str, user: dict = Depends(current_user)):
    async with workspace_locks[chat_id]:
        conversation = database.get_conversation(user["id"], chat_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        workspace_documents = database.list_documents(user["id"], chat_id)
        await run_in_threadpool(delete_vector_index, chat_id)
        database.delete_conversation(user["id"], chat_id)
        for document in workspace_documents:
            Path(document["filePath"]).unlink(missing_ok=True)


@app.post("/chats/{chat_id}/evaluations", status_code=status.HTTP_201_CREATED)
async def run_evaluation(
    chat_id: str, payload: EvaluationRequest, user: dict = Depends(current_user)
):
    conversation = database.get_conversation(user["id"], chat_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    documents = database.list_documents(user["id"], chat_id)
    document_paths = [item["filePath"] for item in documents]
    chatbot = await run_in_threadpool(
        RAGChatbot, document_paths, chat_id, [item["fileName"] for item in documents]
    )
    cases = [item.model_dump() for item in payload.cases]
    metrics = await run_in_threadpool(evaluate_retrieval, chatbot, cases, payload.k)
    evaluation_id = database.save_evaluation(user["id"], chat_id, metrics)
    return {"id": evaluation_id, "chatId": chat_id, **metrics}


@app.get("/evaluations")
async def evaluations(user: dict = Depends(current_user)):
    return {"evaluations": database.list_evaluations(user["id"])}


# Backward-compatible routes for the original frontend and saved demos.
@app.post("/UploadFile")
async def legacy_upload(file: UploadFile = File(...), user: dict = Depends(current_user)):
    return await create_chat_from_upload(file, user)


@app.post("/chat")
async def legacy_chat(chat_id: str, question: str, user: dict = Depends(current_user)):
    return await answer_question(chat_id, question.strip(), user)


@app.get("/get_conversations")
async def legacy_conversations(user: dict = Depends(current_user)):
    return {"conversations": database.list_conversations(user["id"])}


@app.get("/get_chat_by_id")
async def legacy_chat_by_id(
    chat_id: str = Query(min_length=1), user: dict = Depends(current_user)
):
    return {"messages": database.get_messages(user["id"], chat_id)}
