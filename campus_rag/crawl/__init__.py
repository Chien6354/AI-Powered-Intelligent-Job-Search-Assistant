from campus_rag.crawl.boss import fetch_boss_job_page, fetch_boss_list_page
from campus_rag.crawl.generic_html import fetch_official_page

# 浏览器自动化模块（可选）
try:
    from campus_rag.crawl.boss_auto import (
        fetch_boss_jobs_by_search,
        BossSearchConfig,
        search_boss_jobs,
    )
    __all__ = [
        "fetch_boss_job_page",
        "fetch_boss_list_page",
        "fetch_official_page",
        "fetch_boss_jobs_by_search",
        "BossSearchConfig",
        "search_boss_jobs",
    ]
except ImportError:
    __all__ = [
        "fetch_boss_job_page",
        "fetch_boss_list_page",
        "fetch_official_page",
    ]
