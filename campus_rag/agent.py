from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from campus_rag.config_loader import deepseek_config, settings
from campus_rag.prompts import (
    COMPOSE_COACH,
    COMPOSE_INTERVIEW_NO_KB,
    COMPOSE_INTERVIEW_WITH_KB,
    COMPOSE_RECRUITMENT_FALLBACK,
    COMPOSE_RECRUITMENT_WEB,
    COMPOSE_SYSTEM,
    RETRIEVAL_QUERY_REWRITE_SYSTEM,
    ROUTER_SYSTEM,
    abstain_short,
    reject_message,
)
from campus_rag.retrieve import retrieve_and_rerank, RetrievedChunk
from campus_rag.web_search import web_search_debug, web_search_snippets


@dataclass
class PipelineStep:
    name: str
    detail: str


@dataclass
class AgentResult:
    answer: str
    steps: list[PipelineStep] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


def _client() -> OpenAI:
    cfg = deepseek_config()
    if not cfg["api_key"]:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，请在 .env 中配置")
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])


def _router(user_text: str, history_snippet: str) -> dict[str, Any]:
    cfg = deepseek_config()
    client = _client()
    content = f"近期对话摘要（可为空）：\n{history_snippet}\n\n用户最新问题：\n{user_text}"
    resp = client.chat.completions.create(
        model=cfg["model"],
        temperature=0,
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": content},
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
            raise
    data.setdefault("reject_reason", None)
    data.setdefault("intent", "recruitment_rag")
    data.setdefault("user_wants_web", False)
    data.setdefault("filter_company_substring", None)
    if "allowed" not in data:
        data["allowed"] = True
    return data


def _normalize_company_substring(route: dict) -> str | None:
    v = route.get("filter_company_substring")
    if not isinstance(v, str):
        return None
    s = v.strip()
    if len(s) < 2 or len(s) > 24:
        return None
    return s


def _normalize_intent(route: dict) -> str:
    v = (route.get("intent") or "").strip().lower().replace("-", "_")
    valid = frozenset({"recruitment_rag", "interview_exp_rag", "general_job_coach"})
    if v in valid:
        return v
    aliases = {
        "recruitment": "recruitment_rag",
        "interview": "interview_exp_rag",
        "interview_exp": "interview_exp_rag",
        "coach": "general_job_coach",
        "general": "general_job_coach",
    }
    return aliases.get(v, "recruitment_rag")


def _doc_types_for_intent(cfg: dict, intent: str) -> list[str] | None:
    if intent == "recruitment_rag":
        t = cfg.get("router_recruitment_doc_types") or []
        return list(t) if t else None
    if intent == "interview_exp_rag":
        t = cfg.get("router_interview_doc_types") or []
        return list(t) if t else None
    return None


def _rewrite_retrieval_query(
    user_text: str,
    history: list[tuple[str, str]],
    *,
    enabled: bool,
) -> tuple[str, dict[str, Any]]:
    """生成本轮用于向量检索/联网搜索的独立 query；失败或未启用时回退为当前用户句。"""
    cur = (user_text or "").strip()
    debug: dict[str, Any] = {"original_user_text": cur[:500]}
    if not enabled:
        debug["skipped"] = True
        debug["reason"] = "disabled"
        return cur, debug
    if not history:
        debug["skipped"] = True
        debug["reason"] = "no_history"
        return cur, debug

    cfg = deepseek_config()
    client = _client()
    hist = "\n".join(f"{r}:{t}" for r, t in history[-6:])
    content = f"近期对话（含角色，由旧到新）：\n{hist}\n\n用户最新问题：\n{cur}"
    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            temperature=0,
            messages=[
                {"role": "system", "content": RETRIEVAL_QUERY_REWRITE_SYSTEM},
                {"role": "user", "content": content},
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
                raise
        rq = data.get("retrieval_query")
        uctx = data.get("used_prior_context")
        if isinstance(uctx, bool):
            debug["used_prior_context"] = uctx
        if isinstance(rq, str) and rq.strip():
            out = rq.strip()[:400]
            debug["retrieval_query"] = out
            return out, debug
        debug["fallback"] = "empty_retrieval_query"
    except Exception as e:
        debug["fallback"] = "llm_error"
        debug["error"] = str(e)
    return cur, debug


def _compose_chunks(
    user_text: str,
    chunks: list[RetrievedChunk],
    system: str,
    *,
    retrieval_note: str = "",
) -> str:
    cfg = deepseek_config()
    client = _client()
    ctx_lines = []
    for c in chunks:
        ctx_lines.append(
            f"[chunk_id:{c.chunk_id}]\nsource_url:{c.source_url}\n{c.text}\n---"
        )
    ctx = "\n".join(ctx_lines)
    prefix = f"{retrieval_note}\n\n" if retrieval_note else ""
    user = f"{prefix}用户问题：{user_text}\n\n知识片段：\n{ctx}"
    resp = client.chat.completions.create(
        model=cfg["model"],
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def _format_coach_user_message(
    history: list[tuple[str, str]],
    user_text: str,
    *,
    max_messages: int = 16,
    max_chars_per_message: int = 2400,
) -> str:
    """教练模式：把近期多轮对话拼进 user 正文，便于模型利用上下文。"""
    cur = (user_text or "").strip()
    if not history:
        return cur
    lines: list[str] = []
    tail = history[-max_messages:] if len(history) > max_messages else history
    for role, text in tail:
        t = (text or "").strip()
        if not t:
            continue
        if len(t) > max_chars_per_message:
            t = t[: max_chars_per_message - 1] + "…"
        lines.append(f"{role}: {t}")
    if not lines:
        return cur
    block = "\n".join(lines)
    return (
        "【对话上文】（由旧到新；role 为 user 或 assistant）\n"
        f"{block}\n\n"
        "【当前用户输入】\n"
        f"{cur}"
    )


def _compose_direct(user_text: str, system: str, extra: str = "") -> str:
    cfg = deepseek_config()
    client = _client()
    body = user_text.strip()
    if extra.strip():
        body = f"{body}\n\n{extra.strip()}"
    resp = client.chat.completions.create(
        model=cfg["model"],
        temperature=0.3,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": body},
        ],
    )
    return resp.choices[0].message.content or ""


def _chunks_to_cards(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": c.chunk_id,
            "source_url": c.source_url,
            "company": c.company,
            "season": c.season,
            "job_title": c.job_title,
            "vector_similarity": round(c.vector_similarity, 4),
            "rerank_score": None if c.rerank_score is None else round(c.rerank_score, 4),
            "preview": c.text[:240] + ("…" if len(c.text) > 240 else ""),
        }
        for c in chunks
    ]


def run_turn(user_text: str, history: list[tuple[str, str]]) -> AgentResult:
    steps: list[PipelineStep] = []
    debug: dict[str, Any] = {}
    cfg = settings()

    hist = "\n".join(f"{r}:{t}" for r, t in history[-6:])

    try:
        route = _router(user_text, hist)
    except Exception as e:
        err = str(e)
        steps.append(PipelineStep("guardrail_and_route", f"路由失败：{err}"))
        hint = (
            "**常见原因**：\n"
            "1. 项目根目录（与 `streamlit_app.py` 同级）是否有 `.env` 文件，且内容为 `DEEPSEEK_API_KEY=sk-...`（不要只改 `.env.example`）。\n"
            "2. Key 两边不要多余引号或空格；文件请保存为 **UTF-8**。\n"
            "3. 若用代理，需配置系统/终端代理，使本机能访问 `api.deepseek.com`。\n"
            "4. 展开右侧 **guardrail_and_route** 或 **debug JSON** 查看完整报错。"
        )
        return AgentResult(
            answer=f"路由调用失败：\n\n`{err}`\n\n{hint}",
            steps=steps,
            debug={"error": err, "error_type": type(e).__name__},
        )

    steps.append(PipelineStep("guardrail_and_route", json.dumps(route, ensure_ascii=False)))
    debug["route"] = route

    if route.get("allowed") is False:
        reason = route.get("reject_reason") or "不适用"
        return AgentResult(
            answer=reject_message(reason),
            steps=steps,
            debug=debug,
        )

    intent = _normalize_intent(route)
    debug["intent_normalized"] = intent

    if intent == "general_job_coach":
        coach_user = _format_coach_user_message(history, user_text)
        debug["coach_context_messages"] = len(history)
        try:
            answer = _compose_direct(coach_user, COMPOSE_COACH)
        except Exception as e:
            steps.append(PipelineStep("compose_coach", f"失败：{e}"))
            return AgentResult(
                answer="抱歉，暂时无法生成回答，请稍后再试。",
                steps=steps,
                debug={**debug, "error": str(e)},
            )
        steps.append(PipelineStep("compose_coach", "ok"))
        return AgentResult(answer=answer, steps=steps, chunks=[], debug=debug)

    raw_rw = cfg.get("retrieval_query_rewrite_enabled")
    rewrite_on = True if raw_rw is None else bool(raw_rw)
    retrieve_query, rw_debug = _rewrite_retrieval_query(
        user_text, history, enabled=rewrite_on
    )
    debug["retrieve_query_rewrite"] = rw_debug
    debug["retrieve_query"] = retrieve_query[:500]
    steps.append(PipelineStep("query_rewrite", json.dumps(rw_debug, ensure_ascii=False)))

    doc_types = _doc_types_for_intent(cfg, intent)
    company_like = _normalize_company_substring(route)
    debug["filter_company_substring_normalized"] = company_like

    chunks, rdebug = retrieve_and_rerank(
        retrieve_query,
        filter_company=None,
        filter_company_like=company_like,
        filter_season=None,
        filter_doc_types=doc_types,
        apply_min_similarity=True,
    )
    steps.append(PipelineStep("retrieve_rerank", json.dumps(rdebug, ensure_ascii=False)))
    debug["retrieve"] = rdebug

    if intent == "interview_exp_rag":
        # 面经：无库命中 → 仅大模型通用回答（不联网）
        if chunks:
            try:
                answer = _compose_chunks(
                    user_text, chunks, COMPOSE_INTERVIEW_WITH_KB, retrieval_note=""
                )
            except Exception as e:
                steps.append(PipelineStep("compose", f"生成失败：{e}"))
                return AgentResult(
                    answer=abstain_short(user_text),
                    steps=steps,
                    chunks=_chunks_to_cards(chunks),
                    debug={**debug, "error": str(e)},
                )
            steps.append(PipelineStep("compose_interview_kb", "ok"))
            return AgentResult(
                answer=answer,
                steps=steps,
                chunks=_chunks_to_cards(chunks),
                debug=debug,
            )
        try:
            answer = _compose_direct(user_text, COMPOSE_INTERVIEW_NO_KB)
        except Exception as e:
            steps.append(PipelineStep("compose_interview_llm", f"失败：{e}"))
            return AgentResult(
                answer=abstain_short(user_text),
                steps=steps,
                debug={**debug, "error": str(e)},
            )
        steps.append(PipelineStep("compose_interview_llm", "ok"))
        debug["interview_fallback"] = "no_kb_llm"
        return AgentResult(answer=answer, steps=steps, chunks=[], debug=debug)

    # recruitment_rag
    chunk_cards = _chunks_to_cards(chunks)

    if chunks:
        try:
            answer = _compose_chunks(
                user_text, chunks, COMPOSE_SYSTEM, retrieval_note=""
            )
        except Exception as e:
            steps.append(PipelineStep("compose", f"生成失败：{e}"))
            return AgentResult(
                answer=abstain_short(user_text),
                steps=steps,
                chunks=chunk_cards,
                debug={**debug, "error": str(e)},
            )
        steps.append(PipelineStep("compose_recruitment_kb", "ok"))
        return AgentResult(answer=answer, steps=steps, chunks=chunk_cards, debug=debug)

    # 招聘意图且无库命中：默认走在线搜索兜底；settings 中 web_search_enabled=false 可关闭
    abstain = rdebug.get("abstain_reason") or "no_hits"
    raw_web = cfg.get("web_search_enabled")
    web_allowed = True if raw_web is None else bool(raw_web)
    try_web = web_allowed
    snip = web_search_snippets(retrieve_query) if try_web else None
    fb: dict[str, Any] = {
        "abstain_reason": abstain,
        "web_attempted": try_web,
        "web_snippets": bool(snip),
    }
    if try_web:
        wmeta = web_search_debug()
        fb["web_provider"] = wmeta.get("provider")
        fb["web_error"] = wmeta.get("error")
        fb["web_fallback_from"] = wmeta.get("fallback_from")
    debug["recruitment_fallback"] = fb

    try:
        if snip:
            answer = _compose_direct(
                user_text,
                COMPOSE_RECRUITMENT_WEB,
                extra=f"【网络检索摘要】\n{snip}",
            )
            steps.append(PipelineStep("compose_recruitment_web", "ok"))
        else:
            answer = _compose_direct(user_text, COMPOSE_RECRUITMENT_FALLBACK)
            steps.append(PipelineStep("compose_recruitment_fallback", "ok"))
    except Exception as e:
        steps.append(PipelineStep("compose_recruitment_fallback", f"失败：{e}"))
        return AgentResult(
            answer=abstain_short(user_text),
            steps=steps,
            debug={**debug, "error": str(e)},
        )

    return AgentResult(answer=answer, steps=steps, chunks=[], debug=debug)
