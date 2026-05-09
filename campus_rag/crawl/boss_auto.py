from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any

try:
    from jsonpath import jsonpath
    JSONPATH_AVAILABLE = True
except ImportError:
    JSONPATH_AVAILABLE = False
    print("警告: jsonpath 未安装，API数据解析功能受限")
    print("执行: pip install jsonpath")

try:
    from DrissionPage import ChromiumPage, ChromiumOptions, SessionPage
    DRISSIONPAGE_AVAILABLE = True
except ImportError:
    DRISSIONPAGE_AVAILABLE = False
    # 定义空类供类型提示使用
    class ChromiumPage:
        pass
    class ChromiumOptions:
        pass
    class SessionPage:
        pass

from campus_rag.crawl.http_util import sleep_delay
from campus_rag.config_loader import crawl_config, ROOT

# Cookies文件路径
COOKIES_FILE = ROOT / "data" / "boss_cookies.json"


@dataclass
class BossSearchConfig:
    """Boss搜索配置"""
    keywords: List[str]
    city: str  # 城市名，如"北京"
    max_pages: int = 2  # 每关键词最大页数
    max_jobs_per_keyword: int = 20  # 每关键词最大职位数


def save_cookies(cookies: List[Dict[str, Any]]) -> bool:
    """保存cookies到文件"""
    try:
        COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"Cookies已保存到: {COOKIES_FILE}")
        return True
    except Exception as e:
        print(f"保存cookies失败: {e}")
        return False


def load_cookies() -> Optional[List[Dict[str, Any]]]:
    """从文件加载cookies"""
    try:
        if not COOKIES_FILE.exists():
            return None
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        print(f"从文件加载cookies: {len(cookies)} 个")
        return cookies
    except Exception as e:
        print(f"加载cookies失败: {e}")
        return None


def manual_login_and_save_cookies() -> bool:
    """手动登录并保存cookies（供首次使用）

    使用方法：
    1. 调用此函数会打开浏览器
    2. 手动完成登录流程
    3. 登录成功后cookies会自动保存
    """
    print("=== 手动登录教程 ===")
    print("1. 浏览器即将打开，请手动登录Boss直聘")
    print("2. 登录成功后，浏览器会保持打开状态")
    print("3. 按提示操作保存cookies")
    print("=================")

    options = ChromiumOptions()
    options.set_argument('--disable-blink-features=AutomationControlled')
    options.set_argument('--no-sandbox')
    options.set_argument('--disable-dev-shm-usage')
    options.set_argument('--remote-debugging-port=0')  # 0表示随机端口

    page = ChromiumPage(addr_or_opts=options)

    try:
        # 打开登录页
        page.get('https://login.zhipin.com/')
        time.sleep(2)

        print("\n请手动完成登录...")
        print("登录成功后，程序会自动检测并保存cookies")
        print("请等待最多30秒...")

        # 等待用户操作
        for i in range(30):
            time.sleep(1)
            # 检查是否已登录（通过页面元素判断）
            try:
                login_elements = page.eles('text=登录')
                # 检查已登录元素
                logged_in_selectors = ['text=我的简历', 'text=消息', 'text=退出', 'text=我的', 'text=个人中心']
                logged_in_found = False
                for selector in logged_in_selectors:
                    if page.eles(selector):
                        logged_in_found = True
                        break

                # 如果没有登录按钮，或者找到已登录元素，则认为登录成功
                if not login_elements or logged_in_found:
                    print("检测到登录成功")
                    break
            except Exception as e:
                print(f"登录检查出错: {e}")

        # 获取并保存cookies
        cookies = page.cookies()  # DrissionPage 4.x API: returns CookiesList
        if cookies:
            # 转换为列表以确保JSON序列化
            cookies_list = list(cookies)
            save_cookies(cookies_list)
            print(f"成功保存 {len(cookies_list)} 个cookies")
            print("关闭浏览器后，下次运行将自动使用这些cookies")
            return True
        else:
            print("未获取到cookies，登录可能未成功")
            return False

    except Exception as e:
        print(f"手动登录过程出错: {e}")
        return False
    finally:
        print("浏览器将保持打开，请手动关闭")
        # 不自动关闭浏览器，让用户查看状态


