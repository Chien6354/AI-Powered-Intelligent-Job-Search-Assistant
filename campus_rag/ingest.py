from __future__ import annotations

import hashlib
import urllib.parse
from pathlib import Path

import pandas as pd
import yaml

from campus_rag.chunking import build_chunks_for_document
from campus_rag.db import DocumentInput, connect, init_schema, insert_chunk, insert_document

_POSITION_TITLE_COLS = frozenset(
    {
        "职位",
        "岗位",
        "招聘岗位",
        "title",
        "job_title",
        "职位名称",
        "岗位名称",
    }
)
_COMPANY_COLS = frozenset(
    {"公司名称", "公司", "企业名称", "company", "employer", "单位"}
)
_SEASON_COLS = frozenset({"届次", "招聘季", "season"})


def _xlsx_cell_str(val: object) -> str | None:
    if pd.isna(val):
        return None
    if isinstance(val, bool):
        return "是" if val else "否"
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    s = str(val).strip()
    return s or None


def _xlsx_serialize_row(row: pd.Series) -> str:
    lines: list[str] = []
    for col in row.index:
        col_name = str(col).strip() if col is not None else ""
        if not col_name:
            col_name = "(列)"
        v = row[col]
        s = _xlsx_cell_str(v)
        if s is None:
            continue
        lines.append(f"{col_name}: {s}")
    return "\n".join(lines)


def _xlsx_row_job_title(row: pd.Series) -> str | None:
    for key in _POSITION_TITLE_COLS:
        if key in row.index:
            s = _xlsx_cell_str(row[key])
            if s:
                return s
    return None


def _xlsx_row_title(row: pd.Series, path_stem: str, excel_row: int) -> str:
    jt = _xlsx_row_job_title(row)
    if jt:
        return jt
    for key in _COMPANY_COLS:
        if key in row.index:
            s = _xlsx_cell_str(row[key])
            if s:
                return f"{s} | {path_stem} 第{excel_row}行"
    return f"{path_stem} 第{excel_row}行"


def _xlsx_row_company(row: pd.Series) -> str | None:
    for key in _COMPANY_COLS:
        if key in row.index:
            s = _xlsx_cell_str(row[key])
            if s:
                return s
    return None


def _xlsx_row_season(row: pd.Series) -> str | None:
    for key in _SEASON_COLS:
        if key in row.index:
            s = _xlsx_cell_str(row[key])
            if s:
                return s
    return None


def _parse_frontmatter_md(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return meta, body


def md_body_checksum(path: Path) -> str:
    """与 ingest_markdown 一致：仅正文（去掉 YAML 头）的 sha256。"""
    text = path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter_md(text)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def ingest_markdown(path: Path, default_meta: dict | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter_md(text)
    if default_meta:
        for key, val in default_meta.items():
            if key not in meta or meta.get(key) is None:
                meta[key] = val
    title = meta.get("title") or path.stem
    company = meta.get("company")
    season = meta.get("season") or "unknown"
    jt = meta.get("job_title")
    job_title = str(jt).strip() if jt else None
    doc_type = meta.get("doc_type") or "general"
    abs_path = path.resolve()
    source_url = meta.get("source_url") or abs_path.as_uri()
    checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()

    conn = connect()
    init_schema(conn)
    doc = DocumentInput(
        title=str(title),
        company=str(company) if company else None,
        season=str(season) if season else None,
        doc_type=str(doc_type),
        source_url=str(source_url),
        raw_text=body,
        job_title=job_title,
        file_path=str(abs_path),
        mime="text/markdown",
        text_extract_method="paste",
        quality_flags=[],
        checksum=checksum,
    )
    doc_id = insert_document(conn, doc)
    drafts = build_chunks_for_document(
        raw_text=body,
        doc_type=str(doc_type),
        company=doc.company,
        season=doc.season,
        source_url=doc.source_url,
        job_title=doc.job_title,
    )
    for i, d in enumerate(drafts):
        insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_index=i,
            text=d.text,
            company=doc.company,
            season=doc.season,
            source_url=doc.source_url,
            job_title=doc.job_title,
            heading_path=d.heading_path,
        )
    return doc_id


def ingest_pdf(path: Path, meta: dict | None = None) -> str:
    from campus_rag.pdf_text import extract_pdf_text

    meta = meta or {}
    body, flags = extract_pdf_text(path)
    title = meta.get("title") or path.stem
    company = meta.get("company")
    season = meta.get("season") or "unknown"
    jt = meta.get("job_title")
    job_title = str(jt).strip() if jt else None
    doc_type = meta.get("doc_type") or "interview_note"
    source_url = meta.get("source_url") or path.resolve().as_uri()
    abs_path = path.resolve()
    checksum = hashlib.sha256(abs_path.read_bytes()).hexdigest()

    conn = connect()
    init_schema(conn)
    doc = DocumentInput(
        title=str(title),
        company=str(company) if company else None,
        season=str(season) if season else None,
        doc_type=str(doc_type),
        source_url=str(source_url),
        raw_text=body,
        job_title=job_title,
        file_path=str(abs_path),
        mime="application/pdf",
        text_extract_method="pdf_text",
        quality_flags=flags,
        checksum=checksum,
    )
    doc_id = insert_document(conn, doc)
    drafts = build_chunks_for_document(
        raw_text=body,
        doc_type=str(doc_type),
        company=doc.company,
        season=doc.season,
        source_url=doc.source_url,
        job_title=doc.job_title,
    )
    for i, d in enumerate(drafts):
        insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_index=i,
            text=d.text,
            company=doc.company,
            season=doc.season,
            source_url=doc.source_url,
            job_title=doc.job_title,
            heading_path=d.heading_path,
        )
    return doc_id


