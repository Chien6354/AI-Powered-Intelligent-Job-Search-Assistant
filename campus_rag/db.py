from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from campus_rag.config_loader import settings


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path() -> Path:
    cfg = settings()
    p = Path(cfg["sqlite_path"])
    if not p.is_absolute():
        from campus_rag.paths import ROOT

        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_job_title_columns(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(documents)")
    if "job_title" not in {row[1] for row in cur.fetchall()}:
        conn.execute("ALTER TABLE documents ADD COLUMN job_title TEXT")
    cur = conn.execute("PRAGMA table_info(chunks)")
    if "job_title" not in {row[1] for row in cur.fetchall()}:
        conn.execute("ALTER TABLE chunks ADD COLUMN job_title TEXT")
    conn.commit()


def _ensure_file_checksum_column(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(documents)")
    if "file_checksum" not in {row[1] for row in cur.fetchall()}:
        conn.execute("ALTER TABLE documents ADD COLUMN file_checksum TEXT")
    conn.commit()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            season TEXT,
            job_title TEXT,
            doc_type TEXT,
            source_url TEXT,
            captured_at TEXT,
            file_path TEXT,
            mime TEXT,
            text_extract_method TEXT,
            quality_flags TEXT,
            raw_text TEXT,
            checksum TEXT
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            char_len INTEGER NOT NULL,
            company TEXT,
            season TEXT,
            job_title TEXT,
            source_url TEXT,
            heading_path TEXT,
            FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_company ON chunks(company);
        CREATE INDEX IF NOT EXISTS idx_chunks_season ON chunks(season);

        CREATE TABLE IF NOT EXISTS chunk_embeddings (
            chunk_id TEXT PRIMARY KEY,
            dim INTEGER NOT NULL,
            vec BLOB NOT NULL,
            FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
        );
        """
    )
    _ensure_job_title_columns(conn)
    _ensure_file_checksum_column(conn)
    conn.commit()


def delete_all_chunk_embeddings(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM chunk_embeddings")
    conn.commit()


def escape_sql_like_literal(s: str) -> str:
    """转义 LIKE 通配符与反斜杠，配合 ESCAPE '\\\\' 使用。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def replace_chunk_embeddings_batch(
    conn: sqlite3.Connection,
    rows: list[tuple[str, int, bytes]],
) -> None:
    conn.execute("BEGIN")
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, dim, vec) VALUES (?,?,?)",
            rows,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fetch_chunks_with_embeddings(
    conn: sqlite3.Connection,
    filter_company: str | None,
    filter_season: str | None,
    filter_doc_types: list[str] | None = None,
    *,
    filter_company_like: str | None = None,
) -> list[sqlite3.Row]:
    """按 company/season 过滤；filter_company_like 优先于 filter_company（子串 LIKE，可命中「百度在线」等）。"""
    fc = filter_company if filter_company else None
    fcl = filter_company_like.strip() if filter_company_like and filter_company_like.strip() else None
    fs = filter_season if filter_season else None

    if fcl:
        like_pat = f"%{escape_sql_like_literal(fcl)}%"
        company_sql = "c.company LIKE ? ESCAPE '\\'"
        company_params: tuple[Any, ...] = (like_pat,)
    elif fc:
        company_sql = "IFNULL(c.company, '') = ?"
        company_params = (fc,)
    else:
        company_sql = "1=1"
        company_params = ()

    if filter_doc_types:
        placeholders = ",".join("?" * len(filter_doc_types))
        sql = f"""
        SELECT c.chunk_id, c.text, c.source_url, c.company, c.season, c.job_title, e.vec, e.dim
        FROM chunks c
        INNER JOIN documents d ON c.doc_id = d.doc_id
        INNER JOIN chunk_embeddings e ON c.chunk_id = e.chunk_id
        WHERE {company_sql}
          AND (? IS NULL OR IFNULL(c.season, '') = ?)
          AND d.doc_type IN ({placeholders})
        """
        cur = conn.execute(sql, (*company_params, fs, fs, *filter_doc_types))
    else:
        sql = f"""
        SELECT c.chunk_id, c.text, c.source_url, c.company, c.season, c.job_title, e.vec, e.dim
        FROM chunks c
        INNER JOIN chunk_embeddings e ON c.chunk_id = e.chunk_id
        WHERE {company_sql}
          AND (? IS NULL OR IFNULL(c.season, '') = ?)
        """
        cur = conn.execute(sql, (*company_params, fs, fs))
    return cur.fetchall()


@dataclass
class DocumentInput:
    title: str
    company: str | None
    season: str | None
    doc_type: str
    source_url: str
    raw_text: str
    job_title: str | None = None
    file_path: str | None = None
    mime: str | None = None
    text_extract_method: str = "paste"
    quality_flags: list[str] | None = None
    checksum: str | None = None
    file_checksum: str | None = None


def insert_document(conn: sqlite3.Connection, doc: DocumentInput) -> str:
    doc_id = str(uuid.uuid4())
    flags = json.dumps(doc.quality_flags or [], ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO documents (
            doc_id, title, company, season, job_title, doc_type, source_url, captured_at,
            file_path, mime, text_extract_method, quality_flags, raw_text, checksum, file_checksum
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            doc_id,
            doc.title,
            doc.company,
            doc.season,
            doc.job_title,
            doc.doc_type,
            doc.source_url,
            _utc_now_iso(),
            doc.file_path,
            doc.mime,
            doc.text_extract_method,
            flags,
            doc.raw_text,
            doc.checksum,
            doc.file_checksum,
        ),
    )
    conn.commit()
    return doc_id


def insert_chunk(
    conn: sqlite3.Connection,
    *,
    doc_id: str,
    chunk_index: int,
    text: str,
    company: str | None,
    season: str | None,
    source_url: str,
    job_title: str | None = None,
    heading_path: str | None = None,
) -> str:
    chunk_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO chunks (
            chunk_id, doc_id, chunk_index, text, char_len,
            company, season, job_title, source_url, heading_path
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            chunk_id,
            doc_id,
            chunk_index,
            text,
            len(text),
            company,
            season,
            job_title,
            source_url,
            heading_path,
        ),
    )
    conn.commit()
    return chunk_id