def boos_login(page: ChromiumPage, use_cookies: bool = True) -> bool:
    """登录Boss直聘

    Args:
        page: ChromiumPage 实例
        use_cookies: 是否尝试使用保存的cookies

    Returns:
        是否登录成功
    """
    if not DRISSIONPAGE_AVAILABLE:
        print("错误: DrissionPage 未安装，请执行: pip install DrissionPage")
        return False
    # 方法1: 使用已保存的cookies（推荐）
    if use_cookies:
        cookies = load_cookies()
        if cookies:
            print(f"使用保存的cookies登录 ({len(cookies)} 个)")
            try:
                page.set.cookies(cookies)  # DrissionPage 4.x API
                # 访问首页验证登录状态
                page.get('https://www.zhipin.com/')
                time.sleep(2)

                # 检查登录状态 - 多种方式验证
                time.sleep(3)  # 等待页面加载

                # 检查未登录状态：有"登录"按钮
                login_elements = page.eles('text=登录')
                print(f"找到登录按钮: {len(login_elements)} 个")

                # 检查已登录状态：有"我的简历"、"消息"、"退出"等元素
                logged_in_elements = []
                logged_in_selectors = [
                    'text=我的简历',
                    'text=消息',
                    'text=退出',
                    'text=我的',
                    'text=个人中心',
                    'css=.user-info',
                    'css=.user-avatar',
                ]

                for selector in logged_in_selectors:
                    try:
                        elems = page.eles(selector)
                        if elems:
                            logged_in_elements.extend(elems)
                    except:
                        pass

                print(f"找到已登录状态元素: {len(logged_in_elements)} 个")

                # 判断逻辑：如果有已登录元素，或者没有登录按钮，则认为登录成功
                if logged_in_elements or not login_elements:
                    print("Cookies登录成功")
                    return True
                else:
                    print("Cookies可能已过期或未生效")
                    if login_elements:
                        for i, elem in enumerate(login_elements[:3]):
                            print(f"  登录按钮{i+1}: {elem.text[:50] if elem.text else '无文本'}")
            except Exception as e:
                print(f"使用cookies登录失败: {e}")

    # 方法2: 自动登录（需要用户实现）
    print("尝试自动登录...")
    print("注意: 自动登录可能需要处理验证码")
    print("建议先运行 manual_login_and_save_cookies() 保存cookies")

    try:
        # 打开登录页
        page.get('https://login.zhipin.com/')
        time.sleep(3)

        # 这里需要用户根据实际情况完善登录逻辑
        # 示例：查找用户名密码输入框
        # username = page.ele('xpath=//input[@name="username"]')
        # password = page.ele('xpath=//input[@name="password"]')
        # if username and password:
        #     username.input('你的用户名')
        #     password.input('你的密码')
        #     # 点击登录按钮
        #     login_btn = page.ele('xpath=//button[@type="submit"]')
        #     if login_btn:
        #         login_btn.click()
        #         time.sleep(5)

        # 检查是否登录成功
        page.get('https://www.zhipin.com/')
        time.sleep(2)

        # 检查登录状态 - 多种方式验证
        time.sleep(3)  # 等待页面加载

        # 检查未登录状态：有"登录"按钮
        login_elements = page.eles('text=登录')
        print(f"找到登录按钮: {len(login_elements)} 个")

        # 检查已登录状态：有"我的简历"、"消息"、"退出"等元素
        logged_in_elements = []
        logged_in_selectors = [
            'text=我的简历',
            'text=消息',
            'text=退出',
            'text=我的',
            'text=个人中心',
            'css=.user-info',
            'css=.user-avatar',
        ]

        for selector in logged_in_selectors:
            try:
                elems = page.eles(selector)
                if elems:
                    logged_in_elements.extend(elems)
            except:
                pass

        print(f"找到已登录状态元素: {len(logged_in_elements)} 个")

        # 判断逻辑：如果有已登录元素，或者没有登录按钮，则认为登录成功
        if logged_in_elements or not login_elements:
            print("自动登录成功")
            # 保存cookies供下次使用
            cookies = page.cookies()  # DrissionPage 4.x API: returns CookiesList
            if cookies:
                # 转换为列表以确保JSON序列化
                cookies_list = list(cookies)
                save_cookies(cookies_list)
            return True
        else:
            print("自动登录失败，请检查登录逻辑")
            if login_elements:
                for i, elem in enumerate(login_elements[:3]):
                    print(f"  登录按钮{i+1}: {elem.text[:50] if elem.text else '无文本'}")
            return False

    except Exception as e:
        print(f"自动登录过程出错: {e}")
        return False


