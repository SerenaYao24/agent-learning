#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强势标的检测：检查所有自选股近 10 个交易日涨超 5% 的次数，
≥2 次则记录到 strong_stocks.json，否则从列表中移除。

输出：strong_stocks.json（按 first_date 降序排列）
"""

import sys, os, json, time
from datetime import datetime, timedelta

# ── 禁用 tqdm 进度条 ──
import tqdm
tqdm.tqdm.disable = True
class DummyTqdm:
    def __init__(self, iterable=None, *args, **kwargs):
        self.iterable = iterable
        self.disable = True
    def __iter__(self):
        return iter(self.iterable or [])
    def __len__(self):
        return len(self.iterable or [])
    def update(self, n=1): pass
    def close(self): pass
    def set_description(self, desc=None): pass
    def set_postfix(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
tqdm.tqdm = DummyTqdm

import akshare as ak

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
INTEREST_PATH = os.path.join(PROJECT_DIR, "interest_stock.md")
STRONG_FILE = os.path.join(PROJECT_DIR, "strong_stocks.json")


def load_strong_stocks() -> list:
    """加载现有的强势标的数据"""
    if os.path.exists(STRONG_FILE):
        try:
            with open(STRONG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"  加载 strong_stocks.json 失败: {e}")
    return []


def save_strong_stocks(data: list):
    """保存强势标的到 JSON 文件"""
    with open(STRONG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_interest_stock() -> dict:
    """
    解析 interest_stock.md，返回 { 股票名称: { sector, note } }
    """
    from _topic_utils import parse_topic_header

    stocks = {}
    current_sector = ""

    with open(INTEREST_PATH, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("<!--"):
                continue
            if s.startswith("# "):
                topic_name, _ = parse_topic_header(s)
                current_sector = topic_name
                continue
            if not s.startswith("#"):
                # 提取括号前的股票名称
                name = s.split("（")[0].split("(")[0].strip()
                # 提取备注（括号内内容）
                note = s[len(name):].strip()
                if name:
                    stocks[name] = {"sector": current_sector, "note": note}

    return stocks


def main():
    print("=" * 50)
    print("  强势标的检测")
    print("=" * 50)

    # 1. 加载股票名称→代码映射
    print("  加载股票名称映射...", end=" ", flush=True)
    try:
        df = ak.stock_info_a_code_name()
    except Exception as e:
        print(f"❌ {e}")
        return

    name_cache = {}
    for _, row in df.iterrows():
        code = row["code"]
        name = row["name"].replace(" ", "")
        if code.startswith("6"):
            full_code = f"sh{code}"
        elif code.startswith(("0", "3")):
            full_code = f"sz{code}"
        else:
            full_code = code
        name_cache[name] = full_code
    print(f"{len(name_cache)} 个")

    # 2. 解析自选股
    print("  解析 interest_stock.md...", end=" ", flush=True)
    stock_info = parse_interest_stock()
    print(f"{len(stock_info)} 只标的")

    # 3. 加载现有强势标的
    existing = load_strong_stocks()
    existing_map = {s["name"]: s for s in existing}

    # 4. 获取行情数据
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    new_strong = []
    checked = errors = 0
    today_str = datetime.now().strftime("%Y-%m-%d")

    for stock_name, info in stock_info.items():
        code = name_cache.get(stock_name)
        if not code:
            errors += 1
            continue

        checked += 1
        try:
            df = ak.stock_zh_a_hist_tx(
                symbol=code, adjust="qfq",
                start_date=start_date, end_date=end_date,
            )
            if df is None or df.empty:
                errors += 1
                continue

            # 计算每日涨跌幅（%）
            changes = df["close"].pct_change().fillna(0).mul(100).round(2)
            recent = changes.tail(10).tolist()

            # 统计涨超 5% 的天数
            strong_count = sum(1 for c in recent if c >= 5)

            if strong_count >= 2:
                if stock_name in existing_map:
                    # 已在列表中，保留 first_date，更新备注
                    entry = existing_map[stock_name]
                    entry["sector"] = info["sector"]
                    entry["note"] = info["note"]
                    new_strong.append(entry)
                else:
                    # 新入选
                    new_strong.append({
                        "name": stock_name,
                        "code": code.replace("sh", "").replace("sz", ""),
                        "first_date": today_str,
                        "sector": info["sector"],
                        "note": info["note"],
                    })

        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  ⚠ {stock_name}: {e}")

        time.sleep(0.03)

    # 5. 按 first_date 降序排列
    new_strong.sort(key=lambda x: x.get("first_date", ""), reverse=True)

    save_strong_stocks(new_strong)

    # 6. 汇总
    added = sum(1 for s in new_strong if s["name"] not in existing_map)
    kept = len(new_strong) - added
    removed = len(existing) - kept

    print(f"\n  检查: {checked} 只, 失败: {errors}")
    print(f"  新增: {added}, 保留: {kept}, 移除: {removed}")
    print(f"  当前强势标的: {len(new_strong)} 只")
    print("  ✅ 完成\n")


if __name__ == "__main__":
    main()
