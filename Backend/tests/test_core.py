import os
import asyncio
import sqlite3
import tempfile
from io import BytesIO
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document
from fastapi import HTTPException, UploadFile
from pypdf import PdfWriter

import charbot
import chatbot_cache
import database
import main
from auth import (
    AuthenticationError,
    create_access_token,
    hash_password,
    verify_access_token,
    verify_password,
)
from charbot import HybridVectorStore, LocalHashEmbeddings, LocalVectorStore, RAGChatbot
from evaluation import evaluate_generation, evaluate_retrieval, judge_answer
from main import read_and_validate_pdf
from security import SlidingWindowRateLimiter


os.environ["AUTH_SECRET"] = "test-secret-that-is-longer-than-thirty-two-characters"


class AuthenticationTests(unittest.TestCase):
    def test_passwords_are_salted_and_verified(self):
        first = hash_password("correct-horse-battery")
        second = hash_password("correct-horse-battery")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("correct-horse-battery", first))
        self.assertFalse(verify_password("wrong-password", first))

    def test_signed_token_rejects_tampering_and_expiry(self):
        with patch("auth.time.time", return_value=1_000):
            token, _ = create_access_token("usr_test", "test@example.com")
            self.assertEqual(verify_access_token(token)["sub"], "usr_test")
        with self.assertRaises(AuthenticationError):
            verify_access_token(token + "tampered")
        with patch("auth.time.time", return_value=1_000_000):
            with self.assertRaises(AuthenticationError):
                verify_access_token(token)


class ProductionControlTests(unittest.TestCase):
    def test_sliding_window_rate_limiter_returns_retry_after(self):
        now = [100.0]
        limiter = SlidingWindowRateLimiter(clock=lambda: now[0])
        self.assertEqual(limiter.check("login:user", 2, 10), 0)
        self.assertEqual(limiter.check("login:user", 2, 10), 0)
        self.assertEqual(limiter.check("login:user", 2, 10), 10)
        now[0] = 111.0
        self.assertEqual(limiter.check("login:user", 2, 10), 0)

    def test_pdf_validation_returns_hash_size_and_pages(self):
        buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(buffer)
        contents = buffer.getvalue()
        result = asyncio.run(
            read_and_validate_pdf(UploadFile(filename="safe.pdf", file=BytesIO(contents)))
        )
        self.assertEqual(result[0], "safe.pdf")
        self.assertEqual(result[1], contents)
        self.assertEqual(len(result[2]), 64)
        self.assertEqual(result[3], 1)

    def test_pdf_validation_rejects_spoofed_extension(self):
        with self.assertRaises(HTTPException) as context:
            asyncio.run(
                read_and_validate_pdf(
                    UploadFile(filename="fake.pdf", file=BytesIO(b"not a real PDF"))
                )
            )
        self.assertEqual(context.exception.status_code, 415)


