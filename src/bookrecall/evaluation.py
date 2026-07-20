"""Repeatable retrieval evaluation for BookRecall.

The evaluator is intentionally independent from generation. It measures whether
retrieval placed grounded chapters and evidence terms in the top-k candidates,
so a fluent LLM answer cannot hide a recall regression.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

from .models import MemoryCard, SearchHit
from .retrieval import Retriever


class EvaluationDataError(ValueError):
    """Raised when an evaluation dataset is missing required ground truth."""


@dataclass(frozen=True, slots=True)
class RetrievalEvalCase:
    case_id: str
    book_id: str
    query: str
    relevant_chapters: tuple[int, ...]
    evidence_terms: tuple[str, ...] = ()
    max_chapter: int | None = None
    tags: tuple[str, ...] = ()
    note: str = ""


@dataclass(slots=True)
class RetrievalCaseResult:
    case_id: str
    query: str
    relevant_chapters: list[int]
    hit_chapters: list[int] = field(default_factory=list)
    first_relevant_rank: int | None = None
    reciprocal_rank: float = 0.0
    recall_at_k: float = 0.0
    top1_hit: bool = False
    evidence_term_coverage: float | None = None
    matched_evidence_terms: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    hits: list[dict[str, object]] = field(default_factory=list)
    answer: str = ""
    intent: str = ""
    spoiler_violation: bool = False
    error: str = ""


@dataclass(slots=True)
class RetrievalMethodReport:
    method: str
    total_cases: int
    successful_cases: int
    error_cases: int
    top1_accuracy: float
    recall_at_k: float
    mrr: float
    evidence_term_coverage: float | None
    latency_p50_ms: float
    latency_p95_ms: float
    spoiler_violations: int = 0
    cases: list[RetrievalCaseResult] = field(default_factory=list)


@dataclass(slots=True)
class RetrievalEvaluationReport:
    dataset: str
    top_k: int
    case_count: int
    methods: list[RetrievalMethodReport]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class UnavailableRetriever:
    """Represent an unavailable method without silently falling back."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def search(self, book_id: str, query: str, max_chapter: int | None = None) -> list[SearchHit]:
        raise RuntimeError(self.reason)


class EvaluableAgent(Protocol):
    def ask_card(
        self,
        book_id: str,
        question: str,
        user_id: str = "default",
        progress_chapter: int | None = None,
        session_id: str | None = None,
    ) -> MemoryCard:
        ...