def search_boss_jobs_data(
    keyword: str,
    city: str = "北京",
    max_pages: int = 2,
    max_jobs: int = 20
) -> List[Dict[str, Any]]:
    """通过浏览器自动化搜索Boss职位并提取结构化数据
    参考成功爬虫实现，直接从API获取数据，无需访问详情页

    Args:
        keyword: 搜索关键词
        city: 城市名
        max_pages: 最大翻页数
        max_jobs: 最大职位数

    Returns:
        结构化职位数据列表，每个元素包含：title, salary, degree, experience, company, city, district, business_district, job_url等
    """
    print(f"开始搜索: {keyword} in {city}, 页数: {max_pages}")

    # 创建浏览器实例
    options = ChromiumOptions()
    options.set_argument('--disable-blink-features=AutomationControlled')
    options.set_argument('--disable-infobars')
    options.set_argument('--no-sandbox')
    options.set_argument('--disable-dev-shm-usage')
    options.set_argument('--remote-debugging-port=0')  # 0表示随机端口

    page = ChromiumPage(addr_or_opts=options)

    try:
        # 登录（优先使用cookies）
        if not boos_login(page, use_cookies=True):
            print("登录失败，退出")
            print("建议运行 manual_login_and_save_cookies() 先手动登录保存cookies")
            return []

        # 打开首页
        page.get('https://www.zhipin.com/')
        time.sleep(2)

        # 开启监听模式 - 参考成功爬虫的API地址
        page.listen.start('https://www.zhipin.com/wapi/zpgeek/search/joblist.json?_=')
        print("API监听已启动")

        # 定位输入框并输入关键词
        try:
            query_input = page.ele('xpath=//input[@name="query"]')
            query_input.input(keyword)
            time.sleep(1)
        except Exception as e:
            print(f"定位输入框失败: {e}")
            # 备用方案：直接访问搜索URL
            search_url = f"https://www.zhipin.com/web/geek/jobs?query={keyword}&city={city}"
            page.get(search_url)
            time.sleep(3)
            # 重新开启监听（如果页面跳转）
            page.listen.start('https://www.zhipin.com/wapi/zpgeek/search/joblist.json?_=')
            print("重新启动API监听")

        # 定位搜索按钮并点击
        try:
            search_btn = page.ele('xpath=//button[contains(@class, "btn-search")]')
            search_btn.click()
            time.sleep(3)
        except Exception as e:
            print(f"点击搜索按钮失败: {e}")
            # 可能已经在搜索页面，继续

        # 等待数据加载
        time.sleep(3)

        job_urls = []

        # 使用steps方法获取API数据 - 参考成功爬虫
        print("开始收集API数据...")
        for data in page.listen.steps(timeout=3):
            try:
                response_body = data.response.body
                if not response_body:
                    continue

                # 解析JSON响应
                if JSONPATH_AVAILABLE:
                    # 使用jsonpath提取职位列表
                    job_list = jsonpath(response_body, '$..jobList')
                    if job_list and isinstance(job_list, list):
                        # job_list可能是嵌套的，取第一个列表
                        if len(job_list) > 0 and isinstance(job_list[0], list):
                            job_items = job_list[0]
                        else:
                            job_items = job_list

                        # 提取职位ID
                        for item in job_items:
                            if isinstance(item, dict):
                                # 提取职位ID
                                job_id = (item.get("encryptJobId") or
                                         item.get("jobId") or
                                         item.get("encrypt_job_id") or
                                         item.get("lid"))
                                if job_id:
                                    url = f"https://www.zhipin.com/job_detail/{job_id}.html"
                                    if url not in job_urls:
                                        job_urls.append(url)
                                        print(f"找到职位: {item.get('jobName', '未知')} - {url}")

                    # 如果没有通过jsonpath找到，尝试其他方法
                    if len(job_urls) == 0:
                        # 尝试递归查找职位ID
                        def find_job_ids(data):
                            ids = []
                            if isinstance(data, dict):
                                # 检查是否有包含职位ID的字段
                                job_id = (data.get("encryptJobId") or
                                         data.get("jobId") or
                                         data.get("encrypt_job_id") or
                                         data.get("lid"))
                                if job_id:
                                    ids.append(job_id)
                                # 递归查找
                                for value in data.values():
                                    ids.extend(find_job_ids(value))
                            elif isinstance(data, list):
                                for item in data:
                                    ids.extend(find_job_ids(item))
                            return ids

                        job_ids = find_job_ids(response_body)
                        for job_id in job_ids:
                            url = f"https://www.zhipin.com/job_detail/{job_id}.html"
                            if url not in job_urls:
                                job_urls.append(url)
                else:
                    print("警告: jsonpath不可用，尝试解析响应")
                    # 尝试手动解析
                    job_urls.extend(parse_joblist_response(response_body))

            except Exception as e:
                print(f"解析API响应出错: {e}")
                continue

        print(f"从API收集到 {len(job_urls)} 个职位")

        # 如果有分页，翻页获取更多数据
        if len(job_urls) < max_jobs and max_pages > 1:
            print(f"尝试翻页获取更多数据，当前 {len(job_urls)}/{max_jobs}")
            for page_num in range(2, max_pages + 1):
                try:
                    # 查找下一页按钮
                    next_btn = page.ele(f'text={page_num}')
                    if next_btn:
                        next_btn.click()
                        time.sleep(3)

                        # 收集新页面的API数据
                        for data in page.listen.steps(timeout=3):
                            try:
                                response_body = data.response.body
                                if not response_body:
                                    continue

                                # 解析职位ID
                                if JSONPATH_AVAILABLE:
                                    job_list = jsonpath(response_body, '$..jobList')
                                    if job_list and isinstance(job_list, list):
                                        if len(job_list) > 0 and isinstance(job_list[0], list):
                                            job_items = job_list[0]
                                        else:
                                            job_items = job_list

                                        for item in job_items:
                                            if isinstance(item, dict):
                                                job_id = (item.get("encryptJobId") or
                                                         item.get("jobId") or
                                                         item.get("encrypt_job_id") or
                                                         item.get("lid"))
                                                if job_id:
                                                    url = f"https://www.zhipin.com/job_detail/{job_id}.html"
                                                    if url not in job_urls:
                                                        job_urls.append(url)
                                                        print(f"翻页找到职位: {item.get('jobName', '未知')}")
                            except Exception as e:
                                print(f"解析翻页API响应出错: {e}")
                                continue

                        if len(job_urls) >= max_jobs:
                            break

                    else:
                        # 尝试其他分页方式
                        page_btns = page.eles('xpath=//a[contains(@class, "page")]')
                        for btn in page_btns:
                            if btn.text == str(page_num):
                                btn.click()
                                time.sleep(3)
                                # 简单收集数据
                                break
                except Exception as e:
                    print(f"翻页到第 {page_num} 页失败: {e}")
                    break

        # 如果通过API未获取到足够数据，从页面HTML提取（备用方法）
        if len(job_urls) < max_jobs:
            print(f"API数据不足 ({len(job_urls)}), 尝试从HTML提取")
            html_urls = extract_job_urls_from_html(page.html)
            for url in html_urls:
                if url not in job_urls:
                    job_urls.append(url)
            print(f"从HTML提取到 {len(html_urls)} 个URL，去重后总数: {len(job_urls)}")

        # 去重
        unique_urls = []
        seen = set()
        for url in job_urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        print(f"最终获取 {len(unique_urls)} 个唯一职位URL")
        return unique_urls[:max_jobs]

    except Exception as e:
        print(f"搜索过程中发生错误: {e}")
        return []
    finally:
        # 关闭浏览器
        try:
            page.quit()
        except:
            pass


