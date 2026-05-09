"""阶段 1-3 规则指标计算。"""

from __future__ import annotations

from typing import Any

from campus_rag.eval.runner import EvalRow

# ---------------------------------------------------------------------------
# 阶段 1：路由与护栏
# ---------------------------------------------------------------------------

def compute_router_metrics(row: EvalRow) -> dict[str, Any]:
    q = row.question_data
    debug = row.debug
    route = debug.get("route", {})

    expected_intent = q.get("expected_intent")
    expected_action = q.get("expected_action")
    expected_company = q.get("expected_company_filter")

    actual_allowed = route.get("allowed", True)
    actual_intent = debug.get("intent_normalized")

    is_rejected = actual_allowed is False

    if expected_intent == "reject":
        intent_correct = is_rejected
    else:
        intent_correct = (actual_intent == expected_intent) if actual_intent else False

    actual_company = debug.get("filter_company_substring_normalized")
    if expected_company is None:
        company_correct = actual_company is None
    elif actual_company is None:
        company_correct = False
    else:
        company_correct = (
            expected_company.lower() in actual_company.lower()
            or actual_company.lower() in expected_company.lower()
        )

    return {
        "expected_intent": expected_intent,
        "actual_intent": "reject" if is_rejected else actual_intent,
        "intent_correct": intent_correct,
        "is_rejected": is_rejected,
        "expected_company_filter": expected_company,
        "actual_company_filter": actual_company,
        "company_filter_correct": company_correct,
    }


# ---------------------------------------------------------------------------
# 阶段 2：检索质量
# ---------------------------------------------------------------------------

def compute_retrieval_metrics(row: EvalRow) -> dict[str, Any]:
    q = row.question_data
    debug = row.debug
    retrieve = debug.get("retrieve", {})
    expected_action = q.get("expected_action")
    expected_intent = q.get("expected_intent")

    skip = expected_intent in ("general_job_coach", "reject") or row.debug.get("route", {}).get("allowed") is False
    if skip:
        return {"skipped": True, "reason": "non-retrieval intent"}

    has_chunks = len(row.chunks) > 0
    top1_sim = retrieve.get("top1_vector_similarity")
    abstain_reason = retrieve.get("abstain_reason")
    relaxation = retrieve.get("metadata_relaxation", [])

    should_abstain = expected_action == "abstain"
    actually_abstained = not has_chunks and abstain_reason is not None
    abstain_correct = (should_abstain == actually_abstained) if should_abstain else None

    return {
        "skipped": False,
        "has_chunks": has_chunks,
        "top1_vector_similarity": round(top1_sim, 4) if top1_sim is not None else None,
        "abstain_reason": abstain_reason,
        "metadata_relaxation": relaxation,
        "metadata_relaxed": len(relaxation) > 0,
        "expected_action": expected_action,
        "should_abstain": should_abstain,
        "actually_abstained": actually_abstained,
        "abstain_correct": abstain_correct,
    }


# ---------------------------------------------------------------------------
# 阶段 3：重排质量
# ---------------------------------------------------------------------------

def compute_rerank_metrics(row: EvalRow) -> dict[str, Any]:
    debug = row.debug
    retrieve = debug.get("retrieve", {})
    reranked = retrieve.get("reranked", [])

    if not reranked:
        return {"skipped": True, "reason": "no reranked chunks"}

    top1_rerank = reranked[0].get("rerank_score")
    top1_vec_sim = reranked[0].get("vector_similarity")

    head_count = retrieve.get("post_rerank_head_count", 0)
    after_filter = retrieve.get("post_rerank_after_sim_filter", 0)
    filter_loss = 1 - (after_filter / head_count) if head_count > 0 else 0.0

    return {
        "skipped": False,
        "top1_rerank_score": round(top1_rerank, 4) if top1_rerank is not None else None,
        "top1_vector_similarity": round(top1_vec_sim, 4) if top1_vec_sim is not None else None,
        "reranked_count": len(reranked),
        "head_count": head_count,
        "after_sim_filter": after_filter,
        "filter_loss_rate": round(filter_loss, 4),
    }


# ---------------------------------------------------------------------------
# 汇总统计
# ---------------------------------------------------------------------------

def aggregate_router(all_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(all_metrics)
    if n == 0:
        return {}
    intent_acc = sum(1 for m in all_metrics if m["intent_correct"]) / n

    reject_expected = [m for m in all_metrics if m["expected_intent"] == "reject"]
    reject_actual = [m for m in all_metrics if m["is_rejected"]]
    reject_tp = sum(1 for m in reject_actual if m["expected_intent"] == "reject")
    reject_precision = reject_tp / len(reject_actual) if reject_actual else None
    reject_recall = reject_tp / len(reject_expected) if reject_expected else None

    company_applicable = [m for m in all_metrics if m["expected_company_filter"] is not None]
    company_acc = (
        sum(1 for m in company_applicable if m["company_filter_correct"]) / len(company_applicable)
        if company_applicable else None
    )

    return {
        "intent_accuracy": round(intent_acc, 4),
        "reject_precision": round(reject_precision, 4) if reject_precision is not None else None,
        "reject_recall": round(reject_recall, 4) if reject_recall is not None else None,
        "company_filter_accuracy": round(company_acc, 4) if company_acc is not None else None,
        "total_questions": n,
    }


def aggregate_retrieval(all_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    active = [m for m in all_metrics if not m.get("skipped")]
    if not active:
        return {}
    hit_rate = sum(1 for m in active if m["has_chunks"]) / len(active)

    sims = [m["top1_vector_similarity"] for m in active if m["top1_vector_similarity"] is not None]
    avg_sim = sum(sims) / len(sims) if sims else None

    reasons: dict[str, int] = {}
    for m in active:
        r = m.get("abstain_reason")
        if r:
            reasons[r] = reasons.get(r, 0) + 1

    relaxed_count = sum(1 for m in active if m.get("metadata_relaxed"))
    relaxation_rate = relaxed_count / len(active)

    abstain_expected = [m for m in active if m.get("should_abstain")]
    abstain_correct_rate = (
        sum(1 for m in abstain_expected if m.get("abstain_correct")) / len(abstain_expected)
        if abstain_expected else None
    )

    return {
        "hit_rate": round(hit_rate, 4),
        "avg_top1_similarity": round(avg_sim, 4) if avg_sim is not None else None,
        "abstain_reason_distribution": reasons,
        "metadata_relaxation_rate": round(relaxation_rate, 4),
        "correct_abstain_rate": round(abstain_correct_rate, 4) if abstain_correct_rate is not None else None,
        "active_questions": len(active),
    }


def aggregate_rerank(all_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    active = [m for m in all_metrics if not m.get("skipped")]
    if not active:
        return {}

    scores = [m["top1_rerank_score"] for m in active if m["top1_rerank_score"] is not None]
    avg_rerank = sum(scores) / len(scores) if scores else None

    losses = [m["filter_loss_rate"] for m in active]
    avg_loss = sum(losses) / len(losses) if losses else None

    return {
        "avg_top1_rerank_score": round(avg_rerank, 4) if avg_rerank is not None else None,
        "avg_filter_loss_rate": round(avg_loss, 4) if avg_loss is not None else None,
        "active_questions": len(active),
    }
