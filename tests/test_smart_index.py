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
                    {"name": "自由", "type": "概念", "aliases": [], "evidence": "自由残缺变", "confidence": 0.99},
                    {"name": "自由残缺变", "type": "功法", "aliases": [], "evidence": "自由残缺变是白袍蛊仙提到的一记杀招", "confidence": 0.92},
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


class CountingSmartClient(FakeSmartClient):
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, prompt: str) -> dict:
        self.calls += 1
        return super().complete_json(prompt)


class FailingSmartClient:
    def complete_json(self, prompt: str) -> dict:
        raise ValueError("响应中的 JSON object 解析失败。")


class CountingRelationClient(FakeSmartClient):
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, prompt: str) -> dict:
        self.calls += 1
        return {"relations": [], "events": []}


class NarrativeSmartClient:
    def complete_json(self, prompt: str) -> dict:
        return {
            "relations": [
                {
                    "source": "林澈",
                    "target": "黑衣人",
                    "type": "关系变化",
                    "evidence": "黑衣人把星辰之匙交给林澈，林澈意识到黑衣人并非敌人。",
                    "confidence": 0.9,
                }
            ],
            "events": [
                {
                    "type": "道具流转",
                    "summary": "星辰之匙转交给林澈",
                    "item": "星辰之匙",
                    "from": "黑衣人",
                    "to": "林澈",
                    "evidence": "黑衣人把星辰之匙交给林澈",
                    "entities": ["星辰之匙", "黑衣人", "林澈"],
                    "confidence": 0.9,
                },
                {
                    "type": "因果链",
                    "cause": "林澈发现星辰之匙的刻痕",
                    "effect": "他决定打开石门",
                    "evidence": "因为星辰之匙的刻痕与石门吻合，林澈决定打开石门。",
                    "entities": ["林澈", "星辰之匙"],
                    "confidence": 0.9,
                },
                {
                    "type": "伏笔/回收",
                    "foreshadowing": "旧书提到星辰之匙能开门",
                    "payoff": "星辰之匙打开石门",
                    "evidence": "旧书里的线索终于回收，星辰之匙打开石门。",
                    "entities": ["星辰之匙"],
                    "confidence": 0.9,
                },
                {
                    "type": "关系变化",
                    "relation_change": "林澈意识到黑衣人并非敌人",
                    "evidence": "林澈意识到黑衣人并非敌人。",
                    "entities": ["林澈", "黑衣人"],
                    "confidence": 0.9,
                },
            ],
        }


class SmartIndexTest(unittest.TestCase):
    def test_llm_entity_reviewer_filters_function_words(self) -> None:
        chapters = parse_chapters("第1章 阴影\n\n黑衣人在雨里出现，林澈与黑衣人对峙。")

        entities = discover_entities_with_llm(chapters, FakeSmartClient())

        self.assertIn("林澈", entities)
        self.assertIn("黑衣人", entities)
        self.assertIn("自由残缺变", entities)
        self.assertNotIn("就是", entities)
        self.assertNotIn("自由", entities)

    def test_llm_entity_reviewer_can_batch_chapters(self) -> None:
        chapters = parse_chapters(
            "第1章 阴影\n\n黑衣人在雨里出现。\n\n"
            "第2章 星钥\n\n林澈听见星辰之匙的名字。\n\n"
            "第3章 回声\n\n黑衣人再次出现。"
        )
        client = CountingSmartClient()

        discover_entities_with_llm(chapters, client, batch_chapters=2)

        self.assertEqual(client.calls, 2)

    def test_llm_entity_reviewer_keeps_seed_entities_when_model_batch_fails(self) -> None:
        chapters = parse_chapters("第1章 阴影\n\n黑衣人在雨里出现。")

        entities = discover_entities_with_llm(chapters, FailingSmartClient(), seed_entities={"黑衣人": []})

        self.assertEqual(entities, {"黑衣人": []})

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

    def test_build_smart_relation_event_records_skips_failed_chapter(self) -> None:
        chapters = parse_chapters("第1章 阴影\n\n黑衣人在雨里出现，林澈与黑衣人对峙。")
        entity_records = build_entity_records(chapters, {"林澈": [], "黑衣人": []}, DEFAULT_CHUNK_SETTINGS)

        relations, events = build_smart_relation_event_records(
            chapters,
            entity_records,
            DEFAULT_CHUNK_SETTINGS,
            FailingSmartClient(),
        )

        self.assertEqual(relations, [])
        self.assertEqual(events, [])

    def test_build_smart_relation_event_records_can_stride_chapters_for_fast_indexing(self) -> None:
        chapters = parse_chapters(
            "第1章 一\n\n黑衣人在雨里出现，林澈与黑衣人对峙。\n\n"
            "第2章 二\n\n黑衣人在雨里出现，林澈与黑衣人对峙。\n\n"
            "第3章 三\n\n黑衣人在雨里出现，林澈与黑衣人对峙."
        )
        entity_records = build_entity_records(chapters, {"林澈": [], "黑衣人": []}, DEFAULT_CHUNK_SETTINGS)
        client = CountingRelationClient()

        build_smart_relation_event_records(
            chapters,
            entity_records,
            DEFAULT_CHUNK_SETTINGS,
            client,
            chapter_stride=2,
        )

        self.assertEqual(client.calls, 2)

    def test_build_smart_relation_event_records_extracts_narrative_chains(self) -> None:
        chapters = parse_chapters(
            "第1章 石门\n\n"
            "黑衣人把星辰之匙交给林澈。因为星辰之匙的刻痕与石门吻合，林澈决定打开石门。"
            "旧书里的线索终于回收，星辰之匙打开石门。林澈意识到黑衣人并非敌人。"
        )
        entity_records = build_entity_records(chapters, {"林澈": [], "黑衣人": [], "星辰之匙": []}, DEFAULT_CHUNK_SETTINGS)

        relations, events = build_smart_relation_event_records(
            chapters,
            entity_records,
            DEFAULT_CHUNK_SETTINGS,
            NarrativeSmartClient(),
        )

        self.assertEqual(relations[0].relation_type, "关系变化")
        event_types = {event.event_type for event in events}
        self.assertIn("道具流转", event_types)
        self.assertIn("因果链", event_types)
        self.assertIn("伏笔/回收", event_types)
        self.assertIn("关系变化", event_types)
        summaries = " ".join(event.summary for event in events)
        self.assertIn("流转：星辰之匙：黑衣人 -> 林澈", summaries)
        self.assertIn("因果：林澈发现星辰之匙的刻痕 -> 他决定打开石门", summaries)
        self.assertIn("伏笔回收：旧书提到星辰之匙能开门 -> 星辰之匙打开石门", summaries)

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

    def test_auto_discover_entities_does_not_promote_frequency_ngrams_by_default(self) -> None:
        text = "自由自由自由自由自由自由自由自由。自由残缺变是白袍蛊仙提到的一记杀招。"

        entities = auto_discover_entities(text, top_k=20)

        self.assertEqual(entities, {})

    def test_auto_discover_entities_keeps_explicit_rare_technique(self) -> None:
        text = "第1章\n\n白袍蛊仙提到【自由残缺变】，这是只出现一次的杀招。"

        entities = auto_discover_entities(text, top_k=20)

        self.assertIn("自由残缺变", entities)
        self.assertNotIn("自由", entities)


if __name__ == "__main__":
    unittest.main()