def parse_joblist_response(data: dict) -> List[str]:
    """解析职位列表API响应，提取详情页URL"""
    job_urls = []

    # 尝试多种可能的路径
    possible_paths = [
        ["jobList", "list"],
        ["geek", "jobList"],
        ["jobs", "list"],
        ["data", "jobList"],
        ["zpData", "jobList"],
    ]

    for path in possible_paths:
        job_data = data
        for key in path:
            if isinstance(job_data, dict) and key in job_data:
                job_data = job_data[key]
            else:
                job_data = None
                break

        if isinstance(job_data, list):
            for item in job_data:
                if isinstance(item, dict):
                    # 提取职位ID
                    job_id = (item.get("encryptJobId") or
                             item.get("jobId") or
                             item.get("encrypt_job_id") or
                             item.get("lid"))
                    if job_id:
                        job_urls.append(f"https://www.zhipin.com/job_detail/{job_id}.html")
            if job_urls:
                break

    # 如果未找到，尝试递归查找
    if not job_urls:
        def find_job_links(data_dict):
            links = []
            if isinstance(data_dict, dict):
                job_id = (data_dict.get("encryptJobId") or
                         data_dict.get("jobId") or
                         data_dict.get("encrypt_job_id") or
                         data_dict.get("lid"))
                if job_id:
                    links.append(f"https://www.zhipin.com/job_detail/{job_id}.html")
                for value in data_dict.values():
                    links.extend(find_job_links(value))
            elif isinstance(data_dict, list):
                for item in data_dict:
                    links.extend(find_job_links(item))
            return links

        job_urls = find_job_links(data)

    return job_urls


def extract_job_urls_from_html(html: str) -> List[str]:
    """从HTML页面提取职位详情页URL"""
    import re
    from bs4 import BeautifulSoup

    job_urls = []

    # 方法1: 正则匹配
    pattern = r'https?://[^"\']+/job_detail/[^"\']+\.html'
    matches = re.findall(pattern, html)
    job_urls.extend(matches)

    # 方法2: BeautifulSoup解析
    soup = BeautifulSoup(html, 'html.parser')
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/job_detail/' in href:
            if href.startswith('/'):
                full_url = f"https://www.zhipin.com{href}"
            elif href.startswith('http'):
                full_url = href
            else:
                continue
            job_urls.append(full_url)

    return job_urls


