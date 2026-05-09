from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from campus_rag.crawl.http_util import get_with_backoff, sleep_delay


@dataclass
class FetchedPage:
    title: str
    text: str
    source_url: str
    quality_flags: list[str]


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def fetch_official_page(url: str) -> FetchedPage:
    sleep_delay()
    r = get_with_backoff(url)
    flags: list[str] = []
    if r.status_code != 200:
        flags.append(f"http_{r.status_code}")
        return FetchedPage(title="", text="", source_url=url, quality_flags=flags)
    html = r.text
    if len(html) < 200:
        flags.append("empty_html")
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""
    text = _visible_text(html)
    if len(text) < 80:
        flags.append("low_content")
    return FetchedPage(title=title or url, text=text, source_url=url, quality_flags=flags)
