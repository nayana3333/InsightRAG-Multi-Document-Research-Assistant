import json
import logging
import os
import unittest

os.environ.setdefault("AUTH_SECRET", "test-secret-that-is-longer-than-thirty-two-characters")

from fastapi.testclient import TestClient

import observability
from main import app


class MetricsEndpointTests(unittest.TestCase):
    def test_metrics_endpoint_exposes_request_counter(self):
        client = TestClient(app)
        client.get("/health")
        response = client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertIn("insightrag_http_requests_total", response.text)

    def test_request_id_header_matches_the_log_lines_request_id(self):
        """Regression test: the request-context middleware used to reset the
        request_id contextvar before its own summary log line, so JSON logs for
        a request always showed request_id="-" despite the response header
        carrying the real id."""
        client = TestClient(app)
        records = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = ListHandler()
        handler.addFilter(observability.RequestIdFilter())
        api_logger = logging.getLogger("insight_rag.api")
        api_logger.addHandler(handler)
        try:
            response = client.get("/health", headers={"X-Request-ID": "test-request-id-123"})
        finally:
            api_logger.removeHandler(handler)

        self.assertEqual(response.headers["X-Request-ID"], "test-request-id-123")
        matching = [record for record in records if "/health" in record.getMessage()]
        self.assertTrue(matching, "expected a log record for the /health request")
        self.assertEqual(matching[-1].request_id, "test-request-id-123")


class JsonFormatterTests(unittest.TestCase):
    def test_json_formatter_emits_parseable_json_with_request_id(self):
        formatter = observability.JsonFormatter()
        record = logging.LogRecord(
            name="insight_rag.api",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )
        token = observability.request_id_var.set("req-123")
        try:
            observability.RequestIdFilter().filter(record)
            formatted = formatter.format(record)
        finally:
            observability.request_id_var.reset(token)

        payload = json.loads(formatted)
        self.assertEqual(payload["message"], "test message")
        self.assertEqual(payload["request_id"], "req-123")
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "insight_rag.api")

    def test_json_formatter_defaults_request_id_when_unset(self):
        formatter = observability.JsonFormatter()
        record = logging.LogRecord(
            name="insight_rag.api",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="no context",
            args=(),
            exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        self.assertEqual(payload["request_id"], "-")


if __name__ == "__main__":
    unittest.main()
