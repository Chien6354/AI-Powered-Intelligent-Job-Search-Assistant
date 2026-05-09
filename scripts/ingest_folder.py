"""入库 data/campus_kb 下 .md 与 .pdf。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_rag.paths import ROOT as PROJECT_ROOT
from campus_rag.ingest import ingest_markdown, ingest_pdf


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=str, default="data/campus_kb")
    args = parser.parse_args()
    base = PROJECT_ROOT / args.dir
    if not base.is_dir():
        print(f"目录不存在：{base}")
        return
    for path in sorted(base.rglob("*")):
        if path.suffix.lower() == ".md":
            doc_id = ingest_markdown(path)
            print(f"MD  {path.name} -> {doc_id}")
        elif path.suffix.lower() == ".pdf":
            doc_id = ingest_pdf(path)
            print(f"PDF {path.name} -> {doc_id}")


if __name__ == "__main__":
    main()
