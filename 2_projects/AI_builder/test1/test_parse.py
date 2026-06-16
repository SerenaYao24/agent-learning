#!/usr/bin/env python3
"""快速测试：只解析前5篇论文"""
import sys
sys.path.insert(0, '/Users/heloise/Desktop/agent/2_projects/AI_builder/test1')
from scrape_cvpr2024 import fetch_page, parse_list_page

html = fetch_page("https://openaccess.thecvf.com/CVPR2024?day=all")
papers = parse_list_page(html)

print(f"解析到 {len(papers)} 篇论文")
print("\n前5篇论文样本:")
for i, p in enumerate(papers[:5], 1):
    print(f"\n--- 第{i}篇 ---")
    print(f"标题: {p['title']}")
    print(f"作者: {', '.join(p['authors'][:5])}{'...' if len(p['authors']) > 5 else ''}")
    print(f"PDF: {p['pdf_url']}")
    print(f"Supp: {p['supp_url']}")
    print(f"详情: {p['detail_url']}")

# 统计 PDF 链接个数
with_pdf = sum(1 for p in papers if p['pdf_url'])
print(f"\n有PDF链接的论文: {with_pdf}/{len(papers)}")

# 输出一个没有 PDF 链接的例子（如果有）
no_pdf = [p for p in papers if not p['pdf_url']]
if no_pdf:
    print(f"\n无PDF链接示例 (第1个):")
    print(f"  标题: {no_pdf[0]['title']}")

