import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.answer_validation import parse_answer_validation_payload, validate_answer_with_llm


class FakeValidationClient:
    def complete_json(self, prompt: str) -> dict:
        self.prompt = prompt
        return {
            "supported": False,
            "spoiler_safe": True,
            "speculation_risk": "high",
            "issues": ["答案把推测说成事实"],
            "suggested_note": "这条回答需要结合证据谨慎核对。",
            "confidence": 0.91,
        }


class AnswerValidationTest(unittest.TestCase):
    def test_parse_answer_validation_payload_clamps_fields(self) -> None:
        validation = parse_answer_validation_payload(
            {
                "supported": False,
                "spoiler_safe": False,
                "speculation_risk": "unknown",
                "issues": ["a", "a", "b"],
                "confidence": 2,
            }
        )

        self.assertFalse(validation.supported)
        self.assertFalse(validation.spoiler_safe)
        self.assertEqual(validation.speculation_risk, "low")
        self.assertEqual(validation.issues, ["a", "b"])
        self.assertEqual(validation.confidence, 1.0)

    def test_validate_answer_with_llm_reports_risk(self) -> None:
        client = FakeValidationClient()

        validation = validate_answer_with_llm(
            question="星辰之匙有什么作用？",
            answer="星辰之匙能打开所有门。",
            progress_chapter=3,
            evidence=[
                {
                    "chapter_number": 3,
                    "chapter_title": "回声",
                    "excerpt": "黑衣人再次提到【星辰之匙】。",
                }
            ],
            client=client,
        )

        self.assertTrue(validation.risky)
        self.assertEqual(validation.source, "local_llm")
        self.assertIn("待校验答案", client.prompt)

    def test_validate_answer_without_evidence_uses_rule_guardrail(self) -> None:
        validation = validate_answer_with_llm(
            question="星辰之匙有什么作用？",
            answer="星辰之匙能打开所有门。",
            progress_chapter=3,
            evidence=[],
            client=FakeValidationClient(),
        )

        self.assertFalse(validation.supported)
        self.assertEqual(validation.source, "rules")
        self.assertIn("缺少直接证据", validation.suggested_note)


if __name__ == "__main__":
    unittest.main()
