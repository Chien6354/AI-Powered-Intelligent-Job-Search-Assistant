from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from bs4 import BeautifulSoup

from campus_rag.crawl.http_util import get_with_backoff, sleep_delay


@dataclass
class BossJobDoc:
    title: str
    company: str | None
    raw_text: str
    source_url: str
    quality_flags: list[str]
    metadata: Dict[str, Any] | None = None  # 存储结构化信息


def _extract_json_ld_job(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _extract_initial_state(html: str) -> dict | None:
    m = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*JSON\.parse\(\s*'(.+?)'\s*\)\s*;",
        html,
        re.DOTALL,
    )
    if not m:
        m2 = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;</script>",
            html,
            re.DOTALL,
        )
        if m2:
            try:
                return json.loads(m2.group(1))
            except json.JSONDecodeError:
                return None
        return None
    # escaped JSON in parse string - skip fragile parse
    return None


def _extract_sec_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    parts: list[str] = []

    # 更多的选择器，覆盖不同页面结构
    selectors = [
        ".job-sec-text",  # 最常见的
        ".job-detail-section",
        ".detail-content",
        ".job-detail",
        ".job-sec",
        ".job-box",
        ".text",
        ".content",
        ".describtion",  # 可能的拼写错误
        ".description",
        ".job-desc",
        ".job-require",
        ".job-info",
        ".info-text",
        ".job-banner",  # 可能包含重要信息
        ".job-primary",
        ".primary-box",
        ".job-detail__content",
        "[class*='detail']",
        "[class*='content']",
        "[class*='desc']",
        "[class*='text']",
    ]

    for sel in selectors:
        try:
            for node in soup.select(sel):
                t = node.get_text("\n", strip=True)
                if t and len(t) > 20:  # 过滤过短的文本
                    parts.append(t)
        except Exception:
            continue

    # 去重，保持顺序
    unique_parts = []
    seen = set()
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique_parts.append(part)

    return "\n\n".join(unique_parts).strip()


def _extract_structured_info(html: str) -> Dict[str, Any]:
    """提取结构化职位信息"""
    soup = BeautifulSoup(html, "html.parser")
    info: Dict[str, Any] = {}

    # 1. 尝试从标题区域提取关键信息
    title_area_selectors = [
        ".job-title",
        ".name",
        ".title",
        ".job-name",
        ".info-primary",
        ".primary",
        ".job-banner",
        ".banner",
    ]

    for sel in title_area_selectors:
        try:
            elements = soup.select(sel)
            for elem in elements:
                text = elem.get_text(" ", strip=True)
                if text and len(text) > 5:
                    # 尝试提取薪资（通常包含"k"或"万"或"-"）
                    salary_pattern = r'(\d+[kK千]?\s*[-~至]?\s*\d*[kK千]?/?\d*[kK千]?元?/?月?|面议|薪资不限)'
                    salary_match = re.search(salary_pattern, text)
                    if salary_match and 'salary' not in info:
                        info['salary'] = salary_match.group(1)

                    # 尝试提取地点
                    location_pattern = r'([北京上海广州深圳杭州成都重庆武汉西安南京]|异地|远程|混合办公)'
                    location_match = re.search(location_pattern, text)
                    if location_match and 'location' not in info:
                        info['location'] = location_match.group(1)

                    # 尝试提取经验要求
                    exp_pattern = r'(\d+[-~]?\d*年经验|经验不限|应届生|在校生|实习生)'
                    exp_match = re.search(exp_pattern, text)
                    if exp_match and 'experience' not in info:
                        info['experience'] = exp_match.group(1)

                    # 尝试提取学历要求
                    edu_pattern = r'(本科|大专|硕士|博士|学历不限|高中|中专)'
                    edu_match = re.search(edu_pattern, text)
                    if edu_match and 'education' not in info:
                        info['education'] = edu_match.group(1)
        except Exception:
            continue

    # 2. 尝试从特定class中提取信息
    # 薪资通常有特殊class
    for sel in [".salary", ".badge", ".red", "[class*='salary']", "[class*='money']"]:
        try:
            elements = soup.select(sel)
            for elem in elements:
                text = elem.get_text(strip=True)
                if text and ('k' in text.lower() or '万' in text or '元' in text) and 'salary' not in info:
                    info['salary'] = text
                    break
        except Exception:
            continue

    # 3. 尝试从整个页面文本中提取关键信息
    full_text = soup.get_text("\n", strip=True)

    # 提取公司信息
    company_patterns = [
        r'公司[:：]\s*([^\n]+)',
        r'企业[:：]\s*([^\n]+)',
        r'([\u4e00-\u9fa5]{2,20}有限公司|[\u4e00-\u9fa5]{2,20}公司|[\u4e00-\u9fa5]{2,20}集团)',
    ]

    for pattern in company_patterns:
        match = re.search(pattern, full_text[:2000])
        if match and 'company_detail' not in info:
            info['company_detail'] = match.group(1)
            break

    # 提取职位类型（全职/实习/兼职）
    job_type_pattern = r'(全职|实习|兼职|在校|应届)'
    match = re.search(job_type_pattern, full_text[:1000])
    if match and 'job_type' not in info:
        info['job_type'] = match.group(1)

    return info


