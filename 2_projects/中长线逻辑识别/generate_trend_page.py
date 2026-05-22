#!/usr/bin/env python3
"""
走势图页面生成器
从扩展缓存（含 OHLCV）读取数据，生成内嵌 ECharts K 线图 + 量能的单 HTML 文件

两种模式:
    A. 文件模式（默认）: 读标的列表文件，展示全部标的走势图
       python generate_trend_page.py -i 我的自选.md --days 20

    B. 题材模式: 输入题材名，结合分析报告展示该题材所有标的 + 判定结果
       python generate_trend_page.py --sector 光通信
"""

import json
import os
import re
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, date, timedelta

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".stock_cache"
STOCK_DATA_PATH = CACHE_DIR / "stock_data.json"
CODE_MAP_PATH = CACHE_DIR / "stock_code_map.json"
INTEREST_PATH = BASE_DIR / "interest_stock.md"
REPORT_DIR = CACHE_DIR / "reports"
DAILY_DIR = REPORT_DIR / "每日分析"
MULTI_DIR = REPORT_DIR / "多日分析"

DEFAULT_OUTPUT = BASE_DIR / "trend_view.html"
DEFAULT_DAYS = 20

ATTACK_LABELS = {"启动", "强势", "企稳复苏", "主升"}
DEFENSE_LABELS = {"冲高回落", "转跌", "震荡", "弱势下跌"}


# ── 工具函数 ──────────────────────────────────────────────

def clean_name(name: str) -> str:
    return re.sub(r"[（(].+?[）)]", "", name).strip()


# ── 报告日期校验 ──────────────────────────────────────────

def find_latest_report_date() -> str:
    """返回最新每日分析报告的日期（YYYY-MM-DD），不存在则退出"""
    if not DAILY_DIR.exists():
        print("❌ 每日分析报告目录不存在，请先运行 stock_trend_analysis.py")
        sys.exit(1)

    files = sorted(DAILY_DIR.glob("stock_trend_report_*_detail.md"))
    if not files:
        print("❌ 未找到任何每日分析报告")
        sys.exit(1)

    m = re.search(r"(\d{4}-\d{2}-\d{2})", files[-1].name)
    if not m:
        print("❌ 无法从文件名解析日期")
        sys.exit(1)

    report_date_str = m.group(1)
    report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()
    today = date.today()

    # 计算最大允许的报告延迟（工作日）
    if today.weekday() == 5:  # 周六 → 周五数据 OK
        max_gap = 1
    elif today.weekday() == 6:  # 周日 → 周五数据 OK
        max_gap = 2
    else:  # 周一到周五 → 昨天或今天的数据 OK
        max_gap = 1

    # 计算工作日差距
    check_date = report_date + timedelta(days=1)
    biz_days_behind = 0
    while check_date <= today:
        if check_date.weekday() < 5:
            biz_days_behind += 1
        check_date += timedelta(days=1)

    if biz_days_behind > max_gap:
        print(f"❌ 最新报告日期为 {report_date_str}，已滞后 {biz_days_behind} 个工作日")
        print(f"   请先运行 stock_trend_analysis.py -i interest_stock.md")
        sys.exit(1)

    print(f"📅 最新报告日期: {report_date_str} ✅")
    return report_date_str


# ── 报告解析 ──────────────────────────────────────────────

def parse_daily_conclusions(date_str: str) -> dict:
    """解析每日明细报告 → {stock_name: conclusion_label}"""
    detail_path = DAILY_DIR / f"stock_trend_report_{date_str}_detail.md"
    if not detail_path.exists():
        print(f"❌ 每日明细报告不存在: {detail_path}")
        sys.exit(1)

    result = {}
    in_table = False
    for line in detail_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "标的名称" in line and "结论" in line:
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                in_table = False
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) < 2:
                continue
            stock_name = re.sub(r"\*+", "", parts[0]).strip()
            conclusion_raw = parts[-1]
            # 提取判定文字: "🔴 **【转跌】**" → "转跌"
            m = re.search(r"【(.+?)】", conclusion_raw)
            if m:
                result[stock_name] = m.group(1)
    return result


def parse_sector_stats_from_multi(date_str: str, sector_name: str) -> dict:
    """从多日报告解析指定板块的统计数据"""
    multi_path = MULTI_DIR / f"multi_day_trend_{date_str}.md"
    if not multi_path.exists():
        return {}

    in_overview = False
    for line in multi_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "板块强弱概览" in line:
            in_overview = True
            continue
        if in_overview:
            if not line.startswith("|") or "---" in line or "排名" in line:
                continue
            if not line.startswith("|"):
                in_overview = False
                continue
            parts = [p.strip().lstrip("*") for p in line.split("|") if p.strip()]
            if len(parts) < 10:
                continue
            if parts[1] == sector_name:
                return {
                    "total": parts[2],
                    "启动": parts[3], "强势": parts[4], "冲高回落": parts[5],
                    "转跌": parts[6], "震荡": parts[7], "企稳复苏": parts[8],
                    "弱势下跌": parts[9],
                    "强势占比": parts[10].replace("*", "") if len(parts) > 10 else "-",
                }
    return {}


