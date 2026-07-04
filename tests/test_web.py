import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.chunking import build_chunk_hierarchy
from bookrecall.config import DEFAULT_CHUNK_SETTINGS
from bookrecall.entity_index import build_entity_records
from bookrecall.parser import parse_chapters
from bookrecall.storage import BookRecallStore
from bookrecall.web import make_server

SAMPLE_TEXT = """第1章 起点

林澈在旧书里看到【星辰之匙】的名字。

第2章 阴影

黑衣人在雨里出现。

第3章 回声

黑衣人再次提到【星辰之匙】。
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
            {"星辰之匙": ["钥匙"], "黑衣人": ["黑袍人"]},
            DEFAULT_CHUNK_SETTINGS,
        )
        store.replace_book(
            book_id="sample",
            title="测试书",
            source_path="memory",
            chapters=chapters,
            parent_chunks=parents,
            child_chunks=children,
            entity_records=entity_records,
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

    def test_index_page(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/") as response:
            html = response.read().decode("utf-8")
        self.assertIn("BookRecall", html)
        self.assertIn("唤醒这段记忆", html)

    def test_chapters_endpoint(self) -> None:
        data = self._get_json("/api/books/sample/chapters")
        self.assertEqual(data["book_id"], "sample")
        numbers = [item["chapter_number"] for item in data["chapters"]]
        self.assertEqual(numbers, [1, 2, 3])
        self.assertEqual(data["chapters"][0]["title"], "起点")


if __name__ == "__main__":
    unittest.main()