def load_evaluation_cases(
    path: str | Path,
    *,
    book_id_override: str | None = None,
) -> list[RetrievalEvalCase]:
    dataset_path = Path(path)
    if not dataset_path.exists() or not dataset_path.is_file():
        raise EvaluationDataError(f"评测数据集不存在：{dataset_path}")

    records = _read_records(dataset_path)
    if not records:
        raise EvaluationDataError("评测数据集为空。")

    cases: list[RetrievalEvalCase] = []
    seen_ids: set[str] = set()
    for index, record in enumerate(records, start=1):
        case = _parse_case(record, index=index, book_id_override=book_id_override)
        if case.case_id in seen_ids:
            raise EvaluationDataError(f"评测 case_id 重复：{case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)
    return cases


def evaluate_retrievers(
    cases: list[RetrievalEvalCase],
    retrievers: Mapping[str, Retriever],
    *,
    dataset_name: str = "dataset",
    top_k: int = 4,
) -> RetrievalEvaluationReport:
    if not cases:
        raise EvaluationDataError("至少需要一个评测案例。")
    if not retrievers:
        raise EvaluationDataError("至少需要一个待评测检索器。")
    if top_k <= 0:
        raise EvaluationDataError("top_k 必须大于 0。")

    method_reports: list[RetrievalMethodReport] = []
    for method, retriever in retrievers.items():
        results = [_evaluate_case(case, retriever, top_k=top_k) for case in cases]
        method_reports.append(_summarize_method(method, results))
    return RetrievalEvaluationReport(
        dataset=dataset_name,
        top_k=top_k,
        case_count=len(cases),
        methods=method_reports,
    )


def evaluate_agents(
    cases: list[RetrievalEvalCase],
    agents: Mapping[str, EvaluableAgent],
    *,
    dataset_name: str = "dataset",
    top_k: int = 4,
) -> RetrievalEvaluationReport:
    if not cases:
        raise EvaluationDataError("至少需要一个评测案例。")
    if not agents:
        raise EvaluationDataError("至少需要一个待评测 Agent。")
    if top_k <= 0:
        raise EvaluationDataError("top_k 必须大于 0。")

    method_reports: list[RetrievalMethodReport] = []
    for method, agent in agents.items():
        results = [_evaluate_agent_case(case, agent, top_k=top_k) for case in cases]
        method_reports.append(_summarize_method(method, results))
    return RetrievalEvaluationReport(
        dataset=dataset_name,
        top_k=top_k,
        case_count=len(cases),
        methods=method_reports,
    )


def render_evaluation_report(report: RetrievalEvaluationReport) -> str:
    lines = [
        f"召回评测：{report.dataset}",
        f"案例数：{report.case_count} | K={report.top_k}",
        "",
        "方法 | Top1 | Recall@K | MRR | 证据词覆盖 | P50 | P95 | 错误 | 越界",
        "--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---:",
    ]
    for item in report.methods:
        coverage = "-" if item.evidence_term_coverage is None else f"{item.evidence_term_coverage:.3f}"
        lines.append(
            f"{item.method} | {item.top1_accuracy:.3f} | {item.recall_at_k:.3f} | "
            f"{item.mrr:.3f} | {coverage} | {item.latency_p50_ms:.1f}ms | "
            f"{item.latency_p95_ms:.1f}ms | {item.error_cases} | {item.spoiler_violations}"
        )

    failures: list[str] = []
    for method in report.methods:
        for case in method.cases:
            if case.error:
                failures.append(f"- [{method.method}] {case.case_id}：{case.error}")
            elif case.first_relevant_rank is None:
                chapters = ", ".join(str(value) for value in case.hit_chapters) or "无命中"
                failures.append(f"- [{method.method}] {case.case_id}：Top{report.top_k} 未命中，返回章节 {chapters}")
    if failures:
        lines.extend(("", "失败案例：", *failures))
    return "\n".join(lines)


def threshold_failures(
    report: RetrievalEvaluationReport,
    *,
    min_top1: float | None = None,
    min_mrr: float | None = None,
    fail_on_error: bool = False,
    fail_on_spoiler: bool = False,
) -> list[str]:
    failures: list[str] = []
    for method in report.methods:
        if min_top1 is not None and method.top1_accuracy < min_top1:
            failures.append(f"{method.method} Top1={method.top1_accuracy:.3f} < {min_top1:.3f}")
        if min_mrr is not None and method.mrr < min_mrr:
            failures.append(f"{method.method} MRR={method.mrr:.3f} < {min_mrr:.3f}")
        if fail_on_error and method.error_cases:
            failures.append(f"{method.method} 有 {method.error_cases} 个执行错误")
        if fail_on_spoiler and method.spoiler_violations:
            failures.append(f"{method.method} 有 {method.spoiler_violations} 个防剧透越界案例")
    return failures


def report_as_json(report: RetrievalEvaluationReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def _read_records(path: Path) -> list[dict[str, object]]:
    if path.suffix.lower() == ".jsonl":
        records: list[dict[str, object]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise EvaluationDataError(f"JSONL 第 {line_number} 行解析失败：{exc.msg}") from exc
            if not isinstance(payload, dict):
                raise EvaluationDataError(f"JSONL 第 {line_number} 行必须是 JSON object。")
            records.append(payload)
        return records

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvaluationDataError(f"JSON 数据集解析失败：{exc.msg}") from exc
    if isinstance(payload, dict):
        payload = payload.get("cases")
    if not isinstance(payload, list):
        raise EvaluationDataError("JSON 数据集必须是数组，或包含 cases 数组的 object。")
    if not all(isinstance(item, dict) for item in payload):
        raise EvaluationDataError("cases 中的每一项都必须是 JSON object。")
    return payload


def _parse_case(
    record: dict[str, object],
    *,
    index: int,
    book_id_override: str | None,
) -> RetrievalEvalCase:
    case_id = str(record.get("id") or record.get("case_id") or "").strip()
    query = str(record.get("query") or "").strip()
    book_id = str(book_id_override or record.get("book_id") or "").strip()
    if not case_id:
        raise EvaluationDataError(f"第 {index} 个案例缺少 id。")
    if not query:
        raise EvaluationDataError(f"案例 {case_id} 缺少 query。")
    if not book_id:
        raise EvaluationDataError(f"案例 {case_id} 缺少 book_id，或需要通过 --book-id 覆盖。")

    relevant_chapters = _positive_int_tuple(record.get("relevant_chapters"), "relevant_chapters", case_id)
    if not relevant_chapters:
        raise EvaluationDataError(f"案例 {case_id} 至少需要一个 relevant_chapters。")
    max_chapter = _optional_positive_int(record.get("max_chapter"), "max_chapter", case_id)
    if max_chapter is not None and any(chapter > max_chapter for chapter in relevant_chapters):
        raise EvaluationDataError(f"案例 {case_id} 的 relevant_chapters 超出 max_chapter。")

    return RetrievalEvalCase(
        case_id=case_id,
        book_id=book_id,
        query=query,
        relevant_chapters=relevant_chapters,
        evidence_terms=_string_tuple(record.get("evidence_terms")),
        max_chapter=max_chapter,
        tags=_string_tuple(record.get("tags")),
        note=str(record.get("note") or "").strip(),
    )


def _evaluate_case(case: RetrievalEvalCase, retriever: Retriever, *, top_k: int) -> RetrievalCaseResult:
    started = time.perf_counter()
    try:
        hits = list(retriever.search(case.book_id, case.query, max_chapter=case.max_chapter))[:top_k]
        elapsed_ms = (time.perf_counter() - started) * 1000
        relevant = set(case.relevant_chapters)
        first_rank = next(
            (rank for rank, hit in enumerate(hits, start=1) if hit.chapter_number in relevant),
            None,
        )
        hit_relevant = {hit.chapter_number for hit in hits if hit.chapter_number in relevant}
        evidence_text = "\n".join(f"{hit.child_text}\n{hit.parent_text}" for hit in hits).casefold()
        matched_terms = [term for term in case.evidence_terms if term.casefold() in evidence_text]
        term_coverage = None
        if case.evidence_terms:
            term_coverage = len(matched_terms) / len(case.evidence_terms)
        return RetrievalCaseResult(
            case_id=case.case_id,
            query=case.query,
            relevant_chapters=list(case.relevant_chapters),
            hit_chapters=[hit.chapter_number for hit in hits],
            first_relevant_rank=first_rank,
            reciprocal_rank=0.0 if first_rank is None else 1.0 / first_rank,
            recall_at_k=len(hit_relevant) / len(relevant),
            top1_hit=bool(hits and hits[0].chapter_number in relevant),
            evidence_term_coverage=term_coverage,
            matched_evidence_terms=matched_terms,
            elapsed_ms=elapsed_ms,
            hits=[_serialize_hit(hit) for hit in hits],
        )
    except Exception as exc:  # noqa: BLE001 - one method must not abort the comparison
        return RetrievalCaseResult(
            case_id=case.case_id,
            query=case.query,
            relevant_chapters=list(case.relevant_chapters),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error=str(exc),
        )


def _evaluate_agent_case(case: RetrievalEvalCase, agent: EvaluableAgent, *, top_k: int) -> RetrievalCaseResult:
    started = time.perf_counter()
    try:
        card = agent.ask_card(
            book_id=case.book_id,
            question=case.query,
            progress_chapter=case.max_chapter,
            session_id=None,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        evidence = list(card.evidence[:top_k])
        relevant = set(case.relevant_chapters)
        first_rank = next(
            (rank for rank, item in enumerate(evidence, start=1) if item.chapter_number in relevant),
            None,
        )
        hit_relevant = {item.chapter_number for item in evidence if item.chapter_number in relevant}
        evidence_text = f"{card.answer}\n" + "\n".join(item.excerpt for item in evidence)
        normalized_evidence = evidence_text.casefold()
        matched_terms = [term for term in case.evidence_terms if term.casefold() in normalized_evidence]
        term_coverage = None
        if case.evidence_terms:
            term_coverage = len(matched_terms) / len(case.evidence_terms)
        spoiler_violation = bool(
            case.max_chapter is not None
            and any(item.chapter_number > case.max_chapter for item in card.evidence)
        )
        return RetrievalCaseResult(
            case_id=case.case_id,
            query=case.query,
            relevant_chapters=list(case.relevant_chapters),
            hit_chapters=[item.chapter_number for item in evidence],
            first_relevant_rank=first_rank,
            reciprocal_rank=0.0 if first_rank is None else 1.0 / first_rank,
            recall_at_k=len(hit_relevant) / len(relevant),
            top1_hit=bool(evidence and evidence[0].chapter_number in relevant),
            evidence_term_coverage=term_coverage,
            matched_evidence_terms=matched_terms,
            elapsed_ms=elapsed_ms,
            hits=[
                {
                    "chapter_number": item.chapter_number,
                    "chapter_title": item.chapter_title,
                    "excerpt": item.excerpt[:240].replace("\n", " "),
                    "reason": item.reason,
                }
                for item in evidence
            ],
            answer=card.answer,
            intent=card.intent,
            spoiler_violation=spoiler_violation,
        )
    except Exception as exc:  # noqa: BLE001 - one workflow must not abort the comparison
        return RetrievalCaseResult(
            case_id=case.case_id,
            query=case.query,
            relevant_chapters=list(case.relevant_chapters),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error=str(exc),
        )


def _serialize_hit(hit: SearchHit) -> dict[str, object]:
    excerpt = (hit.child_text or hit.parent_text).strip().replace("\n", " ")
    return {
        "rank_score": round(float(hit.score), 6),
        "chapter_number": hit.chapter_number,
        "chapter_title": hit.chapter_title,
        "parent_id": hit.parent_id,
        "excerpt": excerpt[:240],
    }


def _summarize_method(method: str, results: list[RetrievalCaseResult]) -> RetrievalMethodReport:
    total = len(results)
    latencies = [item.elapsed_ms for item in results]
    term_scores = [item.evidence_term_coverage for item in results if item.evidence_term_coverage is not None]
    error_count = sum(bool(item.error) for item in results)
    return RetrievalMethodReport(
        method=method,
        total_cases=total,
        successful_cases=total - error_count,
        error_cases=error_count,
        top1_accuracy=sum(item.top1_hit for item in results) / total,
        recall_at_k=sum(item.recall_at_k for item in results) / total,
        mrr=sum(item.reciprocal_rank for item in results) / total,
        evidence_term_coverage=(sum(term_scores) / len(term_scores)) if term_scores else None,
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        spoiler_violations=sum(item.spoiler_violation for item in results),
        cases=results,
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _positive_int_tuple(value: object, field_name: str, case_id: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise EvaluationDataError(f"案例 {case_id} 的 {field_name} 必须是数组。")
    parsed: list[int] = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError) as exc:
            raise EvaluationDataError(f"案例 {case_id} 的 {field_name} 包含非法章节号。") from exc
        if number <= 0:
            raise EvaluationDataError(f"案例 {case_id} 的 {field_name} 只能包含正整数。")
        if number not in parsed:
            parsed.append(number)
    return tuple(parsed)


def _optional_positive_int(value: object, field_name: str, case_id: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationDataError(f"案例 {case_id} 的 {field_name} 必须是正整数。") from exc
    if parsed <= 0:
        raise EvaluationDataError(f"案例 {case_id} 的 {field_name} 必须是正整数。")
    return parsed


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
