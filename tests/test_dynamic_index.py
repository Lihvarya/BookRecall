import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.dynamic_index import build_dynamic_index_records


class FakeDynamicClient:
    def complete_json(self, prompt: str) -> dict:
        return {
            "entities": [
                {
                    "name": "李四",
                    "aliases": [],
                    "evidence": "李四被王五刺中后倒在雨里",
                    "confidence": 0.9,
                }
            ],
            "relations": [
                {
                    "source": "王五",
                    "target": "李四",
                    "type": "冲突",
                    "evidence": "李四被王五刺中后倒在雨里",
                    "confidence": 0.9,
                }
            ],
            "events": [
                {
                    "type": "冲突/危机",
                    "summary": "王五刺伤李四",
                    "evidence": "李四被王五刺中后倒在雨里",
                    "entities": ["王五", "李四"],
                    "confidence": 0.9,
                }
            ],
        }


class DynamicIndexTest(unittest.TestCase):
    def test_build_dynamic_index_records_validates_evidence_from_hits(self) -> None:
        hits = [
            {
                "chapter_number": 8,
                "chapter_title": "雨夜",
                "child_text": "李四被王五刺中后倒在雨里，众人这才明白旧案真相。",
            }
        ]

        entities, relations, events, report = build_dynamic_index_records(
            question="李四是怎么死的？",
            hits=hits,
            client=FakeDynamicClient(),
            known_entities=["王五"],
        )

        self.assertTrue(report["used"])
        self.assertEqual(entities[0].name, "李四")
        self.assertEqual(relations[0].relation_type, "冲突")
        self.assertEqual(events[0].summary, "王五刺伤李四")


if __name__ == "__main__":
    unittest.main()
