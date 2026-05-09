"""增量同步招聘原始数据（txt + xlsx）到 SQLite。

默认目录：original_data/recruitment_rag
完成后请运行：python scripts/build_index.py --incremental（或首次 --full）
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_rag.db import (
    connect,
    delete_documents_by_file_path_cascade,
    init_schema,
    recruitment_txt_unchanged,
    xlsx_file_unchanged,
)
from campus_rag.ingest import count_xlsx_ingest_rows, ingest_plain_text, ingest_xlsx
from campus_rag.paths import ROOT as PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="增量导入 recruitment_rag 下 txt / xlsx")
    parser.add_argument(
        "--dir",
        type=str,
        default="original_data/recruitment_rag",
        help="相对项目根的目录",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出将扫描的文件与预计操作，不写库",
    )
    args = parser.parse_args()
    base = (PROJECT_ROOT / args.dir).resolve()
    if not base.is_dir():
        print(f"目录不存在：{base}")
        return

    txt_paths = sorted(p for p in base.rglob("*.txt") if p.is_file())
    xlsx_paths = sorted(p for p in base.rglob("*.xlsx") if p.is_file())

    if args.dry_run:
        rows = sum(count_xlsx_ingest_rows(p) for p in xlsx_paths)
        print(f"[dry-run] txt: {len(txt_paths)}  xlsx: {len(xlsx_paths)}（约 {rows} 行）")
        for p in txt_paths:
            print(f"  txt  {p.relative_to(PROJECT_ROOT)}")
        for p in xlsx_paths:
            print(f"  xlsx {p.relative_to(PROJECT_ROOT)} ({count_xlsx_ingest_rows(p)} 行)")
        return

    conn = connect()
    init_schema(conn)

    for path in txt_paths:
        abs_str = str(path.resolve())
        body = path.read_text(encoding="utf-8")
        csum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if recruitment_txt_unchanged(conn, abs_str, csum):
            print(f"SKIP txt（未变化）{path.name}")
            continue
        delete_documents_by_file_path_cascade(conn, abs_str)
        doc_id = ingest_plain_text(path)
        print(f"TXT  {path.name} -> {doc_id}")

    for path in xlsx_paths:
        abs_str = str(path.resolve())
        fsum = hashlib.sha256(path.read_bytes()).hexdigest()
        if xlsx_file_unchanged(conn, abs_str, fsum):
            print(f"SKIP xlsx（未变化）{path.name}")
            continue
        delete_documents_by_file_path_cascade(conn, abs_str)
        n = ingest_xlsx(path)
        print(f"XLSX {path.name} -> {n} 条")


if __name__ == "__main__":
    main()
