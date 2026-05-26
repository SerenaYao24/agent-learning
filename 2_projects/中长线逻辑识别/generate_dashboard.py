#!/usr/bin/env python3
"""生成静态数据看板 HTML: 指数宏观 / ETF宏观 / 板块数据 / 个股数据 (4 Tab)"""
import csv, json, os, re, glob
from datetime import datetime

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR = os.path.join(DATA_DIR, ".index_data")
RANK_DIR = os.path.join(DATA_DIR, ".ma5_ranking")
STOCK_CACHE = os.path.join(DATA_DIR, ".stock_cache")
STOCK_DATA_PATH = os.path.join(STOCK_CACHE, "stock_data.json")
CODE_MAP_PATH = os.path.join(STOCK_CACHE, "stock_code_map.json")
RISK_TAGS_FILE = os.path.join(DATA_DIR, "risk_tags.json")
MONITOR_FILE = os.path.join(DATA_DIR, "monitor.json")
OPP_TAGS_FILE = os.path.join(DATA_DIR, "opp_tags.json")
INDEX_STATE_FILE = os.path.join(DATA_DIR, "index_state.json")
FILTER_TAGS_FILE = os.path.join(INDEX_DIR, "filter_tags.json")
STOCK_DAYS = 20


def load_risk_tags():
    if os.path.exists(RISK_TAGS_FILE):
        with open(RISK_TAGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"manual": [], "deleted": []}


