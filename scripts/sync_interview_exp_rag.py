"""增量同步面经原始数据（md / txt / pdf）到 SQLite。

默认目录：original_data/interview_exp_rag
元数据：仅强调岗位 job_title（company 为空，season=unknown）；doc_type 为 interview_exp 或 interview_note。
侧车：与文件同名的 <stem>.meta.yaml / .meta.yml，可写 job_title、doc_type、title。
完成后请运行：python scripts/build_index.py --incremental（或首次 --full）
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_rag.db import (
    connect,
    delete_documents_by_file_path_cascade,
    init_schema,
    recruitment_txt_unchanged,
)
from campus_rag.ingest import (
    ingest_interview_plain_text,
    ingest_markdown,
    ingest_pdf,
    md_body_checksum,
)
from campus_rag.paths import ROOT as PROJECT_ROOT


def _load_sidecar(path: Path) -> dict:
    for suf in (".meta.yaml", ".meta.yml"):
        p = path.parent / f"{path.stem}{suf}"
        if p.is_file():
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    return {}


def _resolve_job_title(path: Path, sidecar: dict, base: Path) -> str | None:
    jt = sidecar.get("job_title")
    if jt is not None and str(jt).strip():
        return str(jt).strip()
    abs_base = base.resolve()
    parent = path.parent.resolve()
    if parent != abs_base:
        name = parent.name.strip()
        generic = frozenset({"archive", "misc", "temp", "tmp"})
        if name and name.lower() not in generic:
            return name
    stem = path.stem.strip()
    return stem or None


def _resolve_doc_type(sidecar: dict) -> str:
    dt = str(sidecar.get("doc_type") or "interview_exp").strip().lower()
    if dt in ("interview_exp", "interview_note"):
        return dt
    return "interview_exp"


def _sync_file(conn, path: Path, base: Path) -> None:
    abs_str = str(path.resolve())
    sidecar = _load_sidecar(path)
    job_title = _resolve_job_title(path, sidecar, base)
    doc_type = _resolve_doc_type(sidecar)
    title_override = sidecar.get("title")

    suf = path.suffix.lower()
    if suf == ".md":
        csum = md_body_checksum(path)
        if recruitment_txt_unchanged(conn, abs_str, csum):
            print(f"SKIP md（未变化）{path.name}")
            return
        delete_documents_by_file_path_cascade(conn, abs_str)
        default_meta: dict = {
            "doc_type": doc_type,
            "season": "unknown",
            "company": None,
            "job_title": job_title,
        }
        if title_override:
            default_meta["title"] = title_override
        doc_id = ingest_markdown(path, default_meta=default_meta)
        print(f"MD   {path.name} -> {doc_id}")
        return

    if suf == ".txt":
        body = path.read_text(encoding="utf-8")
        csum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if recruitment_txt_unchanged(conn, abs_str, csum):
            print(f"SKIP txt（未变化）{path.name}")
            return
        delete_documents_by_file_path_cascade(conn, abs_str)
        doc_id = ingest_interview_plain_text(path, job_title=job_title, doc_type=doc_type)
        print(f"TXT  {path.name} -> {doc_id}")
        return

    if suf == ".pdf":
        fsum = hashlib.sha256(path.read_bytes()).hexdigest()
        if recruitment_txt_unchanged(conn, abs_str, fsum):
            print(f"SKIP pdf（未变化）{path.name}")
            return
        delete_documents_by_file_path_cascade(conn, abs_str)
        meta = {
            "job_title": job_title,
            "doc_type": doc_type,
            "season": "unknown",
            "company": None,
            "title": (str(title_override).strip() if title_override else None)
            or path.stem,
        }
        doc_id = ingest_pdf(path, meta=meta)
        print(f"PDF  {path.name} -> {doc_id}")
        return

    print(f"忽略（扩展名）{path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="增量导入 interview_exp_rag 下 md / txt / pdf")
    parser.add_argument(
        "--dir",
        type=str,
        default="original_data/interview_exp_rag",
        help="相对项目根的目录",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出将处理的文件，不写库",
    )
    args = parser.parse_args()
    base = (PROJECT_ROOT / args.dir).resolve()
    if not base.is_dir():
        print(f"目录不存在：{base}")
        return

    paths: list[Path] = []
    for pattern in ("*.md", "*.txt", "*.pdf"):
        paths.extend(sorted(p for p in base.rglob(pattern) if p.is_file()))
    paths.sort(key=lambda p: str(p).lower())

    if args.dry_run:
        print(f"[dry-run] 将处理 {len(paths)} 个文件（{base.relative_to(PROJECT_ROOT)}）")
        for p in paths:
            print(f"  {p.relative_to(PROJECT_ROOT)}")
        return

    conn = connect()
    init_schema(conn)
    for path in paths:
        _sync_file(conn, path, base)


if __name__ == "__main__":
    main()
