import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "ragflow.db"
LEGACY_DATA_PATH = BASE_DIR / "chats.json"
_migration_lock = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    return Path(os.environ.get("RAGFLOW_DB_PATH", DEFAULT_DB_PATH))


@contextmanager
def connect():
    connection = sqlite3.connect(get_db_path(), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    get_db_path().parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                owner_id TEXT,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_message TEXT NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ready'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('human', 'ai')),
                text TEXT NOT NULL,
                sources_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY(chat_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready',
                content_hash TEXT NOT NULL DEFAULT '',
                byte_size INTEGER NOT NULL DEFAULT 0,
                page_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(chat_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS evaluation_runs (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(chat_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id, id);
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "owner_id" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN owner_id TEXT")
        document_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        for column, definition in (
            ("content_hash", "TEXT NOT NULL DEFAULT ''"),
            ("byte_size", "INTEGER NOT NULL DEFAULT 0"),
            ("page_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in document_columns:
                connection.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner_id, updated_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_evaluations_owner ON evaluation_runs(owner_id, created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_chat ON documents(chat_id, owner_id, created_at)"
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_content_hash
            ON documents(chat_id, owner_id, content_hash) WHERE content_hash != ''
            """
        )
        # Promote every pre-v3 single-document conversation into the workspace model.
        connection.execute(
            """
            INSERT OR IGNORE INTO documents
            (id, chat_id, owner_id, file_name, file_path, created_at, status)
            SELECT 'doc_' || conversation.id, conversation.id,
                   COALESCE(conversation.owner_id, ''), conversation.file_name,
                   conversation.file_path, conversation.created_at, conversation.status
            FROM conversations AS conversation
            WHERE NOT EXISTS (
                SELECT 1 FROM documents AS document
                WHERE document.chat_id = conversation.id
            )
            """
        )
    migrate_legacy_json()


def migrate_legacy_json() -> None:
    if not LEGACY_DATA_PATH.exists():
        return
    with _migration_lock, connect() as connection:
        if connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]:
            return
        try:
            legacy_data = json.loads(LEGACY_DATA_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for chat_id, item in legacy_data.items():
            created_at = item.get("createdAt") or utc_now()
            file_name = item.get("fileName") or "Imported document"
            connection.execute(
                """
                INSERT OR IGNORE INTO conversations
                (id, file_name, file_path, created_at, updated_at, last_message, message_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    file_name,
                    str(BASE_DIR / "uploads" / file_name),
                    created_at,
                    created_at,
                    item.get("lastMessage") or "",
                    len(item.get("messages", [])),
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO documents
                (id, chat_id, owner_id, file_name, file_path, created_at, status)
                VALUES (?, ?, '', ?, ?, ?, 'ready')
                """,
                (
                    f"doc_{chat_id}",
                    chat_id,
                    file_name,
                    str(BASE_DIR / "uploads" / file_name),
                    created_at,
                ),
            )
            for message in item.get("messages", []):
                connection.execute(
                    "INSERT INTO messages (chat_id, type, text, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, message.get("type", "ai"), message.get("text", ""), created_at),
                )


def create_user(email: str, name: str, password_hash: str) -> dict:
    user_id = f"usr_{uuid.uuid4().hex}"
    with connect() as connection:
        connection.execute(
            "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, email.lower(), name, password_hash, utc_now()),
        )
        # Preserve pre-authentication project data for the first account only.
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1:
            connection.execute(
                "UPDATE conversations SET owner_id = ? WHERE owner_id IS NULL", (user_id,)
            )
            connection.execute(
                "UPDATE documents SET owner_id = ? WHERE owner_id = ''", (user_id,)
            )
    return get_user_by_id(user_id)


def get_user_by_email(email: str):
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str):
    with connect() as connection:
        row = connection.execute(
            "SELECT id, email, name, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def create_conversation(owner_id: str, chat_id: str, file_name: str, file_path: str) -> None:
    now = utc_now()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO conversations
            (id, owner_id, file_name, file_path, created_at, updated_at, last_message, message_count)
            VALUES (?, ?, ?, ?, ?, ?, '', 0)
            """,
            (chat_id, owner_id, file_name, file_path, now, now),
        )


def create_document(
    owner_id: str,
    chat_id: str,
    document_id: str,
    file_name: str,
    file_path: str,
    content_hash: str = "",
    byte_size: int = 0,
    page_count: int = 0,
) -> None:
    with connect() as connection:
        conversation = connection.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND owner_id = ?", (chat_id, owner_id)
        ).fetchone()
        if not conversation:
            raise LookupError("Conversation not found.")
        connection.execute(
            """
            INSERT INTO documents
            (id, chat_id, owner_id, file_name, file_path, created_at, content_hash, byte_size, page_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                chat_id,
                owner_id,
                file_name,
                file_path,
                utc_now(),
                content_hash,
                byte_size,
                page_count,
            ),
        )


def list_documents(owner_id: str, chat_id: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, file_name, file_path, created_at, status,
                   content_hash, byte_size, page_count FROM documents
            WHERE chat_id = ? AND owner_id = ? ORDER BY created_at
            """,
            (chat_id, owner_id),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "fileName": row["file_name"],
            "filePath": row["file_path"],
            "createdAt": row["created_at"],
            "status": row["status"],
            "contentHash": row["content_hash"],
            "byteSize": row["byte_size"],
            "pageCount": row["page_count"],
        }
        for row in rows
    ]


def document_hash_exists(owner_id: str, chat_id: str, content_hash: str) -> bool:
    if not content_hash:
        return False
    with connect() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM documents
            WHERE owner_id = ? AND chat_id = ? AND content_hash = ?
            """,
            (owner_id, chat_id, content_hash),
        ).fetchone()
    return row is not None


def database_is_ready() -> bool:
    try:
        with connect() as connection:
            connection.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def get_document_paths(owner_id: str, chat_id: str) -> list[str]:
    return [item["filePath"] for item in list_documents(owner_id, chat_id)]


def delete_document(owner_id: str, chat_id: str, document_id: str):
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, file_name, file_path FROM documents
            WHERE id = ? AND chat_id = ? AND owner_id = ?
            """,
            (document_id, chat_id, owner_id),
        ).fetchone()
        if row:
            connection.execute(
                "DELETE FROM documents WHERE id = ? AND chat_id = ? AND owner_id = ?",
                (document_id, chat_id, owner_id),
            )
    return dict(row) if row else None


def delete_conversation(owner_id: str, chat_id: str) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE id = ? AND owner_id = ?", (chat_id, owner_id)
        )
    return cursor.rowcount > 0


def append_message(owner_id: str, chat_id: str, message_type: str, text: str, sources=None) -> None:
    now = utc_now()
    with connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND owner_id = ?", (chat_id, owner_id)
        ).fetchone()
        if not exists:
            raise LookupError("Conversation not found.")
        connection.execute(
            "INSERT INTO messages (chat_id, type, text, sources_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, message_type, text, json.dumps(sources or []), now),
        )
        connection.execute(
            """
            UPDATE conversations SET last_message = ?, message_count = message_count + 1,
            updated_at = ? WHERE id = ? AND owner_id = ?
            """,
            (text, now, chat_id, owner_id),
        )


def get_conversation(owner_id: str, chat_id: str):
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM conversations WHERE id = ? AND owner_id = ?", (chat_id, owner_id)
        ).fetchone()
    return dict(row) if row else None


def list_conversations(owner_id: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.file_name, c.created_at, c.updated_at, c.last_message,
                   c.message_count, c.status, COUNT(d.id) AS document_count
            FROM conversations c LEFT JOIN documents d ON d.chat_id = c.id
            WHERE c.owner_id = ? GROUP BY c.id ORDER BY c.updated_at DESC
            """,
            (owner_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "fileName": row["file_name"],
            "timestamp": row["created_at"],
            "updatedAt": row["updated_at"],
            "lastMessage": row["last_message"],
            "messageCount": row["message_count"],
            "status": row["status"],
            "documentCount": row["document_count"],
        }
        for row in rows
    ]


def get_messages(owner_id: str, chat_id: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT m.id, m.type, m.text, m.sources_json, m.created_at
            FROM messages m JOIN conversations c ON c.id = m.chat_id
            WHERE m.chat_id = ? AND c.owner_id = ? ORDER BY m.id
            """,
            (chat_id, owner_id),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "type": row["type"],
            "text": row["text"],
            "sources": json.loads(row["sources_json"]),
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def save_evaluation(owner_id: str, chat_id: str, metrics: dict) -> str:
    evaluation_id = f"eval_{uuid.uuid4().hex}"
    with connect() as connection:
        connection.execute(
            "INSERT INTO evaluation_runs (id, chat_id, owner_id, metrics_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (evaluation_id, chat_id, owner_id, json.dumps(metrics), utc_now()),
        )
    return evaluation_id


def list_evaluations(owner_id: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, chat_id, metrics_json, created_at FROM evaluation_runs WHERE owner_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
    return [
        {"id": row["id"], "chatId": row["chat_id"], "createdAt": row["created_at"], **json.loads(row["metrics_json"])}
        for row in rows
    ]
