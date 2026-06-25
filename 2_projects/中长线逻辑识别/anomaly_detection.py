#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多策略异动检测：ETF异动 + 指数量能 + 震荡区间 + 赚钱效应 + 上证均线支撑/压制

策略 1：ETF 异动（量能超阈值 + 异常买卖滑窗检测）
策略 2：指数量能异动（天量见顶预警）
策略 3：指数震荡区间风险机会判断（仅上证指数）
策略 4：赚钱效应检测（二冰/三冰 + 放量下跌）
策略 5：上证均线支撑/压制检测（MA5/10/20/30/60/120，距收盘最近的均线）

打标规则（--save-tags 时生效）：
- 市场量能 < 3万亿：仅保存风险标签，提示风险
- 其他情况：正常保存所有标签

用法:
    python anomaly_detection.py                    # 检测最新交易日（全部策略）
    python anomaly_detection.py --date 2026-05-22  # 指定日期
    python anomaly_detection.py --json             # JSON 输出
    python anomaly_detection.py --save-tags        # 保存标签到 risk_tags.json / opp_tags.json
"""

import argparse, csv, json, os, sys
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".index_data")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# ETF 配置: 名称, 量能阈值(万手)
ETF_CONFIG = [
    ("中证500ETF", 1000),
    ("沪深300ETF", 2600),
    ("半导体设备ETF", 3000),
    ("上证50ETF", 2500),
    ("科创芯片ETF", 2000),
    ("创业板ETF", 2000),
]


# ======== 数据加载 ========

def find_latest_trading_day():
    """找到 .index_data 中最新的 ETF 日线数据日期"""
    dates = set()
    for f in os.listdir(DATA_DIR):
        if f.startswith("etf_daily_") and f.endswith(".csv"):
            d = f.replace("etf_daily_", "").replace(".csv", "")
            dates.add(d)
    if not dates:
        return None
    return max(dates)


def get_previous_trading_days(target_date, n=5):
    """获取 target_date 之前的 n 个交易日的日期列表（从文件名推断）"""
    dates = set()
    for f in os.listdir(DATA_DIR):
        if f.startswith("etf_daily_") and f.endswith(".csv"):
            d = f.replace("etf_daily_", "").replace(".csv", "")
            if d < target_date:
                dates.add(d)
    return sorted(dates, reverse=True)[:n]


def read_etf_daily(date_str):
    """读取某日 ETF 日线数据，返回 {名称: {成交量_万手, ...}}"""
    path = os.path.join(DATA_DIR, f"etf_daily_{date_str}.csv")
    if not os.path.exists(path):
        return {}
    result = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("名称", "").strip()
            if not name:
                continue
            try:
                result[name] = {
                    "成交量_万手": float(row.get("成交量_万手", 0)),
                    "成交量": float(row.get("成交量", 0)),
                    "收盘": float(row.get("收盘", 0)),
                    "开盘": float(row.get("开盘", 0)),
                }
            except (ValueError, TypeError):
                continue
    return result


def read_etf_minute(etf_name, date_str):
    """读取 ETF m5 分钟线数据，返回 [{time, open, price, volume_shou, amount_yi}, ...]"""
    safe = etf_name.replace("/", "_")
    path = os.path.join(DATA_DIR, f"etf_minute_{safe}_{date_str}.csv")
    if not os.path.exists(path):
        return None
    result = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                item = {
                    "time": row.get("time", ""),
                    "price": float(row.get("price", 0)),
                    "amount_yi": float(row.get("amount_yi", 0)),
                }
                # volume_shou 和 open 是新增字段，可能不存在于旧数据中
                if "volume_shou" in row and row["volume_shou"]:
                    item["volume_shou"] = float(row["volume_shou"])
                else:
                    item["volume_shou"] = None
                if "open" in row and row["open"]:
                    item["open"] = float(row["open"])
                else:
                    item["open"] = None
                result.append(item)
            except (ValueError, TypeError):
                continue
    return result


# ======== 指数日线数据加载 ========

def read_index_daily(date_str):
    """读取某日指数日线数据，返回 {名称: {成交额_亿, ...}}"""
    path = os.path.join(DATA_DIR, f"index_daily_{date_str}.csv")
    if not os.path.exists(path):
        return {}
    result = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("指数", "").strip()
            if not name:
                continue
            try:
                result[name] = {
                    "成交额_亿": float(row.get("成交额_亿", 0)),
                    "成交额": float(row.get("成交额", 0)),
                    "收盘": float(row.get("收盘", 0)),
                }
            except (ValueError, TypeError):
                continue
    return result


def get_previous_index_dates(target_date, n=5):
    """获取 target_date 之前的 n 个指数日线日期"""
    dates = set()
    for f in os.listdir(DATA_DIR):
        if f.startswith("index_daily_") and f.endswith(".csv"):
            d = f.replace("index_daily_", "").replace(".csv", "")
            if d < target_date:
                dates.add(d)
    return sorted(dates, reverse=True)[:n]


# ======== 涨跌家数数据加载 ========

def read_breadth(date_str):
    """读取某日涨跌家数，返回 {上涨: int, 下跌: int, ...} 或 None"""
    path = os.path.join(DATA_DIR, f"breadth_{date_str}.csv")
    if not os.path.exists(path):
        return None
    result = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row.get("指标", "").strip()
            val = row.get("数值", "").strip()
            if key and val and val != "—":
                try:
                    result[key] = int(val)
                except ValueError:
                    pass
    return result if result else None


def get_ordered_breadth(target_date, days_back=4):
    """获取 target_date 及之前 days_back 天的上涨家数，按日期倒序
    返回 [{date, up_count}, ...]，倒序（最新在前）"""
    dates = set()
    for f in os.listdir(DATA_DIR):
        if f.startswith("breadth_") and f.endswith(".csv"):
            d = f.replace("breadth_", "").replace(".csv", "")
            if d <= target_date:
                dates.add(d)
    sorted_dates = sorted(dates, reverse=True)[:days_back + 1]
    result = []
    for d in sorted_dates:
        b = read_breadth(d)
        if b and "上涨" in b:
            result.append({"date": d, "up_count": b["上涨"]})
    return result


# ======== 日期简写 ========

def date_short(date_str):
    """2026-05-22 -> 0522"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.strftime("%m%d")