class RetrievalTests(unittest.TestCase):
    def test_retrieval_restores_original_document_name(self):
        chatbot = object.__new__(RAGChatbot)
        stored_path = str(Path("uploads") / "chat_internal_doc_internal.pdf")
        chatbot.file_names = {
            str(Path(stored_path).resolve()): "research-paper.pdf"
        }

        class VectorStore:
            def similarity_search_with_score(self, question, k=4):
                return [(
                    Document(
                        page_content="Evidence",
                        metadata={"source": stored_path, "fileName": "chat_internal_doc_internal.pdf"},
                    ),
                    0.9,
                )]

        chatbot.vector_store = VectorStore()
        result = chatbot.retrieve("question", k=1)
        self.assertEqual(result[0].metadata["fileName"], "research-paper.pdf")

    def test_provider_content_normalizes_text_blocks(self):
        content = [
            {"type": "text", "text": "Grounded "},
            {"type": "text", "content": "answer."},
        ]
        self.assertEqual(RAGChatbot._content_text(content), "Grounded answer.")

    def test_empty_provider_stream_falls_back_to_completion(self):
        chatbot = object.__new__(RAGChatbot)
        chatbot.api_key = "test-key"
        chatbot.model = "openrouter/free"
        chatbot.retrieve = lambda question, k=4: [
            Document(page_content="Evidence", metadata={"page": 0, "fileName": "test.pdf"})
        ]

        class EmptyStream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                return None

            def iter_lines(self):
                return iter([
                    'data: {"choices":[{"delta":{"reasoning":"thinking"}}]}',
                    "data: [DONE]",
                ])

        with patch("charbot.httpx.stream", return_value=EmptyStream()), patch.object(
            chatbot, "_complete", return_value="Fallback answer [Source 1]."
        ) as fallback:
            events = list(chatbot.stream("What is it?", []))

        self.assertEqual(events[-1]["token"], "Fallback answer [Source 1].")
        fallback.assert_called_once()

    def test_related_text_scores_above_unrelated_text(self):
        embeddings = LocalHashEmbeddings(dimensions=128)
        query = embeddings.embed_query("credit risk classifier")
        related = embeddings.embed_query("credit risk assessment classifier")
        unrelated = embeddings.embed_query("cooking recipe ingredients")
        related_score = sum(a * b for a, b in zip(query, related))
        unrelated_score = sum(a * b for a, b in zip(query, unrelated))
        self.assertGreater(related_score, unrelated_score)

    def test_vector_store_persists_and_returns_page_metadata(self):
        embeddings = LocalHashEmbeddings(dimensions=128)
        documents = [
            Document(page_content="credit risk and lending", metadata={"page": 2}),
            Document(page_content="weather and rainfall", metadata={"page": 7}),
        ]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = str(Path(directory) / "index.json")
            LocalVectorStore.from_documents(documents, embeddings, path)
            results = LocalVectorStore.load(path, embeddings).similarity_search_with_score(
                "credit lending", k=1
            )
            self.assertEqual(results[0][0].metadata["page"], 2)
            self.assertGreater(results[0][1], 0)

    def test_retrieval_evaluation_reports_hit_rate_and_mrr(self):
        class FakeChatbot:
            def retrieve(self, question, k=4):
                pages = [1, 4] if "risk" in question else [7, 8]
                return [
                    Document(page_content="evidence", metadata={"page": page - 1, "relevance": 0.8})
                    for page in pages[:k]
                ]

        metrics = evaluate_retrieval(
            FakeChatbot(),
            [
                {"question": "credit risk", "relevantPages": [4]},
                {"question": "rainfall", "relevantPages": [2]},
            ],
            k=2,
        )
        self.assertEqual(metrics["retrievalHitRate"], 0.5)
        self.assertEqual(metrics["meanReciprocalRank"], 0.25)

    def test_hybrid_retrieval_recovers_exact_lexical_evidence(self):
        keyword_document = Document(
            page_content="Policy identifier ZXQ-991 authorizes the exception.",
            metadata={"page": 4, "chunk_id": "keyword"},
        )
        unrelated = Document(
            page_content="General background and introductory discussion.",
            metadata={"page": 1, "chunk_id": "unrelated"},
        )

        class DenseStore:
            def all_documents(self):
                return [unrelated, keyword_document]

            def similarity_search_with_score(self, query, k):
                return [(unrelated, 0.9)]

        hybrid = HybridVectorStore(DenseStore(), LocalHashEmbeddings(64))
        results = hybrid.similarity_search_with_score("ZXQ-991", k=1)
        self.assertEqual(results[0][0].metadata["chunk_id"], "keyword")
        self.assertIn("lexical", results[0][0].metadata["retrievalSignals"])