def parse_sector_trend_from_multi(date_str: str, sector_name: str) -> dict:
    """从多日报告解析题材的趋势状态"""
    multi_path = MULTI_DIR / f"multi_day_trend_{date_str}.md"
    if not multi_path.exists():
        return {"status": "?", "ret_5d": "?", "gt5_pct": "?", "stars": "?"}

    in_table = False
    for line in multi_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "题材名称" in line and "近5日涨幅" in line:
            in_table = True
            continue
        if in_table:
            if not line.startswith("|") or "---" in line:
                if not line.startswith("|"):
                    in_table = False
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            raw_name = re.sub(r"\*+", "", parts[0]).strip()
            if raw_name != sector_name:
                continue
            # 提取状态（去粗体标记）
            status = re.sub(r"\*+|<[^>]+>", "", parts[1]).strip() if len(parts) > 1 else "?"
            ret_5d_raw = parts[-3] if len(parts) > 3 else "?"
            ret_5d = re.sub(r"\*+|\(.+?\)", "", ret_5d_raw).strip()
            gt5_pct = parts[-2] if len(parts) > 2 else "?"
            stars = parts[-1] if len(parts) > 1 else "?"
            return {"status": status, "ret_5d": ret_5d, "gt5_pct": gt5_pct, "stars": stars}
    return {"status": "?", "ret_5d": "?", "gt5_pct": "?", "stars": "?"}


# ── 标的列表加载 ──────────────────────────────────────────

def load_stocks_by_sector(filepath: Path) -> list:
    """解析标的列表文件 → [(sector, stock_name), ...] 按板块分组"""
    groups = []
    current_sector = None
    current_stocks = []

    if not filepath.exists():
        print(f"❌ {filepath} 不存在")
        return groups

    for line in filepath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if current_stocks:
                groups.append((current_sector, current_stocks))
            current_sector = line.lstrip("#").strip()
            current_stocks = []
        elif current_sector:
            current_stocks.append(clean_name(line))

    if current_stocks:
        groups.append((current_sector, current_stocks))
    return groups


def get_sector_stocks(sector_name: str) -> list:
    """从 interest_stock.md 获取某题材的所有标的名称"""
    groups = load_stocks_by_sector(INTEREST_PATH)
    for sec, names in groups:
        if sec == sector_name:
            return names
    return []


# ── 缓存数据构建 ──────────────────────────────────────────

def _trading_days_back(n: int, stock_data: dict) -> list:
    """从缓存中收集所有标的的日期，取最近 N 个不重复的真实交易日"""
    all_dates = set()
    for sd in stock_data.values():
        all_dates.update(sd.get("dates", []))
    return sorted(all_dates)[-n:]


def _fetch_ohlc(stock_code: str, start_date: str, end_date: str):
    """从 akshare 拉取单只股票的 OHLCV 数据，返回 {dates:[], open:[], close:[], ...} 或 None"""
    import akshare as ak
    try:
        df = ak.stock_zh_a_hist_tx(symbol=stock_code, adjust="qfq",
                                    start_date=start_date, end_date=end_date)
    except Exception as e:
        print(f"  ⚠️ {stock_code} API 调用失败: {e}")
        return None
    if df is None or df.empty:
        return None
    df = df.sort_values("date")
    return {
        "dates": df["date"].astype(str).tolist(),
        "open": df["open"].round(2).tolist(),
        "close": df["close"].round(2).tolist(),
        "high": df["high"].round(2).tolist(),
        "low": df["low"].round(2).tolist(),
        "amount": df["amount"].round(2).tolist(),
    }


