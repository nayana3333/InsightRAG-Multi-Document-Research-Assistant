import os
import hashlib
import json
import logging
import math
import re
from collections import Counter
from typing import List, TypedDict, Annotated
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import START, StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from pypdf import PdfReader

BASE_DIR = Path(__file__).resolve().parent
LLM_MODEL_NAME = os.getenv("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
logger = logging.getLogger("insight_rag.retrieval")


class LocalHashEmbeddings(Embeddings):
    """Small deterministic local embeddings with no external model dependency."""

    def __init__(self, dimensions: int = 768):
        self.dimensions = dimensions
        self.model_id = f"local-hash-v2-{dimensions}"

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimensions
            vector[index] += 1.0 if value & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FastEmbedEmbeddings(Embeddings):
    """ONNX-backed semantic embeddings suitable for CPU and container deployment."""

    def __init__(self):
        from fastembed import TextEmbedding

        self.model_id = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        self.model = TextEmbedding(
            model_name=self.model_id,
            cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"),
        )
        self.dimensions = len(next(iter(self.model.query_embed("dimension probe"))))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text))).tolist()


def create_embeddings() -> Embeddings:
    if os.environ.get("EMBEDDING_BACKEND", "semantic").lower() == "hash":
        return LocalHashEmbeddings()
    try:
        return FastEmbedEmbeddings()
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        logger.warning("Semantic runtime unavailable; using deterministic fallback: %s", error)
        return LocalHashEmbeddings()


class LocalVectorStore:
    """Minimal JSON-backed vector store for local document retrieval."""

    def __init__(
        self,
        path: str,
        embeddings: Embeddings,
        documents: list[Document],
        vectors: list[list[float]],
    ):
        self.path = path
        self.embeddings = embeddings
        self.documents = documents
        self.vectors = vectors

    @classmethod
    def from_documents(
        cls, documents: list[Document], embeddings: Embeddings, path: str
    ) -> "LocalVectorStore":
        vectors = embeddings.embed_documents([doc.page_content for doc in documents])
        store = cls(path, embeddings, documents, vectors)
        store.save()
        return store

    @classmethod
    def load(cls, path: str, embeddings: Embeddings) -> "LocalVectorStore":
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if data.get("embeddingModel") != embeddings.model_id:
            raise ValueError("The vector index uses a different embedding model.")
        documents = [
            Document(page_content=item["page_content"], metadata=item["metadata"])
            for item in data["documents"]
        ]
        return cls(path, embeddings, documents, data["vectors"])

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "embeddingModel": self.embeddings.model_id,
                    "documents": [
                        {"page_content": doc.page_content, "metadata": doc.metadata}
                        for doc in self.documents
                    ],
                    "vectors": self.vectors,
                },
                file,
            )

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        return [document for document, _ in self.similarity_search_with_score(query, k)]

    def similarity_search_with_score(
        self, query: str, k: int = 4
    ) -> list[tuple[Document, float]]:
        query_vector = self.embeddings.embed_query(query)
        ranked = sorted(
            zip(self.documents, self.vectors),
            key=lambda item: sum(a * b for a, b in zip(query_vector, item[1])),
            reverse=True,
        )
        results = []
        for document, vector in ranked[:k]:
            cosine = sum(a * b for a, b in zip(query_vector, vector))
            score = round(max(0.0, min(1.0, cosine)), 4)
            scored_document = Document(
                page_content=document.page_content,
                metadata={**document.metadata, "relevance": score},
            )
            results.append((scored_document, score))
        return results

    def all_documents(self) -> list[Document]:
        return self.documents


