import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from urllib.parse import quote
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.chunking import build_chunk_hierarchy
from bookrecall.config import DEFAULT_CHUNK_SETTINGS
from bookrecall.entity_index import build_entity_records, build_event_records, build_relation_records, build_theme_records
from bookrecall.parser import parse_chapters
from bookrecall.storage import BookRecallStore
from bookrecall.web import make_server

SAMPLE_TEXT = """第1章 起点

林澈在旧书里看到【星辰之匙】的名字。

第2章 阴影

黑衣人在雨里出现，林澈与黑衣人对峙。

第3章 回声

黑衣人再次提到【星辰之匙】，林澈决定追查自由意志的真相。
"""


class BookRecallWebTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "bookrecall.db")
        store = BookRecallStore(self.db_path)
        store.initialize()

        chapters = parse_chapters(SAMPLE_TEXT)
        parents, children = build_chunk_hierarchy("sample", chapters, DEFAULT_CHUNK_SETTINGS)
        entity_records = build_entity_records(
            chapters,
            {"林澈": [], "星辰之匙": ["钥匙"], "黑衣人": ["黑袍人"]},
            DEFAULT_CHUNK_SETTINGS,
        )
        relation_records = build_relation_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
        theme_records = build_theme_records(chapters, {"自由意志": ["真相"]}, DEFAULT_CHUNK_SETTINGS)
        event_records = build_event_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)
        store.replace_book(
            book_id="sample",
            title="测试书",
            source_path="memory",
            chapters=chapters,
            parent_chunks=parents,
            child_chunks=children,
            entity_records=entity_records,
            relation_records=relation_records,
            theme_records=theme_records,
            event_records=event_records,
        )
        store.close()

        self.server = make_server("127.0.0.1", 0, self.db_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def _get_json(self, path: str) -> dict[str, object]:
        with urllib.request.urlopen(f"{self.base_url}{path}") as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_books_endpoint(self) -> None:
        data = self._get_json("/api/books")
        self.assertEqual(data["books"][0]["book_id"], "sample")

    def test_entities_endpoint(self) -> None:
        data = self._get_json("/api/books/sample/entities")
        names = [item["name"] for item in data["entities"]]
        self.assertIn("黑衣人", names)

    def test_progress_and_ask_endpoint(self) -> None:
        saved = self._post_json(
            "/api/progress",
            {"book_id": "sample", "user_id": "alice", "progress_chapter": 2},
        )
        self.assertEqual(saved["progress_chapter"], 2)

        answer = self._post_json(
            "/api/ask",
            {
                "book_id": "sample",
                "user_id": "alice",
                "question": "黑袍人第一次出现在哪一章？",
            },
        )
        self.assertEqual(answer["entity_name"], "黑衣人")
        self.assertIn("第 2 章", answer["answer"])
        self.assertIn("rendered_text", answer)

    def test_runtime_endpoint(self) -> None:
        data = self._get_json("/api/runtime")
        self.assertIn("dependencies", data)
        self.assertIn("model_cache_dir", data)
        self.assertIn("vector_indexes", data)
        providers = data["cloud"]["providers"]
        provider_ids = [item["id"] for item in providers]
        self.assertIn("deepseek", provider_ids)
        self.assertEqual(data["retrievers"][0]["id"], "lexical")

    def test_stats_themes_events_and_relations_endpoints(self) -> None:
        stats = self._get_json("/api/books/sample/stats")
        self.assertGreaterEqual(stats["stats"]["themes"], 1)
        self.assertGreaterEqual(stats["stats"]["events"], 1)
        self.assertGreaterEqual(stats["stats"]["relations"], 1)

        themes = self._get_json("/api/books/sample/themes")
        self.assertEqual(themes["themes"][0]["name"], "自由意志")

        encoded_entity = quote("黑衣人")
        events = self._get_json(f"/api/books/sample/events?entity={encoded_entity}&limit=10")
        self.assertTrue(events["events"])
        self.assertTrue(any("黑衣人" in item["entities"] for item in events["events"]))

        relations = self._get_json(f"/api/books/sample/relations?entity={encoded_entity}&limit=10")
        self.assertTrue(relations["relations"])
        self.assertTrue(
            any("黑衣人" in {item["source_entity"], item["target_entity"]} for item in relations["relations"])
        )

    def test_event_chain_question_via_api(self) -> None:
        answer = self._post_json(
            "/api/ask",
            {
                "book_id": "sample",
                "user_id": "alice",
                "session_id": "event-thread",
                "question": "黑衣人涉及哪些关键事件？",
            },
        )
        self.assertEqual(answer["intent"], "事件链回忆")
        self.assertTrue(any(item["tool_name"] == "search_events" for item in answer["trace"]))

    def test_ask_accepts_runtime_options(self) -> None:
        answer = self._post_json(
            "/api/ask",
            {
                "book_id": "sample",
                "user_id": "alice",
                "question": "黑袍人第一次出现在哪一章？",
                "retriever": "auto",
                "cloud_config": {
                    "enabled": False,
                    "endpoint": "https://api.deepseek.com/v1/chat/completions",
                    "model": "deepseek-chat",
                    "api_key": "not-used",
                },
            },
        )
        self.assertEqual(answer["runtime"]["retriever"], "auto")
        self.assertFalse(answer["runtime"]["cloud_reasoner_enabled"])
        self.assertIn("rendered_text", answer)

    def test_session_memory_via_api(self) -> None:
        first = self._post_json(
            "/api/ask",
            {
                "book_id": "sample",
                "user_id": "alice",
                "session_id": "thread-1",
                "question": "黑袍人第一次出现在哪一章？",
            },
        )
        second = self._post_json(
            "/api/ask",
            {
                "book_id": "sample",
                "user_id": "alice",
                "session_id": "thread-1",
                "question": "后来还有出现过吗？",
            },
        )
        self.assertEqual(first["entity_name"], "黑衣人")
        self.assertEqual(second["entity_name"], "黑衣人")
        self.assertIn("第 2 章", second["answer"])
        self.assertIn("第 3 章", second["answer"])
        self.assertEqual(second["session"]["session_id"], "thread-1")
        self.assertEqual(len(second["session"]["turns"]), 2)
        self.assertTrue(second["trace"])

    def test_session_endpoint(self) -> None:
        self._post_json(
            "/api/ask",
            {
                "book_id": "sample",
                "user_id": "alice",
                "session_id": "thread-2",
                "question": "黑袍人第一次出现在哪一章？",
            },
        )
        data = self._get_json("/api/books/sample/session?user=alice&session=thread-2&limit=10")
        self.assertEqual(data["session_id"], "thread-2")
        self.assertEqual(len(data["turns"]), 1)
        self.assertEqual(data["turns"][0]["entity_name"], "黑衣人")
        self.assertTrue(data["turns"][0]["trace"])

    def test_index_page(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/") as response:
            html = response.read().decode("utf-8")
        self.assertIn("BookRecall", html)
        self.assertIn("会话历史", html)
        self.assertIn("本轮工具轨迹", html)
        self.assertIn("主题线索", html)
        self.assertIn("事件链", html)
        self.assertIn("快捷提问模板", html)

    def test_chapters_endpoint(self) -> None:
        data = self._get_json("/api/books/sample/chapters")
        self.assertEqual(data["book_id"], "sample")
        numbers = [item["chapter_number"] for item in data["chapters"]]
        self.assertEqual(numbers, [1, 2, 3])
        self.assertEqual(data["chapters"][0]["title"], "起点")


if __name__ == "__main__":
    unittest.main()
