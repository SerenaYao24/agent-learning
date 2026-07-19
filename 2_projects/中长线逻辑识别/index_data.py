#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大盘指数 + ETF 数据获取：日线 OHLCV + 分钟级分时
指数分钟线: 腾讯 m1（交易日当天完整）
ETF 分钟线: 腾讯 m5
数据来源: Sina(指数日线) + 腾讯(分钟线+ETF日线)

用法:
    python index_data.py                 # 默认获取最新交易日数据
    python index_data.py --no-minute     # 跳过分钟线
    python index_data.py --no-etf        # 跳过 ETF
    python index_data.py --backfill      # 重建所有指数日线历史数据
    python index_data.py --json          # JSON 输出
"""

import argparse, csv, json, os, re, sys, time
from datetime import datetime
import requests
import akshare as ak

os.environ['TQDM_DISABLE'] = '1'

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(DATA_DIR, ".index_data")
os.makedirs(OUT_DIR, exist_ok=True)

# 指数配置: 名称, Sina代码, 腾讯分钟线代码
INDICES = [
    ("上证指数", "sh000001", "sh000001"),
    ("深证成指", "sz399001", "sz399001"),
    ("创业板指", "sz399006", "sz399006"),
    ("科创综指", "sh000680", "sh000680"),
]

# ETF 配置: 名称, 腾讯代码
ETFS = [
    ("5GETF", "sh515050"),
    ("中证500ETF", "sh510500"),
    ("沪深300ETF", "sh510300"),
    ("半导体设备ETF", "sz159516"),
    ("上证50ETF", "sh510050"),
    ("科创芯片ETF", "sh588200"),
    ("创业板ETF", "sz159915"),
]


# ======== 指数日线（全量历史） ========

def get_daily_sina_all(sina_code):
    """Sina 指数日线: 返回 2026年全量历史。成交额: 沪市用 stock_zh_a_daily 真实数据, 深市用当日成交额/成交量比例校准"""
    try:
        df = ak.stock_zh_index_daily(symbol=sina_code)
        if df is None or len(df) == 0:
            return []
        df = df[df['date'].astype(str) >= '2026-01-01']
        # 尝试从 stock_zh_a_daily 获取全量成交额 (仅上海交易所代码有效)
        amt_map = {}
        try:
            df2 = ak.stock_zh_a_daily(symbol=sina_code, start_date="20260101", end_date="20261231", adjust="")
            for _, r2 in df2.iterrows():
                amt_map[str(r2['date'])] = float(r2['amount'])
        except Exception:
            pass
        # 深市: 用当日 Sina 实时成交额/成交量 作为历史校准比例
        ratio = 0
        if not amt_map:
            latest_amt = _get_amount_sina(sina_code)
            if latest_amt:
                latest_vol = float(df.iloc[-1]['volume'])
                if latest_vol > 0:
                    ratio = latest_amt / latest_vol
        result = []
        for _, row in df.iterrows():
            o, c, h, l = float(row['open']), float(row['close']), float(row['high']), float(row['low'])
            vol = float(row['volume'])
            date_str = str(row['date'])
            amt = amt_map.get(date_str, 0)
            if amt == 0 and vol > 0 and ratio > 0:
                amt = vol * ratio
            result.append({
                "日期": date_str,
                "开盘": o, "收盘": c, "最高": h, "最低": l,
                "成交量": vol, "成交量_亿手": round(vol / 1e10, 2),
                "成交额": amt, "成交额_亿": round(amt / 1e8, 1) if amt else 0,
            })
        return result
    except Exception:
        return []


def _get_amount_sina(sina_code):
    """Sina 实时成交额(万元) → 元"""
    try:
        url = f"http://hq.sinajs.cn/list=s_{sina_code}"
        r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
        val = r.text.split('"')[1] if '"' in r.text else ""
        parts = val.split(",")
        if len(parts) >= 6:
            return float(parts[5]) * 10000
    except Exception:
        pass
    return None


# ======== ETF 日线 ========

def get_daily_etf(code):
    """ETF 日线: 腾讯 fqkline 接口"""
    try:
        url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,320,qfq"
        data = json.loads(requests.get(url, timeout=10).text)
        sub = data["data"][code]
        klines = sub.get("qfqday") or sub.get("day") or []
        if not klines:
            return None
        row = klines[-1]
        o, c, h, l = float(row[1]), float(row[2]), float(row[3]), float(row[4])
        vol = float(row[5])
        amt = vol * 100 * (o + c + h + l) / 4  # 成交额估算
        return {
            "日期": row[0],
            "开盘": o, "收盘": c, "最高": h, "最低": l,
            "成交量": vol, "成交量_万手": round(vol / 10000, 2),
            "成交额": amt, "成交额_亿": round(amt / 1e8, 2),
        }
    except Exception:
        return None


# ======== 市场宽度 ========

def get_market_breadth():
    """上涨/下跌/涨停/跌停家数: 短线侠 qxlive 页面 (Playwright 动态渲染)"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page()
            page.goto("https://duanxianxia.com/web/qxlive", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(5000)  # 等 JS 渲染数据
            text = page.evaluate("() => document.body.innerText")
            b.close()

        patterns = {
            "上涨": r'上涨家数[：:]\s*(\d+)',
            "下跌": r'下跌家数[：:]\s*(\d+)',
            "涨停": r'涨停家数[：:]\s*(\d+)',
            "跌停": r'跌停家数[：:]\s*(\d+)',
        }
        result = {}
        for key, pat in patterns.items():
            m = re.search(pat, text)
            if m:
                result[key] = int(m[1])
        # 日期从页面提取
        date_m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
        if date_m:
            result["日期"] = date_m[1]
        return result if result else None
    except Exception:
        return None


# ======== 分钟线 ========

def get_minute_mkline(code, period="m5", date_str=""):
    """腾讯 mkline 分钟线（m5/m1，含历史）
    row format: [time, open, close, high, low, volume, ...]"""
    try:
        url = f"http://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},{period},,2026-05-10"
        data = json.loads(requests.get(url, timeout=10).text)
        klines = data["data"][code][period]
        target = date_str.replace("-", "") if date_str else ""
        result = []
        for row in klines:
            dt = row[0]
            if target and dt[:8] != target:
                continue
            vol_shou = float(row[5])
            result.append({
                "time": f"{dt[8:10]}:{dt[10:12]}",
                "date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}",
                "open": float(row[1]),
                "price": float(row[2]),
                "volume_shou": vol_shou,
                "amount_yi": round(vol_shou * 100 * float(row[2]) / 1e8, 2),
            })
        return result
    except Exception:
        return []


def get_minute_index(tx_code):
    """指数分钟线: 腾讯 m1（交易日当天完整）"""
    url = f"http://ifzq.gtimg.cn/appstock/app/minute/query?_var=min_data&code={tx_code}"
    try:
        r = requests.get(url, timeout=15)
        text = r.text.strip()
        if text.startswith("min_data="):
            text = text[9:]
        data = json.loads(text)
        if data.get("code") != 0:
            return []
        qt = data.get("data", {}).get(tx_code, {})
        raw = qt.get("data", {}).get("data", [])
        if not raw:
            return []
        result = []
        for row in raw:
            parts = row.split()
            if len(parts) < 4:
                continue
            t = parts[0]
            result.append({
                "time": f"{t[:2]}:{t[2:]}",
                "price": float(parts[1]),
                "amount_yi": round(float(parts[3]) / 1e8, 2),
            })
        return result
    except Exception:
        return []


def get_minute_etf(code, date_str=""):
    """ETF 分钟线: 腾讯 mkline m5"""
    return get_minute_mkline(code, "m5", date_str)


# ======== 输出 ========

INDEX_DAILY_FIELDS = ["指数", "日期", "开盘", "收盘", "最高", "最低", "成交量", "成交量_亿手", "成交额", "成交额_亿"]

ETF_DAILY_FIELDS = ["名称", "代码", "日期", "开盘", "收盘", "最高", "最低", "成交量", "成交量_万手", "成交额", "成交额_亿"]


def save_etf_daily(name, code, row):
    """保存 ETF 日线: 追加到日期文件，已存在则跳过"""
    fd = row["日期"]
    path = os.path.join(OUT_DIR, f"etf_daily_{fd}.csv")
    out = {k: row.get(k, "") for k in ETF_DAILY_FIELDS if k not in ("名称", "代码")}
    out["名称"] = name
    out["代码"] = code
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as fh:
            for existing in csv.DictReader(fh):
                if existing.get("名称") == name and existing.get("日期") == fd:
                    return
    _append_row(path, out, ETF_DAILY_FIELDS)


def save_index_daily_all(name, rows):
    """保存指数全量日线: 每条追加到各自日期文件，已存在则跳过"""
    for r in rows:
        rd = r["日期"]
        path = os.path.join(OUT_DIR, f"index_daily_{rd}.csv")
        row = {k: r.get(k, "") for k in INDEX_DAILY_FIELDS if k != "指数"}
        row["指数"] = name
        # 检查是否已有该(指数,日期)行
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as fh:
                for existing in csv.DictReader(fh):
                    if existing.get("指数") == name and existing.get("日期") == rd:
                        break
                else:
                    _append_row(path, row)
        else:
            _append_row(path, row)
    return len(rows)


def _append_row(path, row, fieldnames=None):
    if fieldnames is None:
        fieldnames = INDEX_DAILY_FIELDS
    file_exists = os.path.exists(path)
    with open(path, "a" if file_exists else "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        if not file_exists:
            w.writeheader()
        w.writerow(row)


MINUTE_FIELDS = ["time", "open", "price", "volume_shou", "amount_yi"]


def save_minute_data(name, minutes, date_str, prefix, suffix=""):
    """保存分钟线 CSV"""
    safe = name.replace("/", "_")
    path = os.path.join(OUT_DIR, f"{prefix}_minute{suffix}_{safe}_{date_str}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MINUTE_FIELDS, extrasaction='ignore')
        w.writeheader()
        w.writerows(minutes)


def save_market_breadth(data, date_str):
    """保存涨跌家数"""
    if not data:
        return
    path = os.path.join(OUT_DIR, f"breadth_{date_str}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["指标", "数值"])
        w.writeheader()
        for k, v in data.items():
            w.writerow({"指标": k, "数值": v})


def print_summary(all_data):
    print(f"\n{'='*90}")
    print(f" 数据概览")
    print(f"{'='*90}")
    hdr = f"{'名称':<14}{'日期':<12}{'开盘':>10}{'收盘':>10}{'最高':>10}{'最低':>10}{'成交额(亿)':>12}"
    print(hdr)
    print("-" * 90)
    for item in all_data:
        d = item.get("daily")
        if not d:
            continue
        if isinstance(d, list):
            if len(d) == 0:
                continue
            d = d[-1]
        amt = d.get("成交额_亿", "-")
        print(f"{item['name']:<14}{d.get('日期',''):<12}{d.get('开盘',''):>10.2f}{d.get('收盘',''):>10.2f}"
              f"{d.get('最高',''):>10.2f}{d.get('最低',''):>10.2f}{str(amt):>12}")

    for item in all_data:
        m = item.get("minutes", [])
        if m:
            highs = [x["price"] for x in m]
            print(f"\n  {item['name']} 分时: {len(m)}条 "
                  f"高{max(highs):.2f} 低{min(highs):.2f}")


# ======== 主入口 ========

def main():
    p = argparse.ArgumentParser(description="大盘指数 + ETF 数据获取")
    p.add_argument("--no-minute", action="store_true", help="跳过分钟线")
    p.add_argument("--no-etf", action="store_true", help="跳过 ETF")
    p.add_argument("--backfill", action="store_true", help="重建所有指数日线历史（先清旧文件）")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    args = p.parse_args()

    t0 = time.time()
    all_data = []

    # === Backfill 模式: 清旧 index_daily 再重建 ===
    if args.backfill:
        for f in os.listdir(OUT_DIR):
            if f.startswith("index_daily_"):
                os.remove(os.path.join(OUT_DIR, f))
        print("已清除旧 index_daily 文件，开始重建...")

    # === 指数 ===
    for name, sina_code, tx_code in INDICES:
        print(f"\n[{name}]")
        rows = get_daily_sina_all(sina_code)
        if rows:
            latest = rows[-1]
            if args.backfill:
                print(f"  {len(rows)}条 ({rows[0]['日期']}~{latest['日期']}) "
                      f"C={latest['收盘']:.2f} Amt={latest.get('成交额_亿','?')}亿")
                save_index_daily_all(name, rows)
            else:
                print(f"  {latest['日期']} C={latest['收盘']:.2f} Amt={latest.get('成交额_亿','?')}亿")
                save_index_daily_all(name, [latest])
        else:
            print(f"  ❌ 日线获取失败")

        minutes_m1, minutes_m5 = [], []
        if not args.no_minute:
            minutes_m1 = get_minute_index(tx_code)
            if minutes_m1:
                print(f"  m1: {len(minutes_m1)}条 ({minutes_m1[0]['time']}~{minutes_m1[-1]['time']})")
                save_minute_data(name, minutes_m1, rows[-1]["日期"], "index")
            # 同时拉 m5（多日，与 ETF 一致）
            if rows:
                all_m5 = get_minute_mkline(tx_code, "m5")
                if all_m5:
                    print(f"  m5: {len(all_m5)}条")
                    m5_by_date = {}
                    for m in all_m5:
                        d = m.get("date", "")
                        if d:
                            m5_by_date.setdefault(d, []).append(m)
                    for d, pts in m5_by_date.items():
                        save_minute_data(name, pts, d, "index", "_m5")
                    minutes_m5 = m5_by_date.get(rows[-1]["日期"], [])

        all_data.append({"name": name, "daily": rows, "minutes": minutes_m1,
                         "minutes_m5": minutes_m5, "prefix": "index"})

    # 上涨/下跌家数
    breadth = get_market_breadth()
    if breadth:
        fallback_date = all_data[0]["daily"][-1]["日期"] if all_data[0]["daily"] else datetime.now().strftime("%Y-%m-%d")
        bd = breadth.pop("日期", fallback_date)
        save_market_breadth(breadth, bd)
        parts = "  ".join(f"{k}{v}" for k, v in breadth.items())
        print(f"\n[市场宽度 {bd}] {parts}")

    # === ETF ===
    if not args.no_etf:
        for name, code in ETFS:
            print(f"\n[{name}]")
            daily = get_daily_etf(code)
            if daily:
                print(f"  {daily['日期']} O={daily['开盘']:.3f} C={daily['收盘']:.3f} "
                      f"Vol={daily.get('成交量_万手','?')}万手")
                save_etf_daily(name, code, daily)
            else:
                print(f"  ❌ 日线获取失败")
            minutes = []
            if not args.no_minute and daily:
                minutes = get_minute_etf(code, daily["日期"])
                if minutes:
                    print(f"  m5: {len(minutes)}条 ({minutes[0]['time']}~{minutes[-1]['time']})")
                    save_minute_data(name, minutes, daily["日期"], "etf")
            all_data.append({"name": name, "daily": daily, "minutes": minutes, "prefix": "etf"})

    if not any(d.get("daily") for d in all_data):
        print("❌ 所有数据获取失败")
        sys.exit(1)

    if args.json:
        printable = [{"name": i["name"], "prefix": i["prefix"],
                       "daily_count": len(i["daily"]) if isinstance(i["daily"], list) else 1,
                       "minute_count": len(i.get("minutes", []))}
                     for i in all_data if i.get("daily")]
        print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
    else:
        print_summary(all_data)

    print(f"\n耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