def fetch_boss_job_page(url: str) -> BossJobDoc:
    """Fetch public Boss job detail page. May fail if anti-bot blocks."""
    sleep_delay()
    flags: list[str] = []
    print(f"抓取职位页面: {url}")

    # 使用cookies访问
    r = get_with_backoff(url, use_cookies=True)

    if r.status_code != 200:
        flags.append(f"http_{r.status_code}")
        print(f"HTTP状态码异常: {r.status_code}")
        return BossJobDoc(
            title="",
            company=None,
            raw_text="",
            source_url=url,
            quality_flags=flags,
            metadata=None,
        )

    html = r.text

    # 检查反爬
    if "安全验证" in html or "验证码" in html:
        flags.append("captcha_or_block")
        print("检测到验证码或安全验证")
    if "请登录" in html and "登录" in html[:2000]:
        flags.append("login_wall_suspected")
        print("检测到登录墙")
    if "请稍候" in html[:1000]:
        flags.append("anti_bot_waiting")
        print("检测到'请稍候'反爬页面")

    title = ""
    company = None
    text = ""

    # 方法1: 尝试JSON-LD结构化数据
    ld = _extract_json_ld_job(html)
    if ld:
        title = str(ld.get("title") or "")
        org = ld.get("hiringOrganization") or {}
        if isinstance(org, dict):
            company = org.get("name")
        text = str(ld.get("description") or "")
        print(f"JSON-LD提取: 标题={title[:30]}, 公司={company}")

    # 方法2: 使用改进的选择器提取文本
    if len(text) < 100:  # JSON-LD文本可能不全
        text2 = _extract_sec_text(html)
        if len(text2) > len(text):
            text = text2
            print(f"选择器提取: 文本长度={len(text2)}")

    # 方法3: 从页面标题提取
    soup = BeautifulSoup(html, "html.parser")
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
        # 清理标题中的无关信息
        if "BOSS直聘" in title:
            title = title.replace("BOSS直聘", "").replace("招聘", "").strip(" -|")
        print(f"页面标题提取: {title[:50]}")

    # 方法4: 尝试从页面其他位置提取公司信息
    if not company:
        # 尝试从特定选择器提取公司
        company_selectors = [
            ".company-info",
            ".company-name",
            ".info-company",
            ".job-company",
            ".company",
            ".brand",
            "[class*='company']",
            "[class*='brand']",
        ]

        for sel in company_selectors:
            try:
                elements = soup.select(sel)
                for elem in elements:
                    elem_text = elem.get_text(strip=True)
                    if elem_text and len(elem_text) > 1 and len(elem_text) < 50:
                        # 过滤掉明显不是公司名的文本
                        if "公司" in elem_text or "有限公司" in elem_text or "集团" in elem_text:
                            company = elem_text
                            break
                        # 或者直接使用第一个非短文本
                        if not any(x in elem_text.lower() for x in ["登录", "注册", "首页", "搜索"]):
                            company = elem_text
                            break
                if company:
                    break
            except Exception:
                continue

    # 提取结构化信息
    structured_info = _extract_structured_info(html)

    # 如果结构化信息中有公司信息，优先使用
    if not company and 'company_detail' in structured_info:
        company = structured_info['company_detail']

    # 数据验证
    if len(text) < 100:
        flags.append("parse_thin")
        print(f"警告: 提取文本过短 ({len(text)} 字符)")

    if not title or title == "请稍候":
        flags.append("title_missing")
        title = "未知职位"

    if not company:
        flags.append("company_missing")
    else:
        print(f"公司信息: {company}")

    # 构建完整的职位描述文本
    final_text = text

    # 如果文本太短，尝试从结构化信息构建
    if len(final_text) < 150 and structured_info:
        info_lines = []
        if title and title != "未知职位":
            info_lines.append(f"职位: {title}")
        if company:
            info_lines.append(f"公司: {company}")
        if 'salary' in structured_info:
            info_lines.append(f"薪资: {structured_info['salary']}")
        if 'location' in structured_info:
            info_lines.append(f"地点: {structured_info['location']}")
        if 'experience' in structured_info:
            info_lines.append(f"经验: {structured_info['experience']}")
        if 'education' in structured_info:
            info_lines.append(f"学历: {structured_info['education']}")
        if 'job_type' in structured_info:
            info_lines.append(f"类型: {structured_info['job_type']}")

        if info_lines:
            structured_summary = "\n".join(info_lines)
            final_text = structured_summary + "\n\n" + final_text if final_text else structured_summary
            print(f"使用结构化信息构建文本，长度={len(final_text)}")

    print(f"最终提取: 标题={title[:30]}, 公司={company}, 文本长度={len(final_text)}, 标志={flags}")

    return BossJobDoc(
        title=title or "Boss职位",
        company=company,
        raw_text=final_text,
        source_url=url,
        quality_flags=flags,
        metadata=structured_info if structured_info else None,
    )


