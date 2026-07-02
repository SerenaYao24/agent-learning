#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从同花顺热榜 API 爬取热门个股 Top 100
数据来源: https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock

API 参数:
  - stock_type=a   (A股)
  - type=hour      (1小时热门) / type=day (24小时热门)
  - list_type=normal (大家都在看) / skyrocket (快速飙升)

提取字段: 排名(order)、标的名(name)、代码(code)、涨跌幅(rise_and_fall)、热度(rate)

用法:
    python3 scrape_ths_hot_rank.py                # 爬取热榜 Top 100
    python3 scrape_ths_hot_rank.py --json         # JSON 输出
    python3 scrape_ths_hot_rank.py --no-save      # 不保存
    python3 scrape_ths_hot_rank.py --risk-check   # 同时检查风险标签
"""

import argparse, json, os, sys, time, ssl
from datetime import datetime
import urllib.request
import urllib.error

# SSL 证书处理（macOS 常见问题）
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

if sys.version_info[0] < 3:
    sys.stderr.write("错误：请使用 python3 运行此脚本\n")
    sys.exit(1)

print("⚠️  请确保已关闭 VPN，否则爬虫可能无法正常工作\n")

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(DATA_DIR, ".index_data")
os.makedirs(OUT_DIR, exist_ok=True)

RISK_TAGS_FILE = os.path.join(DATA_DIR, "risk_tags.json")
HOT_RANK_DATA_FILE = os.path.join(OUT_DIR, "ths_hot_rank.json")

# 同花顺热榜 API
API_BASE = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://eq.10jqka.com.cn/frontend/thsTopRank/index.html",
    "Accept": "application/json",
}


def fetch_api(url):
    """发起 HTTP GET 请求并返回 JSON"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except urllib.error.HTTPError as e:
        print(f"  ⚠ HTTP 错误: {e.code} {e.reason}")
        return None
    except urllib.error.URLError as e:
        print(f"  ⚠ URL 错误: {e.reason}")
        return None
    except Exception as e:
        print(f"  ⚠ 请求失败: {e}")
        return None


def scrape_hot_rank(top_n=100):
    """从同花顺热榜 API 获取热门个股"""
    results = []
    
    # 获取1小时"大家都在看"热门股
    url = f"{API_BASE}?stock_type=a&type=hour&list_type=normal"
    print(f"  请求: {url}")
    
    data = fetch_api(url)
    if not data or data.get("status_code") != 0:
        print("  ⚠ API 返回异常，尝试 fallback...")
        # fallback: 24小时数据
        url = f"{API_BASE}?stock_type=a&type=day&list_type=normal"
        data = fetch_api(url)
    
    if not data or data.get("status_code") != 0:
        print("  ❌ API 请求失败")
        return []
    
    stock_list = data.get("data", {}).get("stock_list", [])
    print(f"  获取到 {len(stock_list)} 条数据")
    
    for item in stock_list[:top_n]:
        rise_fall = item.get("rise_and_fall")
        chg_pct = rise_fall if rise_fall is not None else 0.0
        
        # 格式化涨跌幅字符串
        if chg_pct >= 0:
            chg_str = f"+{chg_pct:.2f}%"
        else:
            chg_str = f"{chg_pct:.2f}%"
        
        # 格式化热度
        rate = float(item.get("rate", 0) or 0)
        if rate >= 10000:
            hot_str = f"{rate/10000:.1f}万热度"
        else:
            hot_str = f"{int(rate)}热度"
        
        entry = {
            "rank": item.get("order", 0),
            "name": item.get("name", ""),
            "code": item.get("code", ""),
            "market": item.get("market", 0),
            "chg": chg_str,
            "chg_pct": chg_pct,
            "hot": hot_str,
            "rate": rate,
            "hot_rank_chg": item.get("hot_rank_chg", 0),
            "concept_tag": (item.get("tag", {}) or {}).get("concept_tag", []),
            "popularity_tag": (item.get("tag", {}) or {}).get("popularity_tag", ""),
        }
        results.append(entry)
    
    return results


