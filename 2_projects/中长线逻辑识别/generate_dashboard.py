#!/usr/bin/env python3
"""生成数据看板: 数据文件写入 .index_data/ + 瘦身 HTML（6 Tab，通过 /api/data/* 动态加载）"""
import csv, json, os, re, glob
from datetime import datetime

from _topic_utils import parse_topic_header

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
    """从 top_list.md 提取关注的股票: {名称: {theme, note}}, theme_order
    只显示近10日涨幅前15的强势标的，排除僵尸股"""
    path = os.path.join(DATA_DIR, "top_list.md")
    if not os.path.exists(path):
        path = os.path.join(DATA_DIR, "interest_stock.md")  # fallback
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
                    current_theme = parse_topic_header(line)[0]  # 只用 topic_name 做分类
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
                name = name.split('#')[0].strip()  # 去掉行内注释
                if re.match(r'^[\u4e00-\u9fffA-Za-z0-9]{2,8}$', name):
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


def parse_attack_defense_ratio(report_path):
    """从多日报告解析进攻票/防守票占比。
    Returns (attack_pct, defense_pct) as floats, or (None, None) on failure.
    示例行: '进攻票占比：30.1% ... | 防守票占比：69.9% ...'"""
    if not report_path or not os.path.exists(report_path):
        return None, None
    try:
        with open(report_path, encoding='utf-8') as f:
            text = f.read()
        m = re.search(r'进攻票占比[：:]\s*\*{0,2}\s*([\d.]+)%', text)
        d = re.search(r'防守票占比[：:]\s*\*{0,2}\s*([\d.]+)%', text)
        attack = float(m.group(1)) if m else None
        defense = float(d.group(1)) if d else None
        return attack, defense
    except Exception:
        return None, None


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
        # Fallback 1: 京东方A → 京东方 (strip trailing A/B/H/ST etc.)
        if not code:
            stripped = re.sub(r'[A-Z]+$', '', name).strip()
            if stripped and stripped != name:
                code = code_map.get(stripped)
        # Fallback 2: TCL中环 → 匹配 TCL中环（...)
        if not code:
            for k, v in code_map.items():
                if k.startswith(name) or k.startswith(name.replace(' ', '')):
                    code = v
                    break
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
COLORS = ["#2563EB", "#f59e0b", "#ef4444", "#10b981", "#8b5cf6", "#ec4899"]


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


def _write_data_file(fname, content):
    """Write data file to .index_data/ for /api/data/* consumption."""
    path = os.path.join(INDEX_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    size_kb = len(content.encode("utf-8")) / 1024
    print(f"  ✓ {fname} ({size_kb:.0f} KB)")


# ======== HTML 生成 ========

def build_html():
    print("写入数据文件到 .index_data/ ...")
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

    # ---- 进攻票/防守票占比 ----
    latest_report = find_latest_multi_report()
    attack_pct, defense_pct = parse_attack_defense_ratio(latest_report) if latest_report else (None, None)
    # 获取前一日数据用于计算变化
    prev_attack, prev_defense = None, None
    if latest_report:
        prev_report_path = latest_report.replace('.md', '')  # will be .../multi_day_trend_2026-05-29
        date_str = re.search(r'(\d{4}-\d{2}-\d{2})', prev_report_path)
        if date_str:
            from datetime import datetime as _dt, timedelta as _td
            prev_date = (_dt.strptime(date_str.group(1), '%Y-%m-%d') - _td(days=1)).strftime('%Y-%m-%d')
            dir_name = os.path.dirname(latest_report)
            prev_report = os.path.join(dir_name, f"multi_day_trend_{prev_date}.md")
            prev_attack, prev_defense = parse_attack_defense_ratio(prev_report)

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
    block_tree.sort(key=lambda x: x["wavg_rate"], reverse=True)  # 按平均折溢率从大到小
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
        return "{" + ",".join(lines) + "}"
    
    # 将数据写入 .index_data/ 供 dashboard_server.py 的 /api/data/* 端点消费
    os.makedirs(INDEX_DIR, exist_ok=True)
    stock_js_raw = _make_stock_js() if stock_map else "{}"
    _write_data_file("stock_map.js", stock_js_raw)
    _write_data_file("index_chart.js", json.dumps(index_data, ensure_ascii=False, default=str))
    _write_data_file("etf_chart.js", json.dumps(etf_data, ensure_ascii=False, default=str))
    _write_data_file("ranking.json", json.dumps(ranking[:30], ensure_ascii=False, default=str) if ranking else "[]")
    _write_data_file("block.json", json.dumps(block_tree, ensure_ascii=False, default=str))
    _write_data_file("sector.json", json.dumps(sector_data, ensure_ascii=False))

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
    strongest_sector = theme_order_sorted[0] if theme_order_sorted else ""
    filter_tags = load_filter_tags()

    # 写入额外数据文件（替代模板嵌入）
    _write_data_file("indicators.json", json.dumps({
        "latest_date": latest_date,
        "total_amt_yi": total_amt_yi,
        "breadth": breadth,
        "attack_pct": attack_pct,
        "defense_pct": defense_pct,
        "prev_attack": prev_attack,
        "prev_defense": prev_defense,
    }, ensure_ascii=False))
    _write_data_file("themes.json", json.dumps({
        "theme_order": theme_order_sorted,
        "theme_avg_map": theme_avg_map,
        "strongest_sector": strongest_sector,
        "filter_tags": filter_tags,
    }, ensure_ascii=False))
    _write_data_file("colors.json", json.dumps(COLORS, ensure_ascii=False))

    print("数据处理完成")


def main():
    build_html()
    print("数据可视化文件已更新到 .index_data/")
    print("dashboard.html 不再需要生成，直接运行 dashboard_server.py 即可")


if __name__ == "__main__":
    main()
