"""读取 config/crawl.yaml，限速抓取 Boss 与官方页并入库。支持列表页自动提取详情链接。支持浏览器自动化搜索。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_rag.config_loader import crawl_config
from campus_rag.crawl import fetch_boss_list_page
from campus_rag.ingest import ingest_boss_job, ingest_official

# 尝试导入浏览器自动化模块（可选）
try:
    from campus_rag.crawl.boss_auto import fetch_boss_jobs_by_search, BossSearchConfig, fetch_and_process_boss_jobs_by_search
    AUTO_CRAWL_AVAILABLE = True
except ImportError as e:
    AUTO_CRAWL_AVAILABLE = False
    print(f"浏览器自动化模块不可用（需安装DrissionPage）: {e}")


def is_detail_page(url: str) -> bool:
    """判断是否为职位详情页（包含/job_detail/）"""
    return "/job_detail/" in url


def process_boss_urls(urls: List[str], max_total: int) -> None:
    """处理Boss URL列表，自动区分列表页和详情页"""
    processed_count = 0

    for url in urls:
        if processed_count >= max_total:
            print(f"已达到最大处理数量 {max_total}，停止处理")
            break

        if is_detail_page(url):
            # 直接处理详情页
            try:
                doc_id = ingest_boss_job(str(url))
                print(f"Boss详情页 {url} -> {doc_id}")
                processed_count += 1
            except Exception as e:
                print(f"处理详情页失败 {url}: {e}")
        else:
            # 列表页，先提取详情链接
            print(f"处理列表页: {url}")
            try:
                # 计算还能处理多少个职位
                remaining = max_total - processed_count
                detail_urls = fetch_boss_list_page(str(url), max_jobs=remaining)
                print(f"从列表页提取到 {len(detail_urls)} 个职位链接")

                for detail_url in detail_urls:
                    if processed_count >= max_total:
                        break
                    try:
                        doc_id = ingest_boss_job(str(detail_url))
                        print(f"  -> 详情页 {detail_url} -> {doc_id}")
                        processed_count += 1
                    except Exception as e:
                        print(f"  处理详情页失败 {detail_url}: {e}")
            except Exception as e:
                print(f"处理列表页失败 {url}: {e}")


def process_boss_search(cfg: dict, max_total: int) -> Optional[int]:
    """处理Boss搜索配置（浏览器自动化方式）

    Args:
        cfg: 配置字典
        max_total: 最大职位数限制

    Returns:
        处理的职位数量，失败返回None
    """
    if not AUTO_CRAWL_AVAILABLE:
        print("错误: 浏览器自动化模块不可用，请安装 DrissionPage")
        print("执行: pip install DrissionPage")
        return None

    boss_search = cfg.get("boss_search")
    if not boss_search:
        return 0

    keywords = boss_search.get("keywords") or []
    if not keywords:
        print("警告: boss_search.keywords 为空，跳过搜索")
        return 0

    city = boss_search.get("city") or "北京"
    max_pages = int(boss_search.get("max_pages_per_keyword") or 2)
    max_jobs_per_keyword = int(boss_search.get("max_jobs_per_keyword") or 10)

    print(f"使用浏览器自动化搜索")
    print(f"关键词: {keywords}")
    print(f"城市: {city}")
    print(f"每关键词最大页数: {max_pages}")
    print(f"每关键词最大职位数: {max_jobs_per_keyword}")
    print(f"总职位数限制: {max_total}")

    # 创建搜索配置
    search_config = BossSearchConfig(
        keywords=keywords,
        city=city,
        max_pages=max_pages,
        max_jobs_per_keyword=min(max_jobs_per_keyword, max_total)
    )

    try:
        # 搜索并获取职位URL
        job_urls = fetch_boss_jobs_by_search(search_config)
        print(f"搜索到 {len(job_urls)} 个职位")

        # 入库
        processed_count = 0
        for url in job_urls:
            if processed_count >= max_total:
                print(f"已达到最大处理数量 {max_total}")
                break

            try:
                doc_id = ingest_boss_job(str(url))
                print(f"Boss详情页 {url} -> {doc_id}")
                processed_count += 1
            except Exception as e:
                print(f"处理详情页失败 {url}: {e}")

        return processed_count

    except Exception as e:
        print(f"浏览器自动化搜索失败: {e}")
        return None


def main() -> None:
    cfg = crawl_config()
    max_total = int(cfg.get("max_job_pages_per_run", 20))

    # 优先使用浏览器自动化搜索（如果配置了）
    processed_count = 0

    if cfg.get("boss_search") and (cfg.get("boss_search", {}).get("keywords")):
        result = process_boss_search(cfg, max_total)
        if result is not None:
            processed_count = result
    else:
        print("未配置boss_search或关键词为空，使用传统URL方式")

    # 如果未使用搜索方式或搜索获取的职位数不足，使用传统URL方式
    urls = list(cfg.get("boss_job_urls") or [])
    if urls and processed_count < max_total:
        remaining = max_total - processed_count
        print(f"传统URL方式，剩余可处理 {remaining} 个职位")
        process_boss_urls(urls, remaining)

    # 处理官方页面（保持不变）
    for item in cfg.get("official_urls") or []:
        if isinstance(item, dict):
            url = item.get("url")
            if not url:
                continue
            doc_id = ingest_official(
                str(url),
                company=item.get("company"),
                season=item.get("season"),
                doc_type=str(item.get("doc_type") or "official_notice"),
            )
            print(f"Official {url} -> {doc_id}")


if __name__ == "__main__":
    main()