class GenerationEvaluationTests(unittest.TestCase):
    def test_judge_answer_parses_well_formed_response(self):
        class FakeChatbot:
            def _complete(self, messages):
                return '{"faithfulness": 0.9, "answerRelevancy": 0.8, "reasoning": "Grounded in evidence."}'

        context = [Document(page_content="Evidence text", metadata={})]
        result = judge_answer(FakeChatbot(), "question?", "answer text", context)
        self.assertFalse(result["parseError"])
        self.assertEqual(result["faithfulness"], 0.9)
        self.assertEqual(result["answerRelevancy"], 0.8)

    def test_judge_answer_extracts_json_from_surrounding_prose(self):
        class FakeChatbot:
            def _complete(self, messages):
                return (
                    'Sure thing! {"faithfulness": 0.5, "answerRelevancy": 1, "reasoning": "ok"} '
                    "Hope that helps."
                )

        result = judge_answer(FakeChatbot(), "question?", "answer text", [])
        self.assertFalse(result["parseError"])
        self.assertEqual(result["faithfulness"], 0.5)
        self.assertEqual(result["answerRelevancy"], 1.0)

    def test_judge_answer_degrades_gracefully_on_malformed_response(self):
        class FakeChatbot:
            def _complete(self, messages):
                return "I cannot comply with returning JSON."

        result = judge_answer(FakeChatbot(), "question?", "answer text", [])
        self.assertTrue(result["parseError"])
        self.assertEqual(result["faithfulness"], 0.0)
        self.assertEqual(result["answerRelevancy"], 0.0)

    def test_judge_answer_degrades_gracefully_when_provider_call_fails(self):
        class FakeChatbot:
            def _complete(self, messages):
                raise RuntimeError("provider unavailable")

        result = judge_answer(FakeChatbot(), "question?", "answer text", [])
        self.assertTrue(result["parseError"])
        self.assertEqual(result["faithfulness"], 0.0)

    def test_evaluate_generation_aggregates_scores_across_cases(self):
        class FakeChatbot:
            def retrieve(self, question, k=4):
                return [Document(page_content="evidence", metadata={"page": 0})]

            @staticmethod
            def _request_messages(context, messages):
                return [{"role": "user", "content": "prompt"}]

        chatbot = FakeChatbot()
        chatbot._complete = MagicMock(
            side_effect=[
                "Generated answer one.",
                '{"faithfulness": 1.0, "answerRelevancy": 0.6, "reasoning": "ok"}',
                "Generated answer two.",
                '{"faithfulness": 0.0, "answerRelevancy": 0.4, "reasoning": "ok"}',
            ]
        )

        metrics = evaluate_generation(chatbot, [{"question": "q1"}, {"question": "q2"}], k=4)
        self.assertEqual(metrics["caseCount"], 2)
        self.assertEqual(metrics["averageFaithfulness"], 0.5)
        self.assertEqual(metrics["averageAnswerRelevancy"], 0.5)
        self.assertEqual(chatbot._complete.call_count, 4)

    def test_evaluate_generation_handles_answer_generation_failure(self):
        class FakeChatbot:
            def retrieve(self, question, k=4):
                return []

            @staticmethod
            def _request_messages(context, messages):
                return []

        chatbot = FakeChatbot()
        chatbot._complete = MagicMock(side_effect=RuntimeError("provider down"))

        metrics = evaluate_generation(chatbot, [{"question": "q1"}], k=4)
        self.assertEqual(metrics["caseCount"], 1)
        self.assertTrue(metrics["cases"][0]["parseError"])
        self.assertEqual(metrics["averageFaithfulness"], 0.0)


class CachingTests(unittest.TestCase):
    def setUp(self):
        charbot.reset_embeddings_cache()
        chatbot_cache.clear()

    def tearDown(self):
        charbot.reset_embeddings_cache()
        chatbot_cache.clear()
        os.environ.pop("EMBEDDING_BACKEND", None)

    def test_embeddings_singleton_returns_same_instance(self):
        os.environ["EMBEDDING_BACKEND"] = "hash"
        first = charbot.create_embeddings()
        second = charbot.create_embeddings()
        self.assertIs(first, second)

    def test_chatbot_cache_round_trip_ttl_and_signature_mismatch(self):
        now = [1_000.0]
        with patch("chatbot_cache.time.monotonic", side_effect=lambda: now[0]):
            chatbot_cache.put("chat_x", ["a.pdf"], "bot-instance", ttl_seconds=10, max_size=5)
            self.assertEqual(chatbot_cache.get("chat_x", ["a.pdf"]), "bot-instance")
            self.assertIsNone(chatbot_cache.get("chat_x", ["a.pdf", "b.pdf"]))
            now[0] += 11
            self.assertIsNone(chatbot_cache.get("chat_x", ["a.pdf"]))

    def test_chatbot_cache_invalidate(self):
        chatbot_cache.put("chat_y", ["a.pdf"], "bot", ttl_seconds=100, max_size=5)
        chatbot_cache.invalidate("chat_y")
        self.assertIsNone(chatbot_cache.get("chat_y", ["a.pdf"]))

    def test_chatbot_cache_evicts_least_recently_used(self):
        chatbot_cache.put("chat_1", ["a.pdf"], "bot-1", ttl_seconds=100, max_size=2)
        chatbot_cache.put("chat_2", ["a.pdf"], "bot-2", ttl_seconds=100, max_size=2)
        chatbot_cache.get("chat_1", ["a.pdf"])
        chatbot_cache.put("chat_3", ["a.pdf"], "bot-3", ttl_seconds=100, max_size=2)
        self.assertIsNone(chatbot_cache.get("chat_2", ["a.pdf"]))
        self.assertEqual(chatbot_cache.get("chat_1", ["a.pdf"]), "bot-1")
        self.assertEqual(chatbot_cache.get("chat_3", ["a.pdf"]), "bot-3")

    def test_get_chatbot_reuses_cached_instance_across_calls(self):
        constructed = []

        def fake_constructor(document_paths, chat_id, file_names):
            constructed.append(chat_id)
            return object()

        with patch("main.RAGChatbot", side_effect=fake_constructor):
            first = asyncio.run(main.get_chatbot("chat_reuse", ["a.pdf"], ["a.pdf"]))
            second = asyncio.run(main.get_chatbot("chat_reuse", ["a.pdf"], ["a.pdf"]))

        self.assertIs(first, second)
        self.assertEqual(len(constructed), 1)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.previous_path = os.environ.get("RAGFLOW_DB_PATH")
        os.environ["RAGFLOW_DB_PATH"] = str(Path(self.temp_dir.name) / "test.db")
        database.initialize_database()
        self.owner = database.create_user("owner@example.com", "Owner", "hash")
        self.other = database.create_user("other@example.com", "Other", "hash")

    def tearDown(self):
        if self.previous_path is None:
            os.environ.pop("RAGFLOW_DB_PATH", None)
        else:
            os.environ["RAGFLOW_DB_PATH"] = self.previous_path
        self.temp_dir.cleanup()

    def test_conversation_sources_are_persisted_and_tenant_isolated(self):
        chat_id = "chat_test"
        database.create_conversation(self.owner["id"], chat_id, "sample.pdf", "sample.pdf")
        database.create_document(
            self.owner["id"], chat_id, "doc_one", "sample.pdf", "sample.pdf",
            "hash-one", 2048, 3
        )
        database.create_document(
            self.owner["id"], chat_id, "doc_two", "appendix.pdf", "appendix.pdf"
        )
        database.append_message(self.owner["id"], chat_id, "human", "What is this?")
        database.append_message(
            self.owner["id"],
            chat_id,
            "ai",
            "A grounded answer [Source 1].",
            [{"id": 1, "page": 3, "snippet": "Evidence"}],
        )
        messages = database.get_messages(self.owner["id"], chat_id)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["sources"][0]["page"], 3)
        self.assertIsNone(database.get_conversation(self.other["id"], chat_id))
        self.assertEqual(database.get_messages(self.other["id"], chat_id), [])
        self.assertEqual(database.list_conversations(self.other["id"]), [])
        self.assertEqual(len(database.list_documents(self.owner["id"], chat_id)), 2)
        first_document = database.list_documents(self.owner["id"], chat_id)[0]
        self.assertEqual(first_document["byteSize"], 2048)
        self.assertEqual(first_document["pageCount"], 3)
        self.assertTrue(database.document_hash_exists(self.owner["id"], chat_id, "hash-one"))
        with self.assertRaises(sqlite3.IntegrityError):
            database.create_document(
                self.owner["id"], chat_id, "doc_duplicate", "copy.pdf", "copy.pdf",
                "hash-one", 2048, 3
            )
        self.assertEqual(database.list_conversations(self.owner["id"])[0]["documentCount"], 2)
        self.assertEqual(database.list_documents(self.other["id"], chat_id), [])


