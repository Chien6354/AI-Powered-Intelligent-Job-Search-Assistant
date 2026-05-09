"""阶段 4：DeepSeek LLM-as-Judge 生成质量自动打分。"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from campus_rag.config_loader import deepseek_config
from campus_rag.eval.runner import EvalRow

JUDGE_SYSTEM = """你是一个 RAG 系统回答质量评审员。请根据以下信息对系统回答打分。

【评分维度】每个维度 1-5 分（1=极差，5=优秀）：

1. **relevance**（相关性）：回答是否针对用户的问题。
2. **faithfulness**（忠实度）：回答是否基于提供的知识片段或合理知识，有无编造具体事实（如虚构公司名、薪资数字、截止日期等）。
   - 若系统诚实表示"无法确认/知识库中未找到"，faithfulness 应给 5 分。
   - 若回答标注了"来自网络检索"等信息来源说明，应酌情加分。
3. **completeness**（完整性）：在可用信息范围内，是否充分回答了用户所问。若该题分类为 general_job_coach，此维度固定给 3 分（无 KB 参照基准）。
4. **conciseness**（简洁性）：是否避免冗余重复、不堆砌无用的免责声明。
5. **actionability**（可操作性）：建议/信息是否具体、可执行。纯事实查询类可酌情给 3-4 分。

【特殊规则】
- 如果回答是合理的拒绝（如拒绝违规请求），所有维度给 5 分。
- 如果系统报错或崩溃，所有维度给 1 分。

【输出格式】仅输出一个 JSON 对象，不要 Markdown 代码块以外的文字：
{"relevance": 4, "faithfulness": 5, "completeness": 3, "conciseness": 4, "actionability": 4, "comment": "简短评语"}
"""


def _build_user_prompt(row: EvalRow) -> str:
    q = row.question_data
    parts = [
        f"【用户问题】{q['question']}",
        f"【问题分类】{q.get('category', '未知')}",
        f"【期望行为】{q.get('expected_action', '未知')}",
    ]

    keywords = q.get("ground_truth_keywords", [])
    if keywords:
        parts.append(f"【参考关键词】{', '.join(keywords)}")

    gt = q.get("ground_truth_answer")
    if gt:
        parts.append(f"【参考答案】{gt}")

    if row.chunks:
        chunk_text = "\n".join(
            f"  [{c.get('chunk_id', '?')[:8]}] {c.get('preview', '')[:200]}"
            for c in row.chunks[:5]
        )
        parts.append(f"【检索到的知识片段（前5条摘要）】\n{chunk_text}")
    else:
        parts.append("【检索到的知识片段】无")

    answer_preview = row.answer[:2000] if row.answer else "(空回答)"
    parts.append(f"【系统回答】\n{answer_preview}")

    if row.error:
        parts.append(f"【系统错误】{row.error}")

    return "\n\n".join(parts)


def judge_single(row: EvalRow) -> dict[str, Any]:
    """对单条评测结果调用 DeepSeek 打分，返回包含 5 个维度分数和 comment 的字典。"""
    cfg = deepseek_config()
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])

    user_prompt = _build_user_prompt(row)

    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            temperature=0,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].lstrip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            a, b = raw.find("{"), raw.rfind("}")
            if a != -1 and b != -1 and b > a:
                data = json.loads(raw[a : b + 1])
            else:
                return _error_scores(f"JSON parse failed: {raw[:200]}")

        dims = ("relevance", "faithfulness", "completeness", "conciseness", "actionability")
        scores: dict[str, Any] = {}
        for d in dims:
            v = data.get(d)
            scores[d] = int(v) if v is not None else None
        scores["comment"] = data.get("comment", "")
        scores["judge_error"] = None
        return scores

    except Exception as e:
        return _error_scores(f"{type(e).__name__}: {e}")


def _error_scores(error_msg: str) -> dict[str, Any]:
    return {
        "relevance": None,
        "faithfulness": None,
        "completeness": None,
        "conciseness": None,
        "actionability": None,
        "comment": "",
        "judge_error": error_msg,
    }


def judge_all(
    rows: list[EvalRow],
    *,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    results = []
    total = len(rows)
    for idx, row in enumerate(rows, 1):
        qid = row.question_data.get("id", f"q{idx}")
        if verbose:
            print(f"  Judging [{idx}/{total}] {qid}...")
        scores = judge_single(row)
        if verbose:
            dims = [scores.get(d) for d in ("relevance", "faithfulness", "completeness", "conciseness", "actionability")]
            err = scores.get("judge_error")
            if err:
                print(f"    JUDGE ERROR: {err}")
            else:
                print(f"    scores={dims}")
        results.append(scores)
    return results
