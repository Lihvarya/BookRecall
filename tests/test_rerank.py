import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.rerank import parse_rerank_payload, rerank_evidence_hits


class FakeRerankClient:
    def complete_json(self, prompt: str) -> dict:
        self.prompt = prompt
        return {
            "ranked_hits": [
                {"index": 2, "relevance": 0.95, "reason": "直接回答问题"},
                {"index": 1, "relevance": 0.2, "reason": "只是背景"},
                {"index": 99, "relevance": 1.0, "reason": "非法编号"},
            ]
        }


class RerankTest(unittest.TestCase):
    def test_parse_rerank_payload_filters_invalid_indexes(self) -> None:
        items = parse_rerank_payload(
            {
                "ranked_hits": [
                    {"index": "2", "relevance": 1.5, "reason": "相关"},
                    {"index": "2", "relevance": 0.1, "reason": "重复"},
                    {"index": "x", "relevance": 0.9},
                    {"index": 4, "relevance": 0.9},
                ]
            },
            hit_count=3,
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].index, 2)
        self.assertEqual(items[0].relevance, 1.0)

    def test_rerank_evidence_hits_reorders_without_inventing_hits(self) -> None:
        hits = [
            {"chapter_number": 1, "chapter_title": "背景", "child_text": "旧书被放在桌上。", "score": 2.0},
            {"chapter_number": 3, "chapter_title": "答案", "child_text": "星辰之匙打开了石门。", "score": 0.5},
        ]
        client = FakeRerankClient()

        result = rerank_evidence_hits("星辰之匙怎么打开石门？", hits, client)

        self.assertTrue(result.used)
        self.assertEqual(result.hits[0]["chapter_number"], 3)
        self.assertEqual(result.hits[0]["rerank_relevance"], 0.95)
        self.assertEqual(len(result.hits), 2)
        self.assertIn("候选片段", client.prompt)


if __name__ == "__main__":
    unittest.main()
