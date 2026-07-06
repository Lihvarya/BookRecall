import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.local_llm import LocalChatClient, LocalLLMError, LocalLLMSettings, extract_json_object


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def close(self) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class LocalLLMClientTest(unittest.TestCase):
    def test_endpoint_request_disables_thinking_for_qwen(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=0):
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {"content": '{"entities":[]}'},
                            "finish_reason": "stop",
                        }
                    ]
                }
            )

        client = LocalChatClient(LocalLLMSettings(endpoint="http://127.0.0.1:1234"))
        with patch("urllib.request.urlopen", fake_urlopen):
            payload = client.complete_json("输出 JSON")

        body = captured["body"]
        self.assertEqual(payload, {"entities": []})
        self.assertIs(body["enable_thinking"], False)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertTrue(body["messages"][1]["content"].startswith("/no_think"))
        self.assertTrue(body["messages"][1]["content"].endswith("/no_think"))
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_endpoint_reasoning_only_length_error_is_actionable(self) -> None:
        def fake_urlopen(_request, timeout=0):
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {"content": "", "reasoning_content": "Thinking Process..."},
                            "finish_reason": "length",
                        }
                    ]
                }
            )

        client = LocalChatClient(LocalLLMSettings(endpoint="http://127.0.0.1:1234"))
        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaisesRegex(LocalLLMError, "关闭 Thinking"):
                client.complete_json("输出 JSON")

    def test_endpoint_retries_without_response_format_when_server_rejects_it(self) -> None:
        bodies: list[dict] = []

        def fake_urlopen(request, timeout=0):
            body = json.loads(request.data.decode("utf-8"))
            bodies.append(body)
            if "response_format" in body:
                raise urllib.error.HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    hdrs={},
                    fp=FakeResponse({"error": {"message": "response_format is unsupported"}}),
                )
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {"content": '{"entities":[]}'},
                            "finish_reason": "stop",
                        }
                    ]
                }
            )

        client = LocalChatClient(LocalLLMSettings(endpoint="http://127.0.0.1:1234"))
        with patch("urllib.request.urlopen", fake_urlopen):
            payload = client.complete_json("输出 JSON")

        self.assertEqual(payload, {"entities": []})
        self.assertEqual(len(bodies), 2)
        self.assertIn("response_format", bodies[0])
        self.assertNotIn("response_format", bodies[1])

    def test_extract_json_recovers_entities_array_from_malformed_wrapper(self) -> None:
        payload = extract_json_object(
            '{"entities":[{"name":"方源","type":"人物","aliases":[],"evidence":"方源","confidence":0.9}]" }'
        )

        self.assertEqual(payload["entities"][0]["name"], "方源")

    def test_extract_json_recovers_chapter_summary_when_one_array_is_malformed(self) -> None:
        payload = extract_json_object(
            '{"summary":"木婉清误认刀白风为仇人。",'
            '"key_entities":["段正淳","木婉清","段誉"],'
            '"key_events":["木婉清射毒箭伤刀白风与段誉"],'
            '"foreshadowing":[["段正淳对木婉清身世隐瞒的深层原因"],'
            '"state_changes":["木婉清从敌视转为震惊与悲伤"],'
            '"confidence":0.95}'
        )

        self.assertEqual(payload["summary"], "木婉清误认刀白风为仇人。")
        self.assertEqual(payload["key_entities"], ["段正淳", "木婉清", "段誉"])
        self.assertEqual(payload["state_changes"], ["木婉清从敌视转为震惊与悲伤"])
        self.assertEqual(payload["confidence"], 0.95)


if __name__ == "__main__":
    unittest.main()