# ======== 量能检测 ========

def check_volume_anomaly(etf_name, today_daily, prev_days_data, threshold_wan):
    """
    量能检测（单位：万手）
    返回 alerts 列表，每条: {type: 'risk'|'opp'|'both', label: str}
    """
    alerts = []
    today_vol = today_daily.get("成交量_万手", 0)
    if today_vol <= 0:
        return alerts

    date_label = date_short(today_daily.get("日期", ""))

    # 1) 当日成交量 > 前 5 日平均量能的 1.5 倍
    prev_vols = [prev_days_data[d].get(etf_name, {}).get("成交量_万手", 0)
                 for d in prev_days_data if prev_days_data[d].get(etf_name, {}).get("成交量_万手", 0) > 0]
    if len(prev_vols) >= 3:  # 至少需要 3 天数据做参考
        avg_vol = sum(prev_vols) / len(prev_vols)
        if avg_vol > 0 and today_vol > avg_vol * 1.5:
            alerts.append({
                "type": "both",
                "label": f"{date_label}：{etf_name}量能超过前{len(prev_vols)}日平均量能1.5倍",
                "detail": f"当日{today_vol:.0f}万手 vs 前{len(prev_vols)}日均{avg_vol:.0f}万手 (倍数:{today_vol/avg_vol:.1f}x)"
            })

    # 2) 当日成交量超过标的量能阈值
    if today_vol > threshold_wan:
        alerts.append({
            "type": "both",
            "label": f"{date_label}：{etf_name}量能超过标的量能阈值{threshold_wan}万手",
            "detail": f"当日{today_vol:.0f}万手 > 阈值{threshold_wan}万手"
        })

    return alerts


# ======== 异常买卖检测 ========

def check_abnormal_trade(etf_name, minute_data, prev_days_data, date_str):
    """
    滑窗检测（10 分钟 = 2 根 m5 柱）
    - 两柱同色 + 窗口总成交量 > 日均的 20%
    - 红色 → 机会预警，绿色 → 风险预警
    """
    if not minute_data or len(minute_data) < 2:
        return []

    date_label = date_short(date_str)

    # 检查数据是否包含 volume_shou 和 open
    has_volume = all(m.get("volume_shou") is not None for m in minute_data)
    has_open = all(m.get("open") is not None for m in minute_data)

    if not has_volume or not has_open:
        # 数据不完整，检查是否可以从 amount_yi 反推 volume
        if not has_volume:
            # amount_yi * 1e8 / (100 * price) = volume_shou (近似)
            for m in minute_data:
                if m["price"] > 0 and m["amount_yi"] > 0:
                    m["volume_shou"] = m["amount_yi"] * 1e8 / (100 * m["price"])
                else:
                    m["volume_shou"] = 0
        if not has_open:
            # 无 open 数据时，用相邻 bar 的 price 变化推断红绿
            # (精确度不足，标记为 fallback)
            pass

    if not has_open:
        print(f"  ⚠ m5 数据缺少 open 字段，红绿判断使用相邻价差近似（建议重新拉取数据）")
        _infer_color_from_price(minute_data)

    # 计算日均成交量（万手）
    prev_vols = [prev_days_data[d].get(etf_name, {}).get("成交量_万手", 0)
                 for d in prev_days_data if prev_days_data[d].get(etf_name, {}).get("成交量_万手", 0) > 0]
    if len(prev_vols) < 3:
        return []
    avg_daily_vol_wan = sum(prev_vols) / len(prev_vols)
    # 阈值: 日均的 20%（转换为手: 万手 * 10000）
    min_window_vol = avg_daily_vol_wan * 10000 * 0.2

    alerts = []
    window_size = 2  # 10 分钟 = 2 根 m5 柱

    for i in range(len(minute_data) - window_size + 1):
        bar1 = minute_data[i]
        bar2 = minute_data[i + 1]

        # 窗口总成交量（手）
        vol1 = bar1.get("volume_shou", 0) or 0
        vol2 = bar2.get("volume_shou", 0) or 0
        window_vol = vol1 + vol2

        if window_vol < min_window_vol:
            continue

        # 判断颜色
        c1 = _bar_color(bar1)
        c2 = _bar_color(bar2)

        if c1 is None or c2 is None or c1 != c2:
            continue

        time_range = f"{bar1['time']}~{bar2['time']}"
        if c1 == "red":
            alerts.append({
                "type": "opp",
                "label": f"{date_label}：{etf_name}异常买入",
                "detail": f"{time_range} 两柱同红，窗口量能{window_vol/10000:.1f}万手"
            })
        else:  # green
            alerts.append({
                "type": "risk",
                "label": f"{date_label}：{etf_name}异常卖出",
                "detail": f"{time_range} 两柱同绿，窗口量能{window_vol/10000:.1f}万手"
            })

    return alerts