def check_risk_tag(stocks):
    """检查热榜风险：跌超-8% 超过5只 → 添加风险标签"""
    big_drop_count = 0
    big_drop_stocks = []
    
    for s in stocks:
        pct = s.get("chg_pct", 0.0)
        if pct < -8:
            big_drop_count += 1
            big_drop_stocks.append(f"{s['name']}({s['code']}) {s['chg']}")
    
    print(f"\n  热榜跌幅超-8%标的: {big_drop_count} 只")
    if big_drop_stocks:
        for ds in big_drop_stocks[:10]:
            print(f"    - {ds}")
    
    if big_drop_count > 5:
        tag = "热门股出现亏钱效应"
        print(f"\n  🔴 风险标签: {tag}")
        
        risk_data = {"manual": [], "deleted": []}
        if os.path.exists(RISK_TAGS_FILE):
            try:
                with open(RISK_TAGS_FILE, encoding="utf-8") as f:
                    risk_data = json.load(f)
            except Exception:
                pass
        
        if tag not in risk_data["manual"] and tag not in risk_data.get("deleted", []):
            risk_data["manual"].append(tag)
            with open(RISK_TAGS_FILE, "w", encoding="utf-8") as f:
                json.dump(risk_data, f, ensure_ascii=False, indent=2)
            print(f"  ✅ 已保存风险标签到 risk_tags.json")
        else:
            print(f"  ⚠ 标签已存在，跳过")
    else:
        print(f"\n  ✅ 未触发风险标签 (跌幅超-8%: {big_drop_count}只, 需>5只)")
    
    return big_drop_count


def save_data(stocks):
    """保存热榜数据到 JSON"""
    up_count = sum(1 for s in stocks if s.get("chg_pct", 0) > 0)
    down_count = sum(1 for s in stocks if s.get("chg_pct", 0) < 0)
    big_drop_count = sum(1 for s in stocks if s.get("chg_pct", 0) < -8)
    
    data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stocks": stocks,
        "total": len(stocks),
        "up_count": up_count,
        "down_count": down_count,
        "big_drop_count": big_drop_count,
    }
    
    with open(HOT_RANK_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"\n  已保存 {len(stocks)} 条热榜数据 → {HOT_RANK_DATA_FILE}")
    return data


def print_table(stocks, top_n=100):
    """打印热榜表格"""
    n = min(top_n, len(stocks))
    print(f"\n{'='*80}")
    print(f" 同花顺热榜 Top {n}  (共{len(stocks)}条)")
    print(f"{'='*80}")
    hdr = f"{'排名':<6}{'代码':<10}{'名称':<14}{'涨跌幅':<10}{'热度':<16}{'人气标签':<12}"
    print(hdr)
    print("-" * 80)
    for s in stocks[:n]:
        pop_tag = s.get("popularity_tag", "") or ""
        print(f"{s['rank']:<6}{s['code']:<10}{s['name']:<14}"
              f"{s['chg']:<10}{s['hot']:<16}{pop_tag:<12}")


def main():
    parser = argparse.ArgumentParser(description="爬取同花顺热榜 Top 100")
    parser.add_argument("--top", type=int, default=100, help="获取数量")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--no-save", action="store_true", help="不保存数据")
    parser.add_argument("--risk-check", action="store_true", help="检查风险标签")
    args = parser.parse_args()
    
    print(f"爬取同花顺热榜 Top {args.top}")
    t0 = time.time()
    
    stocks = scrape_hot_rank(args.top)
    elapsed = time.time() - t0
    
    if not stocks:
        print("❌ 未提取到有效数据")
        sys.exit(1)
    
    if args.json:
        print(json.dumps(stocks, ensure_ascii=False, indent=2))
    else:
        print_table(stocks, args.top)
    
    if not args.no_save:
        save_data(stocks)
    
    if args.risk_check:
        check_risk_tag(stocks)
    
    print(f"\n耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
