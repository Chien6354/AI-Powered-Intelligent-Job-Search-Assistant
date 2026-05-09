"""为评测题集初筛 relevant_chunk_ids 候选。

用法示例：
    python scripts/prescreen_relevant_chunks.py
    python scripts/prescreen_relevant_chunks.py --limit 20 --per-question 30
    python scripts/prescreen_relevant_chunks.py --questions data/eval/eval_questions.json --output data/eval/relevant_candidates.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = ROOT / "data" / "eval" / "eval_questions.json"
DEFAULT_DB = ROOT / "data" / "kb.sqlite3"
DEFAULT_OUTPUT = ROOT / "data" / "eval" / "relevant_candidates.json"


STOPWORDS = {
    "什么",
    "多少",
    "哪些",
    "怎么",
    "如何",
    "是否",
    "可以",
    "一共",
    "关于",
    "一下",
    "以及",
    "这个",
    "那个",
    "相关",
    "问题",
    "请问",
    "校招",
    "秋招",
    "春招",
    "招聘",
    "面试",
    "经验",
}


INTENT_DOC_TYPES: dict[str, tuple[str, ...]] = {
    "recruitment_rag": (
        "recruitment_notice",
        "sheet_row",
        "official_notice",
        "faq",
        "jd",
        "general",
    ),
    "interview_exp_rag": ("interview_exp", "interview_note"),
}


def _tokenize(question: str) -> list[str]:
    # 提取中文连续片段与数字/英文片段，保留长度 >=2 的词段。
    raw = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_.:-]{2,}", question)
    out: list[str] = []
    for tok in raw:
        t = tok.strip()
        if not t:
            continue
        if t in STOPWORDS:
            continue
        out.append(t)
    # 去重并保持顺序
    seen: set[str] = set()
    dedup: list[str] = []
    for t in out:
        if t not in seen:
            dedup.append(t)
            seen.add(t)
    return dedup


def _collect_query_terms(q: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    company = q.get("expected_company_filter")
    if isinstance(company, str) and company.strip():
        terms.append(company.strip())
    for kw in q.get("ground_truth_keywords", []) or []:
        if isinstance(kw, str) and kw.strip():
            terms.append(kw.strip())
    question = str(q.get("question") or "").strip()
    terms.extend(_tokenize(question))

    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _build_sql(doc_types: list[str] | None, term_count: int) -> str:
    term_expr = " + ".join(["(CASE WHEN c.text LIKE ? THEN 1 ELSE 0 END)"] * term_count)
    where_parts = ["(" + " OR ".join(["c.text LIKE ?"] * term_count) + ")"]
    if doc_types:
        placeholders = ",".join(["?"] * len(doc_types))
        where_parts.append(f"d.doc_type IN ({placeholders})")
    where_sql = " AND ".join(where_parts)
    return f"""
    SELECT
        c.chunk_id,
        c.doc_id,
        d.doc_type,
        c.company,
        c.season,
        c.job_title,
        c.source_url,
        c.heading_path,
        c.char_len,
        substr(c.text, 1, 260) AS preview,
        ({term_expr}) AS score
    FROM chunks c
    JOIN documents d ON d.doc_id = c.doc_id
    WHERE {where_sql}
    ORDER BY score DESC, c.char_len DESC, c.rowid DESC
    LIMIT ?
    """


def _query_candidates(
    conn: sqlite3.Connection,
    *,
    terms: list[str],
    doc_types: list[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    if not terms:
        return []

    sql = _build_sql(doc_types, term_count=len(terms))
    like_terms = [f"%{t}%" for t in terms]

    # 参数顺序：score表达式 terms + where terms + doc_types + limit
    params: list[Any] = []
    params.extend(like_terms)
    params.extend(like_terms)
    if doc_types:
        params.extend(doc_types)
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    candidates: list[dict[str, Any]] = []
    for r in rows:
        hit_terms = [t for t in terms if t and t in (r["preview"] or "")]
        candidates.append(
            {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "doc_type": r["doc_type"],
                "company": r["company"],
                "season": r["season"],
                "job_title": r["job_title"],
                "source_url": r["source_url"],
                "heading_path": r["heading_path"],
                "char_len": r["char_len"],
                "score": int(r["score"] or 0),
                "hit_terms": hit_terms,
                "preview": r["preview"],
            }
        )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="为题集初筛 relevant_chunk_ids 候选")
    parser.add_argument("--questions", type=str, default=str(DEFAULT_QUESTIONS), help="题集 JSON 路径")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB), help="SQLite 路径")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT), help="输出 JSON 路径")
    parser.add_argument("--per-question", type=int, default=25, help="每题输出候选上限")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 题（0=全部）")
    parser.add_argument(
        "--with-fallback",
        action="store_true",
        help="若按意图 doc_type 无结果，则自动放开 doc_type 再查一次",
    )
    args = parser.parse_args()

    qpath = Path(args.questions)
    db_path = Path(args.db)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with qpath.open(encoding="utf-8") as f:
        questions = json.load(f)
    if args.limit and args.limit > 0:
        questions = questions[: args.limit]

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    output_rows: list[dict[str, Any]] = []
    for idx, q in enumerate(questions, start=1):
        qid = q.get("id", f"q{idx}")
        intent = str(q.get("expected_intent") or "")
        terms = _collect_query_terms(q)
        doc_types = list(INTENT_DOC_TYPES.get(intent, ()))

        candidates = _query_candidates(
            conn,
            terms=terms,
            doc_types=doc_types if doc_types else None,
            limit=args.per_question,
        )
        used_fallback = False
        if args.with_fallback and not candidates:
            candidates = _query_candidates(
                conn,
                terms=terms,
                doc_types=None,
                limit=args.per_question,
            )
            used_fallback = True

        output_rows.append(
            {
                "id": qid,
                "question": q.get("question"),
                "expected_intent": intent,
                "expected_company_filter": q.get("expected_company_filter"),
                "query_terms": terms,
                "doc_type_filter": doc_types,
                "used_fallback_no_doc_type_filter": used_fallback,
                "candidate_count": len(candidates),
                "candidates": candidates,
            }
        )

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "questions_path": str(qpath),
                    "db_path": str(db_path),
                    "per_question": args.per_question,
                    "limit": args.limit,
                    "with_fallback": args.with_fallback,
                    "total_questions": len(output_rows),
                },
                "rows": output_rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"done: {out_path}")
    print(f"questions: {len(output_rows)}")
    print(f"per_question: {args.per_question}")


if __name__ == "__main__":
    main()