def _bar_color(bar):
    """判断 m5 K 线颜色: red(阳线, close>open), green(阴线, close<open)"""
    op = bar.get("open")
    cl = bar.get("price")  # price 字段即 close
    if op is None or cl is None:
        return None
    if cl > op:
        return "red"
    elif cl < op:
        return "green"
    return None  # 平盘不判断


def _infer_color_from_price(minute_data):
    """无 open 数据时，用前一 bar 的 close 作为近似 open 推断红绿"""
    for i, m in enumerate(minute_data):
        if m.get("open") is not None:
            continue
        if i == 0:
            m["open"] = m["price"]  # 第一根默认平盘
        else:
            m["open"] = minute_data[i - 1]["price"]


# ======== 策略 2: 指数量能异动（天量见顶预警）========

def check_index_volume(date_str, date_label):
    """
    检测市场总成交额异常（总成交额 = (上证+深证)*1.01）：
    - 当日成交额 > 3万亿
    - 当日成交额 > 前5日均值 ×1.1
    """
    alerts = []

    # 当日数据
    today_idx = read_index_daily(date_str)
    sh_today = today_idx.get("上证指数")
    sz_today = today_idx.get("深证成指")
    if not sh_today or not sz_today:
        print("  ⚠ 上证/深证数据缺失，跳过")
        return alerts

    sh_amt = sh_today.get("成交额_亿", 0) or 0
    sz_amt = sz_today.get("成交额_亿", 0) or 0
    if sh_amt <= 0 or sz_amt <= 0:
        print("  ⚠ 上证/深证成交额缺失，跳过")
        return alerts

    today_amt = (sh_amt + sz_amt) * 1.01  # 亿元



    # 情境 2: 超过前 5 日均值的 1.1 倍
    prev_dates = get_previous_index_dates(date_str, n=5)
    prev_amts = []
    for pd_date in prev_dates:
        pd_data = read_index_daily(pd_date)
        pd_sh = pd_data.get("上证指数")
        pd_sz = pd_data.get("深证成指")
        if pd_sh and pd_sz:
            p_sh = pd_sh.get("成交额_亿", 0) or 0
            p_sz = pd_sz.get("成交额_亿", 0) or 0
            if p_sh > 0 and p_sz > 0:
                prev_amts.append((p_sh + p_sz) * 1.01)

    if len(prev_amts) >= 3:
        avg_amt = sum(prev_amts) / len(prev_amts)
        if avg_amt > 0 and today_amt > avg_amt * 1.1:
            alerts.append({
                "type": "both",
                "label": f"{date_label}：市场成交额放大",
                "detail": f"市场总成交额 {today_amt:.0f} 亿 vs 前{len(prev_amts)}日均 {avg_amt:.0f} 亿 (倍数:{today_amt/avg_amt:.2f}x)",
            })

    return alerts


# ======== 策略 3: 指数震荡区间风险机会判断 ========

