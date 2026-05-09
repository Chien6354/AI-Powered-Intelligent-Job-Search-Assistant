"""校招助手 Streamlit 入口。在项目根目录执行: streamlit run streamlit_app.py"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(Path.cwd() / ".env", override=False)

import streamlit as st

from campus_rag.agent import AgentResult, run_turn

DEMO_MODE = os.getenv("DEMO_MODE", "0").strip().lower() in ("1", "true", "yes")
USE_GOOGLE_FONTS = os.getenv("USE_GOOGLE_FONTS", "1").strip().lower() in ("1", "true", "yes")

_STEP_DETAIL_MAX = 2000
_RETRIEVE_QUERY_PREVIEW = 120

SAMPLE_PROMPTS: list[tuple[str, str]] = [
    ("招聘信息查询", "湖南省高速公路集团校招流程是什么？"),
    ("面试经验与练习", "Token是什么？面试怎么回答？"),
    ("通用求职教练", "秋招投了很多简历都没回复，好焦虑怎么办？"),
]


def _inject_global_css() -> None:
    font_import = ""
    if USE_GOOGLE_FONTS:
        font_import = (
            "@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600"
            "&family=Source+Serif+4:opsz,wght@8..60,500;600&display=swap');"
        )
    st.markdown(
        f"""