def fetch_boss_job_with_browser(page: ChromiumPage, url: str):
    """使用浏览器会话获取Boss职位详情页内容

    Args:
        page: 已登录的ChromiumPage实例
        url: 职位详情页URL

    Returns:
        dict: 包含标题、公司、原始文本等信息的字典
    """
    from bs4 import BeautifulSoup
    import re

    print(f"浏览器访问详情页: {url}")

    try:
        page.get(url)
        time.sleep(3)  # 等待页面加载

        # 等待内容加载（检查是否有"请稍候"字样）
        max_wait = 10
        for i in range(max_wait):
            html = page.html
            if "请稍候" not in html:
                print(f"页面加载完成，等待 {i} 秒")
                break
            time.sleep(1)
        else:
            print("警告: 页面可能仍在加载中")

        html = page.html
        soup = BeautifulSoup(html, 'html.parser')

        # 提取标题
        title = ""
        title_elem = soup.find('title')
        if title_elem and title_elem.string:
            title = title_elem.string.strip()
            if title == "请稍候":
                # 尝试从其他位置获取标题
                h1_elem = soup.find('h1', class_='job-title')
                if h1_elem:
                    title = h1_elem.get_text(strip=True)

        # 提取公司名称
        company = None
        company_elem = soup.find('div', class_='company-info') or soup.find('a', class_='company-name')
        if company_elem:
            company = company_elem.get_text(strip=True)

        # 提取职位描述
        raw_text = ""
        job_detail_elem = soup.find('div', class_='job-detail') or soup.find('div', class_='job-sec-text')
        if job_detail_elem:
            raw_text = job_detail_elem.get_text('\n', strip=True)

        # 如果提取的文本太短，尝试其他选择器
        if len(raw_text) < 100:
            # 尝试查找所有包含文本的section
            sections = soup.find_all('div', class_='job-sec')
            texts = []
            for sec in sections:
                text = sec.get_text('\n', strip=True)
                if text:
                    texts.append(text)
            if texts:
                raw_text = '\n\n'.join(texts)

        # 如果仍然太短，使用整个body的文本（排除脚本和样式）
        if len(raw_text) < 100:
            for script in soup(["script", "style", "nav", "header", "footer"]):
                script.decompose()
            raw_text = soup.get_text('\n', strip=True)
            # 清理多余的空行
            lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
            raw_text = '\n'.join(lines[:200])  # 限制行数

        quality_flags = []
        if len(raw_text) < 200:
            quality_flags.append("parse_thin")

        if "安全验证" in html or "验证码" in html:
            quality_flags.append("captcha_or_block")

        return {
            "title": title or "Boss职位",
            "company": company,
            "raw_text": raw_text,
            "source_url": url,
            "quality_flags": quality_flags,
            "html": html[:5000] if len(html) > 5000 else html  # 保存部分HTML用于调试
        }

    except Exception as e:
        print(f"浏览器访问详情页失败 {url}: {e}")
        return {
            "title": "",
            "company": None,
            "raw_text": "",
            "source_url": url,
            "quality_flags": ["browser_error"],
            "html": ""
        }


def fetch_boss_jobs_by_search(config: BossSearchConfig) -> List[str]:
    """根据搜索配置获取职位详情页URL列表"""
    all_urls = []

    for keyword in config.keywords:
        print(f"\n搜索关键词: {keyword}")
        urls = search_boss_jobs_data(
            keyword=keyword,
            city=config.city,
            max_pages=config.max_pages,
            max_jobs=config.max_jobs_per_keyword
        )
        print(f"找到 {len(urls)} 个职位")
        all_urls.extend(urls)

        # 关键词间延迟
        if len(config.keywords) > 1:
            sleep_delay()

    # 去重
    unique_urls = []
    seen = set()
    for url in all_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    return unique_urls


