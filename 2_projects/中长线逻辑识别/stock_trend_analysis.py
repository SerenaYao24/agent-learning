#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票趋势分析工具
分析方法：收集标的近 10 日的每天的涨跌幅，以 5 日为滑动窗口，统计近 10 日中连续 5 日涨跌幅
"""

# 必须在导入 akshare 之前禁用 tqdm，避免进度条输出混乱
import sys
import tqdm
tqdm.tqdm.disable = True

# Monkey patch tqdm to completely disable it (保持迭代功能)
class DummyTqdm:
    """完全禁用的tqdm，但保留基本功能"""
    def __init__(self, iterable=None, *args, **kwargs):
        self.iterable = iterable
        self.disable = True

    def __iter__(self):
        if self.iterable is None:
            return iter([])
        return iter(self.iterable)

    def __len__(self):
        if self.iterable is None:
            return 0
        try:
            return len(self.iterable)
        except TypeError:
            return 0

    def update(self, n=1): pass

    def close(self): pass

    def set_description(self, desc=None): pass

    def set_postfix(self, **kwargs): pass

    def __enter__(self): return self

    def __exit__(self, *args): pass

tqdm.tqdm = DummyTqdm

import akshare as ak
import argparse
from datetime import datetime, timedelta
import os
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from _topic_utils import parse_topic_header, format_topic_header

# 缓存文件路径
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".stock_cache")
CACHE_FILE = os.path.join(CACHE_DIR, "stock_data.json")
CODE_MAP_FILE = os.path.join(CACHE_DIR, "stock_code_map.json")  # 股票代码映射文件
# 报告索引文件（本地，不受 iCloud 同步影响）
REPORT_INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".stock_cache", "report_index.txt")
# 本地报告存储目录（生成时同时写 iCloud + 本地，多日分析只读本地）
LOCAL_REPORT_DIR = os.path.join(CACHE_DIR, "reports")
LOCAL_DAILY_REPORT_DIR = os.path.join(LOCAL_REPORT_DIR, "每日分析")
LOCAL_MULTI_DAY_REPORT_DIR = os.path.join(LOCAL_REPORT_DIR, "多日分析")
# 报告输出到 iCloud 文稿目录下的"股票分析结果"文件夹
BASE_REPORT_DIR = os.path.join(os.path.expanduser("~"), "Library", "Mobile Documents", "com~apple~CloudDocs", "Documents", "股票分析结果")
DAILY_REPORT_DIR = os.path.join(BASE_REPORT_DIR, "每日分析")
MULTI_DAY_REPORT_DIR = os.path.join(BASE_REPORT_DIR, "多日分析")
MAX_CACHE_DAYS = 30  # 缓存最多保留30天


def ensure_cache_dir():
    """确保缓存目录存在"""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)


def append_report_index(filename: str):
    """将已生成的每日报告文件名追加到索引文件（去重）"""
    ensure_cache_dir()
    try:
        # 读取已有索引，去重后写入
        existing = set()
        if os.path.exists(REPORT_INDEX_FILE):
            with open(REPORT_INDEX_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        existing.add(line)
        if filename not in existing:
            with open(REPORT_INDEX_FILE, 'a', encoding='utf-8') as f:
                f.write(filename + '\n')
    except Exception as e:
        print(f"⚠️ 写入报告索引失败: {e}")


def read_report_index(report_type: str = "daily") -> list:
    """
    从索引文件读取报告列表，返回本地完整路径列表（多日分析只读本地，不碰 iCloud）

    参数:
        report_type: "daily"=每日分析, "multi"=多日分析, "all"=全部
    """
    reports = []
    valid_lines = []
    if not os.path.exists(REPORT_INDEX_FILE):
        return reports

    try:
        with open(REPORT_INDEX_FILE, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
    except Exception:
        return reports

    for line in lines:
        # 根据文件名前缀判断所属目录（使用本地路径）
        if line.startswith('multi_day_trend_'):
            base_dir = LOCAL_MULTI_DAY_REPORT_DIR
            is_multi = True
        else:
            base_dir = LOCAL_DAILY_REPORT_DIR
            is_multi = False

        # 按 report_type 过滤
        if report_type == "daily" and is_multi:
            valid_lines.append(line)
            continue
        elif report_type == "multi" and not is_multi:
            valid_lines.append(line)
            continue

        report_path = os.path.join(base_dir, line)
        # 校验本地文件是否真实存在
        if os.path.isfile(report_path):
            reports.append(report_path)
            valid_lines.append(line)
        else:
            # 本地不存在则跳过（可能还没生成到本地）
            valid_lines.append(line)

    # 回写清理后的索引
    if len(valid_lines) != len(lines):
        try:
            with open(REPORT_INDEX_FILE, 'w', encoding='utf-8') as f:
                f.write('\n'.join(valid_lines) + '\n' if valid_lines else '')
        except Exception:
            pass

    return reports


def load_cache() -> dict:
    """
    加载缓存数据

    返回:
        缓存字典 {"股票代码": {"daily_changes": [], "dates": [], "last_update": "日期"}}
    """
    ensure_cache_dir()
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"加载缓存失败: {e}")
        return {}


def save_cache(cache: dict):
    """
    保存缓存数据

    参数:
        cache: 缓存字典
    """
    ensure_cache_dir()
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存缓存失败: {e}")


def _slice_by_indices(lst, indices):
    """按索引列表切片，若 lst 为空或不存在则返回 []"""
    if not lst:
        return []
    return [lst[i] for i in indices if i < len(lst)]


def clean_cache(cache: dict, days: int = MAX_CACHE_DAYS) -> dict:
    """
    清理过期的缓存数据，保留最近N天

    参数:
        cache: 缓存字典
        days: 保留天数

    返回:
        清理后的缓存字典
    """
    cutoff_date = datetime.now() - timedelta(days=days)
    cleaned = {}

    for stock_code, data in cache.items():
        dates = data.get("dates", [])
        daily_changes = data.get("daily_changes", [])

        # 保留最近N天的数据
        valid_indices = []
        for i, date_str in enumerate(dates):
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                if date >= cutoff_date:
                    valid_indices.append(i)
            except:
                pass

        if valid_indices:
            entry = {
                "dates": [dates[i] for i in valid_indices],
                "daily_changes": [daily_changes[i] for i in valid_indices],
                "last_update": data.get("last_update", "")
            }
            # 保留 OHLCV 字段
            for field in ("open", "close", "high", "low", "amount"):
                if data.get(field):
                    entry[field] = _slice_by_indices(data[field], valid_indices)
            cleaned[stock_code] = entry

    return cleaned


def update_cache(cache: dict, stock_code: str, new_data: dict) -> dict:
    """
    更新单个股票的缓存数据

    参数:
        cache: 现有缓存
        stock_code: 股票代码
        new_data: 新数据 {"daily_changes": [], "dates": [], "open": [], ...}

    返回:
        更新后的缓存
    """
    existing = cache.get(stock_code, {"dates": [], "daily_changes": []})
    existing_dates = existing.get("dates", [])
    existing_changes = existing.get("daily_changes", [])

    # 合并数据（去重，按日期排序）
    all_dates = existing_dates + new_data.get("dates", [])
    all_changes = existing_changes + new_data.get("daily_changes", [])

    # OHLCV 字段也合并（旧数据可能没有这些字段，用 None 补齐）
    ohlcv_fields = ("open", "close", "high", "low", "amount")
    existing_ohlcv = {}
    for f in ohlcv_fields:
        vals = existing.get(f, [])
        if not vals:
            vals = []
        # 如果旧缓存有日期但无 OHLCV，用 None 补齐长度
        if len(vals) < len(existing_dates):
            vals = [None] * (len(existing_dates) - len(vals)) + vals
        existing_ohlcv[f] = vals
    new_ohlcv = {f: new_data.get(f, []) for f in ohlcv_fields}
    all_ohlcv = {f: existing_ohlcv[f] + new_ohlcv[f] for f in ohlcv_fields}

    # 去重，保留最新的（以日期为 key）
    seen = set()
    unique_dates = []
    unique_changes = []
    unique_ohlcv = {f: [] for f in ohlcv_fields}
    for i in range(len(all_dates) - 1, -1, -1):
        d = all_dates[i]
        if d not in seen:
            seen.add(d)
            unique_dates.insert(0, d)
            unique_changes.insert(0, all_changes[i])
            for f in ohlcv_fields:
                val = all_ohlcv[f][i] if i < len(all_ohlcv[f]) else None
                unique_ohlcv[f].insert(0, val)

    entry = {
        "dates": unique_dates,
        "daily_changes": unique_changes,
        "last_update": datetime.now().strftime("%Y-%m-%d")
    }
    for f in ohlcv_fields:
        if any(v is not None for v in unique_ohlcv[f]):
            entry[f] = unique_ohlcv[f]
    cache[stock_code] = entry

    return cache


STOCK_CODE_MAP = {
    # 原有标的
    "利通电子": "sh603629",
    "维科技术": "sh600152",
    "中钨高新": "sz000657",
    "国城矿业": "sz000688",
    "蓝色光标": "sz300058",
    "法狮龙": "sh605318",
    "雄韬股份": "sz002733",
    "大元泵业": "sh603757",
    "柏诚股份": "sh601133",
    "长飞光纤": "sh601869",
    "泰晶科技": "sh603738",
    "云南锗业": "sz002428",
    "天通股份": "sh600330",
    "永鼎股份": "sh600105",
    "中天科技": "sh600522",
    "长光华芯": "sh688048",
    "东山精密": "sz002384",
    "中瓷电子": "sz003031",
    "华盛昌": "sz002980",
    "光迅科技": "sz002281",
    "博敏电子": "sh603936",
    "大族激光": "sz002008",
    "鹏鼎控股": "sz002938",
    "广合科技": "sz001389",
    # 光纤板块
    "法尔胜": "sz000890",
    "罗曼股份": "sh605289",
    "特发信息": "sz000070",
}


def load_stock_code_map() -> dict:
    """从文件加载股票代码映射"""
    if not os.path.exists(CODE_MAP_FILE):
        return {}
    try:
        with open(CODE_MAP_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"加载股票代码映射失败: {e}")
        return {}


def save_stock_code_map(code_map: dict):
    """保存股票代码映射到文件"""
    ensure_cache_dir()
    try:
        with open(CODE_MAP_FILE, 'w', encoding='utf-8') as f:
            json.dump(code_map, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存股票代码映射失败: {e}")


import re


def clean_stock_name(name: str) -> str:
    """
    清理股票名称，提取括号前的纯名称

    参数:
        name: 原始股票名称，可能包含括号注释

    返回:
        清理后的股票名称
    """
    # 提取括号前的内容（支持中文括号和英文括号）
    match = re.match(r'^([（(【\[《<]?[\u4e00-\u9fa5]+)', name.strip())
    if match:
        return match.group(1)
    # 如果没有匹配到，返回原名称
    return name.strip()


def extract_stock_code_from_parentheses(text: str) -> str:
    """
    从文本的括号中提取股票代码，并添加市场前缀

    例如："禾望电气（603063）" → "sh603063"

    参数:
        text: 可能包含括号及股票代码的文本

    返回:
        带市场前缀的股票代码（如 sh603063），或 None
    """
    # 匹配中文括号或英文括号内的6位数字
    match = re.search(r'[（(](\d{6})[）)]', text)
    if match:
        code = match.group(1)
        # 判断市场前缀：6开头=沪市，0/3开头=深市，4/8/92开头=北交所
        if code.startswith('6'):
            return f"sh{code}"
        elif code.startswith(('0', '3')):
            return f"sz{code}"
        elif code.startswith(('4', '8')) or code.startswith('92'):
            return f"bj{code}"
        else:
            return None
    return None


def read_stock_list_from_md(file_path: str) -> list:
    """
    从 Markdown 文件读取股票列表，支持按题材分组

    参数:
        file_path: Markdown 文件路径

    返回:
        分组后的股票列表，每个元素为 (分组名, [(清理后名称, 原始行), ...])
        如果文件没有分组，返回 [(None, [(清理后名称, 原始行)])]
    """
    groups = []
    current_group = None
    current_stocks = []

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # 检测分组标题 (## 开头的行)
            if line.startswith('## '):
                # 保存前一个分组
                if current_stocks:
                    groups.append((current_group, current_stocks))
                current_group = parse_topic_header(line)[0]  # 只用 topic_name 做分类
                current_stocks = []
            elif line.startswith('# '):
                # 一级标题作为整个文件的标题，不作为分组
                continue
            elif not line.startswith('#'):
                # 清理股票名称，去除括号内的注释；同时保留原始行用于括号内代码提取
                clean_name = clean_stock_name(line)
                current_stocks.append((clean_name, line))

    # 保存最后一个分组
    if current_stocks:
        groups.append((current_group, current_stocks))

    # 如果没有任何分组，返回默认分组
    if len(groups) == 1 and groups[0][0] is None:
        return [(None, groups[0][1])]

    return groups


# 本地股票名称映射缓存
_STOCK_NAME_TO_CODE_CACHE = {}


def _load_stock_name_mapping():
    """加载股票名称到代码的映射缓存"""
    global _STOCK_NAME_TO_CODE_CACHE
    if _STOCK_NAME_TO_CODE_CACHE:
        return

    try:
        print("  正在加载股票名称映射...")
        df = ak.stock_info_a_code_name()
        for _, row in df.iterrows():
            code = row['code']
            name = row['name'].replace(' ', '')  # 去除空格

            # 判断市场前缀
            if code.startswith('6'):
                full_code = f"sh{code}"
            elif code.startswith(('0', '3')):
                full_code = f"sz{code}"
            else:
                full_code = code

            _STOCK_NAME_TO_CODE_CACHE[name] = full_code
        print(f"  已加载 {len(_STOCK_NAME_TO_CODE_CACHE)} 个股票名称映射")
    except Exception as e:
        print(f"  加载股票名称映射失败: {e}")


def search_stock_code(stock_name: str, max_retries: int = 2) -> str:
    """
    通过股票名称搜索股票代码

    参数:
        stock_name: 股票名称
        max_retries: 最大重试次数

    返回:
        带市场前缀的股票代码，如 sh603629，或 None
    """
    # 确保映射已加载
    _load_stock_name_mapping()

    # 精确匹配
    if stock_name in _STOCK_NAME_TO_CODE_CACHE:
        return _STOCK_NAME_TO_CODE_CACHE[stock_name]

    # 模糊匹配：名称包含搜索词
    matches = [(name, code) for name, code in _STOCK_NAME_TO_CODE_CACHE.items()
               if stock_name in name or name in stock_name]

    if len(matches) == 1:
        return matches[0][1]
    elif len(matches) > 1:
        print(f"  找到多个匹配结果: {[m[0] for m in matches]}，使用第一个")
        return matches[0][1]

    return None


def get_stock_data(stock_code: str, cache: dict = None, max_retries: int = 3) -> dict:
    """
    使用 akshare (腾讯数据源) 获取股票数据，支持增量更新
    智能判断：如果缓存已包含最新交易日数据（3天内的有效工作日数据），则直接返回缓存，跳过API调用

    参数:
        stock_code: 带市场前缀的股票代码，如 sh603629, sz000657
        cache: 现有缓存数据
        max_retries: 最大重试次数

    返回:
        包含涨跌幅和成交量信息的字典，以及是否需要更新缓存的标记
    """
    cached_data = cache.get(stock_code, {}) if cache else {}
    cached_dates = cached_data.get("dates", [])
    cached_changes = cached_data.get("daily_changes", [])

    def _cached_ohlcv(data_dict):
        """从缓存中提取 OHLCV 字段"""
        return {
            "open_prices": data_dict.get("open", []) or [],
            "close_prices": data_dict.get("close", []) or [],
            "high_prices": data_dict.get("high", []) or [],
            "low_prices": data_dict.get("low", []) or [],
            "amounts": data_dict.get("amount", []) or [],
        }

    def _make_cache_return(data_dict):
        """构建缓存命中时的返回字典"""
        return {
            "daily_changes": data_dict.get("daily_changes", []),
            "volumes": [],
            "dates": data_dict.get("dates", []),
            "fetch_mode": "缓存",
            "need_cache_update": False,
            **_cached_ohlcv(data_dict),
        }

    # 判断是否可以直接使用缓存（不需要调API）
    # 核心逻辑：从缓存最后日期的下一天到今天之间，如果存在任何工作日，说明可能有新数据
    if cached_dates and len(cached_changes) >= 5:  # 至少5天数据
        try:
            last_date = datetime.strptime(cached_dates[-1], "%Y-%m-%d")
            today = datetime.now().date()
            cached_day = last_date.date()

            # 缓存日期是今天 → 同一天多次运行，直接用缓存
            if cached_day == today:
                return _make_cache_return(cached_data)

            # 从缓存日期的下一天开始，检查到今天为止是否有工作日
            # 如果有 → 中间可能产生了新的交易数据，需要调API更新
            check_date = cached_day + timedelta(days=1)
            has_missing_trading_day = False
            while check_date <= today:
                if check_date.weekday() < 5:  # 周一到周五 = 工作日
                    has_missing_trading_day = True
                    break
                check_date += timedelta(days=1)

            if not has_missing_trading_day:
                # 中间没有工作日（比如缓存是周五的数据，今天是周六或周日）
                return _make_cache_return(cached_data)
        except:
            pass

    # 需要获取新数据
    if not cached_dates or len(cached_changes) < 5:
        # 全量获取：只需最近约15个交易日（保证有10天涨跌幅）
        api_start_date = (datetime.now() - timedelta(days=25)).strftime("%Y%m%d")
        days_to_fetch = 12
        fetch_mode = "全量"
    else:
        # 增量获取：从缓存最后一天的下一天开始（往前缓冲2天防无数据）
        try:
            last_date = datetime.strptime(cached_dates[-1], "%Y-%m-%d")
            api_start_date = (last_date - timedelta(days=2)).strftime("%Y%m%d")
            days_to_fetch = (datetime.now() - last_date).days + 1
            days_to_fetch = min(max(days_to_fetch, 3), MAX_CACHE_DAYS)
        except:
            api_start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
            days_to_fetch = 10
        fetch_mode = "增量"

    end_date = datetime.now().strftime("%Y%m%d")

    for retry in range(max_retries):
        try:
            # 使用腾讯数据源，传入日期范围减少传输量
            df = ak.stock_zh_a_hist_tx(
                symbol=stock_code,
                adjust="qfq",
                start_date=api_start_date,
                end_date=end_date
            )

            if df is None or df.empty:
                if retry < max_retries - 1:
                    time.sleep(1)  # 减少等待时间
                    continue
                else:
                    print(f"获取股票 {stock_code} 数据失败: 空数据")
                    break

            # 只取需要的数据行
            df = df.tail(days_to_fetch + 5)

            # 获取日期列
            if 'date' in df.columns:
                df['日期'] = df['date']
            elif '日期' not in df.columns:
                # 如果没有日期列，跳过增量逻辑
                df['涨跌幅'] = df['close'].pct_change() * 100
                df = df.dropna(subset=['涨跌幅'])
                df = df.tail(days_to_fetch)

                daily_changes = df['涨跌幅'].tolist()
                daily_changes = [round(x, 2) for x in daily_changes]
                volumes = df['volume'].tolist() if 'volume' in df.columns else []

                return {
                    "daily_changes": daily_changes,
                    "volumes": volumes,
                    "dates": [datetime.now().strftime("%Y-%m-%d")] * len(daily_changes),
                    "open_prices": [], "close_prices": [],
                    "high_prices": [], "low_prices": [], "amounts": [],
                    "fetch_mode": fetch_mode,
                    "need_cache_update": True
                }

            df['涨跌幅'] = df['close'].pct_change() * 100
            df = df.dropna(subset=['涨跌幅'])
            df = df.tail(days_to_fetch)

            # 提取日期和涨跌幅
            dates = df['日期'].astype(str).tolist()
            daily_changes = df['涨跌幅'].tolist()
            daily_changes = [round(x, 2) for x in daily_changes]
            volumes = df['volume'].tolist() if 'volume' in df.columns else []

            # 确保 volumes 长度与 daily_changes 一致
            if len(volumes) > len(daily_changes):
                volumes = volumes[-len(daily_changes):]
            elif len(volumes) < len(daily_changes):
                volumes = volumes + [0] * (len(daily_changes) - len(volumes))

            # 提取 OHLCV 数据（用于 K 线图）
            def _extract_col(col_name):
                vals = df[col_name].tolist() if col_name in df.columns else []
                # 对齐长度
                if len(vals) > len(daily_changes):
                    vals = vals[-len(daily_changes):]
                elif len(vals) < len(daily_changes):
                    vals = vals + [None] * (len(daily_changes) - len(vals))
                return [round(v, 2) if v is not None else None for v in vals]

            return {
                "daily_changes": daily_changes,
                "volumes": volumes,
                "dates": dates,
                "open_prices": _extract_col("open"),
                "close_prices": _extract_col("close"),
                "high_prices": _extract_col("high"),
                "low_prices": _extract_col("low"),
                "amounts": _extract_col("amount"),
                "fetch_mode": fetch_mode,
                "need_cache_update": True
            }
        except Exception as e:
            if retry < max_retries - 1:
                print(f"  重试 {retry + 1}/{max_retries}...")
                time.sleep(1)  # 减少等待时间从3秒到1秒
            else:
                print(f"获取股票 {stock_code} 数据失败: {e}")
                # 如果获取失败但有缓存，返回缓存数据
                if cached_changes:
                    return _make_cache_return(cached_data)
                # API 返回空数据但有缓存 → 兜底用缓存
                if cached_changes:
                    print(f"  {stock_code} API无新数据，使用缓存")
                    return _make_cache_return(cached_data)
                return None


def calc_linear_slope(values: list) -> float:
    """
    计算线性回归斜率（最小二乘法）

    参数:
        values: y 值列表，x 为序号 0,1,2,...

    返回:
        斜率值
    """
    n = len(values)
    if n < 2:
        return 0.0
    x = list(range(n))
    sum_x = sum(x)
    sum_y = sum(values)
    sum_xy = sum(x[i] * values[i] for i in range(n))
    sum_x2 = sum(xi * xi for xi in x)
    denominator = n * sum_x2 - sum_x * sum_x
    if denominator == 0:
        return 0.0
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    return slope


def calc_relative_strength(daily_changes: list, avg_change: float) -> float:
    """
    计算相对强弱 (RS)

    参数:
        daily_changes: 近10日涨跌幅列表
        avg_change: 板块平均涨幅

    返回:
        相对强弱值，正值表示强于板块，负值表示弱于板块
    """
    if not daily_changes or len(daily_changes) < 5:
        return 0.0

    # 计算近5日累计涨幅
    recent_5 = daily_changes[-5:]
    cumulative = 1.0
    for change in recent_5:
        cumulative *= (1 + change / 100)
    recent_return = (cumulative - 1) * 100

    return round(recent_return - avg_change, 2)


def calc_volume_price_match(volumes: list, daily_changes: list) -> dict:
    """
    计算量价配合指标

    参数:
        volumes: 成交量列表
        daily_changes: 涨跌幅列表

    返回:
        量价配合分析结果
    """
    result = {
        "score": 0,           # 综合得分 (-2 到 +2)
        "pattern": "",        # 配合模式描述
        "up_with_volume": 0,  # 上涨且放量次数
        "up_with_drop_vol": 0, # 上涨但缩量次数
        "down_with_volume": 0, # 下跌且放量次数 (不好)
        "down_with_drop_vol": 0 # 下跌且缩量次数 (正常)
    }

    if not volumes or len(volumes) < 3 or not daily_changes:
        result["pattern"] = "数据不足"
        return result

    # 计算成交量移动平均
    vol_ma = sum(volumes) / len(volumes)

    for i in range(len(daily_changes)):
        if i >= len(volumes):
            break

        change = daily_changes[i]
        vol = volumes[i]
        is_up = change > 0
        is_volume_up = vol > vol_ma

        if is_up and is_volume_up:
            result["up_with_volume"] += 1
        elif is_up and not is_volume_up:
            result["up_with_drop_vol"] += 1
        elif not is_up and is_volume_up:
            result["down_with_volume"] += 1
        else:
            result["down_with_drop_vol"] += 1

    # 计算得分
    result["score"] = result["up_with_volume"] - result["down_with_volume"]
    result["score"] = max(-2, min(2, result["score"]))  # 限制在 -2 到 +2

    # 模式描述
    if result["score"] >= 2:
        result["pattern"] = "量价齐升"
    elif result["score"] == 1:
        result["pattern"] = "量价较好配合"
    elif result["score"] == 0:
        result["pattern"] = "量价中性"
    elif result["score"] == -1:
        result["pattern"] = "量价背离"
    else:
        result["pattern"] = "量价背离严重"

    return result


def calc_startup_date(daily_changes: list, threshold: float = 3.0) -> dict:
    """
    计算启动日期（首次大幅上涨的时间）

    参数:
        daily_changes: 近10日涨跌幅列表
        threshold: 大幅上涨阈值，默认3%

    返回:
        启动分析结果
    """
    result = {
        "startup_day": None,  # 首次超过阈值的交易日序号 (1-10)
        "startup_change": 0,  # 当时的涨跌幅
        "is_early_starter": False  # 是否为早期启动者
    }

    if not daily_changes:
        return result

    for i, change in enumerate(daily_changes):
        if change >= threshold:
            result["startup_day"] = i + 1  # 转为1-based
            result["startup_change"] = change
            # 前5天启动算早期
            result["is_early_starter"] = i < 5
            break

    return result


def calc_composite_score(result: dict) -> float:
    """
    计算综合评分 (用于识别领跑者)

    参数:
        result: 分析结果字典

    返回:
        综合评分 (0-100)
    """
    score = 50  # 基础分

    # 1. 趋势斜率 (权重 30%)
    slope = result.get("slope", 0)
    if slope > 0:
        score += min(30, slope * 5)  # 正斜率加分
    else:
        score += max(-30, slope * 3)  # 负斜率扣分

    # 2. 最近5日累计涨幅 (权重 30%)
    window_results = result.get("window_results", [])
    if window_results:
        last_5_return = window_results[-1]
        score += min(30, last_5_return * 2)  # 涨幅越大加分

    # 3. 量价配合 (权重 20%)
    vol_price = result.get("volume_price_match", {})
    score += vol_price.get("score", 0) * 10  # -2 到 +2 映射到 -20 到 +20

    # 4. 启动顺序 (权重 20%)
    startup = result.get("startup_info", {})
    if startup.get("startup_day"):
        if startup["is_early_starter"]:
            score += 20
        else:
            # 越晚启动分越低
            day = startup["startup_day"]
            score += max(0, 20 - (day - 5) * 4)

    return max(0, min(100, round(score, 1)))


def analyze_trend(stock_name: str, stock_code: str, data: dict, avg_change: float = 0) -> dict:
    """
    分析股票趋势

    参数:
        stock_name: 股票名称
        stock_code: 股票代码
        data: 包含 daily_changes 和 volumes 的字典
        avg_change: 板块平均涨幅（用于计算相对强弱）

    返回:
        分析结果字典
    """
    daily_changes = data.get("daily_changes")
    volumes = data.get("volumes", [])

    result = {
        "stock_name": stock_name,
        "stock_code": stock_code,
        "daily_changes": daily_changes,
        "volumes": volumes,
        "latest_change": None,
        "window_results": [],
        "slope": None,
        "relative_strength": 0,
        "volume_price_match": {},
        "startup_info": {},
        "composite_score": 0,
        "alerts": [],
        "status": "success"
    }

    if daily_changes is None or len(daily_changes) < 10:
        result["status"] = "数据不足"
        result["alerts"].append(f"⚠️ 数据不足，需要 10 个交易日数据")
        return result

    # 关键修复：始终只取最近 10 个交易日的数据进行分析
    # 避免 daily_changes 超过 10 天时，滑动窗口用到旧数据而非最新数据
    daily_changes = daily_changes[-10:]
    if volumes and len(volumes) > len(daily_changes):
        volumes = volumes[-len(daily_changes):]

    # DEBUG: 输出实际传入 analyze_trend 的原始数据，便于排查滑动窗口偏差
    # print(f"  🔍 DEBUG {stock_name}({stock_code}): 传入daily_changes长度={len(daily_changes)}, 数据={[f'{x:+.2f}' for x in daily_changes]}")

    # 同步更新 result 中的数据为截断后的最近10天
    result["daily_changes"] = daily_changes
    result["volumes"] = volumes

    result["latest_change"] = daily_changes[-1]

    # 计算滑动窗口
    window_results = []
    for i in range(6):
        window = daily_changes[i:i+5]
        cumulative = 1.0
        for change in window:
            cumulative *= (1 + change / 100)
        cumulative_return = (cumulative - 1) * 100
        window_results.append(round(cumulative_return, 2))

    result["window_results"] = window_results

    last_window = window_results[-1]
    first_window = window_results[0]

    # 1. 趋势斜率
    slope = calc_linear_slope(window_results)
    result["slope"] = round(slope, 4)

    # 2. 相对强弱 (抗跌性)
    result["relative_strength"] = calc_relative_strength(daily_changes, avg_change)

    # 3. 量价配合
    result["volume_price_match"] = calc_volume_price_match(volumes, daily_changes)

    # 4. 启动顺序
    result["startup_info"] = calc_startup_date(daily_changes)

    # 5. 综合评分
    result["composite_score"] = calc_composite_score(result)

    # 告警分析
    if last_window < 3:
        result["alerts"].append({
            "type": "红灯",
            "message": f"最近5日累计涨跌幅为{last_window:.2f}%，低于 3%，注意回调风险！"
        })

    if slope > 0:
        result["alerts"].append({
            "type": "绿灯",
            "message": f"涨幅呈扩大趋势 (斜率={slope:+.4f}，从{first_window:.2f}%到{last_window:.2f}%)，标的启动迹象出现！"
        })
    else:
        result["alerts"].append({
            "type": "红灯",
            "message": f"涨幅呈缩小/跌幅扩大趋势 (斜率={slope:+.4f}，从{first_window:.2f}%到{last_window:.2f}%)，标的走弱迹象出现！"
        })

    # 相对强弱告警
    if result["relative_strength"] > 2:
        result["alerts"].append({
            "type": "绿灯",
            "message": f"相对强弱 +{result['relative_strength']:.2f}%，强于板块"
        })
    elif result["relative_strength"] < -2:
        result["alerts"].append({
            "type": "红灯",
            "message": f"相对强弱 {result['relative_strength']:.2f}%，弱于板块"
        })

    return result


def generate_md_report(grouped_results: list, output_path: str, input_file: str = None):
    """
    生成 Markdown 格式的分析报告（支持分组），拆分为汇总文件和明细文件

    参数:
        grouped_results: 分组后的分析结果列表 [(分组名, [结果列表])]
        output_path: 输出文件路径（汇总报告）
        input_file: 输入文件路径

    输出:
        汇报文件: stock_trend_report_{date}.md      （题材分析表 + 评级统计）
        明细文件: stock_trend_report_{date}_detail.md（标的汇总表 + 各标的分析明细 + 评级统计）
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail_path = output_path.replace('.md', '_detail.md')

    header = f"""# 股票趋势分析报告

**生成时间：** {now}
"""

    if input_file:
        header += f"**输入文件：** {input_file}\n"

    header += "---\n\n"

    # 展平所有结果用于总结和统计（去重：按 stock_name 保留首次出现）
    seen_names = set()
    all_results = []
    for _, results in grouped_results:
        for r in results:
            name = r.get("stock_name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                all_results.append(r)

    # ========== 构建题材分析表 ==========
    main_content = header
    main_content += "## 分析结果\n\n"
    main_content += "### 📊 题材分析（按当日平均涨幅排序）\n\n"
    main_content += "| 题材名称 | 平均涨幅 | 标的数 | 涨超5%数 | 占比 | 最大涨幅标的 |\n"
    main_content += "|:--------|:-------:|:-----:|:------:|:----:|:------------|\n"

    # 使用 parse_sector_mapping 获取 标的→题材 映射（与多日分析一致）
    sector_map = parse_sector_mapping(input_file) if input_file else {}

    # 按 {sector_name: [results]} 分组
    sector_grouped = {}  # {sector_name: [result dicts]}
    for r in all_results:
        stock_name = r.get("stock_name", "")
        sectors = sector_map.get(stock_name, ["默认"])
        for s in sectors:
            if s not in sector_grouped:
                sector_grouped[s] = []
            sector_grouped[s].append(r)

    sector_stats = []  # [(sector_name, avg_change, total, gt5_count, max_name, max_change)]
    for sector_name, results in sector_grouped.items():
        valid = [r for r in results if r.get("status") == "success" and r.get("latest_change") is not None]
        if not valid:
            continue
        changes = [r["latest_change"] for r in valid]
        avg_change = sum(changes) / len(changes)
        gt5_count = sum(1 for c in changes if c >= 5)
        best = max(valid, key=lambda r: r["latest_change"])
        sector_stats.append((
            sector_name,
            round(avg_change, 2),
            len(valid),
            gt5_count,
            f"{gt5_count}/{len(valid)}",
            f"{best['stock_name']}({best['latest_change']:+.2f}%)"
        ))

    # 按平均涨幅降序排序
    sector_stats.sort(key=lambda x: x[1], reverse=True)

    for sname, avg, total, gt5, ratio, best_info in sector_stats:
        pct = f"{gt5/total*100:.0f}" if total > 0 else "-"
        main_content += f"| {sname} | {avg:+.2f}% | {total} | {gt5} | {ratio}({pct}%) | {best_info} |\n"

    if not sector_stats:
        main_content += "| *(无有效数据)* |\n"

    main_content += "\n---\n\n"

    # ========== 评级统计（两个文件都保留） ==========
    # 按窗口6（最近5日累计涨跌幅）从大到小排序，数据不足的排最后
    def _sort_key(r):
        if r.get("status") != "success":
            return float("-inf")
        return r.get("window_results", [0])[-1] if r.get("window_results") else 0
    sorted_results = sorted(all_results, key=_sort_key, reverse=True)

    # 统计各评级数量
    rating_counts = {"启动": 0, "强势": 0, "冲高回落": 0, "转跌": 0, "震荡": 0, "企稳复苏": 0, "弱势下跌": 0}
    # ===== 使用统一的7类评级框架统计（与下方标的汇总表一致）=====
    for r in sorted_results:
        if r.get("status") != "success":
            continue
        windows = r.get("window_results", [])
        last_5d = r.get("window_results", [0])[-1] if r.get("window_results") else 0
        slope_val = r.get("slope", 0)
        w6 = windows[-1] if len(windows) >= 6 else last_5d
        w5 = windows[-2] if len(windows) >= 6 else windows[-1] if len(windows) >= 2 else w6
        latest_change = r.get("latest_change", 0)
        has_positive_window = any(w > 0 for w in windows)
        max_window = max(windows) if windows else 0
        cross_zero = any(w > 0 for w in windows) and any(w < 0 for w in windows)
        consec_above3 = 0
        for w in reversed(windows):
            if w > 3:
                consec_above3 += 1
            else:
                break

        if consec_above3 >= 2 and w6 > w5 and latest_change > 3:
            rating_counts["启动"] += 1
        elif last_5d >= 3 and slope_val > 0:
            rating_counts["强势"] += 1
        elif last_5d >= 3 and slope_val <= 0:
            rating_counts["冲高回落"] += 1
        elif max_window - w6 > 5 and w6 < 0:
            rating_counts["转跌"] += 1
        elif cross_zero and -10 <= last_5d <= 10:
            rating_counts["震荡"] += 1
        elif last_5d < 0 and has_positive_window and w6 >= w5:
            rating_counts["企稳复苏"] += 1
        else:
            rating_counts["弱势下跌"] += 1

    total_valid = sum(rating_counts.values())

    # --- 评级统计字符串 ---
    rating_section = ""
    if total_valid > 0:
        rating_section += "### 📈 评级统计\n\n"
        rating_section += "| 评级 | 数量 | 占比 |\n|------|:-----:|:----:|\n"
        emoji_map = {"启动": "⭐", "强势": "🟢", "冲高回落": "🟡", "转跌": "🔴", "震荡": "🟠", "企稳复苏": "⭐", "弱势下跌": "🔴"}
        for key in ["启动", "强势", "冲高回落", "转跌", "震荡", "企稳复苏", "弱势下跌"]:
            count = rating_counts[key]
            pct = count / total_valid * 100 if total_valid > 0 else 0
            rating_section += f"| {emoji_map[key]} {key} | {count} | {pct:.0f}% |\n"
        rating_section += "\n"

    # 汇总文件：题材分析 + 评级统计
    main_content += rating_section
    main_content += "---\n\n*报告由股票趋势分析工具自动生成*\n"

    # ========== 明细内容：标的汇总表 + 标的明细 + 评级统计 ==========
    detail_content = header
    detail_content += "## 标的详情\n\n"

    # --- 标的汇总表（按结论分表） ---
    # 先对所有标的计算结论，并按结论分类
    rating_order = [
        ("⭐ 启动", "⭐ **【启动】**", "连续强势且加速"),
        ("🟢 强势", "🟢 **【强势】**", "涨且加速"),
        ("🟡 冲高回落", "🟡 **【冲高回落】**", "涨势减弱"),
        ("🔴 转跌", "🔴 **【转跌】**", "由强转弱拐点"),
        ("🟠 震荡", "🟠 **【震荡】**", "穿越0轴整理"),
        ("⭐ 企稳复苏", "⭐ **【企稳复苏】**", "摸过0且回升"),
        ("🔴 弱势下跌", "🔴 **【弱势下跌】**", "跌且走弱"),
    ]
    # 按结论分桶，每桶内按窗口6降序
    categorized = {label: [] for label, _, _ in rating_order}
    data_insufficient = []

    for r in sorted_results:
        stock_name = r.get("stock_name", "未知")

        if r.get("status") != "success":
            data_insufficient.append((stock_name, r))
            continue

        windows = r.get("window_results", [])
        window_strs = []
        for w in windows:
            window_strs.append(f"{w:+.1f}%")
        while len(window_strs) < 6:
            window_strs.insert(0, "-")

        last_5d_return = r.get("window_results", [0])[-1] if r.get("window_results") else 0
        slope = r.get("slope", 0)

        # ===== 7类评级框架（判断优先级从上到下，命中即返回）=====
        windows = r.get("window_results", [])
        last_5d = last_5d_return
        slope_val = slope
        w6 = windows[-1] if len(windows) >= 6 else last_5d
        w5 = windows[-2] if len(windows) >= 6 else windows[-1] if len(windows) >= 2 else w6
        latest_change = r.get("latest_change", 0)

        # 预计算辅助变量
        has_positive_window = any(w > 0 for w in windows)
        max_window = max(windows) if windows else 0
        cross_zero = any(w > 0 for w in windows) and any(w < 0 for w in windows)
        consec_above3 = 0
        for w in reversed(windows):
            if w > 3:
                consec_above3 += 1
            else:
                break

        # 1. ⭐ 启动
        if (consec_above3 >= 2 and w6 > w5 and latest_change > 3):
            conclusion_label = "⭐ 启动"

        # 2. 🟢 强势
        elif last_5d >= 3 and slope_val > 0:
            conclusion_label = "🟢 强势"

        # 3. 🟡 冲高回落
        elif last_5d >= 3 and slope_val <= 0:
            conclusion_label = "🟡 冲高回落"

        # 4. 🔴 转跌
        elif (max_window - w6 > 5 and w6 < 0):
            conclusion_label = "🔴 转跌"

        # 5. 🟠 震荡
        elif cross_zero and -10 <= last_5d <= 10:
            conclusion_label = "🟠 震荡"

        # 6. ⭐ 企稳复苏
        elif last_5d < 0 and has_positive_window and w6 >= w5:
            conclusion_label = "⭐ 企稳复苏"

        # 7. 🔴 弱势下跌
        else:
            conclusion_label = "🔴 弱势下跌"

        categorized[conclusion_label].append((stock_name, window_strs, w6, r))

    # 按类别逐个生成分表
    detail_content += "### 📊 标的汇总表（按评级分表）\n\n"
    for label, conclusion_md, desc in rating_order:
        items = categorized[label]
        if not items:
            continue
        # 桶内按窗口6降序
        items.sort(key=lambda x: x[2], reverse=True)
        detail_content += f"#### {label}（{desc}）— {len(items)} 只\n\n"
        detail_content += "| 标的名称 | 窗口1 | 窗口2 | 窗口3 | 窗口4 | 窗口5 | 窗口6 | 结论 |\n"
        detail_content += "|---------|------|------|------|------|------|------|------|\n"
        for stock_name, window_strs, w6, r in items:
            detail_content += f"| {stock_name} | {window_strs[0]} | {window_strs[1]} | {window_strs[2]} | {window_strs[3]} | {window_strs[4]} | {window_strs[5]} | {conclusion_md} |\n"
        detail_content += "\n"

    # 数据不足的标的
    if data_insufficient:
        detail_content += f"#### ⚠️ 数据不足 — {len(data_insufficient)} 只\n\n"
        detail_content += "| 标的名称 |\n|---------|\n"
        for stock_name, _ in data_insufficient:
            detail_content += f"| {stock_name} |\n"
        detail_content += "\n"

    detail_content += "**结论说明：** ⭐【启动】=连续强势且加速，🟢【强势】=涨且加速，🟡【冲高回落】=涨势减弱，🔴【转跌】=由强转弱拐点，🟠【震荡】=穿越0轴整理，⭐【企稳复苏】=摸过0且回升，🔴【弱势下跌】=跌且走弱\n\n"

    # --- 评级统计（明细也放一份） ---
    detail_content += rating_section
    detail_content += "---\n\n"

    # --- 按分组输出详细分析 ---
    group_idx = 0
    for group_name, results in grouped_results:
        group_idx += 1

        if group_name:
            detail_content += f"## {group_name}\n\n"

        # 找出该组领跑者
        valid_results = [r for r in results if r.get("status") == "success" and r.get("composite_score", 0) > 0]
        if valid_results:
            leader = max(valid_results, key=lambda x: x.get("composite_score", 0))
            detail_content += f"> 🏆 **领跑者：{leader['stock_name']}** (综合评分: {leader['composite_score']:.1f})\n\n"

        for i, r in enumerate(results, 1):
            display_code = r['stock_code'].replace('sh', '').replace('sz', '') if r['stock_code'] != '未知' else '未知'

            # 检查是否是领跑者
            is_leader = valid_results and r.get("stock_name") == leader.get("stock_name")
            leader_tag = " 🏆" if is_leader else ""

            detail_content += f"### {group_idx}.{i}. {r['stock_name']} ({display_code}){leader_tag}\n\n"

            if r["status"] != "success":
                detail_content += f"**状态：** {r['status']}\n\n"
                for alert in r["alerts"]:
                    detail_content += f"- {alert}\n"
                detail_content += "\n---\n\n"
                continue

            # 综合评分
            score = r.get("composite_score", 0)
            score_bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
            detail_content += f"**综合评分：** {score:.1f}/100 {score_bar}\n\n"

            detail_content += f"**最近一日涨跌幅：** {r['latest_change']:.2f}%\n\n"

            detail_content += f"**连续 5 日涨跌幅 (滑动窗口)：**\n\n"
            detail_content += f"```\n{', '.join([f'{x:.1f}%' for x in r['window_results']])}\n```\n\n"

            # 趋势斜率
            if r.get("slope") is not None:
                slope_icon = "📈" if r["slope"] > 0 else "📉"
                detail_content += f"**趋势斜率：** {slope_icon} {r['slope']:+.4f}\n\n"

            # 相对强弱
            rs = r.get("relative_strength", 0)
            rs_icon = "🟢" if rs > 0 else "🔴" if rs < 0 else "⚪"
            detail_content += f"**相对强弱：** {rs_icon} {rs:+.2f}%\n\n"

            # 量价配合
            vp = r.get("volume_price_match", {})
            if vp and vp.get("pattern"):
                detail_content += f"**量价配合：** {vp['pattern']} (得分: {vp.get('score', 0)})\n\n"

            # 启动顺序
            startup = r.get("startup_info", {})
            if startup.get("startup_day"):
                startup_icon = "🚀" if startup["is_early_starter"] else "🐢"
                detail_content += f"**启动时间：** {startup_icon} 第{startup['startup_day']}日 (涨幅 {startup['startup_change']:.2f}%)\n\n"
            else:
                detail_content += f"**启动时间：** ⚪ 近10日无大幅上涨\n\n"

            # 告警
            if r["alerts"]:
                detail_content += "**🚨 告警分析：**\n\n"
                for alert in r["alerts"]:
                    icon = "🔴" if alert["type"] == "红灯" else "🟢"
                    detail_content += f"- {icon} **{alert['type']}告警：** {alert['message']}\n"
                detail_content += "\n"

            detail_content += "---\n\n"

    detail_content += "---\n\n*报告由股票趋势分析工具自动生成*\n"

    # 写入两个位置：iCloud + 本地
    icloud_ok = False
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(main_content)
        with open(detail_path, 'w', encoding='utf-8') as f:
            f.write(detail_content)
        icloud_ok = True
        print(f"\n✅ 汇总报告已生成：{output_path}")
        print(f"✅ 明细报告已生成：{detail_path}")
    except PermissionError as e:
        print(f"\n⚠️ 无法写入 iCloud 报告: {e}")

    # 始终写入本地副本（多日分析只读本地，不碰 iCloud）
    try:
        local_output = output_path.replace(DAILY_REPORT_DIR, LOCAL_DAILY_REPORT_DIR).replace(MULTI_DAY_REPORT_DIR, LOCAL_MULTI_DAY_REPORT_DIR)
        local_detail = local_output.replace('.md', '_detail.md')
        os.makedirs(os.path.dirname(local_output), exist_ok=True)
        with open(local_output, 'w', encoding='utf-8') as f:
            f.write(main_content)
        with open(local_detail, 'w', encoding='utf-8') as f:
            f.write(detail_content)
        print(f"✅ 本地副本已生成：{local_output}")
    except Exception as e:
        print(f"⚠️ 写入本地副本失败: {e}")

    # 追加到报告索引（供多日分析使用）
    if icloud_ok:
        append_report_index(os.path.basename(output_path))

    return rating_counts


def get_latest_trading_date(cache: dict) -> str:
    """
    从缓存中提取最新的交易日日期

    返回格式: "YYYY-MM-DD"
    优先使用缓存中实际数据的最新交易日，而不是当前运行日期。
    这样避免周末运行时用周六/周日作为文件名（实际数据还是周五的）。

    参数:
        cache: 股票数据缓存字典

    返回:
        最新交易日日期字符串，如果无缓存则返回当前日期
    """
    latest_trading_date = None

    for stock_data in cache.values():
        dates = stock_data.get("dates", [])
        if dates:
            try:
                # 取这只股票的最新日期
                stock_max = max(datetime.strptime(d, "%Y-%m-%d") for d in dates)
                # 只考虑工作日 (0-4 = 周一到周五)
                if stock_max.weekday() < 5:
                    if latest_trading_date is None or stock_max > latest_trading_date:
                        latest_trading_date = stock_max
            except (ValueError, TypeError):
                continue

    if latest_trading_date:
        return latest_trading_date.strftime("%Y-%m-%d")

    # 无缓存时回退到当前日期
    return datetime.now().strftime("%Y-%m-%d")


# ======== top_list 生成函数（嵌入本文件）========

_HARDCODED_MAP = {
    "杭电股份": "sh603618", "长飞光纤": "sh601869", "法尔胜": "sz000890",
    "亨通光电": "sh600487", "华盛昌": "sz002980", "远东股份": "sh600869",
    "三孚股份": "sh603938", "通鼎互联": "sz002491", "中天科技": "sh600522",
    "新能泰山": "sz000720", "光电股份": "sh600184", "特发信息": "sz000070",
    "汇源通信": "sz000586",
}


def _parse_sectors_for_toplist(filepath):
    """解析 interest_stock.md，返回 ({题材名: [(原始行, 股票名)]}, {题材名: 备注})"""
    sectors = {}
    sector_notes = {}  # topic_name → topic_note
    current_sector = None
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('# '):
                topic_name, topic_note = parse_topic_header(line)
                current_sector = topic_name
                sectors[current_sector] = []
                if topic_note:
                    sector_notes[current_sector] = topic_note
            elif current_sector:
                name = re.split(r'[（(【]', line)[0].strip()
                sectors[current_sector].append((line, name))
    return sectors, sector_notes


def _get_stock_code_for_toplist(stock_name, code_map):
    """获取股票代码（优先硬编码修正）"""
    if stock_name in _HARDCODED_MAP:
        return _HARDCODED_MAP[stock_name]
    return code_map.get(stock_name)


def _calc_10d_return(data):
    """从缓存数据计算近10日累计涨跌幅"""
    changes = data.get("daily_changes", [])
    closes = data.get("close", [])
    if not changes:
        return None
    recent = changes[-10:] if len(changes) >= 10 else changes
    ret = sum(recent)
    if len(closes) >= 2:
        latest = closes[-1]
        idx = max(0, len(closes) - 11)
        old = closes[idx]
        if old and old > 0:
            exact = (latest - old) / old * 100
            if abs(exact - ret) > 3:
                ret = round(exact, 2)
    return round(ret, 2)


def _fetch_10d_return(code):
    """从 akshare 获取单只股票近10日涨幅"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=25)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist_tx(symbol=code, adjust="qfq", start_date=start, end_date=end)
        if df is None or df.empty or len(df) < 2:
            return None
        changes = df['close'].pct_change().fillna(0).mul(100).round(2).tolist()
        closes = df['close'].tolist()
        recent = changes[-10:] if len(changes) >= 10 else changes
        ret = sum(recent)
        if len(closes) >= 2:
            latest = closes[-1]
            idx = max(0, len(closes) - 11)
            old = closes[idx]
            if old and old > 0:
                exact = (latest - old) / old * 100
                if abs(exact - ret) > 3:
                    ret = round(exact, 2)
        return round(ret, 2)
    except Exception:
        return None


def generate_top_list(input_path, output_path):
    """
    从 interest_stock.md 生成 top_list.md
    对每个题材，按近10日涨幅排序，保留前15名
    """
    print("📌 生成 top_list.md（基于最新缓存数据）...")
    sectors, sector_notes = _parse_sectors_for_toplist(input_path)
    cache = load_cache()
    code_map = load_stock_code_map()

    for sname, stocks in sectors.items():
        for _, name in stocks:
            code = _get_stock_code_for_toplist(name, code_map)
            if code and code not in cache:
                ret = _fetch_10d_return(code)
                # 不实际存入 cache，只是尝试获取

    cache = load_cache()  # 重新读取

    lines = []
    first = True
    for sname, stocks in sectors.items():
        scored = []
        for orig_line, name in stocks:
            code = _get_stock_code_for_toplist(name, code_map)
            if code:
                data = cache.get(code)
                if data:
                    ret = _calc_10d_return(data)
                else:
                    ret = _fetch_10d_return(code)
                if ret is None:
                    ret = float('-inf')
            else:
                ret = float('-inf')
            scored.append((ret, orig_line, name))
        scored.sort(key=lambda x: x[0], reverse=True)
        top15 = scored[:15]
        header = format_topic_header(sname, sector_notes.get(sname, ''))
        if first:
            lines.append(header)
            first = False
        else:
            lines.append(f"\n{header}")
        for ret, ol, _ in top15:
            if ret == float('-inf'):
                lines.append(f"{ol}  # ⚠️ 无近10日数据")
            else:
                lines.append(ol)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"   ✅ top_list.md 已生成")


def main():
    parser = argparse.ArgumentParser(description='股票趋势分析工具')
    parser.add_argument('--input', '-i', type=str, required=True,
                        help='输入 Markdown 文件路径，包含股票名称列表')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='输出文件路径，默认为 stock_trend_report_YYYY-MM-DD.md（自动按日期命名）')
    parser.add_argument('--refresh', action='store_true',
                        help='强制全量刷新，不使用缓存')
    parser.add_argument('--multi', action='store_true',
                        help='仅生成多日趋势汇总报告（不执行单日分析，强制生成）')
    parser.add_argument('--force-gen', action='store_true',
                        help='强制重新生成报告（使用已有缓存数据，不拉取API，但强制输出单日+多日报告）')

    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        print(f"❌ 输入文件不存在：{input_path}")
        return

    # 自动检测 top_list.md，用于题材统计（排除僵尸股）
    input_dir = os.path.dirname(input_path)
    top_list_path = os.path.join(input_dir, "top_list.md")
    sector_input_path = top_list_path if os.path.exists(top_list_path) else input_path
    if os.path.exists(top_list_path):
        print(f"📌 题材统计使用 top_list.md（排除僵尸股）")
    else:
        print(f"📌 未找到 top_list.md，题材统计使用原输入文件")

    # --multi 模式：直接生成多日报告，跳过单日分析
    if args.multi:
        print("="*60)
        print("📊 多日趋势汇总（强制生成模式）")
        print("="*60)
        generate_top_list(input_path, top_list_path)
        sector_input_path = top_list_path
        generate_multi_day_analysis(input_md_path=sector_input_path)
        print("\n" + "="*60)
        print("多日分析完成")
        print("="*60)
        return

    groups = read_stock_list_from_md(input_path)

    if not groups or (len(groups) == 1 and groups[0][1] == []):
        print("❌ 未从输入文件中读取到股票名称")
        return

    # 统计总股票数
    total_stocks = sum(len(stocks) for _, stocks in groups)
    group_names = [g[0] if g[0] else "默认" for g in groups]

    print("="*60)
    print("股票趋势分析系统")
    print("="*60)
    print(f"输入文件：{input_path}")
    print(f"分组数量：{len(groups)}，总标的数：{total_stocks}")
    for gn in group_names:
        print(f"  - {gn}")
    print(f"输出文件：{args.output}")
    if args.refresh:
        print("⚡ 强制全量刷新模式")
    if args.force_gen:
        print("🔄 强制报告生成模式（使用缓存，强制输出）")
    print("="*60)

    # 加载缓存
    cache = load_cache()
    if cache:
        cached_stocks = len(cache)
        print(f"📦 已加载缓存：{cached_stocks} 只股票")
    else:
        print("📦 无缓存，将全量获取数据")

    # 加载股票代码映射（持久化）
    saved_code_map = load_stock_code_map()
    if saved_code_map:
        STOCK_CODE_MAP.update(saved_code_map)
        print(f"📋 已加载股票代码映射：{len(saved_code_map)} 条")

    # 清理过期缓存（保留30天）
    cache = clean_cache(cache)

    grouped_results = []
    has_api_call = False  # 追踪本次是否有API调用（=有新数据）
    not_found_stocks = []  # 收集所有找不到代码的股票

    # 创建线程锁用于缓存更新
    cache_lock = threading.Lock()
    results_lock = threading.Lock()

    # 定义单只股票的处理函数（用于线程池）
    def process_single_stock(stock_name, original_line=None):
        """处理单只股票的数据获取和分析，返回(result, changes, cache_update)"""
        stock_code = None

        # 第1步：优先从原始行的括号中提取股票代码（如"禾望电气（603063）"）
        if original_line:
            paren_code = extract_stock_code_from_parentheses(original_line)
            if paren_code:
                print(f"  ✅ 从括号中提取股票代码：{paren_code}")
                stock_code = paren_code
                # 保存到持久化映射，下次可直接使用
                if stock_name not in STOCK_CODE_MAP:
                    STOCK_CODE_MAP[stock_name] = stock_code
                    save_stock_code_map(STOCK_CODE_MAP)
                    print(f"  💾 已保存到映射文件")

        # 第2步：从预定义映射查找
        if not stock_code and stock_name in STOCK_CODE_MAP:
            stock_code = STOCK_CODE_MAP[stock_name]

        # 第3步：尝试通过名称搜索代码
        if not stock_code:
            # 尝试通过名称搜索代码
            print(f"⚠️ 预定义映射中未找到 '{stock_name}'，尝试搜索...")
            stock_code = search_stock_code(stock_name)
            if stock_code:
                print(f"  ✅ 搜索成功：{stock_code}")
                # 动态添加到映射中，下次使用（并持久化）
                STOCK_CODE_MAP[stock_name] = stock_code
                save_stock_code_map(STOCK_CODE_MAP)
                print(f"  💾 已保存到映射文件")

        # 仍找不到代码，跳过
        if not stock_code:
            print(f"❌ 无法找到股票 '{stock_name}' 的代码，跳过")
            not_found_stocks.append(stock_name)
            return {
                "stock_name": stock_name,
                "stock_code": "未知",
                "daily_changes": [],
                "alerts": ["❌ 未找到股票代码"],
                "status": "未找到代码"
            }, [], None

        print(f"正在获取 {stock_name} ({stock_code}) 数据...")

        # 获取数据（传入缓存，支持增量获取）
        # 检查代码格式：北交所（92开头、4开头、8开头）可能不被 akshare 支持，提前跳过
        _raw_code = stock_code.replace('bj', '').replace('sh', '').replace('sz', '')
        if _raw_code.startswith(('4', '8')) or _raw_code.startswith('92'):
            print(f"  ⚠ {stock_name}({stock_code}) 北交所股票，API 可能不支持，跳过")
            return {
                "stock_name": stock_name,
                "stock_code": stock_code,
                "daily_changes": [],
                "alerts": ["北交所股票，API 不支持"],
                "status": "API不支持"
            }, [], None

        if args.refresh or stock_code not in cache:
            # 全量获取
            data = get_stock_data(stock_code, {})
        else:
            # 增量获取
            data = get_stock_data(stock_code, cache)

        result = None
        changes = []
        cache_update = None

        if data:
            fetch_mode = data.get("fetch_mode", "未知")
            print(f"  {stock_name} 获取模式：{fetch_mode}")

            # 准备缓存更新数据（含 OHLCV 字段）
            if data.get("need_cache_update"):
                cache_update = {
                    "stock_code": stock_code,
                    "dates": data.get("dates", []),
                    "daily_changes": data.get("daily_changes", []),
                    "open": data.get("open_prices", []),
                    "close": data.get("close_prices", []),
                    "high": data.get("high_prices", []),
                    "low": data.get("low_prices", []),
                    "amount": data.get("amounts", []),
                }

            if data.get("daily_changes"):
                print(f"  {stock_name} 获取成功，近 10 日涨跌幅：{[f'{x:.2f}%' for x in data['daily_changes']]}")
                changes = data['daily_changes']

                # 使用合并后的数据进行分析（旧缓存 + 本次新增数据）
                if data.get("need_cache_update") and data.get("dates") and data.get("daily_changes"):
                    # 增量模式：按日期去重，只追加缓存中不存在的新日期
                    old_changes = cache.get(stock_code, {}).get("daily_changes", [])
                    old_dates = cache.get(stock_code, {}).get("dates", [])
                    old_date_set = set(old_dates)
                    # 找到新数据中第一个不在旧缓存里的日期
                    new_dates = data["dates"]
                    new_changes = data["daily_changes"]
                    split_idx = 0
                    for i, d in enumerate(new_dates):
                        if d not in old_date_set:
                            split_idx = i
                            break
                    else:
                        # 所有新日期都已在缓存中，无需追加
                        split_idx = len(new_dates)
                    merged_changes = old_changes + new_dates[split_idx:]  # noqa: F841 — 保留用于调试参考
                    merged_dates = old_dates + new_dates[split_idx:]
                    # 同步对齐 changes
                    merged_changes = old_changes + new_changes[split_idx:]
                    analysis_changes = merged_changes[-10:]
                else:
                    # 全量或缓存命中：直接使用返回的数据
                    analysis_changes = data['daily_changes']

                stock_cache = cache.get(stock_code, {})
                analysis_data = {
                    "daily_changes": analysis_changes,
                    "volumes": data.get("volumes", [])
                }
                result = analyze_trend(stock_name, stock_code, analysis_data)
            else:
                result = analyze_trend(stock_name, stock_code, {"daily_changes": [], "volumes": []})
        else:
            result = analyze_trend(stock_name, stock_code, {"daily_changes": [], "volumes": []})

        return result, changes, cache_update

    # 使用线程池并行处理所有分组
    for group_name, stock_names in groups:
        print(f"\n{'='*60}")
        print(f"📁 分组：{group_name if group_name else '默认'}")
        print(f"{'='*60}")

        results = []
        all_changes = []  # 用于收集该分组所有股票的涨跌幅

        # 使用线程池并行获取数据（最大10个线程，提升并发性能）
        max_workers = min(10, len(stock_names))
        print(f"🚀 使用 {max_workers} 个线程并行获取数据...")

        results = []
        all_changes = []  # 用于收集该分组所有股票的涨跌幅
        cache_updates = []  # 收集需要更新的缓存数据

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务，传入清理后的名称和原始行（用于括号内代码提取）
            future_to_stock = {
                executor.submit(process_single_stock, clean_name, original_line): clean_name
                for clean_name, original_line in stock_names
            }

            # 收集结果（带超时，防止单只股票 API 卡死拖慢整个流程）
            _PENDING_TIMEOUT = 30  # 单只股票最长等待30秒
            for future in as_completed(future_to_stock):
                stock_name = future_to_stock[future]
                try:
                    result, changes, cache_update = future.result(timeout=_PENDING_TIMEOUT)
                    results.append(result)

                    # 收集涨跌幅数据
                    if changes:
                        all_changes.extend(changes)

                    # 收集缓存更新
                    if cache_update:
                        cache_updates.append(cache_update)

                except Exception as exc:
                    print(f"{stock_name} 处理时出错: {exc}")
                    results.append({
                        "stock_name": stock_name,
                        "stock_code": "未知",
                        "daily_changes": [],
                        "alerts": [f"❌ 处理出错: {exc}"],
                        "status": "处理出错"
                    })

        # 统一更新缓存（线程安全），每组处理完即写入磁盘
        if cache_updates:
            for update in cache_updates:
                cache = update_cache(cache, update["stock_code"], {
                    "dates": update["dates"],
                    "daily_changes": update["daily_changes"],
                    "open": update.get("open", []),
                    "close": update.get("close", []),
                    "high": update.get("high", []),
                    "low": update.get("low", []),
                    "amount": update.get("amount", []),
                })
                has_api_call = True  # 有缓存更新说明调用了API，即有新数据
            save_cache(cache)
            print(f"💾 缓存已保存（{group_name or '默认'}组完成）")

        # 计算该分组板块平均涨幅（用于相对强弱计算）
        avg_change = sum(all_changes) / len(all_changes) if all_changes else 0

        # 重新分析以更新相对强弱
        for r in results:
            if r.get("status") == "success":
                r["relative_strength"] = calc_relative_strength(r["daily_changes"], avg_change)
                r["composite_score"] = calc_composite_score(r)

        grouped_results.append((group_name, results))

    print(f"\n💾 全部分组处理完毕，缓存已按组分批保存至：{CACHE_FILE}")

    # 保存股票代码映射
    save_stock_code_map(STOCK_CODE_MAP)
    print(f"💾 股票代码映射已保存至：{CODE_MAP_FILE}")

    # 使用最新交易日日期作为报告文件名（避免周末运行时用周末日期）
    # 同一天多次运行会覆盖，保证数据一致性
    if args.output is None:
        latest_date = get_latest_trading_date(cache)
        args.output = f'stock_trend_report_{latest_date}.md'
        print(f"📅 报告使用数据日期：{latest_date}")

    # 确保报告目录存在（基础目录 + 两个子目录）
    try:
        for d in [BASE_REPORT_DIR, DAILY_REPORT_DIR, MULTI_DAY_REPORT_DIR]:
            if not os.path.exists(d):
                os.makedirs(d)
    except PermissionError as e:
        print(f"❌ 无法创建报告目录: {e}")
        print("   请检查 iCloud 同步状态或目录权限，然后重试")
        return

    # 数据已全部获取完毕，更新 top_list.md
    generate_top_list(input_path, top_list_path)
    sector_input_path = top_list_path

    # 每日分析报告保存到"每日分析"子目录
    output_path = os.path.join(DAILY_REPORT_DIR, args.output)
    rating_counts = generate_md_report(grouped_results, output_path, sector_input_path)

    # 终端打印进攻/防守标的占比（定义与多日分析报告一致）
    total_valid = sum(rating_counts.values())
    if total_valid > 0:
        attack_count = rating_counts["启动"] + rating_counts["强势"] + rating_counts["企稳复苏"]
        defense_count = rating_counts["冲高回落"] + rating_counts["转跌"] + rating_counts["震荡"] + rating_counts["弱势下跌"]
        attack_pct = f"{attack_count/total_valid*100:.1f}%"
        defense_pct = f"{defense_count/total_valid*100:.1f}%"
        print(f"\n📊 进攻票占比：{attack_pct}（启动+强势+企稳复苏={attack_count}只）")
        print(f"📊 防守票占比：{defense_pct}（冲高回落+转跌+震荡+弱势下跌={defense_count}只）")

    # 仅当本次有API调用（即有新数据）或强制生成时，才生成/更新多日趋势报告
    if has_api_call or args.force_gen:
        print("\n" + "-"*60)
        if args.force_gen and not has_api_call:
            print("📊 强制生成模式，正在生成多日趋势分析报告...")
        else:
            print("📊 检测到新数据，正在生成多日趋势分析报告...")
        generate_multi_day_analysis(input_md_path=sector_input_path)
    else:
        print("\n⏭️ 本次无新数据（全部命中缓存），跳过多日报告生成")
        print("   提示：如需强制生成，请加 --force-gen 参数")

    # 汇总输出找不到代码的股票
    if not_found_stocks:
        print("\n" + "="*60)
        print(f"⚠️ 共有 {len(not_found_stocks)} 只股票未找到代码：")
        print("="*60)
        for name in not_found_stocks:
            print(f"  - {name}")

        # 写入文件方便后续处理
        not_found_path = os.path.join(BASE_REPORT_DIR, "not_found_stocks.txt")
        with open(not_found_path, 'w', encoding='utf-8') as f:
            f.write(f"# 未找到股票代码汇总\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"共 {len(not_found_stocks)} 只:\n\n")
            for name in not_found_stocks:
                f.write(f"- {name}\n")
        print(f"\n📝 已写入文件: {not_found_path}")
        print("  请检查输入文件中的股票名称是否正确，或手动添加到 stock_code_map.json")

    print("\n" + "="*60)
    print("分析完成")
    print("="*60)


def parse_daily_report(report_path: str) -> dict:
    """
    解析每日分析报告（本地文件），提取标的汇总表数据

    参数:
        report_path: 本地报告文件路径（主报告或明细报告均可）

    返回:
        {
            "date": "2026-04-29",
            "stocks": {
                "云南锗业": {"conclusion": "🟠 **【弱势回调】**", ...},
                ...
            }
        }
    """
    result = {"date": "", "stocks": {}, "sectors": {}}
    filename = os.path.basename(report_path)

    # 从文件名提取日期
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    if date_match:
        result["date"] = date_match.group(1)

    # 确定要解析的文件对：主报告（含题材表）+ 明细报告（含标的表）
    main_path = report_path
    detail_path = report_path.replace('.md', '_detail.md') if not filename.endswith('_detail.md') else None

    # 第1遍：从主报告解析题材分析表
    if os.path.isfile(main_path):
        try:
            with open(main_path, 'r', encoding='utf-8') as f:
                content = f.read()
            sector_start = content.find("| 题材名称 | 平均涨幅 |")
            if sector_start != -1:
                sector_end = content.find("### ", sector_start)
                if sector_end == -1:
                    sector_end = content.find("\n## ", sector_start)
                if sector_end == -1:
                    sector_end = len(content)
                sector_text = content[sector_start:sector_end]
                sec_lines = [l.strip() for l in sector_text.split('\n') if l.strip().startswith('|')]
                for sl in sec_lines[2:]:
                    sp = [p.strip() for p in sl.split('|') if p.strip()]
                    if len(sp) >= 4:
                        s_name = re.sub(r'\*+', '', sp[0]).strip()
                        try:
                            avg_ret = float(sp[1].replace('%', ''))
                            s_count = int(sp[2])
                            gt5_str = sp[3]
                            gt5 = int(gt5_str.split('/')[0]) if '/' in gt5_str else (int(gt5_str) if gt5_str.isdigit() else 0)
                            result["sectors"][s_name] = {
                                "avg_return": avg_ret,
                                "count": s_count,
                                "gt5_count": gt5,
                            }
                        except (ValueError, IndexError):
                            continue
        except Exception as e:
            print(f"解析题材表 {main_path} 失败: {e}")

    # 第2遍：从明细报告解析标的表
    files_to_try = [report_path]
    if detail_path and os.path.isfile(detail_path):
        files_to_try.append(detail_path)
    for try_path in files_to_try:
        if not os.path.isfile(try_path):
            continue
        try:
            with open(try_path, 'r', encoding='utf-8') as f:
                content = f.read()

            table_start = content.find("| 标的名称 |")
            if table_start == -1:
                continue

            table_end = content.find("**结论说明：**", table_start)
            if table_end == -1:
                table_end = len(content)

            table_text = content[table_start:table_end]
            lines = [l.strip() for l in table_text.split('\n') if l.strip().startswith('|')]

            for line in lines[2:]:
                parts = [p.strip() for p in line.split('|') if p.strip()]
                if len(parts) >= 8:
                    stock_name = parts[0].strip()
                    conclusion = parts[7]
                    window_data = parts[1:7]
                    last_5d = parts[6]

                    result["stocks"][stock_name] = {
                        "conclusion": conclusion,
                        "windows": window_data,
                        "last_5d_return": last_5d
                    }

            if result["stocks"]:
                return result  # 解析成功直接返回

        except Exception as e:
            print(f"解析报告 {try_path} 失败: {e}")
            continue

    return result


def analyze_rating_trend(rating_history: list) -> dict:
    """
    分析评级序列的趋势模式

    参数:
        rating_history: [(date, conclusion), (date, conclusion), ...]

    返回:
        趋势分析结果字典
    """
    if not rating_history or len(rating_history) < 1:
        return {}

    # 评级映射到分数（用于趋势判断）
    rating_scores = {
        "启动": 7,
        "强势": 6,
        "冲高回落": 5,
        "震荡": 4,
        "企稳复苏": 3,
        "转跌": 2,
        "弱势下跌": 1,
        "弱势回调": 1,  # 兼容旧报告
    }

    # 提取结论中的评级文字
    def extract_rating(conclusion):
        match = re.search(r'【(.+?)】', conclusion)
        if match:
            return match.group(1)
        return ""

    ratings = [extract_rating(r[1]) for r in rating_history]
    scores = [rating_scores.get(r, 0) for r in ratings]

    # 统计连续相同评级的次数
    consecutive_counts = []
    current_count = 1
    current_rating = ratings[0] if ratings else ""

    for i in range(1, len(ratings)):
        if ratings[i] == current_rating:
            current_count += 1
        else:
            consecutive_counts.append((current_rating, current_count))
            current_count = 1
            current_rating = ratings[i]
    consecutive_counts.append((current_rating, current_count))

    # 判断整体趋势方向
    trend_direction = "→"
    if len(scores) >= 3:
        first_half_avg = sum(scores[:len(scores)//2]) / max(len(scores)//2, 1)
        second_half_avg = sum(scores[len(scores)//2:]) / max(len(scores) - len(scores)//2, 1)

        if second_half_avg > first_half_avg + 0.5:
            trend_direction = "↗"  # 趋势向上
        elif second_half_avg < first_half_avg - 0.5:
            trend_direction = "↘"  # 趋势向下
        elif abs(first_half_avg - second_half_avg) <= 0.5:
            trend_direction = "→"  # 震荡

    # 最新状态
    latest_rating = ratings[-1] if ratings else ""
    latest_score = scores[-1] if scores else 0

    # 判断关键模式
    patterns = []

    # 获取最新的连续状态（最后一段）
    latest_consecutive = consecutive_counts[-1] if consecutive_counts else ("未知", 0)
    latest_consec_rating, latest_consec_count = latest_consecutive

    # 启动模式（最高优先级信号）
    if latest_rating == "启动":
        if latest_consec_count >= 2:
            patterns.append(f"连续{latest_consec_count}天启动")
        else:
            patterns.append("今日启动")

    # 连续强势天数
    if latest_consec_rating == "强势" and latest_consec_count >= 2:
        patterns.append(f"连续{latest_consec_count}天强势")
    elif latest_rating == "强势" and latest_consec_count == 1:
        patterns.append("今日转为强势")

    # 连续弱势天数（含转跌、弱势下跌、弱势回调）
    weak_ratings = ["弱势下跌", "弱势回调", "转跌"]
    if latest_consec_rating in weak_ratings and latest_consec_count >= 2:
        patterns.append(f"连续{latest_consec_count}天走弱")
    elif latest_rating in weak_ratings and latest_consec_count == 1:
        if latest_rating == "转跌":
            patterns.append("今日出现转跌信号")
        else:
            patterns.append("今日走弱")

    # 震荡持续
    if latest_consec_rating == "震荡" and latest_consec_count >= 2:
        patterns.append(f"连续{latest_consec_count}天震荡整理")

    # 冲高回落 / 从强势转为冲高回落
    if ("强势" in ratings[:-1] or "启动" in ratings[:-1]) and latest_rating == "冲高回落":
        patterns.append("从强势转为冲高回落，注意回调风险")

    # 转跌模式：由强直接变跌
    if latest_rating == "转跌":
        if not any("转跌" in p for p in patterns):
            patterns.append("由强转弱拐点，警惕加速下跌")

    # 企稳复苏模式
    if latest_rating == "企稳复苏":
        patterns.append("企稳复苏迹象，可关注")

    # 从弱转强（放宽条件，score>=6即启动或强势）
    if scores[0] <= 2 and latest_score >= 6:
        patterns.append("由弱转强，趋势向好")
    elif scores[0] <= 3 and latest_score >= 6:
        patterns.append("明显好转")
    elif len(scores) >= 2 and scores[0] < scores[-1] + 2:
        patterns.append("评级有所提升")

    # 由强转弱（score<=2即弱势下跌/转跌）
    if scores[0] >= 5 and latest_score <= 2:
        patterns.append("由强转弱，建议减仓")
    elif len(scores) >= 2 and scores[0] > scores[-1] + 2:
        patterns.append("评级下降，需关注")

    # 如果没有特殊模式，给出简单描述
    if not patterns:
        if trend_direction == "→":
            patterns.append("震荡整理中")
        elif trend_direction == "↗":
            patterns.append("趋势向好")
        elif trend_direction == "↘":
            patterns.append("趋势走弱")

    return {
        "ratings_sequence": "".join([
            {"启动":"🚀","强势":"🟢","冲高回落":"🟡","转跌":"🔴","震荡":"🟠","企稳复苏":"⭐","弱势下跌":"🔴","弱势回调":"🟠"} .get(r, "?")
            for r in ratings
        ]),
        "trend_direction": trend_direction,
        "latest_rating": latest_rating,
        "patterns": patterns,
        "consecutive_info": consecutive_counts[-3:] if len(consecutive_counts) > 3 else consecutive_counts,
        "total_days": len(rating_history),
        "first_date": rating_history[0][0],
        "last_date": rating_history[-1][0],
    }


def parse_sector_mapping(md_file_path: str) -> dict:
    """
    解析 interest_stock.md，建立 标的名称 → 板块列表 的映射

    参数:
        md_file_path: 股票列表 Markdown 文件路径

    返回:
        {
            "杭电股份": ["光纤"],
            "东山精密": ["CPO", "PCB"],
            ...
        }
    """
    mapping = {}
    current_sector = None

    try:
        with open(md_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # 板块标题行
                if line.startswith('#'):
                    current_sector = parse_topic_header(line)[0]  # 只用 topic_name 做分类
                    continue
                # 标的行：清理括号注释后作为 key
                stock_clean = clean_stock_name(line)
                if stock_clean and current_sector:
                    if stock_clean not in mapping:
                        mapping[stock_clean] = []
                    # 去重（同一板块内不会重复，但跨板块可能重复）
                    if current_sector not in mapping[stock_clean]:
                        mapping[stock_clean].append(current_sector)
    except Exception as e:
        print(f"解析板块映射失败: {e}")

    return mapping


def build_sector_time_series(cache: dict, input_md_path: str = None) -> list:
    """
    基于缓存数据，构建每个题材（板块）的每日平均涨幅时间序列

    参数:
        cache: 股票数据缓存字典 {stock_code: {dates, daily_changes, ...}}
        input_md_path: 股票列表文件路径（用于获取题材分组）

    返回:
        [
            {
                "sector_name": "液冷",
                "daily_avg_changes": [(date, avg), ...],  # 按日期排序
                "latest_avg": 2.35,                        # 最近一天平均涨幅
                "gt5_ratio": 0.6,                          # 最近一天涨超5%的标的占比
                "gt5_count": 3,                            # 涨超5%的标的数
                "total_count": 5,                          # 题材内有效标的数
                "return_5d": 12.5,                         # 近5日累计涨幅(复利)
                "data_days": 5,                            # 实际用于计算的天数
            },
            ...
        ]
    """
    # 解析题材映射
    sector_map = parse_sector_mapping(input_md_path) if input_md_path else {}

    # 加载名称→代码映射（用于将中文名称匹配到缓存中的代码）
    saved_code_map = load_stock_code_map()

    # 按题材分组股票代码和名称
    sector_stocks = {}  # {sector_name: [(stock_name, stock_code), ...]}
    code_to_name = {}   # 反向映射 {code: name}
    for stock_name, sectors in sector_map.items():
        found_code = None
        # 优先用 STOCK_CODE_MAP（持久化的名称→代码映射）
        if stock_name in STOCK_CODE_MAP:
            found_code = STOCK_CODE_MAP[stock_name]
        elif stock_name in saved_code_map:
            found_code = saved_code_map[stock_name]
        else:
            # 回退：在缓存keys中模糊搜索
            for code in cache:
                if stock_name in code or code in stock_name:
                    found_code = code
                    break

        if found_code:
            code_to_name[found_code] = stock_name
            for sector in sectors:
                if sector not in sector_stocks:
                    sector_stocks[sector] = []
                sector_stocks[sector].append((stock_name, found_code))

    if not sector_stocks:
        return []

    # 收集所有日期并去重排序
    all_dates_set = set()
    for stock_name_codes in sector_stocks.values():
        for _, code in stock_name_codes:
            if code in cache:
                all_dates_set.update(cache[code].get("dates", []))

    all_dates = sorted(all_dates_set)
    if not all_dates:
        return []

    results = []
    for sector_name, stock_name_codes in sector_stocks.items():
        # 构建该题材内每只股票的 {date: change} 映射
        stock_date_change = []  # [(stock_name, {date: change}), ...]

        valid_count = 0
        for stock_name, code in stock_name_codes:
            if code not in cache:
                continue
            data = cache[code]
            dates = data.get("dates", [])
            changes = data.get("daily_changes", [])

            if len(dates) != len(changes) or not dates:
                continue

            valid_count += 1
            stock_date_change.append((stock_name, dict(zip(dates, changes))))

        if valid_count == 0:
            continue

        # 对每个交易日，计算该题材的平均涨幅
        daily_avgs = []
        for date in all_dates:
            day_changes = []
            for _, sdc in stock_date_change:
                if date in sdc:
                    day_changes.append(sdc[date])

            if day_changes:
                avg = sum(day_changes) / len(day_changes)
                daily_avgs.append((date, avg))

        if not daily_avgs:
            continue

        # 计算最近一天的数据
        latest_date, latest_avg = daily_avgs[-1]

        # 计算最近一天涨超5%的标的数和占比
        gt5_count = 0
        total_latest = 0
        for _, sdc in stock_date_change:
            if latest_date in sdc:
                total_latest += 1
                if sdc[latest_date] >= 5:
                    gt5_count += 1

        gt5_ratio = gt5_count / total_latest if total_latest > 0 else 0

        # 计算近5日累计涨幅（复利）
        last_n = daily_avgs[-5:] if len(daily_avgs) >= 5 else daily_avgs
        cum = 1.0
        for _, avg in last_n:
            cum *= (1 + avg / 100)
        ret_5d = (cum - 1) * 100
        data_days = len(last_n)

        # 计算"近期日均涨幅"：取最近一天的往前历史（排除当天）
        historical_avgs = [avg for d, avg in daily_avgs[:-1]] if len(daily_avgs) > 1 else []
        recent_hist_avg = sum(historical_avgs) / len(historical_avgs) if historical_avgs else 0

        # 计算今日涨幅Top3标的
        today_changes = []
        for sn, sdc in stock_date_change:
            if latest_date in sdc:
                today_changes.append((sn, sdc[latest_date]))
        top_stocks_today = sorted(today_changes, key=lambda x: x[1], reverse=True)[:3]

        results.append({
            "sector_name": sector_name,
            "daily_avgs": daily_avgs,
            "latest_date": latest_date,
            "latest_avg": latest_avg,
            "gt5_count": gt5_count,
            "gt5_ratio": gt5_ratio,
            "total_count": valid_count,
            "total_latest": total_latest,
            "return_5d": ret_5d,
            "data_days": data_days,
            "recent_hist_avg": recent_hist_avg,
            "has_history": len(daily_avgs) > 1,
            "top_stocks_today": top_stocks_today,
        })

    return results


def judge_sector_status(sector_data: dict) -> str:
    """
    判断题材状态：7级分类（对标单日评级颗粒度）

    规则（优先级从高到低）：
    1. ⭐ 启动：当日均幅>3%，且>50%标的涨超5%，且(无历史或近期日均<1.5%) — 从低位爆发
    2. 🟢 主升：近5日涨幅>10%（或>5%且斜率为正） — 持续加速上涨
    3. 🟡 冲高回落：近5日涨幅>3%但日均序列斜率≤0 — 涨势减速见顶
    4. 🔴 转跌：近5日跌幅>-10%但曾达到过较高点后回落 — 从高位拐头向下
    5. 🟠 震荡：近5日累计在[-10%,+10%]区间，且穿越过0轴 — 区间整理
    6. ⭐ 企稳复苏：近期日均曾<0但最新为正，或近5日由负转正 — 底部回升
    7. 🔴 弱势下跌：其余走弱情况 — 持续下跌
    """
    ret_5d = sector_data.get("return_5d", 0)
    latest_avg = sector_data.get("latest_avg", 0)
    gt5_ratio = sector_data.get("gt5_ratio", 0)
    has_history = sector_data.get("has_history", True)
    recent_hist_avg = sector_data.get("recent_hist_avg", 0)
    data_days = sector_data.get("data_days", 0)

    # 计算日均序列的线性斜率（用于判断趋势方向）
    daily_avgs = sector_data.get("daily_avgs", [])
    slope_val = 0.0
    if len(daily_avgs) >= 3:
        vals = [v for _, v in daily_avgs]
        n = len(vals)
        x = list(range(n))
        sum_x, sum_y = sum(x), sum(vals)
        sum_xy = sum(x[i] * vals[i] for i in range(n))
        sum_x2 = sum(xi * xi for xi in x)
        denom = n * sum_x2 - sum_x * sum_x
        if denom != 0:
            slope_val = (n * sum_xy - sum_x * sum_y) / denom

    # 计算是否穿越过0轴
    if len(daily_avgs) >= 2:
        vals = [v for _, v in daily_avgs]
        cross_zero = any(v > 0 for v in vals) and any(v < 0 for v in vals)
    else:
        cross_zero = False

    # 计算历史最高点（用于判断"从高位回落"）
    if len(daily_avgs) >= 3:
        vals = [v for _, v in daily_avgs]
        hist_max = max(vals[:-1])  # 排除今天
    else:
        hist_max = latest_avg

    # ---- 1. ⭐ 启动：当天爆发，从低位起跳 ----
    is_startup = (
        latest_avg > 3 and
        gt5_ratio > 0.5 and
        (not has_history or recent_hist_avg < 1.5)
    )
    if is_startup:
        return "⭐ 启动"

    # ---- 2. 🟢 主升：持续加速上涨 ----
    if data_days >= 3 and (ret_5d > 10 or (ret_5d > 5 and slope_val > 0)):
        return "🟢 主升"

    # ---- 3. 🟡 冲高回落：仍在涨但减速 ----
    if data_days >= 3 and ret_5d > 3 and slope_val <= 0:
        return "🟡 冲高回落"

    # ---- 4. 🔴 转跌：从高位明显回撤 ----
    if data_days >= 3 and hist_max > 3 and latest_avg < 0 and (hist_max - latest_avg) > 4:
        return "🔴 转跌"

    # ---- 5. 🟠 震荡：穿越0轴，幅度有限 ----
    if data_days >= 2 and cross_zero and -10 <= ret_5d <= 10:
        return "🟠 震荡"

    # ---- 6. ⭐ 企稳复苏：从底部回升 ----
    if data_days >= 2 and (
        (recent_hist_avg < 0 and latest_avg > 0) or
        (ret_5d > 0 and recent_hist_avg < -0.5)
    ):
        return "⭐ 企稳复苏"

    # ---- 7. 🔴 弱势下跌：持续走弱 ----
    if data_days >= 2 and ret_5d < -1:
        return "🔴 弱势下跌"

    # 兜底：根据最近一天表现粗判
    if latest_avg > 1:
        return "🟢 主升"
    elif latest_avg < -1:
        return "🔴 弱势下跌"
    else:
        return "🟠 震荡"


def generate_multi_day_analysis(days_to_analyze: int = 10, input_md_path: str = None):
    """
    基于最近N天的每日报告，生成多日趋势分析报告

    参数:
        days_to_analyze: 分析的天数（默认10天）
        input_md_path: 用于获取板块信息的股票列表文件路径

    注意:
        是否调用的判断由调用方(main)负责，本函数只负责生成。
        调用方应根据"本次是否有API拉取新数据"来决定是否触发。
    """

    # ========== 第1步：从索引文件读取所有每日报告（避免 os.listdir 触发 iCloud 权限问题）==========
    reports = read_report_index()

    if not reports:
        print("⚠️ 未找到每日分析报告，跳过多日分析")
        return

    # 按修改时间排序取最近N份
    reports.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    recent_reports = reports[:days_to_analyze]

    # 解析每份报告
    parsed_reports = []
    for report_path in recent_reports:
        data = parse_daily_report(report_path)
        if data.get("date") and data.get("stocks"):
            parsed_reports.append(data)

    if not parsed_reports:
        print("⚠️ 无法解析任何报告")
        return

    # 按日期排序（旧的在前）
    parsed_reports.sort(key=lambda x: x["date"])

    # ========== 第2.5步：构建题材时间序列分析 ==========
    # d1~d4 从历史每日报告的题材表中获取（股票池变化不影响历史数据）
    # d5 从 top_list 缓存计算
    cache_data = load_cache()
    sector_time_series = build_sector_time_series(cache_data, input_md_path) if cache_data else []

    # 将历史题材表数据合并到 sector_time_series
    hist_reports = parsed_reports[:-1][-4:]  # 最近4份历史报告（排除今日）
    hist_sectors = {}  # {sector_name: {date_index: avg_return}}
    for i, report_data in enumerate(hist_reports):
        date_key = f"d{i+1}" if i < 4 else f"d{i+1}"
        for s_name, s_info in report_data.get("sectors", {}).items():
            if s_name not in hist_sectors:
                hist_sectors[s_name] = {}
            hist_sectors[s_name][date_key] = s_info.get("avg_return", 0)

    for st in sector_time_series:
        s_name = st["sector_name"]
        st["hist_daily"] = hist_sectors.get(s_name, {})
        st["status"] = judge_sector_status(st)

    sector_time_series.sort(key=lambda x: x.get("return_5d", -999), reverse=True)

    # ========== 第2步：汇总所有标的的评级历史（含去重）==========
    all_stocks = {}  # { stock_name: [(date, conclusion), ...] }
    for report_data in parsed_reports:
        date = report_data["date"]
        seen_in_this_report = set()
        for stock_name, stock_data in report_data["stocks"].items():
            # 同一报告内去重（如东山精密出现两次）
            if stock_name in seen_in_this_report:
                continue
            seen_in_this_report.add(stock_name)

            if stock_name not in all_stocks:
                all_stocks[stock_name] = []
            conclusion = stock_data.get("conclusion", "") if isinstance(stock_data, dict) else str(stock_data)
            all_stocks[stock_name].append((date, conclusion))

    print(f"  📁 找到 {len(reports)} 份每日报告({parsed_reports[0]['date']}~{parsed_reports[-1]['date']})，"
          f"涉及 {len(all_stocks)} 只标的(去重后)")

    # ========== 第3步：分析每个标的的趋势 ==========
    analysis_results = []
    for stock_name, history in sorted(all_stocks.items()):
        trend = analyze_rating_trend(history)
        if trend:
            trend["stock_name"] = stock_name
            analysis_results.append(trend)

    # ========== 第4步：解析板块信息 ==========
    sector_map = parse_sector_mapping(input_md_path) if input_md_path else {}

    # 为每个标的附加板块
    for r in analysis_results:
        r["sectors"] = sector_map.get(r["stock_name"], ["未知"])

    # ========== 第5步：构建板块统计 ==========
    # 统计口径：按最新一天有数据的评级
    sector_stats = {}  # { sector: { "total", "强势", "冲高回落", ... } }

    for r in analysis_results:
        latest_rating = r.get("latest_rating", "")
        for sector in r.get("sectors", ["未知"]):
            if sector not in sector_stats:
                sector_stats[sector] = {
                    "total": set(),       # 用 set 去重标的
                    "启动": 0,
                    "强势": 0,
                    "冲高回落": 0,
                    "转跌": 0,
                    "震荡": 0,
                    "企稳复苏": 0,
                    "弱势下跌": 0,
                    "缺失": 0,
                }
            sector_stats[sector]["total"].add(r["stock_name"])

            if latest_rating == "":
                sector_stats[sector]["缺失"] += 1
            elif latest_rating in sector_stats[sector]:
                sector_stats[sector][latest_rating] += 1

    # 将 set 转为 count
    for s in sector_stats:
        sector_stats[s]["total_count"] = len(sector_stats[s].pop("total"))
        total = sector_stats[s]["total_count"]
        strong = sector_stats[s]["强势"]
        sector_stats[s]["strong_pct"] = f"{strong/total*100:.0f}%" if total > 0 else "-"

    # 按强势占比从高到低排序
    sorted_sectors = sorted(
        sector_stats.items(),
        key=lambda x: (int(x[1]["strong_pct"].replace("%","")) if x[1]["strong_pct"] != "-" else -1),
        reverse=True
    )

    # ========== 第6步：分类标的（7类评级适配）==========
    startup_stocks = []       # ⭐ 启动（今日启动或连续启动）
    strong_continuous = []    # 🟢 连续≥2天强势
    strong_today = []          # 🟢 今日首次强势
    weak_today = []            # 🔴 今日转跌（由强→弱拐点）
    risk_stocks = []           # 🔴 持续走弱：冲高回落 / 转跌(非当日) / 弱势下跌
    oscillation_stocks = []    # 🟠 震荡整理
    recovery_focus = []        # ⭐ 企稳复苏 或 由弱转强

    for r in analysis_results:
        patterns_str = str(r.get('patterns', []))
        latest = r.get('latest_rating', '')

        if latest == '启动':
            startup_stocks.append(r)
        elif latest == '强势':
            if '连续' in patterns_str:
                strong_continuous.append(r)
            else:
                strong_today.append(r)
        elif latest == '转跌':
            if '转跌信号' in patterns_str or '转弱' in patterns_str:
                weak_today.append(r)  # 今天刚出现转跌信号
            else:
                risk_stocks.append(r)  # 持续转跌状态
        elif latest in ('冲高回落', '弱势下跌'):
            if '由强转弱' in patterns_str or '转弱' in patterns_str:
                weak_today.append(r)
            else:
                risk_stocks.append(r)
        elif latest == '震荡':
            oscillation_stocks.append(r)
        elif latest == '企稳复苏' or '由弱转强' in patterns_str or '好转' in patterns_str:
            recovery_focus.append(r)

    # ========== 第7步：生成报告（拆分为汇总文件 + 明细文件） ==========
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_range = f"{parsed_reports[0]['date']} ~ {parsed_reports[-1]['date']}"
    latest_date = parsed_reports[-1]["date"]

    # --- 公共头部 ---
    header = f"""# 多日趋势汇总报告

**生成时间：** {now}
**分析周期：** 最近 {len(parsed_reports)} 天 ({date_range})
**涉及标的：** {len(analysis_results)} 只

---

"""

    # ========== 汇总内容（整体概览 + 题材情况 + 分类表格 + 板块强弱概览）==========
    main_content = header
    main_content += "## 一、整体概览\n\n"
    total_stocks_count = len(startup_stocks) + len(strong_continuous) + len(strong_today) + len(weak_today) + len(risk_stocks) + len(oscillation_stocks) + len(recovery_focus)
    def _pct(cnt):
        return f"{cnt/total_stocks_count*100:.0f}%" if total_stocks_count > 0 else "0%"
    main_content += "| 分类 | 数量 | 占比 | 说明 |\n|------|------|------|------|\n"
    main_content += f"| 🚀 启动 | {len(startup_stocks)} | {_pct(len(startup_stocks))} | 连续强势且加速，重点关注 |\n"
    main_content += f"| 🟢 连续强势 | {len(strong_continuous)} | {_pct(len(strong_continuous))} | 连续2天以上保持强势 |\n"
    main_content += f"| 🟢 今日转强 | {len(strong_today)} | {_pct(len(strong_today))} | 今天刚转为强势 |\n"
    main_content += f"| 🔴 今日转跌 | {len(weak_today)} | {_pct(len(weak_today))} | 今天出现由强转弱信号 |\n"
    main_content += f"| 🔴 注意风险 | {len(risk_stocks)} | {_pct(len(risk_stocks))} | 持续走弱/下跌 |\n"
    main_content += f"| 🟠 震荡整理 | {len(oscillation_stocks)} | {_pct(len(oscillation_stocks))} | 穿越0轴整理中 |\n"
    main_content += f"| ⭐ 重点关注 | {len(recovery_focus)} | {_pct(len(recovery_focus))} | 企稳复苏/由弱转强 |\n\n"
    # 进攻/防守占比
    attack_count = len(startup_stocks) + len(strong_continuous) + len(strong_today) + len(recovery_focus)
    defense_count = len(weak_today) + len(risk_stocks) + len(oscillation_stocks)
    attack_pct = f"{attack_count/total_stocks_count*100:.1f}%" if total_stocks_count > 0 else "0%"
    defense_pct = f"{defense_count/total_stocks_count*100:.1f}%" if total_stocks_count > 0 else "0%"
    main_content += f"> 📊 **进攻票占比：** {attack_pct}（启动+连续强势+今日转强+重点关注 = {attack_count} 只）| **防守票占比：** {defense_pct}（今日转跌+注意风险+震荡整理 = {defense_count} 只）\n\n"

    # 题材情况分析表（7级细分 + 丰富维度）
    if sector_time_series:
        main_content += "### 题材情况（按近5日涨幅排序）\n\n"
        main_content += "| 题材名称 | 状态 | d1 | d2 | d3 | d4 | d5 | 近5日涨幅 | 涨超5%占比 | 明星标的 |\n"
        main_content += "|:--------|:----:|:--:|:--:|:--:|:--:|:--:|:--------:|:---------:|:--------|\n"

        for st in sector_time_series:
            ret = st["return_5d"]
            days = st["data_days"]
            data_note = f"(近{days}日)" if days < 5 else ""
            status = st["status"]
            gt5_ratio = st.get("gt5_ratio", 0)

            # d1~d4 从历史每日报告题材表获取（排除股票池变化的影响）
            hist_daily = st.get("hist_daily", {})
            hist_keys = ["d1", "d2", "d3", "d4"]
            d_vals = []
            for hk in hist_keys:
                if hk in hist_daily:
                    d_vals.append(f"{hist_daily[hk]:+.2f}%")
                else:
                    d_vals.append("-")
            # d5 从缓存（top_list 股票）计算
            daily_avgs = st.get("daily_avgs", [])
            if daily_avgs:
                d5_avg = daily_avgs[-1][1] if len(daily_avgs[-1]) > 1 else daily_avgs[-1]
                d_vals.append(f"{d5_avg:+.2f}%" if isinstance(d5_avg, (int, float)) else str(d5_avg))
            else:
                d_vals.append("-")
            while len(d_vals) < 5:
                d_vals.append("-")

            # 所有状态都显示明星标的
            top3 = st.get("top_stocks_today", [])
            stars = "；".join([f"{n}({c:+.2f}%)" for n, c in top3]) if top3 else "-"

            # 状态高亮：启动/主升加粗，转跌/弱势标红
            status_display = f"**{status}**" if "🟢" in status or "⭐" in status else status
            if "🔴" in status and ("转跌" in status or "弱势" in status or "退潮" in status):
                status_display = f"<span style='color:red'>{status}</span>"

            main_content += (
                f"| **{st['sector_name']}** "
                f"| {status_display} "
                f"| {d_vals[0]} | {d_vals[1]} | {d_vals[2]} | {d_vals[3]} | {d_vals[4]} "
                f"| {ret:+.2f}% {data_note} "
                f"| {gt5_ratio*100:.0f}% "
                f"| {stars} |\n"
            )

        main_content += "\n> **状态说明（7级）：**\n"
        main_content += "> - **⭐ 启动**：当天爆发（均幅>3%，超半数涨>5%），从低位起跳\n"
        main_content += "> - **🟢 主升**：持续加速上涨（5日>10% 或 5日>5%且趋势向上）\n"
        main_content += "> - **🟡 冲高回落**：仍在涨但增速放缓，涨势见顶\n"
        main_content += "> - **🔴 转跌**：从高位拐头向下，明显回撤\n"
        main_content += "> - **🟠 震荡**：穿越0轴区间整理，幅度有限\n"
        main_content += "> - **⭐ 企稳复苏**：从底部回升，由负转正\n"
        main_content += "> - **🔴 弱势下跌**：持续走弱\n"

    # 板块强弱概览（放在持续强势前面）
    main_content += "---\n\n"
    main_content += f"## 二、板块强弱概览（截至 {latest_date}）\n\n"
    main_content += "> 数据来源：输入文件中的题材划分，统计基于最新一天的评级\n\n"

    if sorted_sectors:
        main_content += "| 排名 | 板块 | 标的数 | 🚀启动 | 强势🟢 | 冲高回落🟡 | 转跌🔴 | 震荡🟠 | 企稳⭐ | 下跌🔴 | 强势占比 |\n"
        main_content += "|:----:|------|:-----:|:-----:|:-----:|:---------:|:----:|:-----:|:----:|:----:|:-------:|\n"
        for i, (sector, stats) in enumerate(sorted_sectors, 1):
            main_content += (
                f"| {i} | {sector} | {stats['total_count']} "
                f"| {stats.get('启动',0)} | {stats['强势']} | {stats['冲高回落']} "
                f"| {stats.get('转跌',0)} | {stats.get('震荡',0)} "
                f"| {stats['企稳复苏']} | {stats['弱势下跌']} "
                f"| **{stats['strong_pct']}** |\n"
            )
    else:
        main_content += "*无法加载板块信息（未提供输入文件）*\n"

    main_content += "\n---\n\n"

    # 分类表格（启动 → 持续强势 → 今日转弱 → 震荡整理 → 注意风险 → 重点关注）
    main_content += """## 三、🚀 启动标的（重点关注）

"""
    startup_sorted = sorted(startup_stocks, key=lambda x: x.get('sectors', ['未知'])[0])
    if startup_sorted:
        main_content += "| 标的 | 板块 | 评级序列 | 趋势 | 关键信号 |\n"
        main_content += "|------|------|---------|------|----------|\n"
        for s in startup_sorted:
            signal = "；".join(s.get('patterns', ['']))[:50]
            sector = "、".join(s.get('sectors', ['未知']))
            main_content += f"| **{s['stock_name']}** | {sector} | {s['ratings_sequence']} | {s['trend_direction']} | {signal} |\n"
    else:
        main_content += "*暂无启动标的*\n"

    main_content += "\n---\n\n## 四、持续强势（可继续关注）\n\n"
    strong_all = sorted(strong_continuous + strong_today,
                        key=lambda x: (x.get('sectors', ['未知'])[0],
                                       -sum(v[1] for v in x.get('consecutive_info', [('', 0)]))))

    if strong_all:
        main_content += "| 标的 | 板块 | 评级序列 | 趋势 | 关键信号 |\n"
        main_content += "|------|------|---------|------|----------|\n"
        for s in strong_all[:20]:
            signal = "；".join(s.get('patterns', ['']))[:50]
            sector = "、".join(s.get('sectors', ['未知']))
            main_content += f"| **{s['stock_name']}** | {sector} | {s['ratings_sequence']} | {s['trend_direction']} | {signal} |\n"
    else:
        main_content += "*暂无强势标的*\n"

    main_content += "\n---\n\n## 四、今日转弱（由强转弱，需警惕）\n\n"

    weak_today_sorted = sorted(weak_today, key=lambda x: x.get('sectors', ['未知'])[0])

    if weak_today_sorted:
        main_content += "| 标的 | 板块 | 评级序列 | 最新状态 | 关键信号 |\n"
        main_content += "|------|------|---------|----------|----------|\n"
        for s in weak_today_sorted:
            signal = "；".join(s.get('patterns', ['']))[:45]
            sector = "、".join(s.get('sectors', ['未知']))
            main_content += f"| {s['stock_name']} | {sector} | {s['ratings_sequence']} | 【{s['latest_rating']}】 | {signal} |\n"
    else:
        main_content += "*暂无转弱标的*\n"

    main_content += "\n---\n\n## 五、🟠 震荡整理\n\n"

    osc_sorted = sorted(oscillation_stocks, key=lambda x: x.get('sectors', ['未知'])[0])
    if osc_sorted:
        main_content += "| 标的 | 板块 | 评级序列 | 趋势 | 关键信号 |\n"
        main_content += "|------|------|---------|------|----------|\n"
        for s in osc_sorted:
            signal = "；".join(s.get('patterns', ['']))[:50]
            sector = "、".join(s.get('sectors', ['未知']))
            main_content += f"| {s['stock_name']} | {sector} | {s['ratings_sequence']} | {s['trend_direction']} | {signal} |\n"
    else:
        main_content += "*暂无震荡整理标的*\n"

    main_content += "\n---\n\n## 六、注意风险（持续走弱）\n\n"

    risk_stocks_sorted = sorted(risk_stocks, key=lambda x: x.get('sectors', ['未知'])[0])

    if risk_stocks_sorted:
        main_content += "| 标的 | 板块 | 评级序列 | 最新状态 | 关键信号 |\n"
        main_content += "|------|------|---------|----------|----------|\n"
        for s in risk_stocks_sorted[:20]:
            signal = "；".join(s.get('patterns', ['无明显变化']))[:45]
            sector = "、".join(s.get('sectors', ['未知']))
            main_content += f"| {s['stock_name']} | {sector} | {s['ratings_sequence']} | 【{s['latest_rating']}】 | {signal} |\n"
    else:
        main_content += "*暂无风险标的*\n"

    main_content += "\n---\n\n## 七、⭐ 重点关注（拐点机会）\n\n"

    if recovery_focus:
        main_content += "| 标的 | 评级序列 | 变化说明 |\n"
        main_content += "|------|---------|----------|\n"
        for s in recovery_focus:
            detail = "；".join(s.get('patterns', []))[:50]
            main_content += f"| **{s['stock_name']}** | {s['ratings_sequence']} | {detail} |\n"
    else:
        main_content += "*暂无拐点信号*\n"

    main_content += "\n---\n\n*报告由股票趋势分析工具自动生成（基于每日报告汇总）*\n"

    # ========== 明细内容（全部标的详情 + 图例）==========
    detail_content = header
    detail_content += "## 全部标的详情\n\n"
    detail_content += "| 标的 | " + " | ".join([p["date"] for p in parsed_reports]) + " | 趋势 | 最新状态 |\n"
    detail_content += "|------|" + "|".join([":------:" for _ in parsed_reports]) + "|:----:|:--------:|\n"

    # emoji 映射
    rating_to_emoji = {"启动":"🚀","强势":"🟢","冲高回落":"🟡","转跌":"🔴","震荡":"🟠","企稳复苏":"⭐","弱势下跌":"🔴","弱势回调":"🟠"}

    # 按最新评级分组排序输出
    def sort_key(r):
        rating_order = {"启动":0, "强势":1, "冲高回落":2, "震荡":3, "转跌":4, "企稳复苏":5, "弱势下跌":6, "弱势回调":6}
        return rating_order.get(r.get('latest_rating',''), 99)

    for r in sorted(analysis_results, key=sort_key):
        name = r['stock_name']
        # 构建每天的 emoji 列
        daily_cells = []
        for pd in parsed_reports:
            found = False
            for d, c in all_stocks.get(name, []):
                if d == pd["date"]:
                    rat = re.search(r'【(.+?)】', c)
                    emoji = rating_to_emoji.get(rat.group(1), "?") if rat else "-"
                    daily_cells.append(emoji)
                    found = True
                    break
            if not found:
                daily_cells.append("-")
        trend = r.get('trend_direction', '-')
        latest = r.get('latest_rating', '?')
        detail_content += f"| {name} | {' | '.join(daily_cells)} | {trend} | {latest} |\n"

    # 图例
    detail_content += f"\n---\n\n### 图例\n\n"
    detail_content += "| 符号 | 含义 | 判断条件 |\n|------|------|----------|\n"
    detail_content += "| 🟢 | 强势 | 涨且加速 |\n| 🟡 | 冲高回落 | 涨势减弱 |\n| 🟠 | 弱势回调 | 涨幅不足且走弱 |\n| ⭐ | 企稳复苏 | 跌幅收窄趋稳 |\n| 🔴 | 弱势下跌 | 跌且加速 |\n"
    detail_content += "| ↗ 趋势向好 | → 震荡整理 | ↘ 趋势走弱 |\n"
    detail_content += "| - | 当日该标的无数据 | 可能当天未运行或输入不同 |\n"

    detail_content += "\n---\n\n*报告由股票趋势分析工具自动生成（基于每日报告汇总）*\n"

    # 写入两个位置：iCloud + 本地
    output_path = os.path.join(MULTI_DAY_REPORT_DIR, f'multi_day_trend_{latest_date}.md')
    detail_path = output_path.replace('.md', '_detail.md')
    icloud_ok = False
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(main_content)
        with open(detail_path, 'w', encoding='utf-8') as f:
            f.write(detail_content)
        icloud_ok = True
        print(f"\n✅ 多日趋势汇总报告已生成：{output_path}")
        print(f"✅ 多日趋势明细报告已生成：{detail_path}")
    except PermissionError as e:
        print(f"\n⚠️ 无法写入 iCloud 报告: {e}")

    # 始终写入本地副本
    try:
        local_output = os.path.join(LOCAL_MULTI_DAY_REPORT_DIR, f'multi_day_trend_{latest_date}.md')
        local_detail = local_output.replace('.md', '_detail.md')
        os.makedirs(LOCAL_MULTI_DAY_REPORT_DIR, exist_ok=True)
        with open(local_output, 'w', encoding='utf-8') as f:
            f.write(main_content)
        with open(local_detail, 'w', encoding='utf-8') as f:
            f.write(detail_content)
        print(f"✅ 本地副本已生成：{local_output}")
    except Exception as e:
        print(f"⚠️ 写入本地副本失败: {e}")

    if icloud_ok:
        append_report_index(os.path.basename(output_path))
    print(f"   - 🚀启动：{len(startup_stocks)} 只 | 🟢连续强势：{len(strong_continuous)} 只 | 🟢今日转强：{len(strong_today)} 只")
    print(f"   - 🔴今日转跌：{len(weak_today)} 只 | 🔴持续风险：{len(risk_stocks)} 只 | 🟠震荡整理：{len(oscillation_stocks)} 只 | ⭐重点关注：{len(recovery_focus)} 只")
    if sorted_sectors:
        top_sector = sorted_sectors[0]
        print(f"   - 最强板块：{top_sector[0]}（强势占{top_sector[1]['strong_pct']}）")


if __name__ == "__main__":
    main()
