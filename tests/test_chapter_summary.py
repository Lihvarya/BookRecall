import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.chapter_summary import build_chapter_summaries_with_llm, parse_chapter_summary_payload
from bookrecall.parser import parse_chapters


class FakeSummaryClient:
    def complete_json(self, prompt: str) -> dict:
        if "阶段回顾助手" in prompt:
            return {"stage_summary": "林澈发现星辰之匙线索，黑衣人的威胁开始浮出水面。", "confidence": 0.8}
        if "第 1 章" in prompt:
            return {
                "summary": "林澈在旧书里看到星辰之匙。",
                "key_entities": ["林澈", "星辰之匙"],
                "key_events": ["林澈发现星辰之匙的名字"],
                "foreshadowing": ["星辰之匙用途未明"],
                "state_changes": ["林澈获得新线索"],
                "confidence": 0.9,
            }
        return {
            "summary": "黑衣人在雨里出现。",
            "key_entities": ["黑衣人"],
            "key_events": ["黑衣人登场"],
            "foreshadowing": [],
            "state_changes": ["威胁升级"],
            "confidence": 0.9,
        }


class FlakySummaryClient(FakeSummaryClient):
    def complete_json(self, prompt: str) -> dict:
        if "第 1 章" in prompt:
            raise ValueError("响应中的 JSON object 解析失败。")
        return super().complete_json(prompt)


class ChapterSummaryTest(unittest.TestCase):
    def test_parse_chapter_summary_payload_renders_memory_fields(self) -> None:
        parsed = parse_chapter_summary_payload(
            {
                "summary": "林澈发现星辰之匙。",
                "key_entities": ["林澈", "星辰之匙"],
                "key_events": ["发现线索"],
                "foreshadowing": ["用途未明"],
                "state_changes": ["目标变化"],
                "confidence": 1.5,
            }
        )

        rendered = parsed.render()
        self.assertIn("摘要：林澈发现星辰之匙。", rendered)
        self.assertIn("关键人物/实体：林澈、星辰之匙", rendered)
        self.assertIn("伏笔/线索：用途未明", rendered)
        self.assertEqual(parsed.confidence, 1.0)

    def test_build_chapter_summaries_with_stage_recap(self) -> None:
        chapters = parse_chapters(
            "第1章 起点\n\n林澈在旧书里看到【星辰之匙】的名字。\n\n"
            "第2章 阴影\n\n黑衣人在雨里出现。"
        )

        summaries = build_chapter_summaries_with_llm(chapters, FakeSummaryClient(), stage_size=2)

        self.assertIn(1, summaries)
        self.assertIn(2, summaries)
        self.assertIn("星辰之匙用途未明", summaries[1])
        self.assertIn("阶段回顾（第 1-2 章）", summaries[2])
        self.assertIn("黑衣人的威胁", summaries[2])

    def test_build_chapter_summaries_skips_malformed_single_chapter(self) -> None:
        chapters = parse_chapters(
            "第1章 起点\n\n林澈在旧书里看到【星辰之匙】的名字。\n\n"
            "第2章 阴影\n\n黑衣人在雨里出现。"
        )

        summaries = build_chapter_summaries_with_llm(chapters, FlakySummaryClient(), stage_size=2)

        self.assertNotIn(1, summaries)
        self.assertIn(2, summaries)
        self.assertIn("黑衣人登场", summaries[2])

    def test_build_chapter_summaries_can_stride_chapters_for_fast_indexing(self) -> None:
        chapters = parse_chapters(
            "第1章 一\n\n林澈看到星辰之匙。\n\n"
            "第2章 二\n\n林澈继续赶路。\n\n"
            "第3章 三\n\n黑衣人在雨里出现。"
        )

        summaries = build_chapter_summaries_with_llm(chapters, FakeSummaryClient(), chapter_stride=2)

        self.assertIn(1, summaries)
        self.assertNotIn(2, summaries)
        self.assertIn(3, summaries)


if __name__ == "__main__":
    unittest.main()
