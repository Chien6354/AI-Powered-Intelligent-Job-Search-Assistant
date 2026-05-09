"""批量执行 run_turn 并收集 AgentResult + 耗时。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalRow:
    question_data: dict[str, Any]
    answer: str = ""
    steps: list[dict[str, str]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str | None = None


def load_questions(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def run_all(
    questions: list[dict[str, Any]],
    *,
    verbose: bool = True,
) -> list[EvalRow]:
    from campus_rag.agent import run_turn

    rows: list[EvalRow] = []
    total = len(questions)
    for idx, q in enumerate(questions, 1):
        qid = q.get("id", f"q{idx}")
        question = q["question"]
        history = [tuple(pair) for pair in q.get("history", [])]

        if verbose:
            print(f"[{idx}/{total}] {qid}: {question[:60]}…" if len(question) > 60 else f"[{idx}/{total}] {qid}: {question}")

        row = EvalRow(question_data=q)
        t0 = time.perf_counter()
        try:
            result = run_turn(question, history)
            row.answer = result.answer
            row.steps = [{"name": s.name, "detail": s.detail} for s in result.steps]
            row.chunks = result.chunks
            row.debug = result.debug
        except Exception as e:
            row.error = f"{type(e).__name__}: {e}"
            if verbose:
                print(f"  ERROR: {row.error}")
        row.elapsed_s = round(time.perf_counter() - t0, 2)

        if verbose and not row.error:
            intent = row.debug.get("intent_normalized", "?")
            n_chunks = len(row.chunks)
            print(f"  -> intent={intent}, chunks={n_chunks}, {row.elapsed_s}s")

        rows.append(row)
    return rows