class QdrantVectorStore:
    """Qdrant REST adapter implementing the same retrieval interface as the local store."""

    def __init__(self, collection: str, embeddings: Embeddings):
        self.collection = collection
        self.embeddings = embeddings
        self.base_url = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
        api_key = os.environ.get("QDRANT_API_KEY", "")
        self.headers = {"api-key": api_key} if api_key else {}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = httpx.request(
            method,
            f"{self.base_url}{path}",
            headers=self.headers,
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        return response

    @classmethod
    def from_documents(
        cls, documents: list[Document], embeddings: Embeddings, collection: str
    ) -> "QdrantVectorStore":
        store = cls(collection, embeddings)
        response = httpx.get(
            f"{store.base_url}/collections/{collection}", headers=store.headers, timeout=10
        )
        if response.status_code == 200:
            config = response.json()["result"]["config"]
            configured = config["params"]["vectors"]["size"]
            configured_model = config.get("metadata", {}).get("embeddingModel")
            if configured != embeddings.dimensions or configured_model != embeddings.model_id:
                store._request("DELETE", f"/collections/{collection}")
                response = httpx.Response(404)
        if response.status_code == 404:
            store._request(
                "PUT",
                f"/collections/{collection}",
                json={
                    "vectors": {"size": embeddings.dimensions, "distance": "Cosine"},
                    "metadata": {"embeddingModel": embeddings.model_id},
                },
            )
        else:
            response.raise_for_status()

        vectors = embeddings.embed_documents([doc.page_content for doc in documents])
        points = [
            {
                "id": index,
                "vector": vector,
                "payload": {
                    "page_content": document.page_content,
                    "metadata": document.metadata,
                },
            }
            for index, (document, vector) in enumerate(zip(documents, vectors))
        ]
        for start in range(0, len(points), 64):
            store._request(
                "PUT",
                f"/collections/{collection}/points?wait=true",
                json={"points": points[start : start + 64]},
            )
        return store

    @classmethod
    def load(cls, collection: str, embeddings: Embeddings) -> "QdrantVectorStore":
        store = cls(collection, embeddings)
        details = store._request("GET", f"/collections/{collection}").json()
        config = details["result"]["config"]
        configured = config["params"]["vectors"]["size"]
        configured_model = config.get("metadata", {}).get("embeddingModel")
        if configured != embeddings.dimensions or configured_model != embeddings.model_id:
            raise ValueError("The Qdrant collection uses a different embedding model.")
        return store

    def similarity_search_with_score(
        self, query: str, k: int = 4
    ) -> list[tuple[Document, float]]:
        response = self._request(
            "POST",
            f"/collections/{self.collection}/points/query",
            json={
                "query": self.embeddings.embed_query(query),
                "limit": k,
                "with_payload": True,
            },
        ).json()
        points = response.get("result", {}).get("points", [])
        results = []
        for point in points:
            payload = point.get("payload", {})
            score = round(max(0.0, min(1.0, float(point.get("score", 0)))), 4)
            document = Document(
                page_content=payload.get("page_content", ""),
                metadata={**payload.get("metadata", {}), "relevance": score},
            )
            results.append((document, score))
        return results

    def all_documents(self) -> list[Document]:
        documents = []
        offset = None
        while True:
            payload = {"limit": 256, "with_payload": True, "with_vector": False}
            if offset is not None:
                payload["offset"] = offset
            result = self._request(
                "POST", f"/collections/{self.collection}/points/scroll", json=payload
            ).json()["result"]
            for point in result.get("points", []):
                item = point.get("payload", {})
                documents.append(
                    Document(
                        page_content=item.get("page_content", ""),
                        metadata=item.get("metadata", {}),
                    )
                )
            offset = result.get("next_page_offset")
            if offset is None:
                break
        return documents


class HybridVectorStore:
    """Fuses semantic and BM25-style lexical ranks, then optionally reranks candidates."""

    def __init__(self, dense_store, embeddings: Embeddings):
        self.dense_store = dense_store
        self.embeddings = embeddings
        self.documents = dense_store.all_documents()
        self._reranker = None
        self._reranker_attempted = False

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _lexical_search(self, query: str, limit: int) -> list[tuple[Document, float]]:
        query_tokens = self._tokens(query)
        if not query_tokens or not self.documents:
            return []
        tokenized = [self._tokens(document.page_content) for document in self.documents]
        document_frequency = Counter(
            token for tokens in tokenized for token in set(tokens)
        )
        average_length = sum(len(tokens) for tokens in tokenized) / len(tokenized)
        scored = []
        for document, tokens in zip(self.documents, tokenized):
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                idf = math.log(1 + (len(tokenized) - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
                denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / max(1, average_length))
                score += idf * frequency * 2.5 / denominator
            if score > 0:
                scored.append((document, score))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]

    def _get_reranker(self):
        if self._reranker_attempted:
            return self._reranker
        self._reranker_attempted = True
        if not isinstance(self.embeddings, FastEmbedEmbeddings):
            return None
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._reranker = TextCrossEncoder(
                model_name=os.environ.get(
                    "RERANKER_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2"
                )
            )
        except (ImportError, OSError, RuntimeError) as error:
            logger.warning("Reranker unavailable; continuing with rank fusion: %s", error)
        return self._reranker

    def similarity_search_with_score(
        self, query: str, k: int = 4
    ) -> list[tuple[Document, float]]:
        candidate_limit = max(20, k * 5)
        dense = self.dense_store.similarity_search_with_score(query, candidate_limit)
        lexical = self._lexical_search(query, candidate_limit)
        fused: dict[str, dict] = {}
        for source, results, weight in (
            ("dense", dense, 1.0),
            ("lexical", lexical, 1.15),
        ):
            for rank, (document, raw_score) in enumerate(results, start=1):
                key = document.metadata.get("chunk_id") or hashlib.sha1(
                    document.page_content.encode("utf-8")
                ).hexdigest()
                item = fused.setdefault(
                    key, {"document": document, "score": 0.0, "signals": {}}
                )
                item["score"] += weight / (60 + rank)
                item["signals"][source] = round(float(raw_score), 4)
        candidates = sorted(fused.values(), key=lambda item: item["score"], reverse=True)[:12]

        reranker = self._get_reranker()
        if reranker and candidates:
            scores = list(
                reranker.rerank(query, [item["document"].page_content for item in candidates])
            )
            for item, score in zip(candidates, scores):
                numeric = float(score)
                item["score"] = 1 / (1 + math.exp(-numeric))
            candidates.sort(key=lambda item: item["score"], reverse=True)
        elif candidates:
            maximum = max(item["score"] for item in candidates) or 1
            for item in candidates:
                item["score"] /= maximum

        results = []
        for item in candidates[:k]:
            score = round(max(0.0, min(1.0, item["score"])), 4)
            document = item["document"]
            results.append(
                (
                    Document(
                        page_content=document.page_content,
                        metadata={
                            **document.metadata,
                            "relevance": score,
                            "retrievalSignals": item["signals"],
                        },
                    ),
                    score,
                )
            )
        return results


def vector_backend() -> str:
    return os.environ.get("VECTOR_BACKEND", "local").lower()


def delete_vector_index(chat_id: str) -> None:
    if vector_backend() == "qdrant":
        store = QdrantVectorStore(chat_id, LocalHashEmbeddings())
        response = httpx.delete(
            f"{store.base_url}/collections/{chat_id}", headers=store.headers, timeout=15
        )
        if response.status_code not in (200, 404):
            response.raise_for_status()
        return
    import shutil

    shutil.rmtree(BASE_DIR / "vector_stores" / chat_id, ignore_errors=True)

class RAGChatbot:
    """Builds and runs the persisted retrieval and grounded generation pipeline."""

    def __init__(
        self,
        pdf_file_paths: str | list[str],
        chat_id: str,
        file_names: list[str] | None = None,
    ):
        self.PDF_FILE_PATHS = (
            [pdf_file_paths] if isinstance(pdf_file_paths, str) else list(pdf_file_paths)
        )
        display_names = file_names or [Path(path).name for path in self.PDF_FILE_PATHS]
        self.file_names = {
            str(Path(path).resolve()): name
            for path, name in zip(self.PDF_FILE_PATHS, display_names)
        }
        self.chat_id = chat_id
        self._setup_api_keys()
        self.memory = MemorySaver()
        self.model = os.getenv("OPENROUTER_MODEL", LLM_MODEL_NAME)
        self.embeddings = create_embeddings()
        self.vector_store = HybridVectorStore(self._get_vector_store(), self.embeddings)
        self.graph = self._build_graph()
        
        self.config = {"configurable": {"thread_id": str(self.chat_id)}}
        logger.info("RAG pipeline initialized chat_id=%s", self.chat_id)

    def _setup_api_keys(self):
        """Loads the OpenRouter API key from the environment or .env file."""
        load_dotenv(BASE_DIR / ".env", override=False)
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not configured. Add it to Backend/.env before uploading a PDF."
            )

    def _openrouter_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:5173",
            "X-OpenRouter-Title": "InsightRAG",
        }

    def _openrouter_payload(self, messages: list[dict], *, stream: bool) -> dict:
        return {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 1400,
            "reasoning": {"effort": "low", "exclude": True},
            "stream": stream,
        }

    @staticmethod
    def _content_text(content) -> str:
        """Normalizes text returned by OpenAI-compatible providers."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text = block.get("text") or block.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    @staticmethod
    def _provider_error(payload: dict) -> str | None:
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "OpenRouter returned a provider error.")
        if error:
            return str(error)
        return None

    def _complete(self, request_messages: list[dict]) -> str:
        """Retries a completion without SSE when a provider emits no text chunks."""
        response = httpx.post(
            OPENROUTER_URL,
            headers=self._openrouter_headers(),
            json=self._openrouter_payload(request_messages, stream=False),
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        provider_error = self._provider_error(payload)
        if provider_error:
            raise RuntimeError(provider_error)
        choices = payload.get("choices") or []
        content = self._content_text(
            choices[0].get("message", {}).get("content") if choices else None
        ).strip()
        if not content:
            raise RuntimeError("OpenRouter returned no answer text.")
        return content

    def _get_vector_store(self):
        """Loads vector store from disk or creates it if it doesn't exist."""
        if vector_backend() == "qdrant":
            try:
                logger.info("Loading Qdrant collection chat_id=%s", self.chat_id)
                return QdrantVectorStore.load(self.chat_id, self.embeddings)
            except ValueError:
                delete_vector_index(self.chat_id)
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
            documents = self._load_and_split_pdf()
            logger.info("Creating Qdrant collection chat_id=%s", self.chat_id)
            return QdrantVectorStore.from_documents(documents, self.embeddings, self.chat_id)

        store_path = str(BASE_DIR / "vector_stores" / str(self.chat_id) / "index.json")
        if os.path.exists(store_path):
            try:
                logger.info("Loading vector index chat_id=%s", self.chat_id)
                return LocalVectorStore.load(store_path, self.embeddings)
            except ValueError:
                logger.info("Rebuilding incompatible vector index chat_id=%s", self.chat_id)

        logger.info("Creating vector index chat_id=%s", self.chat_id)
        splits = self._load_and_split_pdf()
        return LocalVectorStore.from_documents(splits, self.embeddings, store_path)

    def _load_and_split_pdf(self) -> list[Document]:
        docs = []
        for pdf_path in self.PDF_FILE_PATHS:
            if not os.path.exists(pdf_path):
                raise FileNotFoundError("A workspace PDF was not found.")
            reader = PdfReader(pdf_path)
            docs.extend(
                Document(
                    page_content=page.extract_text() or "",
                    metadata={
                        "source": pdf_path,
                        "fileName": Path(pdf_path).name,
                        "page": page_number,
                    },
                )
                for page_number, page in enumerate(reader.pages)
            )
        splits = []
        chunk_size = 1000
        chunk_overlap = 200
        for doc in docs:
            text = doc.page_content.strip()
            start = 0
            while start < len(text):
                end = min(start + chunk_size, len(text))
                splits.append(
                    Document(
                        page_content=text[start:end],
                        metadata={
                            **doc.metadata,
                            "chunk_id": hashlib.sha1(
                                f"{doc.metadata['source']}:{doc.metadata['page']}:{start}".encode(
                                    "utf-8"
                                )
                            ).hexdigest(),
                        },
                    )
                )
                if end == len(text):
                    break
                start = end - chunk_overlap

        if not splits:
            raise ValueError("The uploaded PDF does not contain extractable text.")
        return splits

    def retrieve(self, question: str, k: int = 4) -> list[Document]:
        documents = [
            document
            for document, _ in self.vector_store.similarity_search_with_score(question, k=k)
        ]
        for document in documents:
            source = document.metadata.get("source")
            if source:
                document.metadata["fileName"] = self.file_names.get(
                    str(Path(source).resolve()), document.metadata.get("fileName", Path(source).name)
                )
        return documents

    def _build_graph(self):
        """Builds the conversational RAG graph using LangGraph."""
        # --- FIX: Update State Definition to properly append messages ---
        # We now use the explicit `add_messages` reducer to ensure that the
        # list of messages is appended to, rather than overwritten, on each step.
        class RagState(TypedDict):
            question: str
            messages: Annotated[list, add_messages]
            context: List[Document]

        def handle_input_node(state: RagState):
            """Appends the latest user question to the message history."""
            return {"messages": [HumanMessage(content=state["question"])]}

        def retrieve_node(state: RagState):
            """Retrieves documents based on the latest user question."""
            last_message = state["messages"][-1]
            question = last_message.content
            retrieved_docs = self.retrieve(question, k=4)
            return {"context": retrieved_docs}

        def generate_node(state: RagState):
            """Generates an answer using the LLM, considering context and conversation history."""
            context = state["context"]
            messages = state["messages"]
            request_messages = self._request_messages(context, messages)
            response = httpx.post(
                OPENROUTER_URL,
                headers=self._openrouter_headers(),
                json=self._openrouter_payload(request_messages, stream=False),
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
            provider_error = self._provider_error(payload)
            if provider_error:
                raise RuntimeError(provider_error)
            choices = payload.get("choices") or []
            content = self._content_text(
                choices[0].get("message", {}).get("content") if choices else None
            ).strip()
            if not content:
                raise RuntimeError("OpenRouter returned no answer text.")
            return {"messages": [AIMessage(content=content)]}

        # Graph construction remains the same, but the state update logic is now correct.
        graph_builder = StateGraph(RagState)
        graph_builder.add_node("handle_input", handle_input_node)
        graph_builder.add_node("retrieve", retrieve_node)
        graph_builder.add_node("generate", generate_node)

        graph_builder.add_edge(START, "handle_input")
        graph_builder.add_edge("handle_input", "retrieve")
        graph_builder.add_edge("retrieve", "generate")
        graph_builder.add_edge("generate", END)

        return graph_builder.compile(checkpointer=self.memory)

    @staticmethod
    def _sources(context: list[Document]) -> list[dict]:
        return [
            {
                "id": index,
                "page": document.metadata.get("page", 0) + 1,
                "fileName": document.metadata.get("fileName", "Document"),
                "relevance": document.metadata.get("relevance", 0),
                "retrievalSignals": document.metadata.get("retrievalSignals", {}),
                "snippet": document.page_content[:280].strip(),
            }
            for index, document in enumerate(context, start=1)
        ]

    @staticmethod
    def _request_messages(context: list[Document], messages: list) -> list[dict]:
        docs_content = "\n\n".join(
            f"[Source {index} | {doc.metadata.get('fileName', 'Document')} | Page {doc.metadata.get('page', 0) + 1}]\n{doc.page_content}"
            for index, doc in enumerate(context, start=1)
        )
        request_messages = [
            {
                "role": "system",
                "content": (
                    "You are a precise research assistant. Use only the supplied evidence. "
                    "Cite factual claims with [Source N]. If the answer is absent, say that "
                    "you do not know. Never invent a citation.\n\n"
                    f"Evidence:\n{docs_content}"
                ),
            }
        ]
        request_messages.extend(
            {
                "role": "assistant" if isinstance(message, AIMessage) else "user",
                "content": str(message.content),
            }
            for message in messages
        )
        return request_messages

    def stream(self, question: str, messages: list):
        context = self.retrieve(question, k=4)
        yield {"type": "sources", "sources": self._sources(context)}
        request_messages = self._request_messages(
            context, [*(messages or []), HumanMessage(content=question)]
        )
        with httpx.stream(
            "POST",
            OPENROUTER_URL,
            headers=self._openrouter_headers(),
            json=self._openrouter_payload(request_messages, stream=True),
            timeout=120,
        ) as response:
            response.raise_for_status()
            emitted_content = False
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("Ignoring malformed OpenRouter stream event")
                    continue
                provider_error = self._provider_error(payload)
                if provider_error:
                    raise RuntimeError(provider_error)
                choices = payload.get("choices") or []
                token = self._content_text(
                    choices[0].get("delta", {}).get("content") if choices else None
                )
                if token:
                    emitted_content = True
                    yield {"type": "token", "token": token}
            if not emitted_content:
                logger.warning("OpenRouter emitted no content chunks; retrying without SSE")
                yield {"type": "token", "token": self._complete(request_messages)}

    def invoke(self, question: str, messages: Annotated[list, add_messages]) -> dict:
        """
        Invokes the RAG graph with memory. The user's question is wrapped
        in a HumanMessage and the final AI response content is returned.
        """
        graph_input = {"question": question, "messages": messages or []}
        final_state = self.graph.invoke(graph_input, config=self.config)

        # The final state's messages list contains the full conversation.
        # The last message is the AI's response. We return its string content.
        if final_state and final_state.get("messages"):
            return {
                "answer": final_state["messages"][-1].content,
                "sources": self._sources(final_state.get("context", [])),
            }
        return {
            "answer": "Sorry, something went wrong and I couldn't generate a response.",
            "sources": [],
        }