def _merge_into_cache(cache: dict, code: str, fetched: dict):
    """将拉取的数据合并到 stock_data.json 缓存（新增日期 + 回填 None 值）"""
    if code not in cache:
        cache[code] = {"dates": [], "daily_changes": [], "last_update": ""}
    existing = cache[code]
    existing_dates = existing.get("dates", [])
    exist_idx = {d: i for i, d in enumerate(existing_dates)}
    new_idx = {d: i for i, d in enumerate(fetched["dates"])}

    ohlc_fields = ("open", "close", "high", "low", "amount")

    # 1. 确保现有 OHLCV 数组长度匹配 dates
    for field in ohlc_fields:
        vals = existing.get(field, [])
        if len(vals) < len(existing_dates):
            vals = [None] * (len(existing_dates) - len(vals)) + vals
        existing[field] = vals

    # 2. 回填已有日期但 OHLC 为 None 的位置
    filled = 0
    for d, ei in exist_idx.items():
        ni = new_idx.get(d)
        if ni is None:
            continue
        for field in ohlc_fields:
            fvals = fetched[field]
            if ni < len(fvals) and fvals[ni] is not None:
                evals = existing[field]
                if evals[ei] is None:
                    evals[ei] = fvals[ni]
                    filled += 1

    # 3. 追加新日期
    new_dates = [d for d in fetched["dates"] if d not in exist_idx]
    if new_dates:
        existing["dates"] = existing_dates + new_dates
        for field in ohlc_fields:
            fvals = fetched[field]
            existing[field].extend(fvals[new_idx[d]] if new_idx[d] < len(fvals) else None for d in new_dates)

    # 4. 回填 daily_changes
    old_changes = existing.get("daily_changes", [])
    if len(old_changes) < len(existing_dates):
        old_changes = [0] * (len(existing_dates) - len(old_changes)) + old_changes
    for d, ei in exist_idx.items():
        if old_changes[ei] == 0:
            ni = new_idx.get(d)
            fc = fetched.get("close", [])
            if ni is not None and ni > 0 and fc[ni] is not None and fc[ni-1] is not None and fc[ni-1] != 0:
                old_changes[ei] = round((fc[ni] - fc[ni-1]) / fc[ni-1] * 100, 2)
    for d in new_dates:
        ni = new_idx[d]
        fc = fetched["close"]
        if ni > 0 and fc[ni] is not None and fc[ni-1] is not None and fc[ni-1] != 0:
            old_changes.append(round((fc[ni] - fc[ni-1]) / fc[ni-1] * 100, 2))
        else:
            old_changes.append(0)
    existing["daily_changes"] = old_changes
    existing["last_update"] = datetime.now().strftime("%Y-%m-%d")


