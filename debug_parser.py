#!/usr/bin/env python3
"""
调试解析器
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup

# 读取HTML文件
with open('debug_page.html', 'r', encoding='utf-8') as f:
    html = f.read()

print(f"HTML长度: {len(html)} 字符")

soup = BeautifulSoup(html, 'html.parser')

# 查找所有表格
tables = soup.find_all('table')
print(f"找到 {len(tables)} 个表格")

for i, table in enumerate(tables):
    # 获取表格的class属性
    classes = table.get('class', [])
    class_str = ' '.join(classes) if classes else '(无class)'

    # 查找表格内的行
    rows = table.find_all('tr')
    print(f"\n表格 {i+1}: class='{class_str}', 有 {len(rows)} 行")

    # 检查是否有jobli类的行
    jobli_rows = table.find_all('tr', class_='jobli')
    if jobli_rows:
        print(f"  包含 {len(jobli_rows)} 个jobli行")

        # 显示第一行的结构
        if jobli_rows:
            first_row = jobli_rows[0]
            cells = first_row.find_all('td')
            print(f"  第一行有 {len(cells)} 个单元格")
            for j, cell in enumerate(cells):
                text = cell.get_text(strip=True)
                print(f"    单元格 {j}: {text[:50]}...")

# 直接搜索jobli类
print("\n" + "="*60)
print("直接搜索tr.jobli:")
jobli_elements = soup.find_all('tr', class_='jobli')
print(f"找到 {len(jobli_elements)} 个tr.jobli元素")

if jobli_elements:
    first = jobli_elements[0]
    print("\n第一个tr.jobli的HTML:")
    print(first.prettify()[:500])

# 搜索jobul类
print("\n" + "="*60)
print("直接搜索table.jobul:")
jobul_tables = soup.find_all('table', class_='jobul')
print(f"找到 {len(jobul_tables)} 个table.jobul元素")

if jobul_tables:
    table = jobul_tables[0]
    rows = table.find_all('tr')
    print(f"表格有 {len(rows)} 行")

    jobli_in_table = table.find_all('tr', class_='jobli')
    print(f"其中 {len(jobli_in_table)} 行是jobli")