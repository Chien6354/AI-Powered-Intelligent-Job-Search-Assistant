from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from campus_rag.config_loader import crawl_config, ROOT


def load_boss_cookies() -> dict[str, str]:
    """从boss_cookies.json加载cookies"""
    cookies_file = ROOT / "data" / "boss_cookies.json"
    if not cookies_file.exists():
        return {}

    try:
        with open(cookies_file, 'r', encoding='utf-8') as f:
            cookies_data = json.load(f)

        cookies_dict = {}
        for item in cookies_data:
            if isinstance(item, dict) and 'name' in item and 'value' in item:
                cookies_dict[item['name']] = item['value']
        return cookies_dict
    except Exception as e:
        print(f"加载cookies失败: {e}")
        return {}


def build_headers() -> dict[str, str]:
    cfg = crawl_config()
    contact = os.getenv("CRAWL_CONTACT_EMAIL", "unknown")
    ua_tpl = cfg.get("user_agent") or "CampusRAGBot/0.1 (+{contact})"
    ua = ua_tpl.replace("{contact}", contact)

    # 使用更真实的浏览器User-Agent
    real_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    return {
        "User-Agent": real_ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def sleep_delay() -> None:
    cfg = crawl_config()
    time.sleep(float(cfg.get("delay_seconds", 2.0)))


def get_with_backoff(url: str, use_cookies: bool = True) -> httpx.Response:
    cfg = crawl_config()
    retries = int(cfg.get("max_retries", 3))
    base = float(cfg.get("backoff_base_seconds", 5))
    headers = build_headers()

    # 添加Referer头模拟真实浏览
    if "zhipin.com" in url:
        headers["Referer"] = "https://www.zhipin.com/"

    cookies = {}
    if use_cookies and "zhipin.com" in url:
        cookies = load_boss_cookies()

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=30.0,
                headers=headers,
                cookies=cookies,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                http2=False,  # HTTP/2需要h2包，改为False避免依赖问题
            ) as client:
                r = client.get(url)

            # 检查是否为反爬页面
            content = r.text[:1000] if r.text else ""
            if "安全验证" in content or "验证码" in content or "请稍候" in content:
                print(f"检测到反爬页面，尝试 {attempt + 1}/{retries}")
                if attempt < retries - 1:
                    time.sleep(base * (2**attempt) * 2)  # 更长等待
                    continue
                else:
                    # 最后一次尝试仍然被反爬，返回响应但标记
                    return r

            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(base * (2**attempt))
                continue

            return r
        except Exception as e:
            last_err = e
            print(f"请求失败 {url} (尝试 {attempt + 1}/{retries}): {e}")
            time.sleep(base * (2**attempt))

    raise RuntimeError(f"请求失败：{url}，最后错误：{last_err}")