def fetch_boss_jobs_direct(
    keyword: str,
    city: str = "北京",
    max_pages: int = 2,
    season: str | None = None
) -> List[str]:
    """直接从API获取Boss职位数据并入库（参考成功爬虫实现）

    Args:
        keyword: 搜索关键词
        city: 城市名
        max_pages: 最大翻页数
        season: 招聘季

    Returns:
        文档ID列表
    """
    from campus_rag.ingest import ingest_boss_job_data

    print(f"直接API搜索: {keyword} in {city}, 页数: {max_pages}")

    # 创建浏览器实例（简化，参考成功爬虫）
    options = ChromiumOptions()
    options.set_argument('--disable-blink-features=AutomationControlled')
    options.set_argument('--no-sandbox')
    options.set_argument('--disable-dev-shm-usage')
    options.set_argument('--remote-debugging-port=0')  # 0表示随机端口

    page = ChromiumPage(addr_or_opts=options)
    doc_ids = []

    try:
        # 登录（使用cookies）
        if not boos_login(page, use_cookies=True):
            print("登录失败，退出")
            print("建议运行 manual_login_and_save_cookies() 先手动登录保存cookies")
            return []

        # 打开首页（参考成功爬虫使用城市页面）
        page.get(f'https://www.zhipin.com/{city}/')
        time.sleep(2)

        # 开启监听模式
        page.listen.start('https://www.zhipin.com/wapi/zpgeek/search/joblist.json?_=')
        print("API监听已启动")

        # 定位输入框
        try:
            page.ele('xpath=//input[@name="query"]').input(keyword)
            time.sleep(1)
        except Exception as e:
            print(f"定位输入框失败: {e}")
            # 备用方案：直接访问搜索URL
            search_url = f"https://www.zhipin.com/web/geek/jobs?query={keyword}&city={city}"
            page.get(search_url)
            time.sleep(3)
            # 重新开启监听
            page.listen.start('https://www.zhipin.com/wapi/zpgeek/search/joblist.json?_=')
            print("重新启动API监听")

        # 定位搜索按钮
        try:
            page.ele('xpath=//button[@class="btn btn-search"]').click()
            time.sleep(3)
        except Exception as e:
            print(f"点击搜索按钮失败: {e}")
            # 可能已经在搜索页面

        # 等待加载完毕
        time.sleep(2)

        # 使用循环抓取多页数据
        for page_num in range(max_pages):
            print(f"处理第 {page_num + 1} 页")

            # steps方法获取json数据包，timeout=3是设置等待时间
            for data in page.listen.steps(timeout=3):
                try:
                    response_body = data.response.body
                    if not response_body:
                        continue

                    # 使用jsonpath提取数据
                    if not JSONPATH_AVAILABLE:
                        print("错误: jsonpath未安装，无法提取数据")
                        print("执行: pip install jsonpath")
                        break

                    # 筛选目标数据
                    job_list = jsonpath(response_body, '$..jobList')
                    if not job_list:
                        print("未找到jobList字段")
                        continue

                    # 提取各字段值
                    job_names = jsonpath(job_list, '$..jobName') or []
                    salary_desc = jsonpath(job_list, '$..salaryDesc') or []
                    job_degrees = jsonpath(job_list, '$..jobDegree') or []
                    job_experiences = jsonpath(job_list, '$..jobExperience') or []
                    intern_days = jsonpath(job_list, '$..daysPerWeekDesc') or []
                    intern_months = jsonpath(job_list, '$..leastMonthDesc') or []
                    brand_names = jsonpath(job_list, '$..brandName') or []
                    city_names = jsonpath(job_list, '$..cityName') or []

                    total = len(job_names) if job_names else 0
                    if total == 0:
                        print("未提取到职位数据")
                        continue

                    print(f"第 {page_num + 1} 页提取到 {total} 个职位")

                    for i in range(total):
                        # 构建职位描述文本
                        job_text_parts = []

                        # 添加标题和薪资
                        title = job_names[i] if i < len(job_names) else "未知职位"
                        salary = salary_desc[i] if i < len(salary_desc) else "面议"
                        job_text_parts.append(f"职位: {title}")
                        job_text_parts.append(f"薪资: {salary}")

                        # 添加学历要求
                        degree = job_degrees[i] if i < len(job_degrees) else "学历不限"
                        job_text_parts.append(f"学历: {degree}")

                        # 添加工作经验要求
                        if i < len(job_experiences) and job_experiences[i]:
                            job_text_parts.append(f"工作经验: {job_experiences[i]}")
                        elif (i < len(intern_days) and intern_days[i] and
                              i < len(intern_months) and intern_months[i]):
                            job_text_parts.append(f"实习要求: {intern_days[i]}, {intern_months[i]}")

                        # 添加公司信息
                        company = brand_names[i] if i < len(brand_names) else "未知公司"
                        job_text_parts.append(f"公司: {company}")

                        # 添加城市信息
                        city_name = city_names[i] if i < len(city_names) else city
                        job_text_parts.append(f"城市: {city_name}")

                        # 组合成完整文本
                        raw_text = "\n".join(job_text_parts)

                        # 构建详情页URL（如果有职位ID）
                        job_url = None
                        # 尝试提取职位ID
                        job_ids = []
                        id_fields = ['encryptJobId', 'jobId', 'encrypt_job_id', 'lid']
                        for field in id_fields:
                            ids = jsonpath(job_list, f'$..{field}')
                            if ids and i < len(ids) and ids[i]:
                                job_ids = ids
                                break

                        if i < len(job_ids) and job_ids[i]:
                            job_url = f"https://www.zhipin.com/job_detail/{job_ids[i]}.html"

                        # 入库
                        try:
                            doc_id = ingest_boss_job_data(
                                title=title,
                                company=company,
                                raw_text=raw_text,
                                source_url=job_url or f"boss_search:{keyword}:{i}",
                                quality_flags=["api_direct"],
                                season=season or "unknown"
                            )
                            doc_ids.append(doc_id)
                            print(f"职位入库: {title[:30]} -> {doc_id}")
                        except Exception as e:
                            print(f"职位入库失败: {title[:30]}, 错误: {e}")

                except Exception as e:
                    print(f"处理API数据出错: {e}")
                    continue

            # 如果不是最后一页，尝试翻页
            if page_num < max_pages - 1:
                try:
                    # 查找下一页按钮
                    next_page_num = page_num + 2
                    next_btn = page.ele(f'text={next_page_num}')
                    if next_btn:
                        next_btn.click()
                        time.sleep(3)
                    else:
                        # 尝试其他分页方式
                        page_btns = page.eles('xpath=//a[contains(@class, "page")]')
                        for btn in page_btns:
                            if btn.text == str(next_page_num):
                                btn.click()
                                time.sleep(3)
                                break
                except Exception as e:
                    print(f"翻页失败: {e}")
                    break

        print(f"总共处理 {len(doc_ids)} 个职位")
        return doc_ids

    except Exception as e:
        print(f"直接API搜索过程中发生错误: {e}")
        return []
    finally:
        # 关闭浏览器
        try:
            page.quit()
        except:
            pass


