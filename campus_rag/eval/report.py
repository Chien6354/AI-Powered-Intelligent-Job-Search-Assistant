"""汇总评测数据并输出 Excel 报表（三个 Sheet）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from campus_rag.eval.runner import EvalRow


def _flatten_row(
    row: EvalRow,
    router: dict[str, Any],
    retrieval: dict[str, Any],
    rerank: dict[str, Any],
    judge: dict[str, Any],
) -> dict[str, Any]:
    q = row.question_data
    return {
        "question_id": q.get("id"),
        "category": q.get("category"),
        "question": q.get("question"),
        "expected_intent": q.get("expected_intent"),
        "actual_intent": router.get("actual_intent"),
        "intent_correct": router.get("intent_correct"),
        "expected_company_filter": router.get("expected_company_filter"),
        "actual_company_filter": router.get("actual_company_filter"),
        "company_filter_correct": router.get("company_filter_correct"),
        "has_chunks": retrieval.get("has_chunks") if not retrieval.get("skipped") else None,
        "top1_sim": retrieval.get("top1_vector_similarity"),
        "abstain_reason": retrieval.get("abstain_reason"),
        "metadata_relaxed": retrieval.get("metadata_relaxed"),
        "top1_rerank": rerank.get("top1_rerank_score"),
        "rerank_filter_loss": rerank.get("filter_loss_rate"),
        "relevance": judge.get("relevance"),
        "faithfulness": judge.get("faithfulness"),
        "completeness": judge.get("completeness"),
        "conciseness": judge.get("conciseness"),
        "actionability": judge.get("actionability"),
        "judge_comment": judge.get("comment"),
        "judge_error": judge.get("judge_error"),
        "elapsed_s": row.elapsed_s,
        "error": row.error,
        "answer_preview": (row.answer or "")[:300],
    }


def build_detail_df(
    rows: list[EvalRow],
    router_metrics: list[dict[str, Any]],
    retrieval_metrics: list[dict[str, Any]],
    rerank_metrics: list[dict[str, Any]],
    judge_scores: list[dict[str, Any]],
) -> pd.DataFrame:
    records = []
    for row, rm, ret, rr, js in zip(rows, router_metrics, retrieval_metrics, rerank_metrics, judge_scores):
        records.append(_flatten_row(row, rm, ret, rr, js))
    return pd.DataFrame(records)


def build_summary_df(detail_df: pd.DataFrame) -> pd.DataFrame:
    score_cols = ["relevance", "faithfulness", "completeness", "conciseness", "actionability"]
    metric_cols = ["intent_correct", "has_chunks", "top1_sim", "top1_rerank", "elapsed_s"] + score_cols

    groups = []
    for cat, grp in detail_df.groupby("category"):
        row: dict[str, Any] = {"category": cat, "count": len(grp)}
        for col in metric_cols:
            if col in grp.columns:
                vals = pd.to_numeric(grp[col], errors="coerce").dropna()
                row[f"{col}_mean"] = round(vals.mean(), 3) if len(vals) > 0 else None
        groups.append(row)

    total: dict[str, Any] = {"category": "TOTAL", "count": len(detail_df)}
    for col in metric_cols:
        if col in detail_df.columns:
            vals = pd.to_numeric(detail_df[col], errors="coerce").dropna()
            total[f"{col}_mean"] = round(vals.mean(), 3) if len(vals) > 0 else None
    groups.append(total)

    return pd.DataFrame(groups)


def build_issues_df(detail_df: pd.DataFrame) -> pd.DataFrame:
    score_cols = ["relevance", "faithfulness", "completeness", "conciseness", "actionability"]
    mask = detail_df["intent_correct"] == False  # noqa: E712

    for col in score_cols:
        if col in detail_df.columns:
            numeric = pd.to_numeric(detail_df[col], errors="coerce")
            mask = mask | (numeric < 3)

    mask = mask | detail_df["error"].notna()

    issues = detail_df[mask].copy()
    return issues


def export_excel(
    rows: list[EvalRow],
    router_metrics: list[dict[str, Any]],
    retrieval_metrics: list[dict[str, Any]],
    rerank_metrics: list[dict[str, Any]],
    judge_scores: list[dict[str, Any]],
    output_path: str | Path,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    detail = build_detail_df(rows, router_metrics, retrieval_metrics, rerank_metrics, judge_scores)
    summary = build_summary_df(detail)
    issues = build_issues_df(detail)

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        detail.to_excel(writer, sheet_name="逐题详情", index=False)
        summary.to_excel(writer, sheet_name="分类汇总", index=False)
        issues.to_excel(writer, sheet_name="问题诊断", index=False)

    return out