<style>
{font_import}
html, body, [class*="css"] {{
  font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
}}
h1, h2, h3 {{
  font-family: "Source Serif 4", "Georgia", serif;
  letter-spacing: -0.02em;
}}
/* ---- 整页不滚 ---- */
section[data-testid="stMain"] > div:first-child {{
  overflow: hidden !important;
}}
[data-testid="stAppViewContainer"] .block-container {{
  padding-top: 0.25rem;
  padding-bottom: 0;
  max-width: 1560px;
}}
/* ---- 隐藏 Streamlit 原生 header ---- */
[data-testid="stHeader"] {{
  height: 0 !important;
  min-height: 0 !important;
  overflow: hidden !important;
  border: none !important;
}}
/* ---- 自定义导航栏 ---- */
.custom-navbar {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 3.25rem;
  padding: 0 2rem;
  background: #fafaf9;
  border-bottom: 1px solid #e7e5e4;
  margin: -0.25rem -1rem 0.6rem -1rem;
}}
.custom-navbar .nav-title {{
  font-family: "Source Serif 4", "Georgia", serif;
  font-size: 1.3rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: #1c1917;
}}
.custom-navbar .nav-clear {{
  font-size: 0.88rem;
  color: #78716c;
  text-decoration: none;
  cursor: pointer;
  transition: color 0.15s;
}}
.custom-navbar .nav-clear:hover {{
  color: #dc2626;
}}
/* ---- 对话体：覆盖 st.container(height=N) 的固定像素为视口相对高度 ---- */
[data-testid="stVerticalBlockBorderWrapper"]:has([data-chat-messages-scroll="1"]) {{
  height: calc(100svh - 12rem) !important;
  max-height: calc(100svh - 12rem) !important;
  min-height: 180px !important;
  border: none !important;
  box-shadow: none !important;
}}
/* ---- 证据列滚动区 ---- */
[data-testid="stVerticalBlockBorderWrapper"]:has([data-evidence-scroll="1"]) {{
  height: calc(100svh - 9.5rem) !important;
  max-height: calc(100svh - 9.5rem) !important;
  min-height: 180px !important;
}}
/* ---- 杂项 ---- */
[data-testid="stSidebar"] {{
  border-right: 1px solid #e7e5e4;
}}
div[data-testid="column"] {{
  border-radius: 6px;
}}
</style>
""",
        unsafe_allow_html=True,
    )


def _clear_conversation() -> None:
    st.session_state.history = []
    st.session_state.pop("_last_result", None)
    st.rerun()


def _execute_turn(prompt: str) -> None:
    st.session_state.history.append(("user", prompt))
    with st.spinner("思考中…"):
        result = run_turn(prompt, st.session_state.history[:-1])
    st.session_state.history.append(("assistant", result.answer))
    st.session_state["_last_result"] = result


def _truncate_detail(s: str, max_len: int = _STEP_DETAIL_MAX) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _render_run_summary(res: AgentResult) -> None:
    d = res.debug or {}
    lines: list[str] = []
    intent = d.get("intent_normalized")
    if intent:
        lines.append(f"- **意图** `{intent}`")
    rq = d.get("retrieve_query")
    if rq is not None and str(rq).strip():
        rs = str(rq).strip()
        short = rs[:_RETRIEVE_QUERY_PREVIEW] + ("…" if len(rs) > _RETRIEVE_QUERY_PREVIEW else "")
        lines.append(f"- **检索 query** {short}")
    fc = d.get("filter_company_substring_normalized")
    if fc:
        lines.append(f"- **公司子串** `{fc}`")
    rw = d.get("retrieve_query_rewrite") or {}
    if isinstance(rw.get("used_prior_context"), bool):
        lines.append(f"- **检索用上文** {rw.get('used_prior_context')}")
    fb = d.get("recruitment_fallback")
    if isinstance(fb, dict) and fb:
        lines.append(
            f"- **招聘兜底** 尝试联网={fb.get('web_attempted')}，有摘要={fb.get('web_snippets')}"
        )
    if res.chunks:
        lines.append(f"- **知识库片段** {len(res.chunks)} 条")
    else:
        lines.append("- **知识库片段** 本轮未返回 chunk（教练意图、无命中或仅用联网摘要时常见）")
    st.markdown("#### 本轮摘要")
    st.markdown("\n".join(lines))


def _render_sample_prompts() -> None:
    st.caption("示例问题（一键填入并发送）")
    cols = st.columns(len(SAMPLE_PROMPTS))
    for col, (label, full_q) in zip(cols, SAMPLE_PROMPTS):
        with col:
            if st.button(label, key=f"sample_{label}", use_container_width=True):
                _execute_turn(full_q)
                st.rerun()


# ---------------------------------------------------------------------------
# 页面主体
# ---------------------------------------------------------------------------

st.set_page_config(page_title="校招助手", layout="wide", initial_sidebar_state="collapsed")
_inject_global_css()

if "history" not in st.session_state:
    st.session_state.history = []

# -- 顶栏：纯 HTML 导航栏 -----------------------------------------------------
if "clear" in st.query_params:
    st.query_params.clear()
    _clear_conversation()

st.markdown(
    '<div class="custom-navbar">'
    '<span class="nav-title">校招助手</span>'
    '<a class="nav-clear" href="?clear=1" target="_self">清空对话</a>'
    "</div>",
    unsafe_allow_html=True,
)

# -- 三列布局 ----------------------------------------------------------------
col_gutter, col_chat, col_evidence = st.columns([0.35, 2.2, 1])

with col_gutter:
    pass

# -- 中间列：对话体（可滚动） + 示例（固定底部） + 输入框（Streamlit 底栏） -----
with col_chat:
    with st.container(height=400):
        st.markdown(
            '<div data-chat-messages-scroll="1" style="display:none" aria-hidden="true"></div>',
            unsafe_allow_html=True,
        )
        for role, text in st.session_state.history:
            with st.chat_message("user" if role == "user" else "assistant"):
                st.markdown(text)

    _render_sample_prompts()

    prompt = st.chat_input("输入校招相关问题…")
    if prompt:
        _execute_turn(prompt)
        st.rerun()

# -- 右侧列：证据与流水线（标题固定，内容可滚动） ------------------------------
with col_evidence:
    st.subheader("证据与流水线")
    with st.container(height=400):
        st.markdown(
            '<div data-evidence-scroll="1" style="display:none" aria-hidden="true"></div>',
            unsafe_allow_html=True,
        )
        res = st.session_state.get("_last_result")
        if not res:
            st.info("发送问题或使用左侧示例后，这里会显示本轮摘要、步骤与引用片段。")
        else:
            _render_run_summary(res)
            st.divider()
            for step in res.steps:
                with st.expander(step.name, expanded=False):
                    st.code(_truncate_detail(step.detail or ""))
            if res.chunks:
                with st.expander("引用片段", expanded=False):
                    for c in res.chunks:
                        meta_bits = []
                        if c.get("company"):
                            meta_bits.append(f"公司: {c['company']}")
                        if c.get("job_title"):
                            meta_bits.append(f"岗位: {c['job_title']}")
                        st.markdown(
                            f"- `{c['chunk_id'][:8]}…` sim={c.get('vector_similarity')} "
                            f"rerank={c.get('rerank_score')}\n  {c.get('preview', '')}"
                        )
                        if meta_bits:
                            st.caption(" · ".join(meta_bits))
                        if c.get("source_url"):
                            st.caption(c["source_url"])
            if res.debug:
                if DEMO_MODE:
                    with st.expander("完整 debug（面试官模式已默认隐藏）", expanded=False):
                        st.caption("将环境变量 DEMO_MODE=0 可显示完整 JSON。")
                else:
                    with st.expander("debug JSON", expanded=False):
                        st.json(res.debug)