def extract_job_data_from_api_response(response_body: dict) -> List[Dict[str, Any]]:
    """从API响应中提取职位结构化数据

    Args:
        response_body: API响应数据

    Returns:
        职位数据列表
    """
    job_data_list = []

    if not JSONPATH_AVAILABLE:
        print("警告: jsonpath不可用，无法提取结构化数据")
        return []

    # 筛选目标数据
    job_list = jsonpath(response_body, '$..jobList')
    if not job_list:
        print("未找到jobList字段")
        return []

    # 提取各字段值（原生jsonpath语法）
    job_names = jsonpath(job_list, '$..jobName') or []  # 工作名称
    salary_desc = jsonpath(job_list, '$..salaryDesc') or []  # 薪资待遇
    job_degrees = jsonpath(job_list, '$..jobDegree') or []  # 学历要求
    job_experiences = jsonpath(job_list, '$..jobExperience') or []  # 全职年限
    intern_days = jsonpath(job_list, '$..daysPerWeekDesc') or []  # 实习出勤
    intern_months = jsonpath(job_list, '$..leastMonthDesc') or []  # 实习时长
    brand_names = jsonpath(job_list, '$..brandName') or []  # 企业名称
    city_names = jsonpath(job_list, '$..cityName') or []  # 城市
    districts = jsonpath(job_list, '$..areaDistrict') or []  # 行政区
    business_districts = jsonpath(job_list, '$..businessDistrict') or []  # 商圈

    # 提取职位ID用于构建详情页URL
    job_ids = []
    # 尝试多种可能的字段名
    id_fields = ['encryptJobId', 'jobId', 'encrypt_job_id', 'lid']
    for field in id_fields:
        ids = jsonpath(job_list, f'$..{field}')
        if ids:
            job_ids = ids
            break

    total = len(job_names) if job_names else 0
    if total == 0:
        print("未提取到职位数据")
        return []

    print(f"从API提取到 {total} 个职位数据")

    for i in range(total):
        # 构建职位详情页URL
        job_url = None
        if i < len(job_ids) and job_ids[i]:
            job_url = f"https://www.zhipin.com/job_detail/{job_ids[i]}.html"

        # 区分全职/实习要求
        experience = ""
        if i < len(job_experiences) and job_experiences[i]:
            experience = f"全职，要求{job_experiences[i]}"
        elif (i < len(intern_days) and intern_days[i] and
              i < len(intern_months) and intern_months[i]):
            experience = f"实习，{intern_days[i]}，{intern_months[i]}"
        else:
            experience = "无明确要求"

        # 拼接完整地址
        address = ""
        if (i < len(city_names) and city_names[i] and
            i < len(districts) and districts[i] and
            i < len(business_districts) and business_districts[i]):
            address = f"{city_names[i]}-{districts[i]}-{business_districts[i]}"
        elif i < len(city_names) and city_names[i]:
            address = city_names[i]

        job_data = {
            "title": job_names[i] if i < len(job_names) else "未知职位",
            "salary": salary_desc[i] if i < len(salary_desc) else "面议",
            "degree": job_degrees[i] if i < len(job_degrees) else "学历不限",
            "experience": experience,
            "company": brand_names[i] if i < len(brand_names) else "未知公司",
            "city": city_names[i] if i < len(city_names) else "未知城市",
            "district": districts[i] if i < len(districts) else "",
            "business_district": business_districts[i] if i < len(business_districts) else "",
            "address": address,
            "job_url": job_url,
            # 原始字段用于调试
            "raw_experience": job_experiences[i] if i < len(job_experiences) else "",
            "raw_intern_days": intern_days[i] if i < len(intern_days) else "",
            "raw_intern_months": intern_months[i] if i < len(intern_months) else "",
        }

        job_data_list.append(job_data)

    return job_data_list