def iter_all_chunks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM chunks ORDER BY doc_id, chunk_index")
    return cur.fetchall()


def iter_chunks_with_doc_type(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """chunks 行 + doc_type（供建索引写入 Chroma metadata）。"""
    cur = conn.execute(
        """
        SELECT c.*, d.doc_type AS doc_type
        FROM chunks c
        INNER JOIN documents d ON c.doc_id = d.doc_id
        ORDER BY c.doc_id, c.chunk_index
        """
    )
    return cur.fetchall()


def iter_chunks_missing_embeddings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """尚未写入 chunk_embeddings 的 chunk + doc_type。"""
    cur = conn.execute(
        """
        SELECT c.*, d.doc_type AS doc_type
        FROM chunks c
        INNER JOIN documents d ON c.doc_id = d.doc_id
        LEFT JOIN chunk_embeddings e ON c.chunk_id = e.chunk_id
        WHERE e.chunk_id IS NULL
        ORDER BY c.doc_id, c.chunk_index
        """
    )
    return cur.fetchall()


def get_chunk_rows(conn: sqlite3.Connection, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    cur = conn.execute(
        f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})",
        chunk_ids,
    )
    return {row["chunk_id"]: dict(row) for row in cur.fetchall()}


def delete_embeddings_for_chunk_ids(
    conn: sqlite3.Connection, chunk_ids: list[str]
) -> None:
    if not chunk_ids:
        return
    step = 400
    for i in range(0, len(chunk_ids), step):
        batch = chunk_ids[i : i + step]
        placeholders = ",".join("?" * len(batch))
        conn.execute(
            f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({placeholders})",
            batch,
        )
    conn.commit()


def delete_document_cascade(conn: sqlite3.Connection, doc_id: str) -> None:
    cur = conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,))
    cids = [str(r[0]) for r in cur.fetchall()]
    delete_embeddings_for_chunk_ids(conn, cids)
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
    conn.commit()


def list_doc_ids_by_file_path(conn: sqlite3.Connection, file_path: str) -> list[str]:
    cur = conn.execute(
        "SELECT doc_id FROM documents WHERE file_path = ? ORDER BY captured_at",
        (file_path,),
    )
    return [str(r[0]) for r in cur.fetchall()]


def delete_documents_by_file_path_cascade(conn: sqlite3.Connection, file_path: str) -> None:
    doc_ids = list(list_doc_ids_by_file_path(conn, file_path))
    for doc_id in doc_ids:
        delete_document_cascade(conn, doc_id)


def recruitment_txt_unchanged(
    conn: sqlite3.Connection, file_path: str, content_checksum: str
) -> bool:
    ids = list_doc_ids_by_file_path(conn, file_path)
    if len(ids) != 1:
        return False
    cur = conn.execute(
        "SELECT checksum FROM documents WHERE doc_id = ?",
        (ids[0],),
    )
    row = cur.fetchone()
    return row is not None and (row[0] or "") == content_checksum


def xlsx_file_unchanged(
    conn: sqlite3.Connection, file_path: str, file_checksum: str
) -> bool:
    cur = conn.execute(
        """
        SELECT 1 FROM documents
        WHERE file_path = ? AND IFNULL(file_checksum, '') = ?
        LIMIT 1
        """,
        (file_path, file_checksum),
    )
    return cur.fetchone() is not None
