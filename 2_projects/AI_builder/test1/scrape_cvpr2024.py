#!/usr/bin/env python3
"""CVPR 2024 论文数据抓取脚本
从 https://openaccess.thecvf.com/CVPR2024?day=all 抓取论文信息
包括：标题、作者、摘要、PDF链接、补充材料链接
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://openaccess.thecvf.com"
LIST_URL = f"{BASE_URL}/CVPR2024?day=all"
OUTPUT_FILE = "cvpr2024_papers.json"
SUMMARY_FILE = "cvpr2024_summary.md"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch_page(url, retries=3):
    """带重试的页面获取"""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            print(f"  [重试 {attempt+1}/{retries}] {url}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"获取页面失败: {url}")


def parse_list_page(html):
    """解析列表页，提取所有论文的基本信息"""
    soup = BeautifulSoup(html, "html.parser")
    papers = []

    dt_tags = soup.find_all("dt", class_="ptitle")
    print(f"共发现 {len(dt_tags)} 篇论文", file=sys.stderr)

    for dt in dt_tags:
        try:
            # 标题和详情页链接
            title_link = dt.find("a")
            if not title_link:
                continue
            title = title_link.get_text(strip=True)
            detail_url = urljoin(BASE_URL, title_link["href"])

            # 每篇论文有两个 dd: 第一个含作者表单，第二个含链接
            dd_list = dt.find_all_next("dd", limit=2)
            if len(dd_list) < 2:
                continue
            dd_authors = dd_list[0]  # 含作者
            dd_links = dd_list[1]    # 含 PDF/supp 链接

            # 收集作者
            authors = []
            for form_tag in dd_authors.find_all("form", class_="authsearch"):
                author_link = form_tag.find("a")
                if author_link:
                    authors.append(author_link.get_text(strip=True))
                else:
                    author_input = form_tag.find("input", attrs={"name": "query_author"})
                    if author_input:
                        authors.append(author_input["value"])

            # 提取 PDF / supp / arXiv 链接
            pdf_link = None
            supp_link = None
            arxiv_link = None
            for a_tag in dd_links.find_all("a"):
                href = a_tag.get("href", "")
                text = a_tag.get_text(strip=True)
                if text == "pdf" and href.endswith(".pdf"):
                    pdf_link = urljoin(BASE_URL, href)
                elif text == "supp" and ("supp" in href.lower() or href.endswith(".zip")):
                    supp_link = urljoin(BASE_URL, href)
                elif text == "arXiv" and "arxiv" in href.lower():
                    arxiv_link = href

            papers.append({
                "title": title,
                "authors": authors,
                "pdf_url": pdf_link,
                "supp_url": supp_link,
                "arxiv_url": arxiv_link,
                "detail_url": detail_url,
                "abstract": None,  # 待填充
            })
        except Exception as e:
            print(f"  解析条目出错: {e}", file=sys.stderr)
            continue

    return papers


def fetch_abstract(detail_url, retries=2):
    """获取论文详情页的摘要"""
    for attempt in range(retries):
        try:
            html = fetch_page(detail_url, retries=2)
            # 用正则提取摘要（比 BeautifulSoup 更快）
            match = re.search(
                r'<div id="abstract">\s*(.*?)\s*</div>',
                html, re.DOTALL
            )
            if match:
                abstract = match.group(1)
                # 清理 HTML 标签
                abstract = re.sub(r"<[^>]+>", "", abstract)
                abstract = re.sub(r"\s+", " ", abstract).strip()
                return abstract
            return ""
        except Exception as e:
            print(f"    获取摘要失败: {e}", file=sys.stderr)
            time.sleep(1)
    return ""


def fetch_all_abstracts(papers, max_workers=10):
    """并行获取所有论文的摘要"""
    total = len(papers)
    print(f"\n开始并行获取 {total} 篇论文摘要 (并发数={max_workers})...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(fetch_abstract, p["detail_url"]): i
            for i, p in enumerate(papers)
        }

        done_count = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                papers[idx]["abstract"] = future.result()
            except Exception as e:
                print(f"  论文 #{idx} 摘要获取失败: {e}", file=sys.stderr)

            done_count += 1
            if done_count % 100 == 0 or done_count == total:
                pct = done_count / total * 100
                print(f"  进度: {done_count}/{total} ({pct:.1f}%)", file=sys.stderr)

    return papers


def generate_summary(papers, output_path):
    """生成 Markdown 摘要文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# CVPR 2024 论文汇总\n\n")
        f.write(f"总论文数: {len(papers)}\n\n")

        for i, p in enumerate(papers, 1):
            f.write(f"## {i}. {p['title']}\n\n")
            f.write(f"- **作者**: {', '.join(p['authors'])}\n")
            f.write(f"- **PDF**: [{p['pdf_url']}]({p['pdf_url']})\n")
            if p.get("supp_url"):
                f.write(f"- **补充材料**: [{p['supp_url']}]({p['supp_url']})\n")
            if p.get("arxiv_url"):
                f.write(f"- **arXiv**: [{p['arxiv_url']}]({p['arxiv_url']})\n")
            if p.get("abstract"):
                f.write(f"- **摘要**: {p['abstract']}\n")
            f.write("\n---\n\n")

    print(f"摘要文件已生成: {output_path}", file=sys.stderr)


def main():
    print("=" * 60, file=sys.stderr)
    print("CVPR 2024 论文抓取工具", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # Step 1: 获取列表页
    print("\n[1/3] 获取列表页...", file=sys.stderr)
    list_html = fetch_page(LIST_URL)
    print("  完成", file=sys.stderr)

    # Step 2: 解析列表页
    print("\n[2/3] 解析列表页...", file=sys.stderr)
    papers = parse_list_page(list_html)
    print(f"  解析完成，共 {len(papers)} 篇论文", file=sys.stderr)

    # Step 3: 获取摘要（启用并行）
    print(f"\n[3/3] 获取 {len(papers)} 篇论文摘要（并行10线程）...", file=sys.stderr)
    papers = fetch_all_abstracts(papers, max_workers=10)
    print("  摘要获取完成", file=sys.stderr)

    # 保存 JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
    print(f"\n数据已保存至: {OUTPUT_FILE}", file=sys.stderr)

    # 生成摘要 Markdown
    generate_summary(papers, SUMMARY_FILE)

    # 输出统计
    with_abstract = sum(1 for p in papers if p.get("abstract"))
    with_pdf = sum(1 for p in papers if p.get("pdf_url"))
    with_supp = sum(1 for p in papers if p.get("supp_url"))
    print(f"\n统计:", file=sys.stderr)
    print(f"  总论文数: {len(papers)}", file=sys.stderr)
    print(f"  含 PDF: {with_pdf}", file=sys.stderr)
    print(f"  含补充材料: {with_supp}", file=sys.stderr)
    print(f"  含摘要: {with_abstract}", file=sys.stderr)
    print(f"  摘要文件: {SUMMARY_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
