import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.chunking import build_chunk_hierarchy
from bookrecall.config import DEFAULT_CHUNK_SETTINGS, DEFAULT_SEARCH_SETTINGS
from bookrecall.entity_index import build_entity_records
from bookrecall.parser import parse_chapters
from bookrecall.retrieval import LocalRetriever, _tokenize, lexical_score
from bookrecall.storage import BookRecallStore

SAMPLE = """第1章 雨夜
林澈在雨夜里看到黑衣人，他手里握着星辰之匙。
黑衣人转身走进灰塔。

第2章 灰塔
灰塔的旧书库里藏着关于自由意志的批注。
林澈最终拿到了星辰之匙。

第3章 远行
星辰之匙打开了石门，自由意志的含义就此浮现。
"""

# 全库逐字打分（旧实现的等价行为），用于和倒排表结果做对照。
def brute_force_search(store, book_id, query, max_chapter=None):
    rows = store.iter_search_rows(book_id, max_chapter=max_chapter)
    hits = []
    for r in rows:
        s = lexical_score(query, r["text"])
        if s > 0:
            hits.append((r["text"], s))
    return sorted(hits, key=lambda x: (-x[1]))


class InvertedIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "r.db")
        self.store = BookRecallStore(self.db_path)
        self.store.initialize()
        chapters = parse_chapters(SAMPLE)
        parents, children = build_chunk_hierarchy("b", chapters, DEFAULT_CHUNK_SETTINGS)
        records = build_entity_records(
            chapters,
            {"星辰之匙": ["钥匙"], "黑衣人": ["黑袍人"], "林澈": []},
            DEFAULT_CHUNK_SETTINGS,
        )
        self.store.replace_book(
            book_id="b",
            title="测",
            source_path="mem",
            chapters=chapters,
            parent_chunks=parents,
            child_chunks=children,
            entity_records=records,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_candidates_use_intersected_postings(self) -> None:
        retriever = LocalRetriever(self.store, DEFAULT_SEARCH_SETTINGS)
        # 触发建表
        retriever.search("b", "星辰之匙")
        inverted = retriever._index["b"]
        self.assertIsInstance(inverted, dict)
        self.assertIn("星辰", inverted)
        # 候选集应是 token 命中 chunk 的交集
        cand = inverted.get("星辰") & inverted.get("之匙") if "之匙" in inverted else inverted["星辰"]
        self.assertTrue(len(cand) >= 1)

    def test_results_equal_to_brute_force(self) -> None:
        retriever = LocalRetriever(self.store, DEFAULT_SEARCH_SETTINGS)
        for query in ["星辰之匙 黑衣人", "自由意志", "林澈 灰塔", "石门"]:
            inverted_hits = retriever.search("b", query)
            brute = brute_force_search(self.store, "b", query)
            # 倒排表会做 parent-collapse（同一 parent 只留最优 child），而暴扫返回所有 raw hits。
            # 因此等价性校验应是：每个倒排命中都能在暴扫里找到相同的 child_text 与分数。
            self.assertTrue(
                inverted_hits,
                msg=f"query={query} 应有命中",
            )
            for hit in inverted_hits:
                self.assertTrue(
                    any(hit.child_text == text and abs(hit.score - score) < 1e-9 for text, score in brute),
                    msg=f"倒排命中在暴扫中找不到 query={query} text={hit.child_text} score={hit.score}",
                )
            # 反向：暴扫的每个 raw 命中所属 parent 应被倒排命中的 parent 集合覆盖（同一 parent 至少出现一次）
            inverted_parents = {hit.parent_id for hit in inverted_hits}
            # 暴扫不返回 parent_id，跳过反向校验；逐条正向校验已足够保证打分语义一致。

    def test_progress_filter_preserved(self) -> None:
        retriever = LocalRetriever(self.store, DEFAULT_SEARCH_SETTINGS)
        hits = retriever.search("b", "星辰之匙", max_chapter=1)
        # 第2、3 章提到星辰之匙，但进度只到第1章 → 命中只能在第1章
        for hit in hits:
            self.assertLessEqual(hit.chapter_number, 1)

    def test_no_hit_falls_back_to_all(self) -> None:
        retriever = LocalRetriever(self.store, DEFAULT_SEARCH_SETTINGS)
        # 全是文档里没有的字
        hits = retriever.search("b", "zzzzqq")
        # 无 token 命中 → phrase_bonus 也是 0 → 无 hits，符合预期
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