def load_index_levels(index_name="上证指数"):
    """从 index_levels.json 读取指定指数的日K水平线"""
    path = os.path.join(PROJECT_DIR, "index_levels.json")
    defaults = {"上证指数": [4200, 4050, 4000, 3940, 3794], "创业板指": [2100, 2000, 1900, 1800, 1700]}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            # 新格式：{"上证指数": {"lines": [...]}, "创业板指": {"lines": [...]}}
            if isinstance(data, dict) and index_name in data and "lines" in data[index_name]:
                lines = [l["y"] for l in data[index_name]["lines"]]
                if lines:
                    return sorted(lines, reverse=True)
            # 旧格式兼容
            if isinstance(data, dict) and "lines" in data:
                lines = [l["y"] for l in data.get("lines", [])]
                if lines and index_name == "上证指数":
                    return sorted(lines, reverse=True)
        except Exception:
            pass
    return sorted(defaults.get(index_name, defaults["上证指数"]), reverse=True)


SZ_LINES = load_index_levels("上证指数")
CY_LINES = load_index_levels("创业板指")


def load_index_state():
    """读取指数状态（区间震荡/主升/破位下跌）"""
    path = os.path.join(PROJECT_DIR, "index_state.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f).get("state", "区间震荡")
        except Exception:
            pass
    return "区间震荡"


def check_index_zone(date_str, date_label):
    """
    检测上证指数和创业板指在各自震荡区间内的位置
    - N 条线 → N+1 个区间
    - 每个区间内，上部 50% → 风险，下部 50% → 机会
    - 仅当状态为「区间震荡」时执行
    - 上证和创业板使用各自的水平线（数据独立）和各自的计算逻辑
    """
    alerts = []

    state = load_index_state()
    if state != "区间震荡":
        print(f"  指数状态: {state}，跳过区间判断")
        return alerts

    # 读取当日指数数据
    today_idx = read_index_daily(date_str)

    # 同时检测上证指数和创业板指
    checks = [
        ("上证指数", "上证指数", SZ_LINES),
        ("创业板指", "创业板指", CY_LINES),
    ]

    for idx_name, label_prefix, lines in checks:
        idx_data = today_idx.get(idx_name)
        if not idx_data:
            print(f"  ⚠ {idx_name} 数据缺失，跳过区间判断")
            continue

        close = idx_data.get("收盘", 0)
        if close <= 0:
            continue

        sorted_lines = sorted(lines)  # 从低到高
        n = len(sorted_lines)

        # 找到当前收盘价所在区间
        zone_idx = -1
        for i in range(n + 1):
            low = sorted_lines[i - 1] if i > 0 else float("-inf")
            high = sorted_lines[i] if i < n else float("inf")
            if (i == 0 and close < high) or \
               (i == n and close >= low) or \
               (0 < i < n and low <= close < high):
                zone_idx = i
                break

        if zone_idx < 0:
            continue

        # 计算该区间的上下 50% 分界
        zone_low = sorted_lines[zone_idx - 1] if zone_idx > 0 else sorted_lines[0] - (sorted_lines[1] - sorted_lines[0]) if n > 1 else sorted_lines[0] - 100
        zone_high = sorted_lines[zone_idx] if zone_idx < n else sorted_lines[-1] + (sorted_lines[-1] - sorted_lines[-2]) if n > 1 else sorted_lines[-1] + 100
        mid = zone_low + (zone_high - zone_low) * 0.5

        if close >= mid:
            alerts.append({
                "type": "risk",
                "label": f"{date_label}：{idx_name}运行在区间上沿，回调压力",
                "detail": f"{idx_name}收盘 {close:.0f} 在 [{zone_low:.0f}, {zone_high:.0f}] 上部50% (分界{mid:.0f})",
            })
        else:
            alerts.append({
                "type": "opp",
                "label": f"{date_label}：{idx_name}运行在区间下沿，反弹需求",
                "detail": f"{idx_name}收盘 {close:.0f} 在 [{zone_low:.0f}, {zone_high:.0f}] 下部50% (分界{mid:.0f})",
            })

    return alerts


# ======== 策略 4: 赚钱效应检测 ========

def check_ice_detection(breadth_list, date_label):
    """
    冰点检测：连续 N 天上涨家数 < 2000
    breadth_list: [{date, up_count}] 按日期倒序
    """
    alerts = []
    if not breadth_list:
        return alerts

    # 从最新日期开始，统计连续 < 2000 的天数
    consecutive = 0
    for b in breadth_list:
        if b["up_count"] < 2000:
            consecutive += 1
        else:
            break

    if consecutive >= 3:
        alerts.append({
            "type": "opp",
            "label": f"{date_label}：三冰",
            "detail": f"连续{consecutive}天上涨家数不足2000",
        })
    elif consecutive >= 2:
        alerts.append({
            "type": "opp",
            "label": f"{date_label}：二冰",
            "detail": f"连续{consecutive}天上涨家数不足2000",
        })

    return alerts


def check_panic_sell(date_str, date_label):
    """
    放量下跌检测：
    - 上涨家数从 3000+ → <1000
    - 上证和深证 均下跌
    - 总成交额放大 >1.15 / >1.25 倍前日（总成交额 = (上证+深证)*1.01）
    """
    alerts = []

    # 读取当日和前一日的 breadth + index 数据
    today_b = read_breadth(date_str)
    if not today_b or "上涨" not in today_b:
        return alerts

    # 找前一个交易日
    all_breadth_dates = sorted(
        [f.replace("breadth_", "").replace(".csv", "")
         for f in os.listdir(DATA_DIR) if f.startswith("breadth_") and f.endswith(".csv")],
        reverse=True
    )
    prev_date = None
    for d in all_breadth_dates:
        if d < date_str:
            prev_date = d
            break
    if not prev_date:
        return alerts

    prev_b = read_breadth(prev_date)
    if not prev_b or "上涨" not in prev_b:
        return alerts

    today_up = today_b["上涨"]
    prev_up = prev_b["上涨"]

    # 条件: 上涨从 3000+ 降到 1000 以下
    if not (prev_up >= 3000 and today_up < 1000):
        return alerts

    # 读取指数数据
    today_idx = read_index_daily(date_str)
    prev_idx = read_index_daily(prev_date)

    sh_today = today_idx.get("上证指数", {})
    sh_prev = prev_idx.get("上证指数", {})
    sz_today = today_idx.get("深证成指", {})
    sz_prev = prev_idx.get("深证成指", {})

    # 上证和深证 均下跌
    sh_close_t = sh_today.get("收盘", 0)
    sh_close_p = sh_prev.get("收盘", 0)
    sz_close_t = sz_today.get("收盘", 0)
    sz_close_p = sz_prev.get("收盘", 0)

    if not (sh_close_t < sh_close_p and sz_close_t < sz_close_p):
        return alerts

    # 总成交额 = (上证+深证) * 1.01
    def _total_amt(idx):
        sh_a = idx.get("上证指数", {}).get("成交额_亿", 0) or 0
        sz_a = idx.get("深证成指", {}).get("成交额_亿", 0) or 0
        return (sh_a + sz_a) * 1.01

    total_t = _total_amt(today_idx)
    total_p = _total_amt(prev_idx)

    if total_p <= 0:
        return alerts

    ratio = total_t / total_p

    if ratio >= 1.25:
        alerts.append({
            "type": "risk",
            "label": f"{date_label}：放量下跌，有恐慌盘",
            "detail": f"上涨{prev_up}→{today_up}，总成交额{total_t:.0f}亿/前日{total_p:.0f}亿={ratio:.2f}x",
        })
    elif ratio >= 1.15:
        alerts.append({
            "type": "risk",
            "label": f"{date_label}：放量下跌",
            "detail": f"上涨{prev_up}→{today_up}，总成交额{total_t:.0f}亿/前日{total_p:.0f}亿={ratio:.2f}x",
        })

    return alerts


# ======== 策略 5: 上证均线支撑/压制检测 ========

def load_all_index_data():
    """加载全部历史指数日线数据，返回 {指数名: [{日期, 收盘}, ...]}"""
    index_data = {}
    for f in sorted(os.listdir(DATA_DIR)):
        if f.startswith("index_daily_") and f.endswith(".csv"):
            path = os.path.join(DATA_DIR, f)
            with open(path, encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    name = row.get("指数", "").strip()
                    if not name:
                        continue
                    try:
                        close = float(row.get("收盘", 0))
                        date = row.get("日期", "").strip()
                    except (ValueError, TypeError):
                        continue
                    if name not in index_data:
                        index_data[name] = []
                    index_data[name].append({"date": date, "close": close})
    return index_data


def compute_ma(prices, window):
    """计算移动平均，返回与 prices 等长的数组，前 window-1 个为 None"""
    result = [None] * len(prices)
    if len(prices) < window:
        return result
    for i in range(window - 1, len(prices)):
        result[i] = sum(prices[i - window + 1:i + 1]) / window
    return result


def check_ma_pressure_support(date_str, date_label):
    """
    策略 5: 上证指数均线压制/支撑检测
    - 若收盘价上方有均线 → 找最近的均线，计算距离（点数和百分比）→ 风险标签
    - 若收盘价下方有均线 → 找最近的均线，计算距离（点数和百分比）→ 机会标签
    支持 MA5/10/20/30/60/120，数据不足的周期自动跳过。
    """
    alerts = []
    periods = [5, 10, 20, 30, 60, 120]

    # 加载全部上证指数历史数据
    all_data = load_all_index_data()
    sh_data = all_data.get("上证指数")
    if not sh_data or len(sh_data) < 2:
        print("  ⚠ 上证指数历史数据不足，跳过均线检测")
        return alerts

    closes = [d["close"] for d in sh_data]
    latest_close = closes[-1]

    # 计算各周期均线
    mas = {}
    for p in periods:
        ma_vals = compute_ma(closes, p)
        if ma_vals[-1] is not None:
            mas[p] = ma_vals[-1]

    if not mas:
        return alerts

    # 找上方最近的均线（压制/风险）
    above = {p: v for p, v in mas.items() if v > latest_close}
    if above:
        closest_above_p = min(above, key=lambda p: above[p] - latest_close)
        closest_above_v = above[closest_above_p]
        points = round(closest_above_v - latest_close, 2)
        pct = round((closest_above_v / latest_close - 1) * 100, 2)
        alerts.append({
            "type": "risk",
            "label": f"{date_label}：上证均线压制，距离{closest_above_p}日线{points}点，{pct}%",
            "detail": f"收盘{latest_close:.2f}，{closest_above_p}日线{closest_above_v:.2f}，上方{points}点",
        })

    # 找下方最近的均线（支撑/机会）
    below = {p: v for p, v in mas.items() if v < latest_close}
    if below:
        closest_below_p = max(below, key=lambda p: latest_close - below[p])
        closest_below_v = below[closest_below_p]
        points = round(latest_close - closest_below_v, 2)
        pct = round((closest_below_v / latest_close - 1) * 100, 2)
        alerts.append({
            "type": "opp",
            "label": f"{date_label}：上证均线支撑，距离{closest_below_p}日线{points}点，{pct}%",
            "detail": f"收盘{latest_close:.2f}，{closest_below_p}日线{closest_below_v:.2f}，下方{points}点",
        })

    return alerts


# ======== 策略 6: 上证 MACD 超卖机会检测 ========

def check_macd_oversold(date_label):
    """
    计算上证指数 MACD(12,26,9) 柱值。
    当 MACD 柱 < -50 时提示极端超卖机会。
    """
    alerts = []
    all_data = load_all_index_data()
    sh_data = all_data.get("上证指数")
    if not sh_data or len(sh_data) < 26:
        return alerts

    closes = [d["close"] for d in sh_data]

    # EMA(12)
    ema12 = [None] * len(closes)
    ema12[11] = sum(closes[:12]) / 12
    mult12 = 2 / 13
    for i in range(12, len(closes)):
        ema12[i] = (closes[i] - ema12[i-1]) * mult12 + ema12[i-1]

    # EMA(26)
    ema26 = [None] * len(closes)
    ema26[25] = sum(closes[:26]) / 26
    mult26 = 2 / 27
    for i in range(26, len(closes)):
        ema26[i] = (closes[i] - ema26[i-1]) * mult26 + ema26[i-1]

    # DIF = EMA12 - EMA26
    dif = [ema12[i] - ema26[i] if (ema12[i] and ema26[i]) else None for i in range(len(closes))]

    # DEA = EMA(DIF, 9)
    dea = [None] * len(closes)
    valid_dif = [d for d in dif if d is not None]
    if len(valid_dif) >= 9:
        start = next(i for i, d in enumerate(dif) if d is not None) + 8
        dea[start] = sum(valid_dif[:9]) / 9
        mult9 = 2 / 10
        for i in range(start + 1, len(dif)):
            if dif[i] is not None:
                dea[i] = (dif[i] - dea[i-1]) * mult9 + dea[i-1]

    # MACD 柱 = 2*(DIF - DEA)
    latest_dif = latest_dea = latest_bar = None
    for i in range(len(closes) - 1, -1, -1):
        if dif[i] is not None and dea[i] is not None:
            latest_dif = round(dif[i], 2)
            latest_dea = round(dea[i], 2)
            latest_bar = round(2 * (dif[i] - dea[i]), 2)
            break

    if latest_bar is not None:
        print(f"  上证指数 MACD(12,26,9): DIF={latest_dif}  DEA={latest_dea}  BAR={latest_bar}\n")

    if latest_bar is None:
        return alerts

    if latest_bar < -50:
        alerts.append({
            "type": "opp",
            "label": f"{date_label}：上证 MACD 绿柱 {latest_bar}，极端超卖机会",
            "detail": f"MACD柱{latest_bar} < -50，历史极端水平，反弹概率较高",
        })

    return alerts


# ======== 主逻辑 ========

def run_detection(date_str=None, output_json=False, save_tags=False):
    """执行全部异动检测策略"""
    if date_str is None:
        date_str = find_latest_trading_day()
    if date_str is None:
        print("❌ 未找到任何数据", file=sys.stderr)
        return None

    date_label = date_short(date_str)
    all_alerts = []  # [{type, label, detail, strategy}]

    # ======== 策略 1: ETF 异动 ========
    print(f"{'='*60}")
    print(f"  策略 1: ETF 异动检测 [{date_str}]")
    print(f"{'='*60}\n")

    today_daily = read_etf_daily(date_str)
    if not today_daily:
        print(f"⚠ 未找到 {date_str} 的 ETF 日线数据，跳过策略 1\n")
    else:
        prev_dates = get_previous_trading_days(date_str, n=5)
        prev_days_data = {}
        for pd_date in prev_dates:
            d = read_etf_daily(pd_date)
            if d:
                prev_days_data[pd_date] = d
        print(f"参考: 前{len(prev_dates)}日 ({', '.join(prev_dates) if prev_dates else '无'}), "
              f"有效{len(prev_days_data)}日\n")

        for etf_name, threshold_wan in ETF_CONFIG:
            print(f"[{etf_name}]")

            td = today_daily.get(etf_name)
            if not td:
                print("  ⚠ 当日日线数据缺失，跳过\n")
                continue
            td["日期"] = date_str

            # 量能检测
            vol_alerts = check_volume_anomaly(etf_name, td, prev_days_data, threshold_wan)
            if vol_alerts:
                for a in vol_alerts:
                    a["strategy"] = "ETF量能"
                    tag_icon = "🔴🔵" if a["type"] == "both" else ("🔵" if a["type"] == "opp" else "🔴")
                    print(f"  {tag_icon} {a['label']}")
                    print(f"    └ {a['detail']}")
            else:
                print(f"  量能正常")

            all_alerts.extend(vol_alerts)

            # 异常买卖检测
            minute_data = read_etf_minute(etf_name, date_str)
            if minute_data is None:
                print(f"  ⚠ m5 数据缺失，跳过异常买卖检测\n")
                continue

            trade_alerts = check_abnormal_trade(etf_name, minute_data, prev_days_data, date_str)
            if trade_alerts:
                for a in trade_alerts:
                    a["strategy"] = "ETF异常买卖"
                    tag_icon = "🔵" if a["type"] == "opp" else "🔴"
                    print(f"  {tag_icon} {a['label']}")
                    print(f"    └ {a['detail']}")
            else:
                print(f"  异常买卖: 无信号")

            all_alerts.extend(trade_alerts)
            print()

    # ======== 策略 2: 指数量能异动 ========
    print(f"{'='*60}")
    print(f"  策略 2: 指数量能异动（天量见顶预警）[{date_str}]")
    print(f"{'='*60}\n")

    idx_alerts = check_index_volume(date_str, date_label)
    if idx_alerts:
        for a in idx_alerts:
            a["strategy"] = "指数量能"
            tag_icon = "🔴🔵" if a["type"] == "both" else ("🔵" if a["type"] == "opp" else "🔴")
            print(f"  {tag_icon} {a['label']}")
            print(f"    └ {a['detail']}")
    else:
        print(f"  成交额正常\n")

    all_alerts.extend(idx_alerts)

    # ======== 策略 3: 指数震荡区间 ========
    print(f"{'='*60}")
    print(f"  策略 3: 指数震荡区间风险机会判断 [{date_str}]")
    print(f"{'='*60}\n")

    state = load_index_state()
    print(f"  当前指数状态: {state}")

    zone_alerts = check_index_zone(date_str, date_label)
    if zone_alerts:
        for a in zone_alerts:
            a["strategy"] = "震荡区间"
            tag_icon = "🔴" if a["type"] == "risk" else "🔵"
            print(f"  {tag_icon} {a['label']}")
            print(f"    └ {a['detail']}")
    elif state == "区间震荡":
        print(f"  无信号\n")
    print()

    all_alerts.extend(zone_alerts)

    # ======== 策略 4: 赚钱效应检测 ========
    print(f"{'='*60}")
    print(f"  策略 4: 赚钱效应检测 [{date_str}]")
    print(f"{'='*60}\n")

    breadth_list = get_ordered_breadth(date_str, days_back=5)
    if breadth_list:
        up_strs = [f"{b['date'][5:]}:{b['up_count']}" for b in breadth_list]
        print(f"  近几日上涨家数: {' → '.join(up_strs)}")

        # 冰点检测
        ice_alerts = check_ice_detection(breadth_list, date_label)
        if ice_alerts:
            for a in ice_alerts:
                a["strategy"] = "赚钱效应"
                print(f"  🔵 {a['label']} ({a['detail']})")
        else:
            print(f"  冰点: 无信号")
        all_alerts.extend(ice_alerts)

        # 放量下跌检测
        panic_alerts = check_panic_sell(date_str, date_label)
        if panic_alerts:
            for a in panic_alerts:
                a["strategy"] = "赚钱效应"
                print(f"  🔴 {a['label']} ({a['detail']})")
        else:
            print(f"  放量下跌: 无信号")
        all_alerts.extend(panic_alerts)
    else:
        print(f"  ⚠ 无涨跌家数数据，跳过\n")

    print()

    # ======== 策略 5: 上证均线支撑/压制检测 ========
    print(f"{'='*60}")
    print(f"  策略 5: 上证均线支撑/压制检测 [{date_str}]")
    print(f"{'='*60}\n")

    ma_alerts = check_ma_pressure_support(date_str, date_label)
    if ma_alerts:
        for a in ma_alerts:
            a["strategy"] = "均线支撑/压制"
            tag_icon = "🔴" if a["type"] == "risk" else "🔵"
            print(f"  {tag_icon} {a['label']}")
            print(f"    └ {a['detail']}")
    else:
        print(f"  均线数据不足，无信号\n")
    print()

    all_alerts.extend(ma_alerts)

    # ======== 策略 6: 上证 MACD 超卖机会检测 ========
    print(f"{'='*60}")
    print(f"  策略 6: 上证 MACD 超卖机会检测 [{date_str}]")
    print(f"{'='*60}\n")

    macd_alerts = check_macd_oversold(date_label)
    if macd_alerts:
        for a in macd_alerts:
            a["strategy"] = "MACD超卖"
            print(f"  🔵 {a['label']}")
            print(f"    └ {a['detail']}")
    else:
        print(f"  MACD超卖: 无信号（当前MACD柱 > -50）\n")
    print()

    all_alerts.extend(macd_alerts)

    # ======== 汇总 ========
    print(f"\n{'='*60}")
    print(f"  汇总")
    print(f"{'='*60}")
    risk_count = sum(1 for a in all_alerts if a["type"] in ("risk", "both"))
    opp_count = sum(1 for a in all_alerts if a["type"] in ("opp", "both"))
    print(f"风险信号: {risk_count}  机会信号: {opp_count}  总计: {len(all_alerts)}")
    print(f"{'='*60}\n")

    if output_json:
        print(json.dumps(all_alerts, ensure_ascii=False, indent=2))

# 打标规则（--save-tags 时生效）：
# - 市场量能 < 3万亿：仅保存风险标签，提示风险
# - 其他情况：正常保存所有标签

    if save_tags and all_alerts:
        # 读取当日成交额
        today_idx = read_index_daily(date_str)
        sh_today = today_idx.get("上证指数") if today_idx else None
        sz_today = today_idx.get("深证成指") if today_idx else None
        sh_amt = sh_today.get("成交额_亿", 0) or 0 if sh_today else 0
        sz_amt = sz_today.get("成交额_亿", 0) or 0 if sz_today else 0
        market_vol = (sh_amt + sz_amt) * 1.01 if sh_amt > 0 and sz_amt > 0 else 0
        print(f"\n  市场总成交额: {market_vol:.0f} 亿")

        if 0 < market_vol < 30000:
            # 量能不足 → 仅提示风险，机会标签不提示
            filtered = [a for a in all_alerts if a["type"] in ("risk", "both")]
            print(f"  量能 < 3万亿，仅保留风险标签（过滤掉 {len(all_alerts)-len(filtered)} 条机会标签）")
            if filtered:
                _save_tags(filtered, date_str)
        else:
            # 量能充足或无成交额数据，正常保存所有标签
            _save_tags(all_alerts, date_str)

    return all_alerts


def _save_tags(alerts, date_str):
    """将检测结果追加到 risk_tags.json / opp_tags.json 的 manual 列表（不覆盖已有手动标签）"""
    risk_path = os.path.join(PROJECT_DIR, "risk_tags.json")
    opp_path = os.path.join(PROJECT_DIR, "opp_tags.json")

    def load_tags_file(path):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"manual": [], "deleted": []}

    risk_data = load_tags_file(risk_path)
    opp_data = load_tags_file(opp_path)

    for alert in alerts:
        label = alert["label"]
        if alert["type"] in ("risk", "both"):
            if label not in risk_data["manual"] and label not in risk_data["deleted"]:
                risk_data["manual"].append(label)
        if alert["type"] in ("opp", "both"):
            if label not in opp_data["manual"] and label not in opp_data["deleted"]:
                opp_data["manual"].append(label)

    with open(risk_path, "w", encoding="utf-8") as f:
        json.dump(risk_data, f, ensure_ascii=False, indent=2)
    with open(opp_path, "w", encoding="utf-8") as f:
        json.dump(opp_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 标签已保存: risk_tags.json ({len(risk_data['manual'])}条), opp_tags.json ({len(opp_data['manual'])}条)")


# ======== 入口 ========

def main():
    p = argparse.ArgumentParser(description="多策略异动检测：ETF 异动 + 指数量能异动")
    p.add_argument("--date", help="检测日期，格式 YYYY-MM-DD，默认最新")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    p.add_argument("--save-tags", action="store_true", help="保存标签到 risk_tags.json / opp_tags.json")
    args = p.parse_args()

    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print(f"❌ 日期格式错误: {args.date}，请使用 YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)

    result = run_detection(
        date_str=args.date,
        output_json=args.json,
        save_tags=args.save_tags,
    )

    if result is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
