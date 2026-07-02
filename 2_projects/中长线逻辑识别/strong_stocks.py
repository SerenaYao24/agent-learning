#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强势标的检测：检查所有自选股近 10 个交易日涨超 5% 的天数 ≥2 次，
且近 5 个交易日涨超 5% 的天数 ≥1 次，则记录到 strong_stocks.json，否则从列表中移除。

性能优化：优先读取 stock_trend_analysis.py 生成的 stock_data.json 缓存，
仅在缓存缺失或过期时调用 API，减少 90%+ 的重复 API 请求。

输出：strong_stocks.json（按 first_date 降序排列）
"""

import sys, os, json, time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

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
CACHE_DIR = os.path.join(PROJECT_DIR, ".stock_cache")
CACHE_FILE = os.path.join(CACHE_DIR, "stock_data.json")


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


def get_changes_from_cache(stock_info: dict, name_cache: dict) -> dict:
    """
    从 stock_data.json 缓存中读取每日涨跌幅，减少 API 调用。
    返回 { stock_name: { recent_10, recent_5, five_day_return } }，
    缓存中找不到的返回 None。
    """
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            pass

    result = {}
    miss_list = []  # 缓存缺失或过期的股票名
    today = datetime.now().strftime("%Y-%m-%d")

    for stock_name, info in stock_info.items():
        code = name_cache.get(stock_name)
        if not code:
            result[stock_name] = None
            continue

        cached = cache.get(code)
        if not cached:
            miss_list.append((stock_name, code, info))
            continue

        dates = cached.get("dates", [])
        changes = cached.get("daily_changes", [])
        closes = cached.get("close", [])

        if len(changes) < 5 or len(dates) < 5:
            miss_list.append((stock_name, code, info))
            continue

        # 判断缓存是否包含最近交易日数据（最后日期距今 ≤2 天 或 日期在今天）
        last_date = dates[-1] if dates else ""
        try:
            last_dt = datetime.strptime(last_date, "%Y-%m-%d")
            days_diff = (datetime.now() - last_dt).days
            if days_diff > 2 and datetime.now().weekday() < 5:
                # 缓存太旧且今天是交易日，需要更新
                miss_list.append((stock_name, code, info))
                continue
        except ValueError:
            miss_list.append((stock_name, code, info))
            continue

        # 从缓存中提取所需数据
        recent_10 = changes[-10:] if len(changes) >= 10 else changes
        recent_5 = changes[-5:] if len(changes) >= 5 else changes

        # 5 日累计涨幅 = (最后收盘价 / 6 天前收盘价 - 1) * 100
        five_day_return = 0.0
        if len(closes) >= 6:
            five_day_return = (closes[-1] / closes[-6] - 1) * 100
        elif len(closes) >= 2:
            five_day_return = (closes[-1] / closes[0] - 1) * 100

        result[stock_name] = {
            "recent_10": recent_10,
            "recent_5": recent_5,
            "five_day_return": five_day_return,
        }

    if miss_list:
        print(f"  ⚡ {len(miss_list)} 只需补充获取数据...")
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        api_lock = threading.Lock()

        def fetch_stock(name, code, info):
            try:
                df = ak.stock_zh_a_hist_tx(
                    symbol=code, adjust="qfq",
                    start_date=start_date, end_date=end_date,
                )
                if df is None or df.empty:
                    return name, None
                changes = df["close"].pct_change().fillna(0).mul(100).round(2)
                recent_10 = changes.tail(10).tolist()
                recent_5 = changes.tail(5).tolist()
                closes = df["close"].tolist()
                five_day_return = 0.0
                if len(closes) >= 6:
                    five_day_return = (closes[-1] / closes[-6] - 1) * 100
                return name, {
                    "recent_10": recent_10,
                    "recent_5": recent_5,
                    "five_day_return": five_day_return,
                }
            except Exception as e:
                return name, None

        max_workers = min(10, len(miss_list))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_stock, n, c, i): n for n, c, i in miss_list}
            for future in as_completed(futures):
                name, data = future.result()
                with api_lock:
                    result[name] = data

    return result


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

    # 4. 获取行情数据（优先从 stock_data.json 缓存读取）
    print("  获取涨跌幅数据（优先使用缓存）...", flush=True)
    t0 = time.time()
    stock_data = get_changes_from_cache(stock_info, name_cache)
    elapsed = time.time() - t0
    cached_count = sum(1 for v in stock_data.values() if v is not None)
    missed_count = sum(1 for v in stock_data.values() if v is None)
    print(f"  缓存命中: {cached_count}, 补充拉取: {missed_count}, 耗时: {elapsed:.0f}s")

    # 5. 检测强势标的
    today_str = datetime.now().strftime("%Y-%m-%d")
    new_strong = []
    checked = errors = 0

    for stock_name, data in stock_data.items():
        if data is None:
            errors += 1
            continue

        checked += 1
        recent_10 = data["recent_10"]
        recent_5 = data["recent_5"]
        five_day_return = data["five_day_return"]

        strong_count_10 = sum(1 for c in recent_10 if c >= 5)
        strong_count_5 = sum(1 for c in recent_5 if c >= 5)

        if strong_count_10 >= 2 and strong_count_5 >= 1 and five_day_return > 5:
            info = stock_info.get(stock_name, {})
            code = name_cache.get(stock_name, "")
            if stock_name in existing_map:
                entry = existing_map[stock_name]
                entry["sector"] = info.get("sector", "")
                entry["note"] = info.get("note", "")
                new_strong.append(entry)
            else:
                new_strong.append({
                    "name": stock_name,
                    "code": code.replace("sh", "").replace("sz", ""),
                    "first_date": today_str,
                    "sector": info.get("sector", ""),
                    "note": info.get("note", ""),
                })

    # 6. 按 first_date 降序排列
    new_strong.sort(key=lambda x: x.get("first_date", ""), reverse=True)

    save_strong_stocks(new_strong)

    # 7. 汇总
    added = sum(1 for s in new_strong if s["name"] not in existing_map)
    kept = len(new_strong) - added
    removed = len(existing) - kept

    print(f"\n  检查: {checked} 只, 失败: {errors}")
    print(f"  新增: {added}, 保留: {kept}, 移除: {removed}")
    print(f"  当前强势标的: {len(new_strong)} 只")
    print("  ✅ 完成\n")


if __name__ == "__main__":
    main()