def save_risk_tags(data):
    with open(RISK_TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_opp_tags():
    if os.path.exists(OPP_TAGS_FILE):
        with open(OPP_TAGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"manual": [], "deleted": []}


def save_opp_tags(data):
    with open(OPP_TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_index_state():
    if os.path.exists(INDEX_STATE_FILE):
        with open(INDEX_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"state": "区间震荡"}


def load_filter_tags():
    if os.path.exists(FILTER_TAGS_FILE):
        with open(FILTER_TAGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_watched_stocks():
    """从 interest_stock.md 提取关注的股票: {名称: {theme, note}}, theme_order"""
    path = os.path.join(DATA_DIR, "interest_stock.md")
    stocks = {}  # name -> {theme, note}
    codes = set()
    theme_order = []  # 题材出现顺序
    current_theme = "其他"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    current_theme = line.lstrip("#").strip()
                    if current_theme not in theme_order:
                        theme_order.append(current_theme)
                    continue
                # 提取6位代码
                for m in re.finditer(r'\b(\d{6})\b', line):
                    codes.add(m.group(1))
                # 提取名称和备注
                note = ""
                m_note = re.search(r'[（(](.+)[）)]', line)
                if m_note:
                    note = m_note.group(1)
                name = re.sub(r'[（(].*[）)]', '', line).strip()
                if re.match(r'^[\u4e00-\u9fff]{2,6}$', name):
                    stocks[name] = {"theme": current_theme, "note": note}
    return stocks, codes, theme_order


def find_latest_multi_report():
    """Return path to the latest multi_day_trend_*.md (not _detail), or None."""
    multi_dir = os.path.join(STOCK_CACHE, "reports", "多日分析")
    if not os.path.exists(multi_dir):
        return None
    files = sorted(glob.glob(os.path.join(multi_dir, "multi_day_trend_*.md")))
    files = [f for f in files if "_detail" not in f]
    return files[-1] if files else None


def parse_sector_overview(report_path):
    """Parse the '题材情况' table from a multi-day report.
    Returns [{name, status, ret_5d, gt5_pct, stars}, ...]"""
    if not report_path:
        return []
    sectors = []
    in_table = False
    with open(report_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "题材名称" in line and "近5日涨幅" in line:
                in_table = True
                continue
            if in_table:
                if not line.startswith("|"):
                    break
                if "---" in line or "题材名称" in line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                cells = [p for p in parts if p]
                if len(cells) < 6:
                    continue
                # cells: [题材名称, 状态, d1, d2, d3, d4, d5, 近5日涨幅, 涨超5%占比, 明星标的]
                name = re.sub(r'\*+', '', cells[0]).strip()
                status = re.sub(r'<[^>]+>', '', cells[1]).replace('**', '').strip()
                d1 = cells[2].strip()
                d2 = cells[3].strip()
                d3 = cells[4].strip()
                d4 = cells[5].strip()
                d5 = cells[6].strip()
                ret_5d = cells[-3].strip()
                gt5_pct = cells[-2].strip()
                stars = cells[-1].strip()
                sectors.append({
                    "name": name, "status": status,
                    "d1": d1, "d2": d2, "d3": d3, "d4": d4, "d5": d5,
                    "ret_5d": ret_5d, "gt5_pct": gt5_pct, "stars": stars,
                })
    return sectors


def _trading_days_back(n, stock_data):
    """从缓存中收集所有标的的日期，取最近 N 个不重复的交易日"""
    all_dates = set()
    for sd in stock_data.values():
        if isinstance(sd, dict):
            all_dates.update(sd.get("dates", []))
    return sorted(all_dates)[-n:]


def load_stock_ohlcv_readonly(stock_names, watched_stocks, days=20):
    """从缓存只读加载 OHLCV 数据，不调 API。返回 {name: {sector, code, dates, open, close, low, high, amounts, latest_close, pct_changes, cum5d}}"""
    if not os.path.exists(STOCK_DATA_PATH):
        return {}
    with open(STOCK_DATA_PATH, encoding="utf-8") as f:
        stock_data = json.load(f)
    code_map = {}
    if os.path.exists(CODE_MAP_PATH):
        with open(CODE_MAP_PATH, encoding="utf-8") as f:
            code_map = json.load(f)

    target_dates = _trading_days_back(days, stock_data)
    nd = len(target_dates)
    if nd == 0:
        return {}

    result = {}
    for name in stock_names:
        code = code_map.get(name)
        if not code or code not in stock_data:
            continue
        sd = stock_data[code]
        if not isinstance(sd, dict):
            continue
        date_to_idx = {d: i for i, d in enumerate(sd.get("dates", []))}

        ohlc_out = [[None] * nd for _ in range(4)]
        amounts_out = [0] * nd
        changes_out = [0] * nd

        for col, d in enumerate(target_dates):
            if d not in date_to_idx:
                continue
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

        cum5d = None
        if len(changes_out) >= 5:
            p = 1.0
            for c in changes_out[-5:]:
                p *= (1 + c / 100)
            cum5d = round((p - 1) * 100, 2)

        info = watched_stocks.get(name, {})
        result[name] = {
            "sector": info.get("theme", "其他"),
            "code": code.replace("sh", "").replace("sz", ""),
            "dates": list(target_dates),
            "open": ohlc_out[0], "close": ohlc_out[1],
            "low": ohlc_out[2], "high": ohlc_out[3],
            "amounts": amounts_out,
            "latest_close": latest_close,
            "pct_changes": [round(c, 2) for c in changes_out],
            "cum5d": cum5d,
        }
    return result


def get_block_trades(date_str):
    """获取当日全量大宗交易并保存, 返回自选股过滤后的数据"""
    cache_file = os.path.join(INDEX_DIR, f"block_trades_{date_str}.json")
    try:
        import akshare as ak
        df = ak.stock_dzjy_mrtj(start_date=date_str.replace("-", ""), end_date=date_str.replace("-", ""))
        if df is None or len(df) == 0:
            return []
        result = []
        for _, row in df.iterrows():
            code = str(row.get("证券代码", "")).zfill(6)
            rec = {
                "日期": date_str,
                "代码": code,
                "简称": row.get("证券简称", ""),
                "收盘价": float(row.get("收盘价", 0) or 0),
                "成交价": float(row.get("成交价", 0) or 0),
                "折溢率": round(float(row.get("折溢率", 0) or 0), 2),
                "成交笔数": int(row.get("成交笔数", 0) or 0),
                "成交总额_万": round(float(row.get("成交总额", 0) or 0), 2),
                "占比流通市值": round(float(row.get("成交总额/流通市值", 0) or 0), 2),
            }
            result.append(rec)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        return result
    except Exception as e:
        print(f"  大宗交易获取失败: {e}")
        return []
INDEX_ORDER = ["上证指数", "深证成指", "创业板指", "科创综指"]
INDEX_CODES = {"上证指数": "000001", "深证成指": "399001", "创业板指": "399006", "科创综指": "000680"}
ETF_ORDER = ["中证500ETF", "沪深300ETF", "上证50ETF", "半导体设备ETF", "科创芯片ETF", "创业板ETF"]
COLORS = ["#3b82f6", "#f59e0b", "#ef4444", "#10b981", "#8b5cf6", "#ec4899"]


# ======== 数据加载 ========

def load_daily(prefix="index"):
    """加载所有日线, 返回 {date: {name: {fields}}}"""
    result = {}
    for f in sorted(glob.glob(os.path.join(INDEX_DIR, f"{prefix}_daily_*.csv"))):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f)
        if not m:
            continue
        date_str = m.group(1)
        if date_str not in result:
            result[date_str] = {}
        with open(f, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                name = row.get("名称", row.get("指数", ""))
                result[date_str][name] = row
    return result


def load_minute(name, date_str, prefix="index", m5=False):
    """加载单日期分钟线"""
    safe = name.replace("/", "_")
    suffix = "_m5" if m5 else ""
    path = os.path.join(INDEX_DIR, f"{prefix}_minute{suffix}_{safe}_{date_str}.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_index_m1_minute(rows):
    """区分真实 m1 与历史误存的 m5（index_minute_*.csv 仅 48 根、从 09:35 起）"""
    if not rows:
        return False
    times = [r.get("time", "") for r in rows]
    if len(times) >= 100:
        return True
    if "09:31" in times[:30]:
        return True
    if len(times) < 60 and times and times[0] >= "09:35" and "09:31" not in times:
        return False
    return len(times) >= 60


def load_all_minutes_m1(name, dates, prefix="index"):
    """加载多日期 m1 分钟线，返回 {date: rows}，无真实 m1 则为 []（保留交易日占位）"""
    result = {}
    for d in dates:
        rows = load_minute(name, d, prefix, m5=False)
        result[d] = rows if is_index_m1_minute(rows) else []
    return result


def load_all_minutes_m5(name, dates, prefix="index"):
    """加载多日期分钟数据(先用m5文件名, 回退到无后缀), 返回 {date: [{time,price,amount_yi}]}"""
    result = {}
    for d in dates:
        data = load_minute(name, d, prefix, m5=True)
        if not data:
            data = load_minute(name, d, prefix, m5=False)
        if data:
            result[d] = data
    return result


def load_daily_kline(name, prefix="index"):
    """加载日K线: [{date, open, close, high, low, amount}]"""
    daily = load_daily(prefix)
    result = []
    for d in sorted(daily):
        if name in daily[d]:
            row = daily[d][name]
            o, c, h, l = (float(row.get(k, 0) or 0) for k in ["开盘", "收盘", "最高", "最低"])
            vol = float(row.get("成交量", 0) or 0)
            amt = float(row.get("成交额", row.get("成交额_亿", 0)) or 0)
            if amt == 0 and vol > 0:
                amt = vol * 20  # 粗略估算: vol单位非标准, ×20 使量能与成交额视觉可比
            result.append({
                "date": d, "open": o, "close": c, "high": h, "low": l,
                "amount": amt,
            })
    return result


# ======== HTML 生成 ========

def build_html():
    all_dates = sorted(load_daily("index").keys())
    latest_date = all_dates[-1] if all_dates else "2026-05-22"
    last5 = all_dates[-5:] if len(all_dates) >= 5 else all_dates

    # ---- 总成交额 = (上证+深证) * 1.01 ----
    daily_idx = load_daily("index").get(latest_date, {})
    _sh_amt = float(daily_idx.get("上证指数", {}).get("成交额", 0))
    _sz_amt = float(daily_idx.get("深证成指", {}).get("成交额", 0))
    total_amt_yi = round((_sh_amt + _sz_amt) * 1.01 / 1e12, 2)

    # ---- 指数数据 ----
    index_data = []
    for name in INDEX_ORDER:
        prefix = "index"
        kline = load_daily_kline(name, prefix)
        minute_m1 = load_minute(name, latest_date, prefix)
        minute_m5 = load_minute(name, latest_date, prefix, m5=True)
        minute_data = minute_m1 if minute_m1 else minute_m5
        m5_all = load_all_minutes_m5(name, last5, prefix)
        # m5 缺09:30点，补开盘价（与 ETF 一致）
        for dt in m5_all:
            kline_map = {k["date"]: k["open"] for k in kline}
            op = kline_map.get(dt, 0)
            if op > 0 and m5_all[dt]:
                m5_all[dt].insert(0, {"time": "09:30", "price": str(op), "amount_yi": "0"})
        index_data.append({
            "name": name, "kline": kline, "minute": minute_data,
            "m5_5day": {d: m5_all.get(d, []) for d in last5},
            "latest": daily_idx.get(name, {}),
            "code": INDEX_CODES.get(name, ""),
        })

    # 上证指数量能 = (上证+深证) * 1.01
    _sh_amt_map = {d["date"]: d["amount"] for d in index_data[0]["kline"]}
    _sz_amt_map = {d["date"]: d["amount"] for d in index_data[1]["kline"]}
    for d in index_data[0]["kline"]:
        d["amount"] = (_sh_amt_map.get(d["date"], 0) + _sz_amt_map.get(d["date"], 0)) * 1.01
    # 分时量能 (上证+深证)
    def _minute_amt_map(data):
        return {m["time"]: float(m.get("amount_yi", 0) or 0) * 1e8 for m in data} if data else {}
    sh_min_amt = _minute_amt_map(index_data[0]["minute"])
    sz_min_amt = _minute_amt_map(index_data[1]["minute"])
    for m in index_data[0]["minute"]:
        t = m["time"]
        m["amount_yi"] = round((sh_min_amt.get(t, 0) + sz_min_amt.get(t, 0)) / 1e8, 2)
    # 5日分时量能（指数用 m5，与 ETF 一致）
    for dt in index_data[0]["m5_5day"]:
        sh5 = _minute_amt_map(index_data[0]["m5_5day"].get(dt, []))
        sz5 = _minute_amt_map(index_data[1]["m5_5day"].get(dt, []))
        for m in index_data[0]["m5_5day"][dt]:
            t = m["time"]
            m["amount_yi"] = round((sh5.get(t, 0) + sz5.get(t, 0)) / 1e8, 2)

    # ---- ETF 数据 ----
    etf_data = []
    etf_daily = load_daily("etf")
    for name in ETF_ORDER:
        kline = load_daily_kline(name, "etf")
        minute_m5 = load_minute(name, latest_date, "etf", m5=True) or load_minute(name, latest_date, "etf", m5=False)
        m5_all = load_all_minutes_m5(name, last5, "etf")
        # ETF m5 缺09:30点，补开盘价
        open_price = etf_daily.get(latest_date, {}).get(name, {}).get("开盘", 0)
        if minute_m5 and float(open_price) > 0:
            minute_m5.insert(0, {"time": "09:30", "price": str(open_price), "amount_yi": "0"})
        for dt in m5_all:
            kline_map = {k["date"]: k["open"] for k in kline}
            op = kline_map.get(dt, 0)
            if op > 0 and m5_all[dt]:
                m5_all[dt].insert(0, {"time": "09:30", "price": str(op), "amount_yi": "0"})
        etf_data.append({
            "name": name, "kline": kline, "minute": minute_m5,
            "m5_5day": {d: m5_all.get(d, []) for d in last5},
            "latest": etf_daily.get(latest_date, {}).get(name, {}),
            "code": etf_daily.get(latest_date, {}).get(name, {}).get("代码", ""),
        })

    # ---- MA5排行 ----
    ranking = []
    rank_file = os.path.join(RANK_DIR, f"ranking_{latest_date}.csv")
    if os.path.exists(rank_file):
        with open(rank_file, encoding="utf-8-sig") as f:
            ranking = list(csv.DictReader(f))

    # ---- 大宗交易 (近5日) ----
    watched_stocks, watched_codes, theme_order = load_watched_stocks()
    block_trades = []
    for d in last5:
        block_trades.extend(get_block_trades(d))

    # ---- 重点监控 ----
    monitor_data = []
    if os.path.exists(MONITOR_FILE):
        with open(MONITOR_FILE, encoding="utf-8") as f:
            monitor_data = json.load(f)

    # ---- 涨跌家数 (breadth_*.csv 存在则加载，否则回退到最近可用文件) ----
    breadth = {"上涨": "—", "下跌": "—", "涨停": "—", "跌停": "—"}
    breadth_loaded = False
    for try_date in [latest_date] + sorted(all_dates, reverse=True):
        bf = os.path.join(INDEX_DIR, f"breadth_{try_date}.csv")
        if os.path.exists(bf):
            try:
                with open(bf, encoding="utf-8-sig") as fh:
                    for row in csv.DictReader(fh):
                        breadth[row["指标"]] = row["数值"]
                breadth_loaded = True
                if try_date != latest_date:
                    breadth["_fallback_date"] = try_date
                break
            except Exception:
                pass

    # ---- 风险标签 ----
    stored = load_risk_tags()
    manual_tags = stored.get("manual", [])
    manual_tags_json = json.dumps(manual_tags, ensure_ascii=False)

    # ---- 机会标签 ----
    opp_stored = load_opp_tags()
    opp_manual = opp_stored.get("manual", [])
    opp_manual_json = json.dumps(opp_manual, ensure_ascii=False)

    # ---- 指数状态 ----
    idx_state = load_index_state()
    index_state_default = idx_state.get("state", "区间震荡")

    # ---- 嵌入 JSON ----
    index_json = json.dumps(index_data, ensure_ascii=False, default=str)
    etf_json = json.dumps(etf_data, ensure_ascii=False, default=str)
    rank_json = json.dumps(ranking[:30], ensure_ascii=False, default=str) if ranking else "[]"
    # 按题材树状结构 + 加权平均折溢率
    # 最新交易日（用于标记今日新增）
    latest_trade_date = max((r["日期"] for r in block_trades), default="")
    themes = {}  # theme -> {trades: [], total_amt: 0, weighted_rate: 0}
    for r in block_trades:
        if r["代码"] not in watched_codes and r["简称"] not in watched_stocks:
            continue
        info = watched_stocks.get(r["简称"], {})
        theme = info.get("theme", "其他")
        r["theme"] = theme
        r["note"] = info.get("note", "")
        if theme not in themes:
            themes[theme] = {"trades": [], "total_amt": 0, "weighted_sum": 0}
        amt = r["成交总额_万"]
        themes[theme]["trades"].append(r)
        themes[theme]["total_amt"] += amt
        themes[theme]["weighted_sum"] += r["折溢率"] * amt
    # 构建树 (按 interest_stock.md 题材顺序, 内层按日期降序)
    block_tree = []
    for theme in theme_order:
        if theme not in themes:
            continue
        t = themes[theme]
        wavg = t["weighted_sum"] / t["total_amt"] * 100 if t["total_amt"] > 0 else 0
        new_count = sum(1 for r in t["trades"] if r["日期"] == latest_trade_date)
        block_tree.append({
            "theme": theme,
            "wavg_rate": round(wavg, 2),
            "total_amt": round(t["total_amt"], 0),
            "count": len(t["trades"]),
            "new_count": new_count,
            "trades": sorted(t["trades"], key=lambda x: x["日期"], reverse=True),
        })
    block_json = json.dumps(block_tree, ensure_ascii=False, default=str)
    monitor_json = json.dumps(monitor_data, ensure_ascii=False, default=str)

    # ---- 板块数据 ----
    multi_report = find_latest_multi_report()
    sector_data = parse_sector_overview(multi_report) if multi_report else []

    # ---- 个股数据 (STOCK_MAP) ----
    watched_names = list(watched_stocks.keys())
    stock_map = load_stock_ohlcv_readonly(watched_names, watched_stocks, STOCK_DAYS)

    def _make_stock_js():
        """Serialize stock_map to JS STOCK_MAP, matching trend_view.html field names."""
        lines = []
        for name, info in stock_map.items():
            esc_name = name.replace("\\", "\\\\").replace("'", "\\'")
            esc_sector = info["sector"].replace("\\", "\\\\").replace("'", "\\'")
            lines.append(
                f"'{esc_name}':{{"
                f"s:'{esc_sector}',c:'{info['code']}',"
                f"d:{json.dumps(info['dates'])},"
                f"o:{json.dumps(info['open'])},"
                f"cl:{json.dumps(info['close'])},"
                f"l:{json.dumps(info['low'])},"
                f"h:{json.dumps(info['high'])},"
                f"a:{json.dumps(info['amounts'])},"
                f"lc:{json.dumps(info['latest_close'])},"
                f"pct:{json.dumps(info['pct_changes'])},"
                f"cum5d:{json.dumps(info['cum5d'])}"
                f"}}"
            )
        return "var STOCK_MAP={"+",".join(lines)+"};"

    stock_js_block = _make_stock_js() if stock_map else "var STOCK_MAP={};"
    sector_json = json.dumps(sector_data, ensure_ascii=False)

    # 计算每个题材的日平均涨幅 (d1-d5)，按降序排列 pills
    theme_avg_map = {}
    for s in sector_data:
        vals = []
        for key in ["d1", "d2", "d3", "d4", "d5"]:
            v = s.get(key, "")
            if v and v != "-":
                try:
                    vals.append(float(v.replace("%", "")))
                except ValueError:
                    pass
        if vals:
            theme_avg_map[s["name"]] = round(sum(vals) / len(vals), 2)
    theme_order_sorted = sorted(theme_order, key=lambda t: theme_avg_map.get(t, -999), reverse=True)
    theme_order_json = json.dumps(theme_order_sorted, ensure_ascii=False)
    theme_avg_json = json.dumps(theme_avg_map, ensure_ascii=False)
    strongest_sector = theme_order_sorted[0] if theme_order_sorted else ""
    stock_count = len(stock_map)
    filter_tags = load_filter_tags()
    filter_tags_json = json.dumps(filter_tags, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>中长线逻辑识别 · 数据看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0e17;color:#e0e6ed;font-family:'Fira Sans',-apple-system,sans-serif;min-height:100vh}}
.header{{padding:20px 24px 12px;border-bottom:1px solid #1e2a3a;display:flex;justify-content:space-between;align-items:flex-end}}
.header h1{{font-size:20px;font-weight:600;color:#f0f4f8}}
.header .date{{font-size:13px;color:#6b7d95}}

/* Tabs */
.tabs{{display:flex;gap:0;padding:0 24px;border-bottom:1px solid #1e2a3a}}
.tab-btn{{padding:10px 20px;font-size:13px;color:#6b7d95;background:none;border:none;border-bottom:2px solid transparent;cursor:pointer;transition:all .2s;font-family:inherit}}
.tab-btn:hover{{color:#94a3b8}}
.tab-btn.active{{color:#3b82f6;border-bottom-color:#3b82f6}}

.tab-pane{{display:none;padding:16px 24px}}
.tab-pane.active{{display:block}}

/* Indicators */
.indicators{{display:flex;gap:24px;padding:14px 24px;border-bottom:1px solid #1e2a3a;flex-wrap:wrap}}
.indicator{{display:flex;flex-direction:column}}
.indicator .label{{font-size:11px;color:#6b7d95;text-transform:uppercase;letter-spacing:.5px}}
.indicator .value{{font-size:22px;font-weight:700;color:#f0f4f8;margin-top:2px}}
.value.up{{color:#ef4444}}
.value.down{{color:#10b981}}
.indicator .sub{{font-size:12px;color:#3b82f6;margin-top:2px}}
.risk-tags{{display:flex;gap:8px;padding:8px 24px 14px;flex-wrap:wrap}}
.risk-tag{{padding:4px 12px;border-radius:12px;font-size:11px;background:#2d1506;color:#f97316;border:1px solid #7c2d12;display:inline-flex;align-items:center;gap:4px;cursor:default}}
.risk-tag .del{{cursor:pointer;color:#c2410c;margin-left:2px;font-weight:700;line-height:1}}
.risk-tag .del:hover{{color:#ef4444}}
.risk-tag-add{{padding:4px 10px;border-radius:12px;font-size:11px;background:transparent;color:#f97316;border:1px dashed #7c2d12;cursor:pointer;font-family:inherit}}
.risk-tag-add:hover{{background:#2d1506}}
.opp-tags{{display:flex;gap:8px;padding:8px 24px 14px;flex-wrap:wrap}}
.opp-tag{{padding:4px 12px;border-radius:12px;font-size:11px;background:#0a1f0a;color:#84cc16;border:1px solid #3f6212;display:inline-flex;align-items:center;gap:4px;cursor:default}}
.opp-tag .del{{cursor:pointer;color:#65a30d;margin-left:2px;font-weight:700;line-height:1}}
.opp-tag .del:hover{{color:#ef4444}}
.opp-tag-add{{padding:4px 10px;border-radius:12px;font-size:11px;background:transparent;color:#84cc16;border:1px dashed #3f6212;cursor:pointer;font-family:inherit}}
.opp-tag-add:hover{{background:#0a1f0a}}
.monitor-form{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 0}}
.monitor-form input,.monitor-form button,.monitor-form select{{padding:6px 10px;border-radius:6px;font-size:12px;background:#1e2a3a;color:#e0e6ed;border:1px solid #334155;font-family:inherit}}
.monitor-form input{{width:100px}}
.monitor-form input[type=date]{{width:130px;color-scheme:dark}}
.monitor-form button{{cursor:pointer;background:#1d4ed8;border-color:#1d4ed8;color:#fff}}
.monitor-form button:hover{{background:#2563eb}}
.del-btn{{background:transparent;border:1px solid transparent;color:#6b7d95;cursor:pointer;font-size:14px;padding:0 6px;border-radius:4px;line-height:1.2}}
.del-btn:hover{{color:#ef4444;background:#2d1506}}
.monitor-inactive{{color:#6b7d95}}
.monitor-active{{color:#e0e6ed}}

/* Grid */
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;max-width:1600px}}
.grid-row-header{{grid-column:1/-1;font-size:14px;font-weight:600;color:#3b82f6;padding:8px 0 4px;border-bottom:1px solid #1e2a3a;margin-top:8px;display:flex;justify-content:space-between;align-items:center}}
.card{{background:#111827;border:1px solid #1e2a3a;border-radius:8px;padding:12px;display:flex;flex-direction:column}}
.card .card-title{{font-size:12px;color:#6b7d95;margin-bottom:6px;display:flex;justify-content:space-between}}
.card .card-title .name{{color:#e0e6ed;font-weight:600}}
.card .chart{{flex:1;min-height:260px}}
.card .kline-controls{{display:flex;gap:6px;align-items:center}}
.card .kline-controls button{{background:#1e2a3a;color:#94a3b8;border:1px solid #334155;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:11px}}
.card .kline-controls button:hover{{background:#2a3a4e;color:#e0e6ed}}
.card .kline-controls .range-info{{font-size:10px;color:#6b7d95;min-width:70px;text-align:center}}

/* MA5 排行表 */
.rank-table{{width:100%;border-collapse:collapse;font-size:12px}}
.block-table{{table-layout:fixed}}
.block-table th:nth-child(1),.block-table td:nth-child(1){{width:15%}}
.block-table th:nth-child(2),.block-table td:nth-child(2){{width:8%}}
.block-table th:nth-child(3),.block-table td:nth-child(3){{width:10%}}
.block-table th:nth-child(4),.block-table td:nth-child(4){{width:7%}}
.block-table th:nth-child(5),.block-table td:nth-child(5){{width:10%}}
.block-table th:nth-child(6),.block-table td:nth-child(6){{width:10%}}
.block-table th:nth-child(7),.block-table td:nth-child(7){{width:auto}}
.rank-table th{{text-align:left;padding:6px 8px;color:#6b7d95;font-weight:500;border-bottom:1px solid #1e2a3a;position:sticky;top:0;background:#0a0e17}}
.rank-table td{{padding:5px 8px;border-bottom:1px solid #111827}}
.rank-table .up{{color:#ef4444}}
.rank-table .down{{color:#10b981}}
.rank-table tr:hover td{{background:#1a2030}}
.rank-wrap{{max-height:600px;overflow-y:auto;border:1px solid #1e2a3a;border-radius:8px}}

/* Empty state */
.empty-state{{text-align:center;padding:60px 20px;color:#6b7d95}}
.empty-state .icon{{font-size:32px;margin-bottom:12px}}
.empty-state p{{font-size:13px}}

/* Tooltip hint */
.tooltip-hint{{position:relative;display:inline-block;color:#4b5563;cursor:help;font-weight:700;width:16px;text-align:center;border:1px solid #374151;border-radius:50%;font-size:10px;line-height:14px}}
.tooltip-hint:hover .tooltip-text{{visibility:visible;opacity:1}}
.tooltip-text{{visibility:hidden;opacity:0;position:absolute;bottom:140%;left:50%;transform:translateX(-50%);background:#1e293b;color:#e0e6ed;font-size:11px;font-weight:400;padding:5px 10px;border-radius:6px;border:1px solid #334155;white-space:nowrap;z-index:100;transition:opacity .15s;pointer-events:none}}
.tooltip-text::after{{content:'';position:absolute;top:100%;left:50%;transform:translateX(-50%);border:5px solid transparent;border-top-color:#334155}}

/* Theme pills */
.theme-pills{{display:flex;gap:6px;padding:0 24px 14px;flex-wrap:wrap}}
.theme-pill{{position:relative;padding:5px 14px;border-radius:16px;font-size:12px;background:transparent;color:#94a3b8;border:1px solid #334155;cursor:pointer;transition:all .2s;font-family:inherit;white-space:nowrap}}
.theme-pill:hover{{border-color:#3b82f6;color:#e0e6ed}}
.theme-pill.active{{background:#1d4ed8;border-color:#3b82f6;color:#fff}}
.filter-dot{{position:absolute;top:-3px;right:-3px;width:8px;height:8px;border-radius:50%;background:#10b981;border:1px solid #0a0e17;cursor:help}}
.filter-tag{{display:inline-block;margin-left:4px;padding:1px 6px;border-radius:8px;font-size:10px;font-weight:500;background:#0a2a1a;color:#10b981;border:1px solid #1a4a2a;vertical-align:middle}}

/* Stock card grid */
.stock-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;padding:0 24px 16px;max-width:1800px}}
.stock-card{{background:#111827;border:1px solid #1e2a3a;border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;min-width:0}}
.stock-card:hover{{border-color:#3b82f6}}
.stock-card .stock-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:2px}}
.stock-card .stock-name{{font-size:13px;font-weight:600;color:#e0e6ed;line-height:1.3}}
.stock-card .stock-code{{font-size:11px;color:#6b7d95;margin-left:6px;font-weight:400}}
.stock-card .stock-theme{{font-size:10px;color:#4b5563;margin-top:1px}}
.stock-card .stock-price{{font-size:12px;color:#6b7d95;text-align:right;white-space:nowrap}}
.stock-card .stock-price .val{{font-size:15px;font-weight:600;color:#f0f4f8}}
.stock-card .stock-price .pct{{font-size:12px;margin-left:4px;font-weight:600}}
.stock-card .stock-price .pct.up{{color:#ef4444}}
.stock-card .stock-price .pct.down{{color:#10b981}}
.stock-chart{{height:200px;margin-top:2px}}

/* Sector table rows */
.sector-row{{cursor:pointer;transition:background .15s}}
.sector-row:hover td{{background:#1a2744 !important}}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>数据看板</h1>
    <div class="date">数据日期: {latest_date}</div>
  </div>
</div>

<div class="indicators">
  <div class="indicator"><span class="label">上涨家数</span><span class="value up">{breadth.get('上涨','—')}</span></div>
  <div class="indicator"><span class="label">下跌家数</span><span class="value down">{breadth.get('下跌','—')}</span></div>
  <div class="indicator"><span class="label">涨停</span><span class="value up">{breadth.get('涨停','—')}</span></div>
  <div class="indicator"><span class="label">跌停</span><span class="value down">{breadth.get('跌停','—')}</span></div>
  <div class="indicator">
    <span class="label">成交额</span>
    <span class="value">{total_amt_yi:.2f}<span class="sub">万亿</span></span>
  </div>
  <div class="indicator" id="index-state-indicator">
    <span class="label">指数状态</span>
    <select id="index-state-select" onchange="onIndexStateChange(this.value)" style="padding:4px 8px;border-radius:6px;font-size:12px;background:#1e2a3a;color:#e0e6ed;border:1px solid #334155;font-family:inherit;cursor:pointer;margin-top:2px">
      <option value="区间震荡">区间震荡</option>
      <option value="主升">主升</option>
      <option value="破位下跌">破位下跌</option>
    </select>
  </div>
</div>
<div class="risk-tags" id="risk-tags">
</div>
<div class="opp-tags" id="opp-tags">
</div>

<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('index')">指数宏观</button>
  <button class="tab-btn" onclick="switchTab('etf')">ETF宏观</button>
  <button class="tab-btn" onclick="switchTab('sector')">板块数据</button>
  <button class="tab-btn" onclick="switchTab('stock')">个股数据</button>
  <button class="tab-btn" onclick="switchTab('block')">大宗交易</button>
  <button class="tab-btn" onclick="switchTab('monitor')">重点监控</button>
</div>

<!-- ====== Tab 1: 指数宏观 ====== -->
<div class="tab-pane active" id="tab-index">
  <div class="grid" id="grid-index"></div>
</div>

<!-- ====== Tab 2: ETF宏观 ====== -->
<div class="tab-pane" id="tab-etf">
  <div class="grid" id="grid-etf"></div>
</div>

<!-- ====== Tab 3: 板块数据 ====== -->
<div class="tab-pane" id="tab-sector">
  <div class="rank-wrap" style="max-height:none">
    <table class="rank-table" id="sector-table">
      <thead><tr>
        <th>题材名称</th><th>状态</th><th>d1</th><th>d2</th><th>d3</th><th>d4</th><th>d5</th><th>近5日涨幅</th><th>涨超5%占比</th><th>明星标的</th>
      </tr></thead>
      <tbody id="sector-table-body"></tbody>
    </table>
  </div>
</div>

<!-- ====== Tab 4: 个股数据 ====== -->
<div class="tab-pane" id="tab-stock">
  <div class="theme-pills" id="theme-pills"></div>
  <div class="stock-grid" id="grid-stock"></div>
</div>

<!-- ====== Tab 5: 大宗交易 ====== -->
<div class="tab-pane" id="tab-block">
  <div id="block-table"></div>
</div>

<!-- ====== Tab 6: 重点监控 ====== -->
<div class="tab-pane" id="tab-monitor">
  <div id="monitor-add" style="margin-bottom:12px"></div>
  <div id="monitor-table"></div>
</div>

<script>
// ========== 个股数据 (内嵌 STOCK_MAP) ==========
{stock_js_block}

// ========== 风险标签 ==========
var _riskState = {{"manual":[],"deleted":[]}};

function loadRiskState() {{
  fetch('/api/risk-tags').then(r=>r.json()).then(function(d){{ _riskState=d; renderRiskTags(); }}).catch(function(){{ renderRiskTags(); }});
}}
function saveRiskState(s) {{
  _riskState = s;
  fetch('/api/risk-tags',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(s)}}).catch(function(){{}});
}}

function renderRiskTags() {{
  var st = _riskState;
  var manual = st.manual || [];
  var deleted = st.deleted || [];
  var all = manual.filter(function(t){{return deleted.indexOf(t)<0;}});
  var el = document.getElementById('risk-tags');
  if (!el) return;
  el.innerHTML = '';
  var lbl = document.createElement('span');
  lbl.style.cssText = 'font-size:11px;color:#6b7d95;margin-right:4px;line-height:22px';
  lbl.textContent = '风险提示';
  el.appendChild(lbl);
  all.forEach(function(t){{
    var span = document.createElement('span');
    span.className = 'risk-tag';
    span.innerHTML = t + '<span class=\"del\" data-tag=\"'+t+'\" onclick=\"deleteRiskTag(this)\">×</span>';
    el.appendChild(span);
  }});
  var btn = document.createElement('button');
  btn.className = 'risk-tag-add';
  btn.textContent = '+';
  el.appendChild(btn);
  btn.onclick = function(){{ var inp=document.createElement('input'); inp.type='text'; inp.placeholder='风险标签'; inp.style.cssText='width:80px;background:#1e2a3a;color:#f97316;border:1px solid #7c2d12;border-radius:8px;padding:2px 8px;font-size:11px;margin-left:4px;outline:none'; inp.onblur=function(){{ var v=inp.value.trim(); inp.remove(); if(v) addRiskTag(v); }}; inp.onkeydown=function(e){{ if(e.key==='Enter'){{ var v=inp.value.trim(); inp.remove(); if(v) addRiskTag(v); }}}}; el.appendChild(inp); setTimeout(function(){{inp.focus();}},50); }};
}}

function addRiskTag(tag) {{
  if (!_riskState.manual) _riskState.manual = [];
  if (_riskState.manual.indexOf(tag)<0) _riskState.manual.push(tag);
  var delIdx = (_riskState.deleted||[]).indexOf(tag);
  if (delIdx>=0) _riskState.deleted.splice(delIdx,1);
  saveRiskState(_riskState);
  renderRiskTags();
}}

function deleteRiskTag(el) {{
  var tag = el.getAttribute('data-tag');
  var mIdx = (_riskState.manual||[]).indexOf(tag);
  if (mIdx>=0) {{ _riskState.manual.splice(mIdx,1); }}
  else {{
    if (!_riskState.deleted) _riskState.deleted = [];
    if (_riskState.deleted.indexOf(tag)<0) _riskState.deleted.push(tag);
  }}
  saveRiskState(_riskState);
  renderRiskTags();
}}

// ========== 机会标签 ==========
var _oppState = {{"manual":[],"deleted":[]}};

function loadOppState() {{
  fetch('/api/opp-tags').then(r=>r.json()).then(function(d){{ _oppState=d; renderOppTags(); }}).catch(function(){{ renderOppTags(); }});
}}
function saveOppState(s) {{
  _oppState = s;
  fetch('/api/opp-tags',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(s)}}).catch(function(){{}});
}}

function renderOppTags() {{
  var st = _oppState;
  var manual = st.manual || [];
  var deleted = st.deleted || [];
  var all = manual.filter(function(t){{return deleted.indexOf(t)<0;}});
  var el = document.getElementById('opp-tags');
  if (!el) return;
  el.innerHTML = '';
  var lbl = document.createElement('span');
  lbl.style.cssText = 'font-size:11px;color:#6b7d95;margin-right:4px;line-height:22px';
  lbl.textContent = '机会提示';
  el.appendChild(lbl);
  all.forEach(function(t){{
    var span = document.createElement('span');
    span.className = 'opp-tag';
    span.innerHTML = t + '<span class=\"del\" data-tag=\"'+t+'\" onclick=\"deleteOppTag(this)\">×</span>';
    el.appendChild(span);
  }});
  var btn = document.createElement('button');
  btn.className = 'opp-tag-add';
  btn.textContent = '+';
  el.appendChild(btn);
  btn.onclick = function(){{ var inp=document.createElement('input'); inp.type='text'; inp.placeholder='机会标签'; inp.style.cssText='width:80px;background:#0a1f0a;color:#84cc16;border:1px solid #3f6212;border-radius:8px;padding:2px 8px;font-size:11px;margin-left:4px;outline:none'; inp.onblur=function(){{ var v=inp.value.trim(); inp.remove(); if(v) addOppTag(v); }}; inp.onkeydown=function(e){{ if(e.key==='Enter'){{ var v=inp.value.trim(); inp.remove(); if(v) addOppTag(v); }}}}; el.appendChild(inp); setTimeout(function(){{inp.focus();}},50); }};
}}

function addOppTag(tag) {{
  if (!_oppState.manual) _oppState.manual = [];
  if (_oppState.manual.indexOf(tag)<0) _oppState.manual.push(tag);
  var delIdx = (_oppState.deleted||[]).indexOf(tag);
  if (delIdx>=0) _oppState.deleted.splice(delIdx,1);
  saveOppState(_oppState);
  renderOppTags();
}}

function deleteOppTag(el) {{
  var tag = el.getAttribute('data-tag');
  var mIdx = (_oppState.manual||[]).indexOf(tag);
  if (mIdx>=0) {{ _oppState.manual.splice(mIdx,1); }}
  else {{
    if (!_oppState.deleted) _oppState.deleted = [];
    if (_oppState.deleted.indexOf(tag)<0) _oppState.deleted.push(tag);
  }}
  saveOppState(_oppState);
  renderOppTags();
}}

window.onerror = function(msg) {{
  if (typeof msg==='string' && msg.indexOf('getBoundingClientRect')>=0) return true;
}};
// ========== Tab 切换 ==========
var _tabRendered = {{}};
function switchTab(name) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelector('.tab-btn[onclick*="'+name+'"]').classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
  if (!_tabRendered[name]) {{
    _tabRendered[name] = true;
    if (name==='etf') buildEtfGrid();
    if (name==='sector') buildSectorTable();
    if (name==='stock') buildStockTab();
    if (name==='block') buildBlockTable();
    if (name==='monitor') buildMonitorTab();
  }}
}}

// ========== 图表工具 ==========
const COLORS = {json.dumps(COLORS, ensure_ascii=False)};
const BASE_OPTION = {{
  backgroundColor:'transparent',
  textStyle:{{color:'#94a3b8',fontSize:10}},
  animation:false,
  tooltip:{{trigger:'axis',backgroundColor:'#1e293b',borderColor:'#334155',textStyle:{{color:'#e0e6ed',fontSize:11}}}},
}};

function makeChart(domId, option) {{
  const el = document.getElementById(domId);
  if (!el) return null;
  try {{
    const c = echarts.init(el, null, {{renderer:'canvas'}});
    c.setOption(Object.assign({{}}, BASE_OPTION, option));
    return c;
  }} catch(e) {{ return null; }}
}}

// ========== 分时图 ==========
function renderMinute(chartId, minuteData, prevClose, indexName, isETF) {{
  if (!minuteData.length) return;
  const times = minuteData.map(m=>m.time);
  const prices = minuteData.map(m=>parseFloat(m.price));
  const amounts = minuteData.map(m=>parseFloat(m.amount_yi||0));
  let amountDiffs = isETF ? amounts : amounts.map((v,i)=>i===0?Math.max(0,v):Math.max(0,v-amounts[i-1]));
  // ETF: 量能柱左移一格，末位填0
  if (isETF) {{
    for (var si=0;si<amountDiffs.length-1;si++) amountDiffs[si]=amountDiffs[si+1];
    amountDiffs[amountDiffs.length-1]=0;
  }}
  const barColors = prices.map((p,i)=>{{
    const n=(prices[i+1]??p)-p;
    return n>0?'#ef4444':n<0?'#10b981':'#6b7280';
  }});
  const pc = parseFloat(prevClose) || prices[0] || 1;
  const pcts = prices.map(p=>((p-pc)/pc*100).toFixed(2));
  // 参考线: 按百分比涨幅画水平线
  const pMax = Math.max(...prices), pMin = Math.min(...prices);
  const pad = (pMax-pMin)*0.08;
  const axisMin = pMin-pad, axisMax = pMax+pad;
  const maxPct = (pMax-pc)/pc*100, minPct = (pMin-pc)/pc*100;
  const gainLevels = maxPct<0.3 ? [0.1,0.2] : [0.3,0.6,0.9];
  const lossLevels = minPct>-0.3 ? [-0.1,-0.2] : [-0.3,-0.6,-0.9];
  const markData = [];
  // 上证指数专属: 百分比涨跌参考线
  if (indexName==='上证指数') {{
    gainLevels.forEach(pct=>{{
      const price = pc*(1+pct/100);
      markData.push({{yAxis:price,label:{{show:true,formatter:'+'+pct+'%',position:'insideEndTop',fontSize:8,color:'#ef4444'}},lineStyle:{{color:'rgba(239,68,68,0.35)',type:'dashed',width:1}}}});
    }});
    lossLevels.forEach(pct=>{{
      const price = pc*(1+pct/100);
      markData.push({{yAxis:price,label:{{show:true,formatter:pct+'%',position:'insideEndTop',fontSize:8,color:'#10b981'}},lineStyle:{{color:'rgba(16,185,129,0.35)',type:'dashed',width:1}}}});
    }});
    // 100点整数倍辅助线
    const step = 100;
    for (let p=Math.ceil(axisMin/step)*step; p<=axisMax; p+=step) {{
      markData.push({{yAxis:p,label:{{show:false}},lineStyle:{{color:'rgba(148,163,184,0.2)',type:'dashed',width:1}}}});
    }}
  }}
  makeChart(chartId, {{
    grid:[
      {{top:4,left:44,right:4,height:'66%'}},
      {{top:'72%',left:44,right:4,height:'22%'}},
    ],
    xAxis:[
      {{type:'category',data:times,gridIndex:0,axisLine:{{show:false}},axisTick:{{show:false}},axisLabel:{{show:false}}}},
      {{type:'category',data:times,gridIndex:1,axisLine:{{show:false}},axisTick:{{show:false}},axisLabel:{{show:false}}}},
    ],
    yAxis:[
      {{type:'value',gridIndex:0,min:axisMin,max:axisMax,splitLine:{{lineStyle:{{color:'#111827'}}}},axisLabel:{{show:indexName==='上证指数'}},position:'left'}},
      {{type:'value',gridIndex:1,splitLine:{{show:false}},axisLabel:{{show:false}},axisTick:{{show:false}}}},
    ],
    series:[
      {{name:'价格',type:'line',xAxisIndex:0,yAxisIndex:0,data:prices,smooth:true,lineStyle:{{color:'#3b82f6',width:1.5}},symbol:'none',
        markLine:{{silent:true,symbol:'none',data:markData}}}},
      {{name:'成交(亿)',type:'bar',xAxisIndex:1,yAxisIndex:1,data:amountDiffs,
        itemStyle:{{color:function(p){{return barColors[p.dataIndex];}}}}}},
    ],
    tooltip:{{trigger:'axis',backgroundColor:'#1e293b',borderColor:'#334155',textStyle:{{color:'#e0e6ed',fontSize:11}},
      formatter:function(params){{var h='',amt=null;for(var i=0;i<params.length;i++){{var p=params[i];if(p.seriesName==='价格'){{h=p.axisValue+'<br/>价格：'+p.value;}}else if(p.seriesName==='成交(亿)'){{amt=p.value;}}}}if(amt!=null&&amt>0)h+='<br/>成交：'+amt.toFixed(1)+'亿';return h;}}}},
  }});
}}

// ========== 日K线 (带 +/- 控制) ==========
function renderKline(chartId, klineAll, indexName) {{
  if (!klineAll.length) return;
  const DEFAULT_MONTHS = 2;  // 默认显示近2个月
  let windowStart = Math.max(0, klineAll.length - 22 * DEFAULT_MONTHS);

  function render() {{
    const k = klineAll.slice(windowStart);
    if (!k.length) return;
    const dates = k.map(r=>r.date.slice(5));
    const kdata = k.map(r=>[r.open,r.close,r.low,r.high]);
    const amounts = k.map(r=>(r.amount||0)/1e8);
    const barColors = k.map(r=>r.close>=r.open?'#ef4444':'#10b981');
    // 上证指数固定关键位
    const kMarkLine = [];
    if (indexName==='上证指数') {{
      const fixedLines = [
        {{y:4200,color:'#10b981',label:'4200'}},
        {{y:4050,color:'#fbbf24',label:'4050'}},
        {{y:4000,color:'#ef4444',label:'4000'}},
        {{y:3940,color:'#ef4444',label:'3940'}},
        {{y:3794,color:'#ef4444',label:'3794'}},
      ];
      fixedLines.forEach(fl=>{{
        kMarkLine.push({{yAxis:fl.y,label:{{show:false}},lineStyle:{{color:fl.color,type:'solid',width:0.8}}}});
      }});
    }}
    const chart = makeChart(chartId, {{
      grid:[
        {{top:4,left:44,right:4,height:'60%'}},
        {{top:'68%',left:44,right:4,height:'20%',bottom:16}},
      ],
      xAxis:[
        {{type:'category',data:dates,gridIndex:0,axisLine:{{show:false}},axisTick:{{show:false}},axisLabel:{{show:false}},boundaryGap:true}},
        {{type:'category',data:dates,gridIndex:1,axisLine:{{lineStyle:{{color:'#334155'}}}},boundaryGap:true}},
      ],
      yAxis:[
        {{type:'value',gridIndex:0,scale:true,splitLine:{{lineStyle:{{color:'#111827'}}}},position:'left'}},
        {{type:'value',gridIndex:1,splitLine:{{show:false}},axisLabel:{{show:false}},axisTick:{{show:false}}}},
      ],
      series:[
        {{name:'K线',type:'candlestick',xAxisIndex:0,yAxisIndex:0,data:kdata,
          itemStyle:{{color:'#ef4444',color0:'#10b981',borderColor:'#ef4444',borderColor0:'#10b981'}},
          markLine:{{silent:true,symbol:'none',data:kMarkLine}}}},
        {{name:'成交额(亿)',type:'bar',xAxisIndex:1,yAxisIndex:1,data:amounts,
          itemStyle:{{color:function(p){{return barColors[p.dataIndex];}}}},
          markLine:{{silent:true,symbol:'none',
            data:indexName==='上证指数'?[{{yAxis:30000,label:{{show:true,formatter:'3万亿',position:'insideStartTop',fontSize:8,color:'#cbd5e1'}},lineStyle:{{color:'rgba(203,213,225,0.5)',type:'dashed',width:1.5}}}}]:[]}}}},
      ],
      tooltip:{{trigger:'axis',backgroundColor:'#1e293b',borderColor:'#334155',textStyle:{{color:'#e0e6ed',fontSize:11}},transitionDuration:0,
        formatter:function(params){{var h='',amt=null,dt=null;for(var i=0;i<params.length;i++){{var p=params[i];dt=p.axisValue;if(p.seriesName==='K线'){{var idx=p.dataIndex,d=k[idx],pc=idx>0?k[idx-1].close:p.data[2],chg=((p.data[2]-pc)/pc*100).toFixed(2),sign=chg>=0?'+':'',c=chg>0?'#ef4444':chg<0?'#10b981':'#e0e6ed';h=dt+'<br/>开盘：'+p.data[1]+'<br/>收盘：'+p.data[2]+'<br/>最高：'+p.data[4]+'<br/>最低：'+p.data[3]+'<br/><span style=\"color:'+c+'\">涨跌幅：'+sign+chg+'%</span>';}}else if(p.seriesName==='成交额(亿)'){{amt=p.value;}}}}if(!h&&dt)h=dt;if(amt!=null)h+='<br/>成交额：'+(amt>=10000?(amt/10000).toFixed(2)+'万亿':amt.toFixed(0)+'亿');return h;}}}},
    }});
    // 更新范围文字
    const info = document.getElementById(chartId+'_range');
    if (info) {{
      info.textContent = k[0].date.slice(5) + ' ~ ' + k[k.length-1].date.slice(5);
    }}
  }}

  // 暴露加减方法
  window[chartId+'_minus'] = function() {{
    windowStart = Math.max(0, windowStart - 22);
    render();
  }};
  window[chartId+'_plus'] = function() {{
    if (windowStart + 22 < klineAll.length) {{
      windowStart += 22;
      render();
    }}
  }};

  render();
}}

// ========== 5日分时 ==========
function render5Day(chartId, m5ByDate, isETF) {{
  const dates = Object.keys(m5ByDate).sort();
  if (!dates.length) return;

  let allPrices = [], allAmounts = [], allLabels = [], markLines = [];
  dates.forEach((d, di) => {{
    const pts = (m5ByDate[d]||[]).sort((a,b)=>a.time.localeCompare(b.time));
    if (isETF && !pts.length) return;
    allLabels.push(d.slice(5));
    allPrices.push(null); allAmounts.push(null);
    pts.forEach(m => {{
      allLabels.push(m.time);
      allPrices.push(parseFloat(m.price));
      allAmounts.push(parseFloat(m.amount_yi||0));
    }});
    if (di < dates.length-1) {{
      var sepLabel = 'sep_'+di;
      allLabels.push(sepLabel);
      allPrices.push(null); allAmounts.push(null);
      markLines.push({{xAxis:sepLabel,label:{{show:true,formatter:dates[di+1].slice(5),position:'start',fontSize:9,color:'#94a3b8'}},lineStyle:{{color:'#475569',width:1,type:'solid'}}}});
    }}
  }});

  if (!allPrices.length) return;

  // 量能差分 + 颜色 (ETF m5: per-interval, 不需要diff + 左移)
  var amountDiffs = [], barColors = [];
  for (var i=0;i<allAmounts.length;i++) {{
    if (allAmounts[i]==null) {{ amountDiffs.push(null); }}
    else if (isETF) {{ amountDiffs.push(allAmounts[i]); }}
    else if (i===0 || (i>0 && allAmounts[i-1]==null)) {{ amountDiffs.push(Math.max(0, allAmounts[i])); }}
    else {{ amountDiffs.push(Math.max(0, allAmounts[i]-allAmounts[i-1])); }}
    var pCur=allPrices[i], pNext=i<allPrices.length-1?allPrices[i+1]:pCur;
    if (pCur==null) {{ barColors.push('transparent'); }}
    else if (pNext==null || pNext>pCur) {{ barColors.push('#ef4444'); }}
    else if (pNext<pCur) {{ barColors.push('#10b981'); }}
    else {{ barColors.push('#6b7280'); }}
  }}
  // ETF: 量能柱左移一格，末位填0
  if (isETF) {{
    for (var si=0;si<amountDiffs.length-1;si++) amountDiffs[si]=amountDiffs[si+1];
    amountDiffs[amountDiffs.length-1]=0;
  }}

  makeChart(chartId, {{
    grid:[
      {{top:4,left:44,right:4,height:'66%'}},
      {{top:'72%',left:44,right:4,height:'22%'}},
    ],
    xAxis:[
      {{type:'category',data:allLabels,gridIndex:0,axisLine:{{show:false}},axisTick:{{show:false}},axisLabel:{{show:false}}}},
      {{type:'category',data:allLabels,gridIndex:1,axisLine:{{show:false}},axisTick:{{show:false}},axisLabel:{{show:false}}}},
    ],
    yAxis:[
      {{type:'value',gridIndex:0,scale:true,splitLine:{{lineStyle:{{color:'#111827'}}}},position:'left',axisLabel:{{show:false}}}},
      {{type:'value',gridIndex:1,splitLine:{{show:false}},axisLabel:{{show:false}},axisTick:{{show:false}}}},
    ],
    series:[
      {{name:'价格',type:'line',xAxisIndex:0,yAxisIndex:0,data:allPrices,smooth:true,connectNulls:false,
        lineStyle:{{color:'#3b82f6',width:1.2}},symbol:'none',
        markLine:{{silent:true,symbol:'none',data:markLines}}}},
      {{name:'成交(亿)',type:'bar',xAxisIndex:1,yAxisIndex:1,data:amountDiffs,
        itemStyle:{{color:function(p){{return barColors[p.dataIndex];}}}}}},
    ],
    tooltip:{{trigger:'axis',backgroundColor:'#1e293b',borderColor:'#334155',textStyle:{{color:'#e0e6ed',fontSize:11}},transitionDuration:0,
      formatter:function(params){{var v=null,amt=null;for(var i=0;i<params.length;i++){{if(params[i].seriesName==='价格'&&params[i].value!=null)v=params[i];else if(params[i].seriesName==='成交(亿)')amt=params[i].value;}}var h=v?params[0].axisValue+'<br/>价格：'+v.value:'';if(amt!=null&&amt>0)h+='<br/>成交：'+amt.toFixed(1)+'亿';return h;}}}},
  }});
}}

// ========== 构建指数网格 ==========
function buildIndexGrid() {{
  const DATA = {index_json};
  const grid = document.getElementById('grid-index');
  if (!grid) return;

  DATA.forEach((item, idx) => {{
    const bid = 'idx_'+idx;

    // Row header
    const hdr = document.createElement('div');
    hdr.className = 'grid-row-header';
    hdr.textContent = item.name + ' ' + (item.code||'');
    grid.appendChild(hdr);

    // Col 1: 分时
    const c1 = document.createElement('div'); c1.className = 'card';
    const klinePrev = item.kline.length>=2 ? item.kline[item.kline.length-2] : null;
    const prevClose = klinePrev ? (klinePrev.close||0) : (item.latest['开盘']||0);
    const latestChg = prevClose ? ((parseFloat(item.latest['收盘']||item.latest.close||0)-parseFloat(prevClose))/parseFloat(prevClose)*100).toFixed(2) : '—';
    c1.innerHTML = '<div class="card-title"><span class="name">分时走势</span><span style="color:'+(latestChg>=0?'#ef4444':'#10b981')+'">'+(latestChg>=0?'+':'')+latestChg+'%</span></div><div class="chart" id="'+bid+'_m"></div>';
    grid.appendChild(c1);
    renderMinute(bid+'_m', item.minute, prevClose, item.name);

    // Col 2: 日K线
    const c2 = document.createElement('div'); c2.className = 'card';
    c2.innerHTML = '<div class="card-title"><span class="name">日K线</span><div class="kline-controls"><button onclick="'+bid+'_k_minus()">-</button><span class="range-info" id="'+bid+'_k_range">—</span><button onclick="'+bid+'_k_plus()">+</button></div></div><div class="chart" id="'+bid+'_k"></div>';
    grid.appendChild(c2);
    renderKline(bid+'_k', item.kline, item.name);

    // Col 3: 5日分时
    const c3 = document.createElement('div'); c3.className = 'card';
    c3.innerHTML = '<div class="card-title"><span class="name">5日分时</span></div><div class="chart" id="'+bid+'_5d"></div>';
    grid.appendChild(c3);
    render5Day(bid+'_5d', item.m5_5day, true);
  }});
}}

// ========== 构建 ETF 网格 ==========
function buildEtfGrid() {{
  const DATA = {etf_json};
  const grid = document.getElementById('grid-etf');
  if (!grid || !DATA.length) return;

  DATA.forEach((item, idx) => {{
    const bid = 'etf_'+idx;

    const hdr = document.createElement('div');
    hdr.className = 'grid-row-header';
    hdr.textContent = item.name + ' ' + (item.code||'');
    grid.appendChild(hdr);

    // 分时 (ETF 用 m5)
    const c1 = document.createElement('div'); c1.className = 'card';
    const etfKlinePrev = item.kline.length>=2 ? item.kline[item.kline.length-2] : null;
    const etfPrevClose = etfKlinePrev ? (etfKlinePrev.close||0) : (item.latest['开盘']||0);
    const etfChg = etfPrevClose ? ((parseFloat(item.latest['收盘']||item.latest.close||0)-parseFloat(etfPrevClose))/parseFloat(etfPrevClose)*100).toFixed(2) : '—';
    c1.innerHTML = '<div class="card-title"><span class="name">分时走势</span><span style="color:'+(etfChg>=0?'#ef4444':'#10b981')+'">'+(etfChg>=0?'+':'')+etfChg+'%</span></div><div class="chart" id="'+bid+'_m"></div>';
    grid.appendChild(c1);
    renderMinute(bid+'_m', item.minute, etfPrevClose, item.name, true);

    // 日K
    const c2 = document.createElement('div'); c2.className = 'card';
    c2.innerHTML = '<div class="card-title"><span class="name">日K线</span><div class="kline-controls"><button onclick="'+bid+'_k_minus()">-</button><span class="range-info" id="'+bid+'_k_range">—</span><button onclick="'+bid+'_k_plus()">+</button></div></div><div class="chart" id="'+bid+'_k"></div>';
    grid.appendChild(c2);
    renderKline(bid+'_k', item.kline, item.name);

    // 5日分时
    const c3 = document.createElement('div'); c3.className = 'card';
    c3.innerHTML = '<div class="card-title"><span class="name">5日分时</span></div><div class="chart" id="'+bid+'_5d"></div>';
    grid.appendChild(c3);
    render5Day(bid+'_5d', item.m5_5day, true);
  }});
}}

// ========== MA5排行表 ==========
function buildRankTable() {{
  const data = {rank_json};
  const el = document.getElementById('rank-table-body');
  if (!el || !data.length) return;
  let html = '';
  data.forEach((r,i) => {{
    const chg = r['涨跌幅']||'';
    const cls = chg.startsWith('-') ? 'down' : 'up';
    html += '<tr><td>'+r['排名']+'</td><td>'+r['代码']+'</td><td>'+r['名称']+'</td><td>'+r['角度']+'°</td><td>'+r['最新价']+'</td><td class="'+cls+'">'+chg+'</td></tr>';
  }});
  el.innerHTML = html;
}}

// ========== 大宗交易表格 (树状) ==========
function buildBlockTable() {{
  const data = {block_json};
  const el = document.getElementById('block-table');
  if (!el) return;
  if (!data.length) {{
    el.innerHTML = '<div class=\"empty-state\"><div class=\"icon\">📋</div><p>自选股近5日无大宗交易</p></div>';
    return;
  }}
  let h = '<table class=\"rank-table block-table\"><thead><tr><th>名称</th><th>代码</th><th>日期</th><th>成交价</th><th>折溢率<span class=\"tooltip-hint\">?<span class=\"tooltip-text\">近5日加权折溢率 = Σ(折溢率×成交额) / Σ成交额</span></span></th><th>总额(万)</th><th>备注</th></tr></thead><tbody>';
  data.forEach(function(theme) {{
    var wr = theme['wavg_rate']; var ws = wr>=0?'+':''; var wc = wr>0?'up':wr<0?'down':'';
    var newBadge = theme['new_count'] > 0 ? '<span style=\"color:#f59e0b;font-size:11px\">今日新增 '+theme['new_count']+'笔</span>' : '';
    h += '<tr class=\"theme-row\" style=\"background:#111827;cursor:pointer\" onclick=\"toggleTheme(this)\">';
    h += '<td><b>'+theme['theme']+'</b></td><td></td><td></td><td></td>';
    h += '<td class=\"'+wc+'\">'+ws+wr.toFixed(2)+'%</td>';
    h += '<td>'+theme['total_amt'].toFixed(0)+'</td>';
    h += '<td>'+newBadge+'</td></tr>';
    (theme['trades']||[]).forEach(function(r) {{
      var rate=(r['折溢率']*100); var sign=rate>=0?'+':''; var cls=rate>0?'up':rate<0?'down':'';
      h += '<tr class=\"trade-row\" style=\"display:none\"><td>'+r['简称']+'</td><td>'+r['代码']+'</td><td>'+r['日期'].slice(5)+'</td><td>'+r['成交价'].toFixed(2)+'</td><td class=\"'+cls+'\">'+sign+rate.toFixed(2)+'%</td><td>'+r['成交总额_万'].toFixed(0)+'</td><td style=\"color:#6b7d95;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap\" title=\"'+(r['note']||'').replace(/\"/g,'&quot;')+'\">'+(r['note']||'')+'</td></tr>';
    }});
  }});
  h += '</tbody></table>';
  el.innerHTML = h;
}}

function toggleTheme(row) {{
  var n = row.nextElementSibling;
  while (n && n.classList.contains('trade-row')) {{
    n.style.display = n.style.display==='none'?'':'none';
    n = n.nextElementSibling;
  }}
}}

// ========== 板块数据表格 ==========
function buildSectorTable() {{
  var data = {sector_json};
  var el = document.getElementById('sector-table-body');
  if (!el || !data.length) {{
    if (el) el.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:24px;color:#6b7d95">暂无板块数据</td></tr>';
    return;
  }}
  var h = '';
  data.forEach(function(s) {{
    h += '<tr class="sector-row" onclick="switchTabWithTheme(&quot;stock&quot;,&quot;' + s.name.replace(/"/g,'&quot;') + '&quot;)" title="点击查看该题材个股">';
    var ftags = _stockFilterTags;
    var tagHtml = '';
    for (var fk in ftags) {{
      if (ftags[fk].indexOf(s.name) >= 0) {{
        tagHtml += '<span class="filter-tag">' + fk + '</span>';
      }}
    }}
    h += '<td style="font-weight:600">' + s.name + tagHtml + '</td>';
    h += '<td>' + s.status + '</td>';
    h += '<td>' + (s.d1 || '') + '</td>';
    h += '<td>' + (s.d2 || '') + '</td>';
    h += '<td>' + (s.d3 || '') + '</td>';
    h += '<td>' + (s.d4 || '') + '</td>';
    h += '<td>' + (s.d5 || '') + '</td>';
    h += '<td>' + s.ret_5d + '</td>';
    h += '<td>' + s.gt5_pct + '</td>';
    h += '<td style="font-size:11px;color:#6b7d95;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + s.stars + '</td>';
    h += '</tr>';
  }});
  el.innerHTML = h;
}}

// ========== 个股数据卡片 ==========
var _stockTabBuilt = false;
var _stockThemeOrder = {theme_order_json};
var _stockThemeAvg = {theme_avg_json};
var _stockFilterTags = {filter_tags_json};
var _stockDefaultTheme = '{strongest_sector}';

function buildStockTab() {{
  if (_stockTabBuilt) return;
  _stockTabBuilt = true;

  // 1. 题材切换按钮 (默认选中最强板块)
  var pillEl = document.getElementById('theme-pills');
  if (pillEl) {{
    var defaultTheme = _stockDefaultTheme || '全部';
    var ph = '<span class="theme-pill" data-theme="全部" onclick="renderStockCards(&quot;全部&quot;)">全部</span>';
    _stockThemeOrder.forEach(function(t) {{
      var activeClass = (t === defaultTheme) ? ' active' : '';
      var avg = _stockThemeAvg[t];
      var avgStr = '';
      if (avg != null) {{
        var avgClr = avg > 0 ? '#ef4444' : avg < 0 ? '#10b981' : '#94a3b8';
        avgStr = ' <span style="color:' + avgClr + '">' + (avg >= 0 ? '+' : '') + avg.toFixed(2) + '%</span>';
      }}
      var dotStr = '';
      var ft = _stockFilterTags;
      var tags = [];
      for (var fk in ft) {{ if (ft[fk].indexOf(t) >= 0) tags.push(fk); }}
      if (tags.length) {{
        dotStr = '<span class="filter-dot" title="' + tags.join(', ') + '"></span>';
      }}
      ph += '<span class="theme-pill' + activeClass + '" data-theme="' + t.replace(/"/g,'&quot;') + '" onclick="renderStockCards(&quot;' + t.replace(/"/g,'&quot;') + '&quot;)">' + t + avgStr + dotStr + '</span>';
    }});
    pillEl.innerHTML = ph;
  }}

  // 2. 卡片容器 (按5日涨幅降序)
  var grid = document.getElementById('grid-stock');
  if (!grid) return;
  var h = '';
  var names = Object.keys(STOCK_MAP);
  names.sort(function(a,b) {{
    var ca = STOCK_MAP[a].cum5d, cb = STOCK_MAP[b].cum5d;
    if (ca == null && cb == null) return 0;
    if (ca == null) return 1;
    if (cb == null) return -1;
    return cb - ca;
  }});
  names.forEach(function(name) {{
    var info = STOCK_MAP[name];
    var lc = info.lc;
    var pct = (info.pct && info.pct.length) ? info.pct[info.pct.length - 1] : 0;
    var pctCls = pct > 0 ? 'up' : pct < 0 ? 'down' : '';
    var sign = pct > 0 ? '+' : '';
    var priceStr = (lc != null) ? lc.toFixed(2) : '-';
    var safeName = name.replace(/[^a-zA-Z0-9\\u4e00-\\u9fff]/g, '_');
    h += '<div class="stock-card" data-themes="' + info.s.replace(/'/g,"\\'") + '">';
    h += '<div class="stock-header">';
    var emPrefix = (info.c && info.c.charAt(0) === '6') ? 'sh' : 'sz';
    var emUrl = 'https://quote.eastmoney.com/concept/' + emPrefix + info.c + '.html';
    h += '<div><div class="stock-name"><a href="' + emUrl + '" target="_blank" style="color:inherit;text-decoration:none" title="在东方财富查看">' + name + '</a><span class="stock-code">' + info.c + '</span></div>';
    h += '<div class="stock-theme">' + info.s + '</div></div>';
    h += '<div class="stock-price"><span class="val">' + priceStr + '</span>';
    h += '<span class="pct ' + pctCls + '">' + sign + pct.toFixed(2) + '%</span></div>';
    h += '</div>';
    h += '<div class="stock-chart" id="sc_' + safeName + '"></div>';
    h += '</div>';
  }});
  grid.innerHTML = h;

  // 3. 初始化图表 (仅默认选中板块的可见图表)
  var defaultTheme = _stockDefaultTheme || '全部';
  var initNames = (defaultTheme === '全部') ? names : names.filter(function(n) {{
    return STOCK_MAP[n].s === defaultTheme;
  }});
  initNames.forEach(function(name) {{
    var safeName = name.replace(/[^a-zA-Z0-9\\u4e00-\\u9fff]/g, '_');
    makeStockChart('sc_' + safeName, STOCK_MAP[name]);
  }});

  // 4. 默认筛选到最强板块
  if (defaultTheme !== '全部') {{
    renderStockCards(defaultTheme);
  }}
}}

function makeStockChart(domId, info) {{
  var dom = document.getElementById(domId);
  if (!dom || !info) return;
  var d = info.d, o = info.o, cl = info.cl, l = info.l, h = info.h, a = info.a;
  if (!d || !d.length) return;

  var kdata = [], vdata = [], hasValid = false;
  for (var i = 0; i < d.length; i++) {{
    var ov = (o && o[i] != null) ? o[i] : null;
    var cv = (cl && cl[i] != null) ? cl[i] : null;
    var lv = (l && l[i] != null) ? l[i] : null;
    var hv = (h && h[i] != null) ? h[i] : null;
    var av = (a && a[i] != null) ? a[i] : 0;
    if (ov != null && cv != null) {{
      hasValid = true;
      kdata.push([ov, cv, lv != null ? lv : Math.min(ov, cv), hv != null ? hv : Math.max(ov, cv)]);
      vdata.push(av);
    }} else {{
      kdata.push(['-','-','-','-']);
      vdata.push(0);
    }}
  }}
  if (!hasValid) return;

  var UP = '#ef4444', DOWN = '#10b981';
  var chart = echarts.init(dom, null, {{renderer:'canvas'}});
  chart.setOption({{
    animation: false,
    backgroundColor: 'transparent',
    grid: [
      {{left:4,right:4,top:2,height:'60%'}},
      {{left:4,right:4,top:'70%',height:'26%'}}
    ],
    xAxis: [
      {{type:'category',data:d,gridIndex:0,axisLine:{{show:false}},axisTick:{{show:false}},axisLabel:{{show:false}},splitLine:{{show:false}}}},
      {{type:'category',data:d,gridIndex:1,axisLine:{{show:false}},axisTick:{{show:false}},axisLabel:{{show:false}},splitLine:{{show:false}}}}
    ],
    yAxis: [
      {{type:'value',gridIndex:0,splitNumber:3,axisLabel:{{fontSize:9,color:'#6b7d95'}},splitLine:{{lineStyle:{{color:'#1e2a3a',type:'dashed'}}}},position:'right',scale:true}},
      {{type:'value',gridIndex:1,splitNumber:2,axisLabel:{{fontSize:8,color:'#6b7d95',formatter:function(v){{return v>=10000?(v/10000).toFixed(1)+'亿':v.toFixed(0)+'万'}}}},splitLine:{{show:false}},position:'right'}}
    ],
    series: [
      {{
        type:'candlestick',xAxisIndex:0,yAxisIndex:0,data:kdata,
        itemStyle:{{color:UP,color0:DOWN,borderColor:UP,borderColor0:DOWN}},
        barWidth:'60%',markPoint:{{silent:true,symbol:'none',label:{{show:false}}}}
      }},
      {{
        type:'bar',xAxisIndex:1,yAxisIndex:1,data:vdata,
        itemStyle:{{color:function(p){{var i=p.dataIndex;return(i<kdata.length&&kdata[i][1]>=kdata[i][0])?UP:DOWN;}},opacity:0.3}},
        barWidth:'60%'
      }}
    ],
    tooltip:{{
      trigger:'axis',axisPointer:{{type:'shadow',shadowStyle:{{color:'rgba(200,200,200,0.08)'}}}},
      backgroundColor:'#1e293b',borderColor:'#334155',
      textStyle:{{fontSize:11,color:'#e0e6ed'}},
      formatter:function(params){{
        var cs=null,date='';
        for(var j=0;j<params.length;j++){{
          if(params[j].seriesType==='candlestick')cs=params[j];
          date=params[j].axisValue;
        }}
        var k=cs?(cs.value||cs.data):null;
        if(!k||k.length<5||typeof k[1]!=='number')return date;
        return date+'<br/>开 '+k[1].toFixed(2)+'<br/>收 '+k[2].toFixed(2)+'<br/>高 '+k[4].toFixed(2)+'<br/>低 '+k[3].toFixed(2);
      }}
    }}
  }});

  var timer;
  var ro = new ResizeObserver(function(){{clearTimeout(timer);timer=setTimeout(function(){{chart.resize();}},100);}});
  ro.observe(dom);
}}

// ========== 题材筛选 & 联动 ==========
function renderStockCards(themeName) {{
  document.querySelectorAll('.theme-pill').forEach(function(p) {{
    p.classList.remove('active');
    if (p.getAttribute('data-theme') === themeName) p.classList.add('active');
  }});
  document.querySelectorAll('.stock-card').forEach(function(card) {{
    if (themeName === '全部') {{
      card.style.display = '';
    }} else {{
      var themes = card.getAttribute('data-themes') || '';
      card.style.display = themes.indexOf(themeName) >= 0 ? '' : 'none';
    }}
  }});
  setTimeout(function() {{
    document.querySelectorAll('.stock-card:not([style*="display: none"]) .stock-chart').forEach(function(d) {{
      var chart = echarts.getInstanceByDom(d);
      if (!chart) {{
        var card = d.closest('.stock-card');
        var stockName = card.querySelector('.stock-name').firstChild.textContent.trim();
        var info = STOCK_MAP[stockName];
        if (info) {{
          makeStockChart(d.id, info);
          chart = echarts.getInstanceByDom(d);
        }}
      }}
      if (chart) chart.resize();
    }});
  }}, 80);
}}
window.renderStockCards = renderStockCards;

function switchTabWithTheme(tabName, themeName) {{
  switchTab(tabName);
  if (tabName === 'stock' && themeName) {{
    buildStockTab();
    var pills = document.querySelectorAll('.theme-pill');
    var found = false;
    pills.forEach(function(p) {{
      p.classList.remove('active');
      if (p.getAttribute('data-theme') === themeName) {{ p.classList.add('active'); found = true; }}
    }});
    if (!found) {{
      var allBtn = document.querySelector('.theme-pill[data-theme="全部"]');
      if (allBtn) allBtn.classList.add('active');
    }}
    renderStockCards(found ? themeName : '全部');
  }}
}}
window.switchTabWithTheme = switchTabWithTheme;

// ========== 重点监控 ==========
var _monitorData = [];

function loadMonitorDataFromServer() {{
  fetch('/api/monitor').then(r=>r.json()).then(function(d){{ _monitorData=d; }}).catch(function(){{}});
}}
function saveMonitorData(d) {{
  _monitorData = d;
  fetch('/api/monitor',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(d)}}).catch(function(){{}});
}}

function buildMonitorTab() {{
  fetch('/api/monitor').then(r=>r.json()).then(function(d){{ _monitorData=d; renderMonitorTable(d); }}).catch(function(){{ renderMonitorTable(_monitorData); }});
  var today = new Date().toISOString().slice(0,10);
  // 添加表单
  var addEl = document.getElementById('monitor-add');
  if (addEl) {{
    addEl.innerHTML = '<div class=\"monitor-form\">'+
      '<input type=\"text\" id=\"mon-name\" placeholder=\"股票名称\">'+
      '<select id=\"mon-type\" style=\"padding:6px 10px;border-radius:6px;font-size:12px;background:#1e2a3a;color:#e0e6ed;border:1px solid #334155;font-family:inherit\"><option>重点监控</option><option>触发严重异动（暂未监管）</option></select>'+
      '<select id=\"mon-rule\" style=\"padding:6px 10px;border-radius:6px;font-size:12px;background:#1e2a3a;color:#e0e6ed;border:1px solid #334155;font-family:inherit\"><option>30天200%</option><option>10天100%</option></select>'+
      '<input type=\"date\" id=\"mon-start\" value=\"'+today+'\" onchange=\"onMonStartChange()\">'+
      '<input type=\"date\" id=\"mon-end\" value=\"'+addDays(today,14)+'\">'+
      '<button onclick=\"addMonitor()\">添加</button>'+
      '</div>';
  }}
}}

function addDays(d, n) {{ var dt=new Date(d); dt.setDate(dt.getDate()+n); return dt.toISOString().slice(0,10); }}

function onMonStartChange() {{
  var s = document.getElementById('mon-start').value;
  if (s) document.getElementById('mon-end').value = addDays(s, 14);
}}

function addMonitor() {{
  var name = document.getElementById('mon-name').value.trim();
  var type = document.getElementById('mon-type').value;
  var rule = document.getElementById('mon-rule').value;
  var start = document.getElementById('mon-start').value;
  var end = document.getElementById('mon-end').value;
  if (!name) return;
  _monitorData.push({{'名称':name,'类型':type,'规则':rule,'开始':start,'结束':end}});
  saveMonitorData(_monitorData);
  renderMonitorTable(_monitorData);
  document.getElementById('mon-name').value = '';
}}

function deleteMonitor(idx) {{
  _monitorData.splice(idx,1);
  saveMonitorData(_monitorData);
  renderMonitorTable(_monitorData);
}}

function toggleMonType(idx) {{
  if (_monitorData[idx]) {{
    _monitorData[idx]['类型'] = _monitorData[idx]['类型']==='重点监控' ? '触发严重异动（暂未监管）' : '重点监控';
    saveMonitorData(_monitorData);
    renderMonitorTable(_monitorData);
  }}
}}

function renderMonitorTable(data) {{
  var el = document.getElementById('monitor-table');
  if (!el) return;
  if (!data.length) {{ el.innerHTML = '<div class=\"empty-state\"><div class=\"icon\">📋</div><p>暂无监控记录</p></div>'; return; }}
  var today = new Date().toISOString().slice(0,10);
  var h = '<table class=\"rank-table\"><thead><tr><th>名称</th><th>类型</th><th>触发规则</th><th>开始</th><th>结束</th><th>操作</th></tr></thead><tbody>';
  data.forEach(function(r,i) {{
    var active = r['结束'] >= today;
    var cls = active ? 'monitor-active' : 'monitor-inactive';
    h += '<tr class=\"'+cls+'\"><td>'+r['名称']+'</td><td style=\"cursor:pointer\" onclick=\"toggleMonType('+i+')\">'+r['类型']+'</td><td>'+(r['规则']||'—')+'</td><td>'+r['开始']+'</td><td>'+r['结束']+'</td><td><button class=\"del-btn\" onclick=\"deleteMonitor('+i+')\">×</button></td></tr>';
  }});
  h += '</tbody></table>';
  el.innerHTML = h;
}}

// ========== 指数状态 ==========
var _indexState = "{index_state_default}";
function loadIndexState() {{
  fetch('/api/index-state').then(r=>r.json()).then(function(d){{
    _indexState = d.state || "区间震荡";
    var sel = document.getElementById('index-state-select');
    if (sel) sel.value = _indexState;
  }}).catch(function(){{}});
}}
function onIndexStateChange(state) {{
  _indexState = state;
  fetch('/api/index-state',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{state:state}})}}).catch(function(){{}});
}}

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', function() {{
  loadRiskState();
  loadOppState();
  loadIndexState();
  buildIndexGrid();
  buildRankTable();
  _tabRendered['index'] = true;
}});
</script>
</body>
</html>"""
    return html


def main():
    out_path = os.path.join(DATA_DIR, "dashboard.html")
    html = build_html()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"生成: {out_path}")
    print(f"大小: {len(html):,} 字节")


if __name__ == "__main__":
    main()
