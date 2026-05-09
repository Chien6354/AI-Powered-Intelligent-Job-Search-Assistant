"""联网摘要：可选 Tavily API 或 DuckDuckGo 文本检索；失败时可降级。"""

from __future__ import annotations

from typing import Any

_last_web_debug: dict[str, Any] = {}


def web_search_debug() -> dict[str, Any]:
    """最近一次 web_search_snippets 的 provider / error / fallback 信息（供 agent debug）。"""
    return dict(_last_web_debug)


def _reset_web_debug() -> None:
    global _last_web_debug
    _last_web_debug = {
        "provider": None,
        "error": None,
        "fallback_from": None,
    }


def _ddgs_snippets(query: str, *, max_results: int) -> str | None:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        _last_web_debug["error"] = "duckduckgo_search not installed"
        return None
    try:
        lines: list[str] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                title = (r.get("title") or "").strip()
                body = (r.get("body") or "").strip()
                url = (r.get("href") or r.get("url") or "").strip()
                if title or body:
                    if url:
                        lines.append(f"- **{title}** | {url}\n  {body}")
                    else:
                        lines.append(f"- **{title}**: {body}")
        return "\n".join(lines) if lines else None
    except Exception as e:
        _last_web_debug["error"] = f"ddgs:{e}"[:300]
        return None


def _tavily_snippets(
    query: str,
    *,
    max_results: int,
    search_depth: str,
    api_key: str,
) -> str | None:
    import httpx

    depth = (search_depth or "basic").strip().lower()
    if depth not in ("advanced", "basic", "fast", "ultra-fast"):
        depth = "basic"
    n = max(1, min(int(max_results), 20))
    try:
        payload = {
            "query": query[:400],
            "max_results": n,
            "search_depth": depth,
        }
        resp = httpx.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=45.0,
        )
        if resp.status_code == 401:
            resp = httpx.post(
                "https://api.tavily.com/search",
                json={**payload, "api_key": api_key},
                timeout=45.0,
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _last_web_debug["error"] = f"tavily:{e}"[:300]
        return None

    results = data.get("results") or []
    lines: list[str] = []
    for item in results:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        content = (item.get("content") or "").strip()
        if not (title or content):
            continue
        head = title or "(无标题)"
        if url:
            lines.append(f"- **{head}** | {url}\n  {content}")
        else:
            lines.append(f"- **{head}**\n  {content}")
    return "\n".join(lines) if lines else None


def web_search_snippets(query: str, *, max_results: int = 5) -> str | None:
    from campus_rag.config_loader import settings, tavily_api_key

    _reset_web_debug()
    q = (query or "").strip()
    if not q:
        return None

    cfg = settings()
    prov = str(cfg.get("web_search_provider") or "auto").strip().lower()
    key = tavily_api_key()
    if prov == "auto":
        prov = "tavily" if key else "duckduckgo"

    tavily_n = int(cfg.get("tavily_max_results") or max_results)
    tavily_n = max(1, min(tavily_n, 20))
    cap = max(1, min(int(max_results), tavily_n, 20))
    depth = str(cfg.get("tavily_search_depth") or "basic")

    if prov == "tavily":
        if not key:
            _last_web_debug["error"] = "TAVILY_API_KEY missing"
            return None
        out = _tavily_snippets(q, max_results=cap, search_depth=depth, api_key=key)
        if out:
            _last_web_debug["provider"] = "tavily"
            return out
        _last_web_debug["fallback_from"] = "tavily"
        out = _ddgs_snippets(q, max_results=cap)
        if out:
            _last_web_debug["provider"] = "duckduckgo"
        return out

    # duckduckgo
    out = _ddgs_snippets(q, max_results=cap)
    if out:
        _last_web_debug["provider"] = "duckduckgo"
    return out
