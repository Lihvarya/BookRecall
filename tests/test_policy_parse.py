"""LLM ReAct 输出解析的单测（不联网）。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.agent.policies.llm_react import _parse_react


class ReactParseTest(unittest.TestCase):
    def test_parse_tool_call(self) -> None:
        raw = (
            "thought: 先查实体轨迹\n"
            "action: lookup_timeline\n"
            "arguments: {\"entity\": \"方源\"}\n"
        )
        parsed = _parse_react(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["action"], "lookup_timeline")
        self.assertEqual(parsed["arguments"], {"entity": "方源"})

    def test_parse_finish(self) -> None:
        raw = (
            "thought: 证据够了\n"
            "action: finish\n"
            "arguments: {}\n"
            "final_answer: 方源通过石门拿到了星辰之匙。\n"
        )
        parsed = _parse_react(raw)
        self.assertEqual(parsed["action"], "finish")
        self.assertEqual(parsed["final_answer"], "方源通过石门拿到了星辰之匙。")

    def test_parse_chinese_colon(self) -> None:
        raw = "thought：思考\naction：lookup_first_appearance\narguments：{\"entity\":\"星辰之匙\"}\n"
        parsed = _parse_react(raw)
        self.assertEqual(parsed["action"], "lookup_first_appearance")
        self.assertEqual(parsed["arguments"], {"entity": "星辰之匙"})

    def test_parse_garbage_returns_none(self) -> None:
        self.assertIsNone(_parse_react("这不是任何格式的输出"))
        self.assertIsNone(_parse_react(""))


if __name__ == "__main__":
    unittest.main()