def build_stock_data(stock_names: list, sector: str, days: int) -> dict:
    """从缓存构建 OHLCV 数据，缺失的自动从 API 补齐并更新缓存"""
    if not STOCK_DATA_PATH.exists():
        print("❌ stock_data.json 不存在，请先运行 stock_trend_analysis.py")
        return {}

    stock_data = json.loads(STOCK_DATA_PATH.read_text(encoding="utf-8"))
    code_map = json.loads(CODE_MAP_PATH.read_text(encoding="utf-8")) if CODE_MAP_PATH.exists() else {}

    # 1. 从缓存收集所有真实交易日，取最近 N 个（自动跳过节假日）
    target_dates = _trading_days_back(days, stock_data)
    target_set = set(target_dates)
    print(f"📅 目标日期范围: {target_dates[0]} ~ {target_dates[-1]} ({len(target_dates)} 个真实交易日)")

    # 2. 检查每个标的的覆盖情况：日期缺失 或 日期存在但 OHLC 为 None → 都需要拉取
    need_fetch = []
    for name in stock_names:
        code = code_map.get(name)
        if not code:
            continue
        if code not in stock_data:
            need_fetch.append((name, code))
            continue
        sd = stock_data[code]
        date_to_idx = {d: i for i, d in enumerate(sd.get("dates", []))}
        for td in target_dates:
            if td not in date_to_idx:
                need_fetch.append((name, code))
                break
            idx = date_to_idx[td]
            # 检查 OHLC 四字段在该日期是否都有有效值
            for field in ("open", "close", "high", "low"):
                arr = sd.get(field, [])
                if idx >= len(arr) or arr[idx] is None:
                    need_fetch.append((name, code))
                    break
            else:
                continue
            break

    if need_fetch:
        print(f"🔄 {len(need_fetch)} 只标的需要补齐数据，正在从 API 拉取（多线程）...")
        fetch_start = target_dates[0].replace("-", "")
        fetch_end = target_dates[-1].replace("-", "")

        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_ohlc, code, fetch_start, fetch_end): (name, code)
                       for name, code in need_fetch}
            for f in as_completed(futures):
                name, code = futures[f]
                fetched = f.result()
                if fetched is not None:
                    _merge_into_cache(stock_data, code, fetched)
                    results[name] = len(fetched["dates"])
                else:
                    results[name] = None

        ok = sum(1 for v in results.values() if v is not None)
        fail = sum(1 for v in results.values() if v is None)
        print(f"  ✅ 成功 {ok} 只, ❌ 失败 {fail} 只")

        # 保存更新后的缓存
        STOCK_DATA_PATH.write_text(json.dumps(stock_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 缓存已更新: {STOCK_DATA_PATH}")

    # 3. 构建对齐的数据
    result = {}
    stats = {"total": 0, "with_ohlc": 0, "no_data": 0}

    for name in stock_names:
        stats["total"] += 1
        code = code_map.get(name)
        if not code or code not in stock_data:
            stats["no_data"] += 1
            continue

        sd = stock_data[code]
        # 建立缓存日期→索引的映射
        date_to_idx = {d: i for i, d in enumerate(sd.get("dates", []))}

        # 按目标日期提取数据
        nd = len(target_dates)
        dates_out = list(target_dates)
        ohlc_out = [[None] * nd for _ in range(4)]
        amounts_out = [0] * nd
        changes_out = [0] * nd

        for col, d in enumerate(target_dates):
            if d in date_to_idx:
                idx = date_to_idx[d]
                for arr_i, field in enumerate(["open", "close", "low", "high"]):
                    arr = sd.get(field, [])
                    if idx < len(arr) and arr[idx] is not None:
                        ohlc_out[arr_i][col] = arr[idx]
                amt_arr = sd.get("amount", [])
                if idx < len(amt_arr) and amt_arr[idx] is not None:
                    amounts_out[col] = amt_arr[idx]
                chg_arr = sd.get("daily_changes", [])
                if idx < len(chg_arr):
                    changes_out[col] = chg_arr[idx]

        latest_close = None
        for v in reversed(ohlc_out[1]):
            if v is not None:
                latest_close = v
                break

        # 计算近5日复利涨幅
        cum5d = None
        if len(changes_out) >= 5:
            p = 1.0
            for c in changes_out[-5:]:
                p *= (1 + c / 100)
            cum5d = round((p - 1) * 100, 2)

        stats["with_ohlc"] += 1
        result[name] = {
            "sector": sector,
            "code": code.replace("sh", "").replace("sz", ""),
            "dates": dates_out,
            "open": ohlc_out[0], "close": ohlc_out[1],
            "low": ohlc_out[2], "high": ohlc_out[3],
            "amounts": amounts_out,
            "latest_close": latest_close,
            "pct_changes": [round(c, 2) for c in changes_out],
            "cum5d": cum5d,
        }

    print(f"📊 统计: 总计 {stats['total']} 只, 有K线数据 {stats['with_ohlc']} 只, "
          f"无数据/无代码 {stats['no_data']} 只")
    return result


# ── HTML 生成 ─────────────────────────────────────────────

def _escape_js_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


def generate_html(stock_map: dict, output_path: str, days: int = DEFAULT_DAYS,
                  sector_summary: dict = None, sector_trend: dict = None,
                  report_date: str = ""):
    """生成 HTML 页面。sector_summary/sector_trend 非空时显示板块概览卡片"""
    # JS 数据
    js_data_lines = []
    for name, info in stock_map.items():
        conclusion = info.get("conclusion", "")
        js_data_lines.append(
            f"'{_escape_js_str(name)}':{{"
            f"s:'{_escape_js_str(info['sector'])}',"
            f"c:'{info['code']}',"
            f"d:{json.dumps(info['dates'])},"
            f"o:{json.dumps(info['open'])},"
            f"cl:{json.dumps(info['close'])},"
            f"l:{json.dumps(info['low'])},"
            f"h:{json.dumps(info['high'])},"
            f"a:{json.dumps(info['amounts'])},"
            f"lc:{json.dumps(info['latest_close'])},"
            f"pct:{json.dumps(info['pct_changes'])},"
            f"conc:'{_escape_js_str(conclusion)}'"
            f"}}"
        )
    js_data_block = "var STOCK_MAP={"+",".join(js_data_lines)+"};"

    # 按结论分组排序: 进攻在前，防守在后
    def _cum5d(info):
        changes = info.get("pct_changes", [])
        if len(changes) < 5:
            return -999
        p = 1.0
        for c in changes[-5:]:
            p *= (1 + c / 100)
        return round((p - 1) * 100, 2)

    sorted_names = sorted(stock_map.keys(), key=lambda n: _cum5d(stock_map[n]), reverse=True)

    # 构建页面
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>标的走势图</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:16px}}
h1{{font-size:20px;font-weight:600;margin-bottom:4px;color:#f0f6fc}}
.subtitle{{font-size:13px;color:#8b949e;margin-bottom:16px}}

/* 板块概览卡片 */
.overview{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 16px;margin-bottom:20px;display:flex;gap:24px;flex-wrap:wrap}}
.overview-col{{min-width:160px}}
.overview-label{{font-size:11px;color:#8b949e;margin-bottom:2px;text-transform:uppercase}}
.help-tip{{display:inline-block;width:14px;height:14px;line-height:14px;text-align:center;font-size:10px;color:#8b949e;border:1px solid #30363d;border-radius:50%;margin-left:4px;cursor:help;position:relative}}
.help-tip:hover{{color:#58a6ff;border-color:#58a6ff}}
.help-tip::after{{content:attr(data-tip);display:none;position:absolute;top:18px;left:0;background:#1f2a37;border:1px solid #30363d;color:#c9d1d9;font-size:11px;white-space:nowrap;padding:4px 8px;border-radius:3px;z-index:200;text-transform:none;font-weight:400}}
.help-tip:hover::after{{display:block}}
.overview-val{{font-size:18px;font-weight:600;color:#f0f6fc}}
.overview-val.status-startup{{color:#ffd700}}
.overview-val.status-strong{{color:#3fb950}}
.overview-val.status-pullback{{color:#d29922}}
.overview-val.status-turn{{color:#f85149}}
.overview-val.status-osc{{color:#db6d28}}
.overview-val.status-recover{{color:#ffd700}}
.overview-val.status-weak{{color:#f85149}}
.overview-stats{{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;width:100%}}
.overview-stat{{font-size:12px;padding:3px 8px;border-radius:3px;background:#0d1117;border:1px solid #30363d;cursor:pointer;user-select:none}}
.overview-stat:hover{{border-color:#58a6ff}}
.overview-stat.active{{border-color:#58a6ff;background:#1f2a37;box-shadow:inset 0 0 0 1px #58a6ff}}
.overview-stat .n{{font-weight:600}}
.stat-attack{{border-color:#3fb950!important}}
.stat-attack.active{{border-color:#3fb950!important;background:rgba(63,185,80,.1);box-shadow:inset 0 0 0 1px #3fb950}}
.stat-defend{{border-color:#f85149!important}}
.stat-defend.active{{border-color:#f85149!important;background:rgba(248,81,73,.1);box-shadow:inset 0 0 0 1px #f85149}}
.card.hidden{{display:none}}
.dropdown{{position:relative;display:inline-flex}}
.dropdown-trigger{{cursor:pointer}}
.dropdown-menu{{display:none;position:absolute;top:100%;left:0;margin-top:2px;background:#161b22;border:1px solid #30363d;border-radius:4px;z-index:100;min-width:120px;padding:4px 0}}
.dropdown:hover .dropdown-menu{{display:block}}
.dropdown-item{{font-size:12px;padding:3px 10px;cursor:pointer;white-space:nowrap}}
.dropdown-item:hover{{background:#1f2a37;color:#58a6ff}}

.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,500px));gap:12px}}

.card{{background:#161b22;border-radius:8px;border:1px solid #30363d;overflow:hidden;transition:border-color .15s}}
.card:hover{{border-color:#58a6ff}}
.card.attack-card{{border-left:3px solid #3fb950}}
.card.defend-card{{border-left:3px solid #f85149}}
.card-header{{padding:8px 10px 4px;display:flex;justify-content:space-between;align-items:flex-start}}
.card-name{{font-size:14px;font-weight:600;color:#f0f6fc;line-height:1.3}}
.card-code{{font-size:11px;color:#8b949e;margin-left:6px;font-weight:400}}
.card-badges{{display:flex;gap:4px;align-items:center;margin-top:3px;flex-wrap:wrap}}
.conc-badge{{font-size:10px;padding:1px 6px;border-radius:3px;font-weight:600;white-space:nowrap}}
.conc-attack{{color:#3fb950;background:rgba(63,185,80,.15)}}
.conc-defend{{color:#f85149;background:rgba(248,81,73,.15)}}
.type-badge{{font-size:10px;padding:1px 6px;border-radius:3px;font-weight:600;white-space:nowrap}}
.type-attack{{color:#ffd700;background:rgba(255,215,0,.1)}}
.type-defend{{color:#8b949e;background:rgba(139,148,158,.1)}}
.card-price{{font-size:12px;color:#8b949e;text-align:right;white-space:nowrap}}
.card-price .val{{font-size:15px;font-weight:600;color:#f0f6fc}}
.card-price .pct{{font-size:12px;margin-left:4px;font-weight:600}}
.card-price .up{{color:#ef5350}}
.card-price .down{{color:#26a69a}}
.pct-badge{{font-size:10px;padding:0 4px;border-radius:2px;margin-left:4px}}
.pct-badge.up{{color:#ef5350;background:rgba(239,83,80,.1)}}
.pct-badge.down{{color:#26a69a;background:rgba(38,166,154,.1)}}
.chart-wrap{{width:100%;height:220px;position:relative}}
</style>
</head>
<body>
<h1>📈 标的走势图</h1>
'''

    # 板块概览（题材模式时显示）
    if sector_summary and sector_trend:
        sector_name_raw = sector_summary.get("_sector", "")
        status = sector_trend.get("status", "?")
        ret_5d = sector_trend.get("ret_5d", "?")
        gt5 = sector_trend.get("gt5_pct", "?")

        # 状态 CSS class
        status_css = ""
        if "启动" in status:
            status_css = "status-startup"
        elif "主升" in status or "强势" in status:
            status_css = "status-strong"
        elif "冲高回落" in status:
            status_css = "status-pullback"
        elif "转跌" in status:
            status_css = "status-turn"
        elif "震荡" in status:
            status_css = "status-osc"
        elif "企稳复苏" in status:
            status_css = "status-recover"
        elif "弱势" in status:
            status_css = "status-weak"

        total = sector_summary.get("total", "?")
        att_count = int(sector_summary.get("启动", "0")) + int(sector_summary.get("强势", "0")) + int(sector_summary.get("企稳复苏", "0"))
        def_count = int(sector_summary.get("冲高回落", "0")) + int(sector_summary.get("转跌", "0")) + int(sector_summary.get("震荡", "0")) + int(sector_summary.get("弱势下跌", "0"))

        html += f'''<div class="subtitle">报告日期: {report_date} · {sector_name_raw} · 近 {days} 个交易日 · 共 {len(stock_map)}/{total} 只有K线数据</div>
<div class="overview">
  <div class="overview-col">
    <div class="overview-label">板块状态</div>
    <div class="overview-val {status_css}">{status}</div>
  </div>
  <div class="overview-col">
    <div class="overview-label">近5日涨幅</div>
    <div class="overview-val">{ret_5d}</div>
  </div>
  <div class="overview-col">
    <div class="overview-label">涨超5%占比</div>
    <div class="overview-val">{gt5}</div>
  </div>
  <div class="overview-col">
    <div class="overview-label">强势占比<span class="help-tip" data-tip="强势=连续≥2天为进攻状态 | 强势占比=强势票数÷板块总票数">?</span></div>
    <div class="overview-val">{sector_summary.get("强势占比", "?")}</div>
  </div>
  <div class="overview-stats" id="filterBar">
    <div class="overview-stat active" data-filter="all" onclick="filterCards('all')">📋 全部 <span class="n">{att_count + def_count}</span> 只</div>
    <div class="dropdown">
      <div class="overview-stat stat-attack dropdown-trigger" data-filter="attack" onclick="filterCards('attack')">⭐ 进攻 <span class="n">{att_count}</span> 只 ▼</div>
      <div class="dropdown-menu">
        <div class="dropdown-item" onclick="event.stopPropagation();filterByConc('启动')">🚀 启动 ({sector_summary.get("启动","0")})</div>
        <div class="dropdown-item" onclick="event.stopPropagation();filterByConc('强势')">🟢 强势 ({sector_summary.get("强势","0")})</div>
        <div class="dropdown-item" onclick="event.stopPropagation();filterByConc('企稳复苏')">⭐ 企稳复苏 ({sector_summary.get("企稳复苏","0")})</div>
      </div>
    </div>
    <div class="dropdown">
      <div class="overview-stat stat-defend dropdown-trigger" data-filter="defend" onclick="filterCards('defend')">🛡 防守 <span class="n">{def_count}</span> 只 ▼</div>
      <div class="dropdown-menu">
        <div class="dropdown-item" onclick="event.stopPropagation();filterByConc('冲高回落')">🟡 冲高回落 ({sector_summary.get("冲高回落","0")})</div>
        <div class="dropdown-item" onclick="event.stopPropagation();filterByConc('转跌')">🔴 转跌 ({sector_summary.get("转跌","0")})</div>
        <div class="dropdown-item" onclick="event.stopPropagation();filterByConc('震荡')">🟠 震荡 ({sector_summary.get("震荡","0")})</div>
        <div class="dropdown-item" onclick="event.stopPropagation();filterByConc('弱势下跌')">🔴 弱势下跌 ({sector_summary.get("弱势下跌","0")})</div>
      </div>
    </div>
  </div>
</div>
'''
    else:
        html += f'<div class="subtitle">近 {days} 个交易日 · K线 + 成交额 · 共 {len(stock_map)} 只标的</div>'

    # 标的卡片
    html += '<div class="grid">'
    for name in sorted_names:
        info = stock_map[name]
        lc = info["latest_close"]
        pct = info["pct_changes"][-1] if info["pct_changes"] else 0
        pct_cls = "up" if pct > 0 else "down" if pct < 0 else ""
        price_str = f'{lc:.2f}' if lc is not None else '-'
        conc = info.get("conclusion", "")
        is_attack = conc in ATTACK_LABELS
        card_class = "attack-card" if is_attack else "defend-card" if conc else ""
        conc_class = "conc-attack" if is_attack else "conc-defend" if conc else ""
        type_class = "type-attack" if is_attack else "type-defend" if conc else ""
        type_text = "进攻" if is_attack else "防守" if conc else ""
        safe_name = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]', '_', name)

        badges_html = ""
        if conc:
            badges_html = f'<span class="conc-badge {conc_class}">{conc}</span><span class="type-badge {type_class}">{type_text}</span>'

        # 近5日涨幅显示
        cum5d = info.get("cum5d")
        cum5d_html = ""
        if cum5d is not None:
            c5_cls = "up" if cum5d > 0 else "down" if cum5d < 0 else ""
            cum5d_html = f'<span class="pct-badge {c5_cls}">5日{cum5d:+.1f}%</span>'

        data_type = 'attack' if is_attack else 'defend' if conc else ''
        html += f'''
<div class="card {card_class}" data-type="{data_type}" data-conc="{conc}">
  <div class="card-header">
    <div>
      <div class="card-name">{name}<span class="card-code">{info["code"]}</span></div>
      <div class="card-badges">{badges_html}{cum5d_html}</div>
    </div>
    <div class="card-price">
      <span class="val">{price_str}</span>
      <span class="pct {pct_cls}">{pct:+.2f}%</span>
    </div>
  </div>
  <div class="chart-wrap" id="chart_{safe_name}"></div>
</div>'''

    html += '</div>\n'
    html += f'''
<script>
{js_data_block}

(function(){{
var UP_COLOR='#ef5350',DOWN_COLOR='#26a69a',BG='#161b22',TEXT='#8b949e',LINE='#30363d';

function makeChart(domId, info) {{
  var dom = document.getElementById(domId);
  if (!dom) return;
  var dates = info.d, o = info.o, cl = info.cl, l = info.l, h = info.h, a = info.a;
  if (!dates || !dates.length) return;

  var kdata = [], vdata = [], hasValid = false;
  for (var i = 0; i < dates.length; i++) {{
    var openVal = (o && o[i] != null) ? o[i] : null;
    var closeVal = (cl && cl[i] != null) ? cl[i] : null;
    var lowVal = (l && l[i] != null) ? l[i] : null;
    var highVal = (h && h[i] != null) ? h[i] : null;
    if (openVal != null && closeVal != null) {{
      hasValid = true;
      kdata.push([openVal, closeVal, lowVal != null ? lowVal : Math.min(openVal, closeVal), highVal != null ? highVal : Math.max(openVal, closeVal)]);
      vdata.push(i < a.length ? a[i] : 0);
    }} else {{
      kdata.push(['-','-','-','-']);
      vdata.push(0);
    }}
  }}
  if (!hasValid) return;

  var chart = echarts.init(dom, null, {{renderer:'canvas'}});
  chart.setOption({{
    animation: false,
    grid: [{{left:8,right:8,top:4,height:'60%'}}, {{left:8,right:8,top:'74%',height:'22%'}}],
    xAxis: [
      {{type:'category',data:dates,gridIndex:0,axisLine:{{show:false}},axisTick:{{show:false}},axisLabel:{{show:false}},splitLine:{{show:false}}}},
      {{type:'category',data:dates,gridIndex:1,axisLine:{{show:false}},axisTick:{{show:false}},axisLabel:{{show:false}},splitLine:{{show:false}}}}
    ],
    yAxis: [
      {{type:'value',gridIndex:0,splitNumber:3,axisLabel:{{fontSize:9,color:TEXT}},splitLine:{{lineStyle:{{color:LINE,type:'dashed'}}}},position:'right',scale:true}},
      {{type:'value',gridIndex:1,splitNumber:2,axisLabel:{{fontSize:8,color:TEXT,formatter:function(v){{return v>=10000?(v/10000).toFixed(1)+'亿':v.toFixed(0)+'万';}}}},splitLine:{{show:false}},position:'right'}}
    ],
    series: [
      {{
        type:'candlestick',xAxisIndex:0,yAxisIndex:0,data:kdata,
        itemStyle:{{color:UP_COLOR,color0:DOWN_COLOR,borderColor:UP_COLOR,borderColor0:DOWN_COLOR}},
        barWidth:'60%',markPoint:{{silent:true,symbol:'none',label:{{show:false}}}}
      }},
      {{
        type:'bar',xAxisIndex:1,yAxisIndex:1,data:vdata,
        itemStyle:{{color:function(p){{var i=p.dataIndex;return(i<kdata.length&&kdata[i][1]>=kdata[i][0])?UP_COLOR:DOWN_COLOR;}},opacity:0.35}},
        barWidth:'60%'
      }}
    ],
    tooltip:{{
      trigger:'axis',axisPointer:{{type:'shadow',shadowStyle:{{color:'rgba(200,200,200,0.08)'}}}},backgroundColor:BG,borderColor:LINE,
      textStyle:{{fontSize:11,color:'#f0f6fc'}},
      formatter:function(params){{
        var cs = null, date = '';
        for (var j = 0; j < params.length; j++) {{
          if (params[j].seriesType === 'candlestick') cs = params[j];
          date = params[j].axisValue;
        }}
        var k = cs ? (cs.value || cs.data) : null;
        if (!k || k.length < 5 || typeof k[1] !== 'number') return date;
        return date + '<br/>开 ' + k[1].toFixed(2) + '<br/>收 ' + k[2].toFixed(2)
             + '<br/>高 ' + k[4].toFixed(2) + '<br/>低 ' + k[3].toFixed(2);
      }}
    }}
  }});

  var timer;
  var ro = new ResizeObserver(function(){{clearTimeout(timer);timer=setTimeout(function(){{chart.resize();}},100);}});
  ro.observe(dom);
}}

function filterCards(type){{
  var bar=document.getElementById('filterBar');
  if(!bar) return;
  Array.from(bar.querySelectorAll('.overview-stat')).forEach(function(b){{b.classList.remove('active');}});
  var activeBtn = bar.querySelector('[data-filter=\"'+type+'\"]');
  if(activeBtn) activeBtn.classList.add('active');
  document.querySelectorAll('.card').forEach(function(c){{
    c.classList.remove('hidden');
    if(type==='all') return;
    if(c.getAttribute('data-type')!==type) c.classList.add('hidden');
  }});
  setTimeout(function(){{
    document.querySelectorAll('.card:not(.hidden) .chart-wrap>div').forEach(function(d){{
      var chart = echarts.getInstanceByDom(d);
      if(chart) chart.resize();
    }});
  }},50);
}}
window.filterCards = filterCards;
function filterByConc(concName){{
  var bar=document.getElementById('filterBar');
  if(!bar) return;
  Array.from(bar.querySelectorAll('.overview-stat')).forEach(function(b){{b.classList.remove('active');}});
  document.querySelectorAll('.card').forEach(function(c){{
    c.classList.remove('hidden');
    if(c.getAttribute('data-conc')!==concName) c.classList.add('hidden');
  }});
  setTimeout(function(){{
    document.querySelectorAll('.card:not(.hidden) .chart-wrap>div').forEach(function(d){{
      var chart = echarts.getInstanceByDom(d);
      if(chart) chart.resize();
    }});
  }},50);
}}
window.filterByConc = filterByConc;

Object.keys(STOCK_MAP).forEach(function(name){{
  var safeName = name.replace(/[^a-zA-Z0-9\\u4e00-\\u9fff]/g,'_');
  makeChart('chart_'+safeName, STOCK_MAP[name]);
}});

}})();
</script>
</body>
</html>'''

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ 页面已生成: {output_path}")


# ── 文件模式入口 ──────────────────────────────────────────

def run_file_mode(input_path: Path, output: str, days: int):
    """按标的列表文件生成走势图"""
    groups = load_stocks_by_sector(input_path)
    all_names = []
    for _, names in groups:
        all_names.extend(names)

    # 去重
    seen = set()
    unique_names = []
    for n in all_names:
        if n not in seen:
            seen.add(n)
            unique_names.append(n)

    print(f"📂 标的来源: {input_path} ({len(unique_names)} 只)")

    # 构建数据（sector 用 "自选"）
    stock_map = build_stock_data(unique_names, "自选", days)
    if not stock_map:
        print("⚠️ 没有可用的 K 线数据")
        return

    generate_html(stock_map, output, days)


# ── 题材模式入口 ──────────────────────────────────────────

def run_sector_mode(sector_name: str, output: str, days: int):
    """按题材名生成走势图 + 判定结果"""
    # 1. 获取标的列表
    stock_names = get_sector_stocks(sector_name)
    if not stock_names:
        print(f"❌ 在 interest_stock.md 中未找到题材「{sector_name}」")
        sys.exit(1)
    print(f"📂 题材「{sector_name}」共 {len(stock_names)} 只标的")

    # 2. 查找 & 校验最新报告日期
    report_date = find_latest_report_date()

    # 3. 解析每日分析报告 → 每个标的的结论
    conclusions = parse_daily_conclusions(report_date)
    print(f"📋 每日报告: 解析到 {len(conclusions)} 只标的的结论")

    # 4. 解析多日分析报告 → 板块数据
    sector_stats = parse_sector_stats_from_multi(report_date, sector_name)
    sector_trend = parse_sector_trend_from_multi(report_date, sector_name)
    if sector_stats:
        sector_stats["_sector"] = sector_name
        print(f"📊 多日报告: 板块「{sector_name}」- "
              f"状态={sector_trend.get('status','?')}, "
              f"强势占比={sector_stats.get('强势占比','?')}")

    # 5. 构建 OHLCV 数据
    stock_map = build_stock_data(stock_names, sector_name, days)
    if not stock_map:
        print("⚠️ 没有可用的 K 线数据")
        return

    # 6. 附加结论信息
    attack_count = 0
    defend_count = 0
    for name in stock_map:
        conc = conclusions.get(name, "")
        stock_map[name]["conclusion"] = conc
        if conc in ATTACK_LABELS:
            attack_count += 1
        elif conc in DEFENSE_LABELS:
            defend_count += 1

    print(f"⚔️  进攻票: {attack_count} 只 | 防守票: {defend_count} 只")

    # 7. 生成 HTML
    generate_html(stock_map, output, days, sector_stats, sector_trend, report_date)


# ── main ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="生成标的走势图 HTML 页面")
    parser.add_argument("--input", "-i", type=str, default=None,
                        help="标的列表文件 (文件模式，默认: interest_stock.md)")
    parser.add_argument("--sector", type=str, default=None,
                        help="题材名称 (题材模式，如: 光通信、CPO、PCB)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"展示近 N 个交易日 (默认{DEFAULT_DAYS})")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出文件路径")
    args = parser.parse_args()

    output = args.output or str(DEFAULT_OUTPUT)

    if args.sector:
        run_sector_mode(args.sector, output, args.days)
    else:
        input_path = Path(args.input) if args.input else INTEREST_PATH
        run_file_mode(input_path, output, args.days)

    print(f"📄 用浏览器打开即可查看")


if __name__ == "__main__":
    main()
