import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.config import DEFAULT_CHUNK_SETTINGS
from bookrecall.entity_index import auto_discover_entities, build_entity_records, build_relation_records
from bookrecall.parser import parse_chapters
from bookrecall.smart_index import build_smart_relation_event_records, discover_entities_with_llm


class FakeSmartClient:
    def complete_json(self, prompt: str) -> dict:
        if "识别真正有索引价值的专名实体" in prompt:
            return {
                "entities": [
                    {"name": "林澈", "type": "人物", "aliases": [], "evidence": "林澈与黑衣人对峙", "confidence": 0.9},
                    {"name": "黑衣人", "type": "人物", "aliases": ["黑袍人"], "evidence": "黑衣人出现", "confidence": 0.9},
                    {"name": "就是", "type": "其他", "aliases": [], "evidence": "就是", "confidence": 0.99},
                ]
            }
        return {
            "relations": [
                {
                    "source": "林澈",
                    "target": "黑衣人",
                    "type": "冲突",
                    "evidence": "黑衣人在雨里出现，林澈与黑衣人对峙。",
                    "confidence": 0.91,
                }
            ],
            "events": [
                {
                    "type": "冲突/危机",
                    "summary": "林澈与黑衣人对峙",
                    "evidence": "黑衣人在雨里出现，林澈与黑衣人对峙。",
                    "entities": ["林澈", "黑衣人"],
                    "confidence": 0.88,
                }
            ],
        }


class SmartIndexTest(unittest.TestCase):
    def test_llm_entity_reviewer_filters_function_words(self) -> None:
        chapters = parse_chapters("第1章 阴影\n\n黑衣人在雨里出现，林澈与黑衣人对峙。")

        entities = discover_entities_with_llm(chapters, FakeSmartClient())

        self.assertIn("林澈", entities)
        self.assertIn("黑衣人", entities)
        self.assertNotIn("就是", entities)

    def test_build_smart_relation_event_records(self) -> None:
        chapters = parse_chapters("第1章 阴影\n\n黑衣人在雨里出现，林澈与黑衣人对峙。")
        entity_records = build_entity_records(chapters, {"林澈": [], "黑衣人": []}, DEFAULT_CHUNK_SETTINGS)

        relations, events = build_smart_relation_event_records(
            chapters,
            entity_records,
            DEFAULT_CHUNK_SETTINGS,
            FakeSmartClient(),
        )

        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].source_entity, "林澈")
        self.assertEqual(relations[0].target_entity, "黑衣人")
        self.assertEqual(relations[0].relation_type, "冲突")
        self.assertIn("对峙", relations[0].mentions[0].excerpt)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "冲突/危机")
        self.assertIn("林澈", events[0].entities)

    def test_rule_relation_fallback_uses_sentence_window_not_whole_chapter(self) -> None:
        chapters = parse_chapters(
            "第1章 远近\n\n林澈独自走进旧城。中间隔着很多无关叙述。黑衣人在另一条街出现。"
        )
        entity_records = build_entity_records(chapters, {"林澈": [], "黑衣人": []}, DEFAULT_CHUNK_SETTINGS)

        relations = build_relation_records(chapters, entity_records, DEFAULT_CHUNK_SETTINGS)

        self.assertEqual(relations, [])

    def test_auto_discover_entities_filters_common_words(self) -> None:
        text = "就是没有他的时间心中手段之前" * 20 + " 黑衣人走来，黑衣人开口。"

        entities = auto_discover_entities(text, top_k=20)

        for bad in ["就是", "没有", "他的", "时间", "心中", "手段", "之前"]:
            self.assertNotIn(bad, entities)


if __name__ == "__main__":
    unittest.main()
