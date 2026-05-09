"""从初筛候选生成 relevant_chunk_ids 草稿，并可写出合并后的题集文件。

典型流程：
1) 先运行初筛：
   python scripts/prescreen_relevant_chunks.py --output data/eval/relevant_candidates.json
2) 生成草稿：
   python scripts/draft_relevant_chunk_ids.py
3) 人工复核 `data/eval/eval_questions.with_draft.json` 中每题的 `relevant_chunk_ids_draft`
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = ROOT / "data" / "eval" / "eval_questions.json"
DEFAULT_CANDIDATES = ROOT / "data" / "eval" / "relevant_candidates.json"
DEFAULT_DRAFT = ROOT / "data" / "eval" / "relevant_chunk_ids_draft.json"
DEFAULT_MERGED = ROOT / "data" / "eval" / "eval_questions.with_draft.json"


def _pick_candidates(
    candidates: list[dict[str, Any]],
    *,
    top_n: int,
    min_score: int,
    unique_by_source: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    seen_sources: set[str] = set()
    for c in candidates:
        cid = str(c.get("chunk_id") or "").strip()
        if not cid or cid in seen_chunk_ids:
            continue
        score = int(c.get("score") or 0)
        if score < min_score:
            continue
        src = str(c.get("source_url") or "")
        if unique_by_source and src and src in seen_sources:
            continue
        out.append(c)
        seen_chunk_ids.add(cid)
        if unique_by_source and src:
            seen_sources.add(src)
        if len(out) >= top_n:
            break
    return out


def _build_draft_rows(
    candidate_rows: list[dict[str, Any]],
    *,
    top_n: int,
    min_score: int,
    unique_by_source: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in candidate_rows:
        picked = _pick_candidates(
            row.get("candidates", []) or [],
            top_n=top_n,
            min_score=min_score,
            unique_by_source=unique_by_source,
        )
        rows.append(
            {
                "id": row.get("id"),
                "question": row.get("question"),
                "expected_intent": row.get("expected_intent"),
                "query_terms": row.get("query_terms", []),
                "candidate_count": int(row.get("candidate_count") or 0),
                "draft_rule": {
                    "top_n": top_n,
                    "min_score": min_score,
                    "unique_by_source": unique_by_source,
                },
                "relevant_chunk_ids_draft": [c.get("chunk_id") for c in picked],
                "draft_candidates": [
                    {
                        "chunk_id": c.get("chunk_id"),
                        "score": c.get("score"),
                        "doc_type": c.get("doc_type"),
                        "company": c.get("company"),
                        "source_url": c.get("source_url"),
                        "heading_path": c.get("heading_path"),
                        "hit_terms": c.get("hit_terms", []),
                        "preview": c.get("preview"),
                    }
                    for c in picked
                ],
            }
        )
    return rows


def _merge_into_questions(
    questions: list[dict[str, Any]],
    draft_rows: list[dict[str, Any]],
    *,
    overwrite_existing: bool,
) -> list[dict[str, Any]]:
    draft_map = {str(r.get("id")): r for r in draft_rows if r.get("id") is not None}
    merged: list[dict[str, Any]] = []
    for q in questions:
        item = dict(q)
        qid = str(item.get("id") or "")
        d = draft_map.get(qid)
        if not d:
            merged.append(item)
            continue
        if overwrite_existing or "relevant_chunk_ids" not in item:
            item["relevant_chunk_ids"] = d.get("relevant_chunk_ids_draft", [])
        item["relevant_chunk_ids_draft"] = d.get("relevant_chunk_ids_draft", [])
        item["relevant_chunk_ids_draft_meta"] = {
            "rule": d.get("draft_rule"),
            "query_terms": d.get("query_terms", []),
            "draft_candidates": d.get("draft_candidates", []),
        }
        merged.append(item)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="从初筛候选生成 relevant_chunk_ids 草稿")
    parser.add_argument("--questions", type=str, default=str(DEFAULT_QUESTIONS), help="题集 JSON")
    parser.add_argument("--candidates", type=str, default=str(DEFAULT_CANDIDATES), help="初筛候选 JSON")
    parser.add_argument("--draft-output", type=str, default=str(DEFAULT_DRAFT), help="草稿输出 JSON")
    parser.add_argument("--merged-output", type=str, default=str(DEFAULT_MERGED), help="合并题集输出 JSON")
    parser.add_argument("--top-n", type=int, default=2, help="每题自动挑选候选数")
    parser.add_argument("--min-score", type=int, default=2, help="最小命中分数阈值")
    parser.add_argument(
        "--allow-same-source",
        action="store_true",
        help="允许同一个 source_url 选入多个 chunk（默认去重）",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="覆盖题集中已有 relevant_chunk_ids",
    )
    args = parser.parse_args()

    qpath = Path(args.questions)
    cpath = Path(args.candidates)
    draft_out = Path(args.draft_output)
    merged_out = Path(args.merged_output)
    draft_out.parent.mkdir(parents=True, exist_ok=True)
    merged_out.parent.mkdir(parents=True, exist_ok=True)

    with qpath.open(encoding="utf-8") as f:
        questions = json.load(f)
    with cpath.open(encoding="utf-8") as f:
        candidate_json = json.load(f)
    candidate_rows = candidate_json.get("rows", [])

    draft_rows = _build_draft_rows(
        candidate_rows,
        top_n=max(args.top_n, 1),
        min_score=max(args.min_score, 1),
        unique_by_source=not args.allow_same_source,
    )
    merged_questions = _merge_into_questions(
        questions,
        draft_rows,
        overwrite_existing=args.overwrite_existing,
    )

    with draft_out.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "questions_path": str(qpath),
                    "candidates_path": str(cpath),
                    "top_n": args.top_n,
                    "min_score": args.min_score,
                    "allow_same_source": args.allow_same_source,
                    "overwrite_existing": args.overwrite_existing,
                    "total_rows": len(draft_rows),
                },
                "rows": draft_rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    with merged_out.open("w", encoding="utf-8") as f:
        json.dump(merged_questions, f, ensure_ascii=False, indent=2)

    print(f"draft_done: {draft_out}")
    print(f"merged_done: {merged_out}")
    print(f"rows: {len(draft_rows)}")


if __name__ == "__main__":
    main()
