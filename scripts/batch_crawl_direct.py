#!/usr/bin/env python3
"""批量直接API爬取Boss职位数据，支持大规模数据收集"""

from __future__ import annotations

import sys
import time
import random
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_rag.config_loader import crawl_config
from campus_rag.crawl.boss_auto import fetch_boss_jobs_direct


def batch_crawl_direct():
    """批量直接API爬取主函数"""
    cfg = crawl_config()

    # 获取搜索配置
    boss_search = cfg.get("boss_search")
    if not boss_search:
        print("错误: 配置文件中未找到boss_search配置")
        return

    keywords = boss_search.get("keywords") or []
    if not keywords:
        print("警告: boss_search.keywords 为空，跳过搜索")
        return

    city = boss_search.get("city") or "北京"
    max_pages_per_keyword = int(boss_search.get("max_pages_per_keyword") or 1)
    max_jobs_per_keyword = int(boss_search.get("max_jobs_per_keyword") or 10)

    # 总职位数限制
    max_total = int(cfg.get("max_job_pages_per_run", 30))

    print("=" * 60)
    print("批量直接API爬取Boss职位数据")
    print("=" * 60)
    print(f"关键词: {keywords}")
    print(f"城市: {city}")
    print(f"每关键词最大页数: {max_pages_per_keyword}")
    print(f"每关键词最大职位数: {max_jobs_per_keyword}")
    print(f"总职位数限制: {max_total}")
    print()

    total_docs = 0
    all_doc_ids = []

    for i, keyword in enumerate(keywords):
        print(f"\n处理关键词 {i+1}/{len(keywords)}: '{keyword}'")

        try:
            # 计算当前关键词的最大页数（考虑总限制）
            remaining = max_total - total_docs
            if remaining <= 0:
                print(f"已达到总职位数限制 {max_total}，停止处理")
                break

            # 调整当前关键词的最大页数
            current_max_pages = max_pages_per_keyword
            # 估计每页大约15-30个职位
            estimated_per_page = 15
            max_pages_needed = min(current_max_pages, (remaining + estimated_per_page - 1) // estimated_per_page)

            if max_pages_needed < 1:
                print(f"剩余职位数 {remaining} 不足，跳过此关键词")
                continue

            print(f"  剩余职位数: {remaining}")
            print(f"  使用页数: {max_pages_needed}")

            # 调用直接API函数
            doc_ids = fetch_boss_jobs_direct(
                keyword=keyword,
                city=city,
                max_pages=max_pages_needed,
                season=f"2026春招"  # 可以根据需要调整
            )

            print(f"  成功获取 {len(doc_ids)} 个职位数据")
            total_docs += len(doc_ids)
            all_doc_ids.extend(doc_ids)

            # 关键词间延迟（避免过快请求）
            if i < len(keywords) - 1 and total_docs < max_total:
                delay_seconds = float(cfg.get("delay_seconds", 15.0))
                # 添加一些随机性
                actual_delay = delay_seconds + random.uniform(-3.0, 3.0)
                actual_delay = max(5.0, actual_delay)  # 最少5秒

                print(f"  等待 {actual_delay:.1f} 秒后处理下一个关键词...")
                time.sleep(actual_delay)

        except Exception as e:
            print(f"  处理关键词 '{keyword}' 时出错: {e}")
            import traceback
            traceback.print_exc()

            # 出错后延迟更长时间
            error_delay = 30.0 + random.uniform(0, 10.0)
            print(f"  出错后等待 {error_delay:.1f} 秒...")
            time.sleep(error_delay)
            continue

    print("\n" + "=" * 60)
    print(f"批量爬取完成")
    print(f"总共处理关键词: {len(keywords)} 个")
    print(f"成功获取职位: {total_docs} 个")
    print(f"所有文档ID: {len(all_doc_ids)} 个")

    if all_doc_ids:
        print(f"\n前10个文档ID:")
        for doc_id in all_doc_ids[:10]:
            print(f"  - {doc_id}")

    return all_doc_ids


def main() -> None:
    """主函数"""
    try:
        batch_crawl_direct()
    except KeyboardInterrupt:
        print("\n\n用户中断，停止爬取")
        sys.exit(1)
    except Exception as e:
        print(f"\n爬取过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()