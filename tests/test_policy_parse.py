"""LLM ReAct 输出解析的单测（不联网）。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.agent.policies.base import Decision, DecisionPolicy, ToolCall
from bookrecall.agent.policies.langgraph import LangGraphPolicy, is_langgraph_available
from bookrecall.agent.policies.llm_react import LLMReActPolicy, _parse_react
from bookrecall.agent.policies.local_planner import LocalPlannerPolicy, parse_local_plan
from bookrecall.agent.state import AgentState
from bookrecall.agent.tools import Tool, ToolRegistry, ToolSchema


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

    def test_policy_prefers_native_tool_calls(self) -> None:
        class FakeReasoner:
            def chat(self, *, messages, tools=None, tool_choice="auto"):
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "lookup_timeline",
                            "arguments": "{\"entity\": \"方源\"}",
                        }
                    ],
                }

        registry = ToolRegistry()
        registry.register(
            Tool(
                schema=ToolSchema(
                    name="lookup_timeline",
                    description="test tool",
                    parameters={"entity": {"type": "str", "required": True}},
                ),
                run=lambda state, args: {},
            )
        )
        state = AgentState(book_id="sample", question="方源后来还出现过吗？", progress_chapter=10)
        decision = LLMReActPolicy(FakeReasoner()).next_action(state, registry)
        self.assertFalse(decision.is_terminal)
        self.assertEqual(
            decision.tool_call,
            ToolCall(name="lookup_timeline", arguments={"entity": "方源"}, thought="tool_call"),
        )

    def test_policy_falls_back_to_text_protocol(self) -> None:
        class FakeReasoner:
            def chat(self, *, messages, tools=None, tool_choice="auto"):
                return {
                    "content": "thought: 先查实体轨迹\naction: lookup_timeline\narguments: {\"entity\": \"方源\"}\n",
                    "tool_calls": [],
                }

        registry = ToolRegistry()
        registry.register(
            Tool(
                schema=ToolSchema(
                    name="lookup_timeline",
                    description="test tool",
                    parameters={"entity": {"type": "str", "required": True}},
                ),
                run=lambda state, args: {},
            )
        )
        state = AgentState(book_id="sample", question="方源后来还出现过吗？", progress_chapter=10)
        decision = LLMReActPolicy(FakeReasoner()).next_action(state, registry)
        self.assertFalse(decision.is_terminal)
        self.assertEqual(decision.tool_call.name, "lookup_timeline")
        self.assertEqual(decision.tool_call.arguments, {"entity": "方源"})

    def test_parse_local_plan_filters_invalid_tools(self) -> None:
        registry = ToolRegistry()
        registry.register(
            Tool(
                schema=ToolSchema(name="lookup_timeline", description="test tool"),
                run=lambda state, args: {},
            )
        )
        registry.register(
            Tool(
                schema=ToolSchema(name="search_evidence", description="test tool"),
                run=lambda state, args: {},
            )
        )

        plan = parse_local_plan(
            {
                "tool_calls": [
                    {"tool": "lookup_timeline", "arguments": {"entity": "$primary_entity"}, "thought": "查轨迹"},
                    {"tool": "missing_tool", "arguments": {}},
                    {"tool": "lookup_timeline", "arguments": {"entity": "重复"}},
                    {"tool": "search_evidence", "arguments": {"query": "$question"}},
                ]
            },
            registry,
        )

        self.assertEqual([call.name for call in plan], ["lookup_timeline", "search_evidence"])
        self.assertEqual(plan[0].arguments, {"entity": "$primary_entity"})

    def test_local_planner_policy_executes_plan_then_fallback(self) -> None:
        class FakePlanner:
            def complete_json(self, prompt: str) -> dict:
                self.prompt = prompt
                return {
                    "tool_calls": [
                        {"tool": "lookup_timeline", "arguments": {"entity": "$primary_entity"}, "thought": "先查实体轨迹"}
                    ]
                }

        registry = ToolRegistry()
        registry.register(
            Tool(
                schema=ToolSchema(name="lookup_timeline", description="test tool"),
                run=lambda state, args: {},
            )
        )
        state = AgentState(
            book_id="sample",
            question="方源后来还出现过吗？",
            progress_chapter=10,
            matched_entities=["方源"],
            primary_entity="方源",
        )
        planner = FakePlanner()
        policy = LocalPlannerPolicy(planner)

        decision = policy.next_action(state, registry)

        self.assertFalse(decision.is_terminal)
        self.assertEqual(decision.tool_call.name, "lookup_timeline")
        self.assertEqual(decision.tool_call.arguments, {"entity": "方源"})
        self.assertIn("工具清单", planner.prompt)

    def test_local_planner_replaces_embedded_primary_entity_placeholder(self) -> None:
        call = ToolCall(
            name="search_evidence",
            arguments={"query": "$primary_entity 怎么死的"},
            thought="查死亡原因",
        )
        state = AgentState(
            book_id="sample",
            question="花酒行者怎么死的",
            progress_chapter=10,
            matched_entities=["花酒行者"],
            primary_entity="花酒行者",
        )

        resolved = LocalPlannerPolicy._resolve_dynamic_arguments(call, state)

        self.assertEqual(resolved.arguments["query"], "花酒行者 怎么死的")

    def test_local_planner_falls_back_to_question_when_placeholder_unresolved(self) -> None:
        call = ToolCall(
            name="search_evidence",
            arguments={"query": "$primary_entity 怎么死的"},
            thought="查死亡原因",
        )
        state = AgentState(
            book_id="sample",
            question="花酒行者怎么死的",
            progress_chapter=10,
        )

        resolved = LocalPlannerPolicy._resolve_dynamic_arguments(call, state)

        self.assertEqual(resolved.arguments["query"], "花酒行者怎么死的")

    @unittest.skipUnless(is_langgraph_available(), "langgraph optional dependency is not installed")
    def test_langgraph_policy_can_invoke_delegate(self) -> None:
        class FixedPolicy(DecisionPolicy):
            def next_action(self, state, registry):
                return Decision(
                    is_terminal=False,
                    tool_call=ToolCall(name="lookup_timeline", arguments={"entity": "方源"}, thought="graph"),
                )

            def name(self) -> str:
                return "fixed"

        registry = ToolRegistry()
        registry.register(
            Tool(
                schema=ToolSchema(
                    name="lookup_timeline",
                    description="test tool",
                    parameters={"entity": {"type": "str", "required": True}},
                ),
                run=lambda state, args: {},
            )
        )
        state = AgentState(book_id="sample", question="方源后来还出现过吗？", progress_chapter=10)
        decision = LangGraphPolicy(delegate=FixedPolicy()).next_action(state, registry)
        self.assertFalse(decision.is_terminal)
        self.assertEqual(decision.tool_call.name, "lookup_timeline")
        self.assertEqual(decision.tool_call.thought, "graph")


if __name__ == "__main__":
    unittest.main()
