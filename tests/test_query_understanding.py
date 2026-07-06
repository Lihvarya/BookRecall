import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.query_understanding import (
    parse_query_understanding_payload,
    understand_query_with_llm,
    understand_query_with_rules,
)


class FakeQueryClient:
    def complete_json(self, prompt: str) -> dict:
        self.prompt = prompt
        return {
            "intent": "entity_timeline",
            "entities": ["黑衣人"],
            "themes": [],
            "time_range": {"start_chapter": None, "end_chapter": 50, "relative": "after"},
            "spoiler_sensitive": True,
            "tools": ["lookup_timeline", "unknown_tool"],
            "confidence": 0.88,
        }


class QueryUnderstandingTest(unittest.TestCase):
    def test_parse_query_understanding_payload_clamps_fields(self) -> None:
        parsed = parse_query_understanding_payload(
            {
                "intent": "made_up",
                "entities": ["黑衣人", "黑衣人", ""],
                "time_range": {"start_chapter": "2", "end_chapter": "x", "relative": "after"},
                "spoiler_sensitive": False,
                "tools": ["search_evidence", "bad_tool"],
                "confidence": 2,
            }
        )

        self.assertEqual(parsed.intent, "semantic_search")
        self.assertEqual(parsed.entities, ["黑衣人"])
        self.assertEqual(parsed.time_range.start_chapter, 2)
        self.assertIsNone(parsed.time_range.end_chapter)
        self.assertEqual(parsed.tools, ["search_evidence"])
        self.assertEqual(parsed.confidence, 1.0)

    def test_understand_query_with_llm_uses_context_hints(self) -> None:
        client = FakeQueryClient()

        parsed = understand_query_with_llm(
            "他后来呢？",
            client,
            known_entities=["黑衣人", "林澈"],
            recent_entities=["黑衣人"],
            progress_chapter=10,
            max_chapter=100,
        )

        self.assertEqual(parsed.intent, "entity_timeline")
        self.assertEqual(parsed.entities, ["黑衣人"])
        self.assertEqual(parsed.tools, ["lookup_timeline"])
        self.assertIn("最近会话实体：黑衣人", client.prompt)

    def test_rule_understanding_keeps_spoiler_signal(self) -> None:
        parsed = understand_query_with_rules("可以剧透，黑衣人后面怎么样？", matched_entities=["黑衣人"])

        self.assertEqual(parsed.intent, "entity_timeline")
        self.assertFalse(parsed.spoiler_sensitive)


if __name__ == "__main__":
    unittest.main()
