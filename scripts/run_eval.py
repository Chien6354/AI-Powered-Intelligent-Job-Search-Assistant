"""校招助手 Agent 四阶段自动化评测入口。

用法：
    python scripts/run_eval.py                          # 默认题集 + 输出到 data/eval/eval_report.xlsx
    python scripts/run_eval.py --questions path/to/q.json --output path/to/report.xlsx
    python scripts/run_eval.py --skip-judge              # 跳过 LLM 打分（仅计算规则指标，速度快）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(Path.cwd() / ".env", override=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="校招助手 Agent 四阶段评测")
    parser.add_argument(
        "--questions",
        type=str,
        default=str(ROOT / "data" / "eval" / "eval_questions.json"),
        help="评测题集 JSON 路径",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(ROOT / "data" / "eval" / "eval_report.xlsx"),
        help="Excel 报表输出路径",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="跳过 LLM-as-Judge 打分（仅计算阶段 1-3 规则指标）",
    )
    args = parser.parse_args()

    from campus_rag.eval.runner import load_questions, run_all
    from campus_rag.eval.metrics import (
        compute_router_metrics,
        compute_retrieval_metrics,
        compute_rerank_metrics,
        aggregate_router,
        aggregate_retrieval,
        aggregate_rerank,
    )
    from campus_rag.eval.llm_judge import judge_all
    from campus_rag.eval.report import export_excel

    print("=" * 60)
    print("校招助手 Agent 评测")
    print("=" * 60)

    # 1. 加载题集
    questions = load_questions(args.questions)
    print(f"\n加载 {len(questions)} 道评测题")

    # 2. 批量执行 run_turn
    print("\n--- 阶段 0：执行 Agent ---")
    rows = run_all(questions, verbose=True)

    # 3. 计算阶段 1-3 规则指标
    print("\n--- 阶段 1：路由与护栏指标 ---")
    router_metrics = [compute_router_metrics(r) for r in rows]
    router_agg = aggregate_router(router_metrics)
    print(f"  意图准确率:     {router_agg.get('intent_accuracy', '?')}")
    print(f"  拒绝精确率:     {router_agg.get('reject_precision', '?')}")
    print(f"  拒绝召回率:     {router_agg.get('reject_recall', '?')}")
    print(f"  公司过滤准确率: {router_agg.get('company_filter_accuracy', '?')}")

    print("\n--- 阶段 2：检索质量指标 ---")
    retrieval_metrics = [compute_retrieval_metrics(r) for r in rows]
    retrieval_agg = aggregate_retrieval(retrieval_metrics)
    print(f"  召回命中率:     {retrieval_agg.get('hit_rate', '?')}")
    print(f"  Top-1 相似度均值: {retrieval_agg.get('avg_top1_similarity', '?')}")
    print(f"  弃权原因分布:   {retrieval_agg.get('abstain_reason_distribution', {})}")
    print(f"  元数据放松率:   {retrieval_agg.get('metadata_relaxation_rate', '?')}")
    print(f"  合理弃权率:     {retrieval_agg.get('correct_abstain_rate', '?')}")

    print("\n--- 阶段 3：重排质量指标 ---")
    rerank_metrics = [compute_rerank_metrics(r) for r in rows]
    rerank_agg = aggregate_rerank(rerank_metrics)
    print(f"  Top-1 重排分均值:   {rerank_agg.get('avg_top1_rerank_score', '?')}")
    print(f"  重排后过滤损失率: {rerank_agg.get('avg_filter_loss_rate', '?')}")

    # 4. LLM-as-Judge 打分
    if args.skip_judge:
        print("\n--- 阶段 4：LLM 打分 [已跳过] ---")
        judge_scores = [
            {"relevance": None, "faithfulness": None, "completeness": None,
             "conciseness": None, "actionability": None, "comment": "", "judge_error": "skipped"}
            for _ in rows
        ]
    else:
        print("\n--- 阶段 4：LLM-as-Judge 生成质量打分 ---")
        judge_scores = judge_all(rows, verbose=True)

        scored = [s for s in judge_scores if s.get("judge_error") is None]
        if scored:
            dims = ("relevance", "faithfulness", "completeness", "conciseness", "actionability")
            for d in dims:
                vals = [s[d] for s in scored if s[d] is not None]
                avg = sum(vals) / len(vals) if vals else 0
                print(f"  {d:15s} 均分: {avg:.2f}")

    # 5. 输出 Excel
    print("\n--- 输出 Excel 报表 ---")
    out_path = export_excel(rows, router_metrics, retrieval_metrics, rerank_metrics, judge_scores, args.output)
    print(f"  报表已写入: {out_path}")

    print("\n" + "=" * 60)
    print("评测完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