def ingest_boss_job(url: str, season: str | None = None) -> str:
    from campus_rag.crawl.boss import fetch_boss_job_page

    job = fetch_boss_job_page(url)
    body = job.raw_text
    title = job.title
    company = job.company
    season_val = season or "unknown"
    conn = connect()
    init_schema(conn)
    doc = DocumentInput(
        title=title,
        company=company,
        season=season_val,
        doc_type="jd",
        source_url=url,
        raw_text=body,
        file_path=None,
        mime="text/html",
        text_extract_method="crawl",
        quality_flags=job.quality_flags,
        checksum=hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest(),
    )
    doc_id = insert_document(conn, doc)
    drafts = build_chunks_for_document(
        raw_text=body,
        doc_type="jd",
        company=doc.company,
        season=doc.season,
        source_url=doc.source_url,
        job_title=doc.job_title,
    )
    for i, d in enumerate(drafts):
        insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_index=i,
            text=d.text,
            company=doc.company,
            season=doc.season,
            source_url=doc.source_url,
            job_title=doc.job_title,
            heading_path=d.heading_path,
        )
    return doc_id


def ingest_boss_job_data(
    title: str,
    company: str | None,
    raw_text: str,
    source_url: str,
    quality_flags: list[str],
    season: str | None = None
) -> str:
    """直接使用已提取的数据入库Boss职位

    Args:
        title: 职位标题
        company: 公司名称
        raw_text: 原始文本
        source_url: 源URL
        quality_flags: 质量标志
        season: 招聘季

    Returns:
        文档ID
    """
    season_val = season or "unknown"
    conn = connect()
    init_schema(conn)
    doc = DocumentInput(
        title=title,
        company=company,
        season=season_val,
        doc_type="jd",
        source_url=source_url,
        raw_text=raw_text,
        file_path=None,
        mime="text/html",
        text_extract_method="crawl",
        quality_flags=quality_flags,
        checksum=hashlib.sha256(raw_text.encode("utf-8", errors="ignore")).hexdigest(),
    )
    doc_id = insert_document(conn, doc)
    drafts = build_chunks_for_document(
        raw_text=raw_text,
        doc_type="jd",
        company=doc.company,
        season=doc.season,
        source_url=doc.source_url,
        job_title=doc.job_title,
    )
    for i, d in enumerate(drafts):
        insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_index=i,
            text=d.text,
            company=doc.company,
            season=doc.season,
            source_url=doc.source_url,
            job_title=doc.job_title,
            heading_path=d.heading_path,
        )
    return doc_id


def ingest_plain_text(path: Path) -> str:
    body = path.read_text(encoding="utf-8")
    lines = body.splitlines()
    title = next((ln.strip() for ln in lines if ln.strip()), path.stem)
    if len(title) > 500:
        title = title[:500]
    abs_path = path.resolve()
    source_url = abs_path.as_uri()
    checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()

    company_name = path.stem.strip() or None

    conn = connect()
    init_schema(conn)
    doc = DocumentInput(
        title=title,
        company=company_name,
        season="unknown",
        doc_type="recruitment_notice",
        source_url=source_url,
        raw_text=body,
        job_title=None,
        file_path=str(abs_path),
        mime="text/plain",
        text_extract_method="utf8_file",
        quality_flags=[],
        checksum=checksum,
        file_checksum=None,
    )
    doc_id = insert_document(conn, doc)
    drafts = build_chunks_for_document(
        raw_text=body,
        doc_type="recruitment_notice",
        company=doc.company,
        season=doc.season,
        source_url=doc.source_url,
        job_title=doc.job_title,
    )
    for i, d in enumerate(drafts):
        insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_index=i,
            text=d.text,
            company=doc.company,
            season=doc.season,
            source_url=doc.source_url,
            job_title=doc.job_title,
            heading_path=d.heading_path,
        )
    return doc_id


