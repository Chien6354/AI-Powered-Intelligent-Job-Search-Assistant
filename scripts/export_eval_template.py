"""导出评测 Excel 空模板。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from campus_rag.paths import ROOT as PROJECT_ROOT


def main() -> None:
    cols = [
        "round",
        "question_id",
        "question",
        "category",
        "expected_behavior",
        "model_output",
        "pass",
        "failure_tag",
        "notes",
    ]
    df = pd.DataFrame(columns=cols)
    out = PROJECT_ROOT / "data" / "eval" / "eval_template.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out, index=False)
    print(f"写入 {out}")


if __name__ == "__main__":
    main()