def fetch_boss_list_page(url: str, max_jobs: int = 10) -> List[str]:
    """从Boss列表页提取职位详情页URL

    Args:
        url: 列表页URL，如 https://www.zhipin.com/web/geek/jobs?query=互联网&city=101190400
        max_jobs: 最多提取的职位数量

    Returns:
        职位详情页URL列表
    """
    sleep_delay()
    flags: List[str] = []
    r = get_with_backoff(url)
    if r.status_code != 200:
        flags.append(f"http_{r.status_code}")
        return []

    html = r.text
    if "安全验证" in html or "验证码" in html:
        flags.append("captcha_or_block")
    if "请登录" in html and "登录" in html[:2000]:
        flags.append("login_wall_suspected")

    job_urls = []

    # 方法1: 尝试从 window.__INITIAL_STATE__ 提取
    initial_state = _extract_initial_state(html)
    if initial_state:
        # Boss直聘列表页数据结构通常包含 jobList 或类似字段
        # 尝试多种可能的路径
        possible_paths = [
            ["jobList", "list"],
            ["geek", "jobList"],
            ["jobs", "list"],
            ["jobData", "list"],
            ["data", "jobList"],
        ]

        for path in possible_paths:
            data = initial_state
            for key in path:
                if isinstance(data, dict) and key in data:
                    data = data[key]
                else:
                    data = None
                    break
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        # 尝试提取职位ID
                        job_id = (item.get("encryptJobId") or
                                 item.get("jobId") or
                                 item.get("encrypt_job_id") or
                                 item.get("lid"))
                        if job_id:
                            job_urls.append(f"https://www.zhipin.com/job_detail/{job_id}.html")
                if job_urls:
                    break

        # 如果上述路径未找到，尝试递归查找
        if not job_urls:
            def find_job_links(data):
                links = []
                if isinstance(data, dict):
                    # 检查是否有包含职位ID的字段
                    job_id = (data.get("encryptJobId") or
                             data.get("jobId") or
                             data.get("encrypt_job_id") or
                             data.get("lid"))
                    if job_id:
                        links.append(f"https://www.zhipin.com/job_detail/{job_id}.html")
                    # 递归查找
                    for value in data.values():
                        links.extend(find_job_links(value))
                elif isinstance(data, list):
                    for item in data:
                        links.extend(find_job_links(item))
                return links

            job_urls = find_job_links(initial_state)

    # 方法2: 从HTML中提取链接（备用方法）
    if not job_urls:
        soup = BeautifulSoup(html, "html.parser")
        # 查找所有包含/job_detail/的链接
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/job_detail/" in href:
                # 补全完整的URL
                if href.startswith("/"):
                    full_url = urllib.parse.urljoin("https://www.zhipin.com", href)
                elif href.startswith("http"):
                    full_url = href
                else:
                    continue
                job_urls.append(full_url)

    # 去重并限制数量
    unique_urls = []
    seen = set()
    for url in job_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    return unique_urls[:max_jobs]
