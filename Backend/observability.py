"""Structured logging and Prometheus metrics for the InsightRAG API."""

import json
import logging
import os
from contextvars import ContextVar

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

__all__ = [
    "CONTENT_TYPE_LATEST",
    "generate_latest",
    "configure_logging",
    "request_id_var",
    "HTTP_REQUESTS",
    "HTTP_LATENCY",
    "RAG_ANSWER_LATENCY",
    "RETRIEVAL_TOP_RELEVANCE",
    "RATE_LIMIT_REJECTIONS",
    "CHATBOT_CACHE_RESULT",
]

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

HTTP_REQUESTS = Counter(
    "insightrag_http_requests_total",
    "Total HTTP requests handled, by method/route/status.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "insightrag_http_request_duration_seconds",
    "HTTP request duration in seconds, by method/route.",
    ["method", "route"],
)
RAG_ANSWER_LATENCY = Histogram(
    "insightrag_rag_answer_duration_seconds",
    "End-to-end retrieval+generation latency in seconds, by endpoint.",
    ["endpoint"],
)
RETRIEVAL_TOP_RELEVANCE = Histogram(
    "insightrag_retrieval_top_relevance",
    "Relevance score of the top retrieved source per answer.",
    buckets=(0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0),
)
RATE_LIMIT_REJECTIONS = Counter(
    "insightrag_rate_limit_rejections_total",
    "Requests rejected by the sliding-window rate limiter, by bucket.",
    ["bucket"],
)
CHATBOT_CACHE_RESULT = Counter(
    "insightrag_chatbot_cache_total",
    "RAGChatbot cache lookups, by result (hit/miss).",
    ["result"],
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


_RESERVED_RECORD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and key not in payload:
                try:
                    json.dumps(value)
                except TypeError:
                    value = str(value)
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    log_format = os.environ.get("LOG_FORMAT", "json").lower()
    level = os.environ.get("LOG_LEVEL", "INFO")

    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    if log_format == "text":
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
            )
        )
    else:
        handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
