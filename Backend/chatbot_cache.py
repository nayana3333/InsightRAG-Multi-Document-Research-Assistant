"""Per-workspace RAGChatbot cache.

RAGChatbot construction reloads the embedding model handle and reparses the
on-disk vector index (or re-queries Qdrant), so rebuilding it on every single
chat/stream/evaluation request is wasted work once a workspace's documents
haven't changed. This cache keeps the most recently used chatbots warm,
bounded by size and TTL, and is invalidated explicitly whenever a workspace's
documents change.
"""

import threading
import time
from collections import OrderedDict
from typing import Any

_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_lock = threading.Lock()


def get(chat_id: str, document_paths: list[str]) -> Any | None:
    signature = tuple(document_paths)
    with _lock:
        entry = _cache.get(chat_id)
        if entry is None:
            return None
        if entry["signature"] != signature:
            return None
        if time.monotonic() > entry["expiresAt"]:
            del _cache[chat_id]
            return None
        _cache.move_to_end(chat_id)
        return entry["chatbot"]


def put(
    chat_id: str,
    document_paths: list[str],
    chatbot: Any,
    ttl_seconds: int,
    max_size: int,
) -> None:
    with _lock:
        _cache[chat_id] = {
            "chatbot": chatbot,
            "signature": tuple(document_paths),
            "expiresAt": time.monotonic() + ttl_seconds,
        }
        _cache.move_to_end(chat_id)
        while len(_cache) > max_size:
            _cache.popitem(last=False)


def invalidate(chat_id: str) -> None:
    with _lock:
        _cache.pop(chat_id, None)


def clear() -> None:
    """Test-only helper: empties the cache."""
    with _lock:
        _cache.clear()