def ingest_interview_plain_text(
    path: Path,
    *,
    job_title: str | None = None,
    doc_type: str = "interview_exp",
) -> str:
    body = path.read_text(encoding="utf-8")
    lines = body.splitlines()
    title = next((ln.strip() for ln in lines if ln.strip()), path.stem)
    if len(title) > 500:
        title = title[:500]
    abs_path = path.resolve()
    source_url = abs_path.as_uri()
    checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()

    conn = connect()
    init_schema(conn)
    doc = DocumentInput(
        title=title,
        company=None,
        season="unknown",
        doc_type=doc_type,
        source_url=source_url,
        raw_text=body,
        job_title=job_title,
        file_path=str(abs_path),
        mime="text/plain",
        text_extract_method="utf8_file",
        quality_flags=[],
        checksum=checksum,
        file_checksum=None,
    )
    doc_id = insert_document(conn, doc)
    drafts = build_chunks_for_document(
        raw_text=body,
        doc_type=doc_type,
        company=doc.company,
        season=doc.season,
        source_url=doc.source_url,
        job_title=doc.job_title,
    )
    for i, d in enumerate(drafts):
        insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_index=i,
            text=d.text,
            company=doc.company,
            season=doc.season,
            source_url=doc.source_url,
            job_title=doc.job_title,
            heading_path=d.heading_path,
        )
    return doc_id


def ingest_xlsx(path: Path) -> int:
    """每个非空行写入一条 document + 对应 chunk(s)。返回写入行数。"""
    abs_path = path.resolve()
    file_checksum = hashlib.sha256(abs_path.read_bytes()).hexdigest()
    base_uri = abs_path.as_uri()
    xls = pd.ExcelFile(abs_path)
    conn = connect()
    init_schema(conn)
    count = 0
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name, header=0)
        if df.empty:
            continue
        df.columns = [str(c).strip() for c in df.columns]
        for pos, (_, row) in enumerate(df.iterrows(), start=1):
            body = _xlsx_serialize_row(row)
            if not body.strip():
                continue
            excel_row = pos + 1
            frag = urllib.parse.urlencode(
                {"sheet": sheet_name, "row": str(excel_row)},
                encoding="utf-8",
            )
            source_url = f"{base_uri}#{frag}"
            checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
            title = _xlsx_row_title(row, path.stem, excel_row)
            if len(title) > 500:
                title = title[:500]
            company = _xlsx_row_company(row)
            row_job_title = _xlsx_row_job_title(row)
            season_val = _xlsx_row_season(row) or "unknown"
            doc = DocumentInput(
                title=title,
                company=company,
                season=season_val,
                doc_type="sheet_row",
                source_url=source_url,
                raw_text=body,
                job_title=row_job_title,
                file_path=str(abs_path),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                text_extract_method="xlsx_row",
                quality_flags=[],
                checksum=checksum,
                file_checksum=file_checksum,
            )
            doc_id = insert_document(conn, doc)
            drafts = build_chunks_for_document(
                raw_text=body,
                doc_type="sheet_row",
                company=doc.company,
                season=doc.season,
                source_url=doc.source_url,
                job_title=doc.job_title,
            )
            for i, d in enumerate(drafts):
                insert_chunk(
                    conn,
                    doc_id=doc_id,
                    chunk_index=i,
                    text=d.text,
                    company=doc.company,
                    season=doc.season,
                    source_url=doc.source_url,
                    job_title=doc.job_title,
                    heading_path=d.heading_path,
                )
            count += 1
    return count


def count_xlsx_ingest_rows(path: Path) -> int:
    """与 ingest_xlsx 相同的非空行计数（不写库）。"""
    xls = pd.ExcelFile(path.resolve())
    n = 0
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name, header=0)
        if df.empty:
            continue
        df.columns = [str(c).strip() for c in df.columns]
        for _, row in df.iterrows():
            if _xlsx_serialize_row(row).strip():
                n += 1
    return n


def ingest_official(url: str, company: str | None, season: str | None, doc_type: str) -> str:
    from campus_rag.crawl.generic_html import fetch_official_page

    page = fetch_official_page(url)
    body = page.text
    conn = connect()
    init_schema(conn)
    doc = DocumentInput(
        title=page.title,
        company=company,
        season=season or "unknown",
        doc_type=doc_type,
        source_url=url,
        raw_text=body,
        job_title=None,
        file_path=None,
        mime="text/html",
        text_extract_method="crawl",
        quality_flags=page.quality_flags,
        checksum=hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest(),
    )
    doc_id = insert_document(conn, doc)
    drafts = build_chunks_for_document(
        raw_text=body,
        doc_type=doc_type,
        company=doc.company,
        season=doc.season,
        source_url=doc.source_url,
        job_title=doc.job_title,
    )
    for i, d in enumerate(drafts):
        insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_index=i,
            text=d.text,
            company=doc.company,
            season=doc.season,
            source_url=doc.source_url,
            job_title=doc.job_title,
            heading_path=d.heading_path,
        )
    return doc_id
