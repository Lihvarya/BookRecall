import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote
from unittest.mock import patch
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


class TinyWebEmbedder:
    def __init__(self, model_name: str, *, cache_dir: str | Path | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir

    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vectors.append(
                [
                    float(text.count("星辰之匙") + text.count("钥匙")),
                    float(text.count("黑衣人") + text.count("黑袍人")),
                    float(text.count("林澈")),
                ]
            )
        return vectors


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
        self.assertIn("book_group", data["books"][0])
        self.assertIn("tags", data["books"][0])

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
        policy_ids = [item["id"] for item in data["agent_policies"]]
        self.assertIn("rule_based", policy_ids)
        self.assertIn("langgraph", policy_ids)

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

    def test_book_metadata_endpoint(self) -> None:
        saved = self._post_json(
            "/api/books/sample/metadata",
            {
                "book_group": "测试分组",
                "tags": "玄幻, 二刷, 重点",
            },
        )
        self.assertEqual(saved["book"]["book_group"], "测试分组")
        self.assertEqual(saved["book"]["tags"], ["玄幻", "二刷", "重点"])

        books = self._get_json("/api/books")
        sample = next(item for item in books["books"] if item["book_id"] == "sample")
        self.assertEqual(sample["book_group"], "测试分组")
        self.assertIn("重点", sample["tags"])

    def test_build_book_endpoint_from_pasted_text(self) -> None:
        created = self._post_json(
            "/api/books/build",
            {
                "book_id": "web-built",
                "title": "网页导入书",
                "text": SAMPLE_TEXT,
                "entities": "黑衣人|黑袍人\n星辰之匙|钥匙\n林澈",
                "themes": "自由意志|真相",
                "source_name": "web_book.txt",
            },
        )
        self.assertEqual(created["book"]["book_id"], "web-built")
        self.assertGreaterEqual(created["book"]["entities"], 3)
        self.assertGreaterEqual(created["book"]["events"], 1)

        books = self._get_json("/api/books")
        built_book = next(item for item in books["books"] if item["book_id"] == "web-built")
        self.assertEqual(built_book["source_path"], "web://file/web_book.txt")

        answer = self._post_json(
            "/api/ask",
            {
                "book_id": "web-built",
                "question": "黑袍人第一次出现在哪一章？",
            },
        )
        self.assertEqual(answer["entity_name"], "黑衣人")
        self.assertIn("第 2 章", answer["answer"])

    def test_build_book_endpoint_requires_overwrite_for_existing_book(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._post_json(
                "/api/books/build",
                {
                    "book_id": "sample",
                    "title": "重复书",
                    "text": SAMPLE_TEXT,
                },
            )
        self.assertEqual(context.exception.code, 400)

    def test_rebuild_book_endpoint(self) -> None:
        rebuilt = self._post_json(
            "/api/books/sample/rebuild",
            {
                "entities": "黑衣人|黑袍人\n星辰之匙|钥匙\n林澈",
                "themes": "自由意志|真相",
            },
        )
        self.assertEqual(rebuilt["book"]["book_id"], "sample")
        self.assertGreaterEqual(rebuilt["book"]["entities"], 3)
        self.assertGreaterEqual(rebuilt["book"]["child_chunks"], 1)

    def test_build_vector_index_endpoint(self) -> None:
        with patch("bookrecall.web.SentenceTransformerEmbedder", TinyWebEmbedder):
            data = self._post_json(
                "/api/books/sample/vectors",
                {
                    "model": "test-web-embedder",
                    "backend": "numpy",
                    "limit_chunks": 2,
                },
            )

        info = data["vector_index"]
        self.assertEqual(info["book_id"], "sample")
        self.assertEqual(info["model_name"], "test-web-embedder")
        self.assertEqual(info["backend"], "numpy")
        self.assertEqual(info["chunk_count"], 2)
        self.assertTrue(Path(info["path"]).exists())

        runtime = self._get_json("/api/runtime")
        sample_index = next(item for item in runtime["vector_indexes"] if item["book_id"] == "sample")
        self.assertTrue(sample_index["built"])
        self.assertEqual(sample_index["model_name"], "test-web-embedder")

        deleted = self._post_json("/api/books/sample/vectors/delete", {})
        self.assertGreaterEqual(deleted["vector_index"]["deleted_count"], 1)
        self.assertFalse(Path(info["path"]).exists())

    def test_search_endpoint_lexical(self) -> None:
        data = self._post_json(
            "/api/books/sample/search",
            {
                "query": "黑衣人 雨里",
                "retriever": "lexical",
                "progress_chapter": 2,
                "limit": 5,
            },
        )
        search = data["search"]
        self.assertEqual(search["book_id"], "sample")
        self.assertEqual(search["retriever"], "lexical")
        self.assertTrue(search["hits"])
        self.assertLessEqual(max(item["chapter_number"] for item in search["hits"]), 2)
        self.assertTrue(any("黑衣人" in item["child_text"] for item in search["hits"]))

    def test_search_endpoint_embedding(self) -> None:
        with patch("bookrecall.web.SentenceTransformerEmbedder", TinyWebEmbedder):
            self._post_json(
                "/api/books/sample/vectors",
                {
                    "model": "test-web-embedder",
                    "backend": "numpy",
                },
            )
            data = self._post_json(
                "/api/books/sample/search",
                {
                    "query": "钥匙",
                    "retriever": "embedding",
                    "limit": 3,
                },
            )
        search = data["search"]
        self.assertEqual(search["retriever"], "embedding")
        self.assertEqual(search["effective_retriever"], "EmbeddingRetriever")
        self.assertTrue(search["hits"])

    def test_delete_book_endpoint(self) -> None:
        deleted = self._post_json("/api/books/sample/delete", {})
        self.assertEqual(deleted["deleted"]["book_id"], "sample")
        self.assertGreaterEqual(deleted["deleted"]["deleted_chunks"], 1)
        books = self._get_json("/api/books")
        self.assertNotIn("sample", [item["book_id"] for item in books["books"]])

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
                "agent_policy": "rule_based",
                "retriever": "auto",
                "cloud_config": {
                    "enabled": False,
                    "endpoint": "https://api.deepseek.com/v1/chat/completions",
                    "model": "deepseek-chat",
                    "api_key": "not-used",
                },
            },
        )
        self.assertEqual(answer["runtime"]["agent_policy"], "rule_based")
        self.assertEqual(answer["runtime"]["effective_policy"], "rule_based")
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
        self.assertIn("turn_id", second["session"]["turns"][0])
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
        turn_id = data["turns"][0]["turn_id"]

        updated = self._post_json(
            f"/api/books/sample/session/turns/{turn_id}",
            {
                "operation": "update",
                "user_id": "alice",
                "session_id": "thread-2",
                "question": "编辑后的问题",
                "answer": "编辑后的回答",
                "summary": "人工修订",
            },
        )
        self.assertEqual(updated["session"]["turn"]["question"], "编辑后的问题")
        self.assertEqual(updated["session"]["turn"]["answer"], "编辑后的回答")

        removed = self._post_json(
            f"/api/books/sample/session/turns/{turn_id}",
            {
                "operation": "delete",
                "user_id": "alice",
                "session_id": "thread-2",
            },
        )
        self.assertEqual(removed["session"]["deleted"], 1)
        empty = self._get_json("/api/books/sample/session?user=alice&session=thread-2&limit=10")
        self.assertEqual(empty["turns"], [])

    def test_index_page(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/") as response:
            html = response.read().decode("utf-8")
        self.assertIn("BookRecall", html)
        self.assertIn('/assets/app.css', html)
        self.assertIn('/assets/app.js', html)
        self.assertIn("会话历史", html)
        self.assertIn("本轮工具轨迹", html)
        self.assertIn("主题线索", html)
        self.assertIn("事件链", html)
        self.assertIn("快捷提问模板", html)
        self.assertIn("导入书籍并建索引", html)
        self.assertIn("为当前书构建向量索引", html)
        self.assertIn("测试当前召回层", html)
        self.assertIn('type="file"', html)
        self.assertIn("不预览全文", html)
        self.assertIn("重建当前书结构化索引", html)
        self.assertIn("删除当前书数据", html)
        self.assertIn("删除当前书向量索引", html)
        self.assertIn("原文阅读器", html)
        self.assertIn("分组筛选", html)
        self.assertIn("书籍分组与标签", html)
        self.assertIn("Agent 执行策略", html)
        self.assertIn("保存控制台偏好", html)

    def test_static_assets(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/assets/app.css") as response:
            css = response.read().decode("utf-8")
            self.assertIn("text/css", response.headers["Content-Type"])
        self.assertIn("--primary", css)
        self.assertIn(".answer-card", css)
        self.assertIn(".chapter-reader", css)

        with urllib.request.urlopen(f"{self.base_url}/assets/app.js") as response:
            js = response.read().decode("utf-8")
            self.assertIn("javascript", response.headers["Content-Type"])
        self.assertIn("loadBooks", js)
        self.assertIn("askQuestion", js)
        self.assertIn("openChapter", js)
        self.assertIn("saveBookMetadata", js)
        self.assertIn("bookrecall.preferences", js)
        self.assertIn("bookrecall.apiSettings", js)
        self.assertIn("saveLocalPreferences", js)
        self.assertIn("policySelect", js)
        self.assertIn("readSelectedBookFile", js)
        self.assertIn("importedBookText", js)
        self.assertNotIn("els.buildTextInput.value = text", js)
        self.assertIn("deleteCurrentBook", js)
        self.assertIn("deleteCurrentVectorIndex", js)
        self.assertIn("handleSessionAction", js)
        self.assertIn("查看轨迹", js)
        self.assertIn("renderTrace(turn.trace || [])", js)
        self.assertIn("searchEvidenceFromPanel", js)
        self.assertIn("renderSearchResults", js)

    def test_chapters_endpoint(self) -> None:
        data = self._get_json("/api/books/sample/chapters")
        self.assertEqual(data["book_id"], "sample")
        numbers = [item["chapter_number"] for item in data["chapters"]]
        self.assertEqual(numbers, [1, 2, 3])
        self.assertEqual(data["chapters"][0]["title"], "起点")

    def test_chapter_detail_endpoint(self) -> None:
        data = self._get_json("/api/books/sample/chapters/2")
        chapter = data["chapter"]
        self.assertEqual(chapter["book_id"], "sample")
        self.assertEqual(chapter["chapter_number"], 2)
        self.assertEqual(chapter["title"], "阴影")
        self.assertIn("黑衣人", chapter["content"])


if __name__ == "__main__":
    unittest.main()