def fetch_and_process_boss_jobs_by_search(config: BossSearchConfig, season: str | None = None) -> List[str]:
    """根据搜索配置获取职位并直接处理入库（使用浏览器自动化）

    Args:
        config: 搜索配置
        season: 招聘季，默认为None（使用unknown）

    Returns:
        文档ID列表
    """
    from campus_rag.ingest import ingest_boss_job_data

    all_doc_ids = []

    for keyword in config.keywords:
        print(f"\n搜索并处理关键词: {keyword}")

        # 创建浏览器实例（每个关键词一个浏览器会话，避免页面状态混乱）
        options = ChromiumOptions()
        options.set_argument('--disable-blink-features=AutomationControlled')
        options.set_argument('--disable-infobars')
        options.set_argument('--no-sandbox')
        options.set_argument('--disable-dev-shm-usage')
        options.set_argument('--remote-debugging-port=0')  # 0表示随机端口

        page = ChromiumPage(addr_or_opts=options)

        try:
            # 登录
            if not boos_login(page, use_cookies=True):
                print(f"关键词 {keyword} 登录失败，跳过")
                continue

            # 搜索并获取职位URL
            urls = search_boss_jobs_data(
                keyword=keyword,
                city=config.city,
                max_pages=config.max_pages,
                max_jobs=config.max_jobs_per_keyword
            )

            print(f"找到 {len(urls)} 个职位，开始处理详情页")

            # 处理每个职位
            for url in urls:
                try:
                    # 使用浏览器获取详情页数据
                    job_data = fetch_boss_job_with_browser(page, url)

                    if not job_data['raw_text']:
                        print(f"职位 {url} 未提取到文本，跳过")
                        continue

                    # 入库
                    doc_id = ingest_boss_job_data(
                        title=job_data['title'],
                        company=job_data['company'],
                        raw_text=job_data['raw_text'],
                        source_url=url,
                        quality_flags=job_data['quality_flags'],
                        season=season
                    )

                    print(f"职位处理完成: {job_data['title'][:50]} -> {doc_id}")
                    all_doc_ids.append(doc_id)

                except Exception as e:
                    print(f"处理职位 {url} 失败: {e}")
                    continue

            # 关键词间延迟
            if len(config.keywords) > 1:
                sleep_delay()

        except Exception as e:
            print(f"处理关键词 {keyword} 时出错: {e}")
        finally:
            # 关闭浏览器
            try:
                page.quit()
            except:
                pass

    return all_doc_ids


# 使用示例和测试
if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Boss直聘自动化爬虫")
    parser.add_argument("--setup", action="store_true", help="设置登录cookies")
    parser.add_argument("--test", action="store_true", help="测试搜索功能")
    parser.add_argument("--keyword", type=str, default="python", help="搜索关键词")
    parser.add_argument("--city", type=str, default="北京", help="城市")
    parser.add_argument("--pages", type=int, default=1, help="页数")

    args = parser.parse_args()

    if args.setup:
        print("=== Boss直聘登录设置 ===")
        print("请按以下步骤操作：")
        print("1. 确保已安装 Chrome/Chromium 浏览器")
        print("2. 浏览器打开后，手动登录 Boss 直聘")
        print("3. 登录成功后，cookies 会自动保存")
        print("======================")
        success = manual_login_and_save_cookies()
        if success:
            print("设置完成！下次运行将自动使用保存的cookies")
        else:
            print("设置失败，请重试")
        sys.exit(0)

    elif args.test:
        print(f"测试搜索: {args.keyword} in {args.city}")
        urls = search_boss_jobs_data(
            keyword=args.keyword,
            city=args.city,
            max_pages=args.pages,
            max_jobs=5
        )
        print(f"找到 {len(urls)} 个职位链接:")
        for url in urls:
            print(f"  - {url}")

    else:
        print("使用方法:")
        print("  python boss_auto.py --setup          # 设置登录cookies")
        print("  python boss_auto.py --test           # 测试搜索功能")
        print("  python boss_auto.py --test --keyword java --city 上海 --pages 2")