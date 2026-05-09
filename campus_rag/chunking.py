from __future__ import annotations

import re
from dataclasses import dataclass

from campus_rag.config_loader import settings


@dataclass
class ChunkDraft:
    text: str
    heading_path: str | None = None


def _header(
    company: str | None,
    season: str | None,
    doc_type: str,
    job_title: str | None = None,
) -> str:
    c = company or "未知公司"
    s = season or "unknown"
    parts = [f"公司:{c}", f"届次:{s}", f"类型:{doc_type}"]
    if job_title and str(job_title).strip():
        parts.append(f"岗位:{job_title.strip()}")
    return "[" + " | ".join(parts) + "]"


def _attach_header(header: str, body: str) -> str:
    body = body.strip()
    if not body:
        return ""
    return f"{header}\n\n{body}"


def _split_faq_pairs(text: str) -> list[str]:
    """Heuristic: split by lines starting with Q/问 and pair until next Q."""
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    q_pat = re.compile(r"^\s*(Q[:：\s]|问[:：\s]|\d+[\.、]\s*问)")

    for line in lines:
        if q_pat.match(line) and current:
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    parts = ["\n".join(b).strip() for b in blocks if "\n".join(b).strip()]
    return parts if parts else [text.strip()]


# 面经：在 faq 规则基础上增加「第 n 题」等题标（行首匹配）
_INTERVIEW_Q_LINE = re.compile(
    r"^\s*("
    r"Q[:：\s]|"
    r"问[:：\s]|"
    r"\d+[\.、]\s*问|"
    r"第\s*\d+\s*题\s*[:：\.、\s]?"
    r")",
    re.IGNORECASE,
)


def _split_interview_qa_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _INTERVIEW_Q_LINE.match(line) and current:
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    parts = ["\n".join(b).strip() for b in blocks if "\n".join(b).strip()]
    return parts if parts else [text.strip()]


# 行首「一、」「十一、」等一级标题（仅用中文数字，避免正文「1. xxx」误判）
_RECRUITMENT_SECTION_START = re.compile(
    r"^([一二三四五六七八九十百零]+)\s*[、.．]",
    re.MULTILINE,
)


def _split_recruitment_primary_sections(text: str) -> list[tuple[str, str]]:
    """按一级标题切分；返回 (heading_path 片段, 节全文)。无匹配时返回空列表。"""
    text = text.strip()
    if not text:
        return []
    matches = list(_RECRUITMENT_SECTION_START.finditer(text))
    if not matches:
        return []
    out: list[tuple[str, str]] = []
    pre = text[: matches[0].start()].strip()
    if pre:
        out.append(("preamble", pre))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end].strip()
        if not section:
            continue
        first_line = section.split("\n", 1)[0].strip()
        label = (first_line[:80] if len(first_line) > 80 else first_line).replace("/", "_")
        out.append((label, section))
    return out


def _slide_window(text: str, window: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= window:
        return [text]
    chunks: list[str] = []
    step = max(window - overlap, 1)
    start = 0
    while start < len(text):
        end = min(start + window, len(text))
        piece = text[start:end]
        if end < len(text):
            tail = piece[-100:]
            br = -1
            for sep in ("\n\n", "\n", "。", "！", "？", "；"):
                j = tail.rfind(sep)
                br = max(br, j)
            if br > 20:
                piece = piece[: len(piece) - len(tail) + br + 1]
        piece = piece.strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start += step
    return chunks


def build_chunks_for_document(
    *,
    raw_text: str,
    doc_type: str,
    company: str | None,
    season: str | None,
    source_url: str,
    job_title: str | None = None,
) -> list[ChunkDraft]:
    cfg = settings()
    window = int(cfg["window_chars"])
    overlap = int(cfg["overlap_chars"])
    faq_max = int(cfg["faq_max_single_chunk_chars"])
    section_max = int(cfg.get("recruitment_section_max_chars") or faq_max)
    header = _header(company, season, doc_type, job_title)

    raw_text = raw_text.strip()
    if not raw_text:
        return []

    drafts: list[ChunkDraft] = []

    if doc_type == "sheet_row":
        if len(raw_text) <= section_max:
            t = _attach_header(header, raw_text)
            return [ChunkDraft(text=t, heading_path="row")] if t else []
        for j, win in enumerate(_slide_window(raw_text, window, overlap)):
            t = _attach_header(header, win)
            if t:
                drafts.append(ChunkDraft(text=t, heading_path=f"row/part{j}"))
        return drafts

    if doc_type == "recruitment_notice":
        parts = _split_recruitment_primary_sections(raw_text)
        if not parts:
            for j, win in enumerate(_slide_window(raw_text, window, overlap)):
                t = _attach_header(header, win)
                if t:
                    drafts.append(ChunkDraft(text=t, heading_path=f"body/part{j}"))
            return drafts
        for label, part in parts:
            if len(part) <= section_max:
                t = _attach_header(header, part)
                if t:
                    drafts.append(ChunkDraft(text=t, heading_path=f"section/{label}"))
            else:
                for j, win in enumerate(_slide_window(part, window, overlap)):
                    t = _attach_header(header, win)
                    if t:
                        drafts.append(
                            ChunkDraft(text=t, heading_path=f"section/{label}/part{j}")
                        )
        return drafts

    if doc_type == "faq":
        parts = _split_faq_pairs(raw_text)
        for idx, part in enumerate(parts):
            if len(part) <= faq_max:
                t = _attach_header(header, part)
                if t:
                    drafts.append(ChunkDraft(text=t, heading_path=f"faq#{idx}"))
            else:
                for j, win in enumerate(_slide_window(part, window, overlap)):
                    t = _attach_header(header, win)
                    if t:
                        drafts.append(
                            ChunkDraft(text=t, heading_path=f"faq#{idx}/part{j}")
                        )
        return drafts

    if doc_type in ("interview_exp", "interview_note"):
        parts = _split_interview_qa_blocks(raw_text)
        for idx, part in enumerate(parts):
            if len(part) <= faq_max:
                t = _attach_header(header, part)
                if t:
                    drafts.append(ChunkDraft(text=t, heading_path=f"qa#{idx}"))
            else:
                for j, win in enumerate(_slide_window(part, window, overlap)):
                    t = _attach_header(header, win)
                    if t:
                        drafts.append(
                            ChunkDraft(text=t, heading_path=f"qa#{idx}/part{j}")
                        )
        return drafts

    for j, win in enumerate(_slide_window(raw_text, window, overlap)):
        t = _attach_header(header, win)
        if t:
            drafts.append(ChunkDraft(text=t, heading_path=f"body/part{j}"))
    return drafts