class MigrationTests(unittest.TestCase):
    def test_modern_workspace_does_not_gain_legacy_duplicate_document(self):
        previous_path = os.environ.get("RAGFLOW_DB_PATH")
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = str(Path(directory) / "modern.db")
            os.environ["RAGFLOW_DB_PATH"] = path
            try:
                database.initialize_database()
                owner = database.create_user("modern@example.com", "Modern", "hash")
                database.create_conversation(owner["id"], "chat_modern", "paper.pdf", "stored.pdf")
                database.create_document(
                    owner["id"], "chat_modern", "doc_modern", "paper.pdf", "stored.pdf",
                    "modern-hash", 1024, 4
                )
                database.initialize_database()
                self.assertEqual(len(database.list_documents(owner["id"], "chat_modern")), 1)
            finally:
                if previous_path is None:
                    os.environ.pop("RAGFLOW_DB_PATH", None)
                else:
                    os.environ["RAGFLOW_DB_PATH"] = previous_path

    def test_existing_conversations_table_gains_owner_column_before_index(self):
        previous_path = os.environ.get("RAGFLOW_DB_PATH")
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = str(Path(directory) / "legacy.db")
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY, file_name TEXT NOT NULL, file_path TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    last_message TEXT NOT NULL DEFAULT '', message_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'ready'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO conversations
                (id, file_name, file_path, created_at, updated_at)
                VALUES ('chat_legacy', 'legacy.pdf', 'legacy.pdf', '2026-01-01', '2026-01-01')
                """
            )
            connection.commit()
            connection.close()
            os.environ["RAGFLOW_DB_PATH"] = path
            try:
                database.initialize_database()
                with database.connect() as migrated:
                    columns = {
                        row["name"]
                        for row in migrated.execute("PRAGMA table_info(conversations)").fetchall()
                    }
                self.assertIn("owner_id", columns)
                with database.connect() as migrated:
                    document_count = migrated.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                self.assertEqual(document_count, 1)
            finally:
                if previous_path is None:
                    os.environ.pop("RAGFLOW_DB_PATH", None)
                else:
                    os.environ["RAGFLOW_DB_PATH"] = previous_path


if __name__ == "__main__":
    unittest.main()
