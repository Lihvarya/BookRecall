import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.cli import build_parser
from bookrecall.evaluation import (
    EvaluationDataError,
    RetrievalEvalCase,
    UnavailableRetriever,
    evaluate_agents,
    evaluate_retrievers,
    load_evaluation_cases,
    render_evaluation_report,
    report_as_json,
    threshold_failures,
)
from bookrecall.models import EvidenceCard, MemoryCard, SearchHit


def _hit(chapter: int, text: str, score: float = 1.0) -> SearchHit:
    return SearchHit(
        score=score,
        chapter_number=chapter,
        chapter_title=f"第 {chapter} 章",
        parent_id=f"sample:p:{chapter}",
        child_text=text,
        parent_text=text,
    )


class FakeRetriever:
    def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
        if query == "条件是什么":
            hits = [_hit(1, "无关背景"), _hit(2, "第一条件"), _hit(3, "第二条件")]
        else:
            hits = [_hit(1, "角色最终死了，留下尸躯")]
        if max_chapter is not None:
            hits = [hit for hit in hits if hit.chapter_number <= max_chapter]
        return hits


class FakeAgent:
    def ask_card(
        self,
        book_id: str,
        question: str,
        user_id: str = "default",
        progress_chapter: int | None = None,
        session_id: str | None = None,
    ) -> MemoryCard:
        chapter = 2 if question == "条件是什么" else 1
        excerpt = "第一条件和第二条件" if chapter == 2 else "角色最终死了，留下尸躯"
        return MemoryCard(
            question=question,
            intent="测试",
            answer=excerpt,
            progress_chapter=int(progress_chapter or chapter),
            evidence=[
                EvidenceCard(
                    chapter_number=chapter,
                    chapter_title=f"第 {chapter} 章",
                    excerpt=excerpt,
                    reason="测试证据",
                )
            ],
        )


class EvaluationDatasetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_jsonl(self, lines: list[dict[str, object] | str]) -> Path:
        path = Path(self.tempdir.name) / "cases.jsonl"
        rendered = [item if isinstance(item, str) else json.dumps(item, ensure_ascii=False) for item in lines]
        path.write_text("\n".join(rendered), encoding="utf-8")
        return path

    def test_loads_jsonl_and_overrides_book_id(self) -> None:
        path = self._write_jsonl(
            [
                "# comment",
                {
                    "id": "conditions",
                    "book_id": "old-id",
                    "query": "条件是什么",
                    "relevant_chapters": [2, 3, 3],
                    "evidence_terms": ["第一", "第二"],
                    "max_chapter": 3,
                },
            ]
        )
        cases = load_evaluation_cases(path, book_id_override="sample")
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].book_id, "sample")
        self.assertEqual(cases[0].relevant_chapters, (2, 3))

    def test_rejects_duplicate_case_ids(self) -> None:
        record = {"id": "same", "book_id": "sample", "query": "问题", "relevant_chapters": [1]}
        path = self._write_jsonl([record, record])
        with self.assertRaisesRegex(EvaluationDataError, "重复"):
            load_evaluation_cases(path)

    def test_rejects_ground_truth_beyond_progress(self) -> None:
        path = self._write_jsonl(
            [{"id": "spoiler", "book_id": "sample", "query": "问题", "relevant_chapters": [3], "max_chapter": 2}]
        )
        with self.assertRaisesRegex(EvaluationDataError, "超出 max_chapter"):
            load_evaluation_cases(path)


class RetrievalEvaluationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = [
            RetrievalEvalCase(
                case_id="conditions",
                book_id="sample",
                query="条件是什么",
                relevant_chapters=(2, 3),
                evidence_terms=("第一条件", "第二条件"),
            ),
            RetrievalEvalCase(
                case_id="death",
                book_id="sample",
                query="怎么死的",
                relevant_chapters=(1,),
                evidence_terms=("死了", "尸躯"),
            ),
        ]

    def test_calculates_top1_recall_mrr_and_evidence_coverage(self) -> None:
        report = evaluate_retrievers(self.cases, {"fake": FakeRetriever()}, dataset_name="sample", top_k=3)
        method = report.methods[0]
        self.assertEqual(method.total_cases, 2)
        self.assertEqual(method.error_cases, 0)
        self.assertAlmostEqual(method.top1_accuracy, 0.5)
        self.assertAlmostEqual(method.recall_at_k, 1.0)
        self.assertAlmostEqual(method.mrr, 0.75)
        self.assertAlmostEqual(method.evidence_term_coverage or 0.0, 1.0)
        self.assertEqual(method.cases[0].first_relevant_rank, 2)
        self.assertEqual(method.cases[0].hit_chapters, [1, 2, 3])

    def test_unavailable_method_is_reported_without_fallback(self) -> None:
        report = evaluate_retrievers(
            self.cases,
            {"embedding": UnavailableRetriever("模型未安装")},
            top_k=2,
        )
        method = report.methods[0]
        self.assertEqual(method.error_cases, 2)
        self.assertEqual(method.top1_accuracy, 0.0)
        self.assertIn("模型未安装", method.cases[0].error)

    def test_report_rendering_and_thresholds(self) -> None:
        report = evaluate_retrievers(self.cases, {"fake": FakeRetriever()}, top_k=3)
        text = render_evaluation_report(report)
        payload = json.loads(report_as_json(report))
        self.assertIn("Recall@K", text)
        self.assertEqual(payload["methods"][0]["method"], "fake")
        self.assertTrue(threshold_failures(report, min_top1=0.8))
        self.assertFalse(threshold_failures(report, min_top1=0.5, min_mrr=0.75, fail_on_error=True))

    def test_evaluates_agent_evidence_and_spoiler_boundary(self) -> None:
        report = evaluate_agents(self.cases, {"agent:fake": FakeAgent()}, top_k=3)
        method = report.methods[0]
        self.assertEqual(method.top1_accuracy, 1.0)
        self.assertEqual(method.mrr, 1.0)
        self.assertEqual(method.spoiler_violations, 0)
        self.assertIn("第一条件", method.cases[0].answer)

    def test_cli_exposes_eval_retrieval_command(self) -> None:
        args = build_parser().parse_args(["eval-retrieval", "--dataset", "cases.jsonl"])
        self.assertEqual(args.command, "eval-retrieval")
        self.assertEqual(args.retrievers, "lexical,embedding")
        self.assertEqual(args.top_k, 4)

        agent_args = build_parser().parse_args(["eval-agent", "--dataset", "cases.jsonl"])
        self.assertEqual(agent_args.command, "eval-agent")
        self.assertEqual(agent_args.retrievers, "lexical")


if __name__ == "__main__":
    unittest.main()
