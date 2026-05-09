"""入库 original_data 下 .txt（招聘公告结构分块）与 .xlsx（一行一条）。

已推荐使用增量脚本：python scripts/sync_recruitment_rag.py（招聘专用）。
本脚本仍保留为无增量、一次性全目录导入。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_rag.paths import ROOT as PROJECT_ROOT
from campus_rag.ingest import (
    count_xlsx_ingest_rows,
    ingest_plain_text,
    ingest_xlsx,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="离线导入 original_data 中的 txt / xlsx")
    parser.add_argument(
        "--dir",
        type=str,
        default="original_data",
        help="相对项目根的目录（默认 original_data）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计将处理的文件数与 xlsx 行数，不写库",
    )
    args = parser.parse_args()
    base = PROJECT_ROOT / args.dir
    if not base.is_dir():
        print(f"目录不存在：{base}")
        return

    txt_paths = sorted(p for p in base.rglob("*.txt") if p.is_file())
    xlsx_paths = sorted(p for p in base.rglob("*.xlsx") if p.is_file())

    if args.dry_run:
        xlsx_rows = sum(count_xlsx_ingest_rows(p) for p in xlsx_paths)
        print(f"[dry-run] txt 文件: {len(txt_paths)}")
        print(f"[dry-run] xlsx 文件: {len(xlsx_paths)}，可导入非空行合计: {xlsx_rows}")
        for p in txt_paths:
            print(f"  txt  {p.relative_to(PROJECT_ROOT)}")
        for p in xlsx_paths:
            n = count_xlsx_ingest_rows(p)
            print(f"  xlsx {p.relative_to(PROJECT_ROOT)} ({n} 行)")
        return

    for path in txt_paths:
        doc_id = ingest_plain_text(path)
        print(f"TXT  {path.name} -> {doc_id}")

    for path in xlsx_paths:
        n = ingest_xlsx(path)
        print(f"XLSX {path.name} -> {n} 条")


if __name__ == "__main__":
    main()
