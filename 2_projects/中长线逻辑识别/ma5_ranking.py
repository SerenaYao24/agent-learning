#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场 5 日均线角度排行 + 多日排名变化追踪

用法:
    python ma5_ranking.py              # 计算今日排行，显示 Top 30
    python ma5_ranking.py --top 50     # 显示 Top 50
    python ma5_ranking.py --compare    # 对比今日与昨日排名变化
    python ma5_ranking.py --trend 5    # 近 5 日趋势分析（哪些排名在上升/下降）

数据保存: .ma5_ranking/ranking_YYYY-MM-DD.csv
"""

import akshare as ak
import pandas as pd
import numpy as np
import re
import time
import os
import sys
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
RANKING_DIR = os.path.join(DATA_DIR, ".ma5_ranking")
os.makedirs(RANKING_DIR, exist_ok=True)

# 每个 API 调用的超时（秒）
FETCH_TIMEOUT = 8


def calc_ma5_angle(closes):
    """
    东方财富同款算法：单日 MA5 变化率转角度
    angle = ATAN((MA5今 / MA5昨 - 1) × 100) × 180 / π
    与 xuangu.eastmoney.com 数据完全一致（11只验证股偏差<0.05°）
    """
    if len(closes) < 10:
        return None, None
    ma = pd.Series(closes).rolling(5).mean().dropna()
    if len(ma) < 5:
        return None, None
    y = ma.tail(5).values
    if y[-2] <= 0:
        return None, None
    daily_chg = (y[-1] / y[-2] - 1) * 100  # MA5单日变化率%
    angle = round(np.arctan(daily_chg) * 180 / np.pi, 2)
    return angle, round(float(ma.iloc[-1]), 2)


def fetch_one(code, name):
    """获取单只股票的MA5角度"""
    try:
        df = ak.stock_zh_a_hist_tx(symbol=code, adjust="qfq")
        if df.empty:
            return None
        closes = df['close'].tolist()
        angle, ma5_val = calc_ma5_angle(closes)
        if angle is None:
            return None
        return {
            'name': name, 'code': code, 'angle': angle,
            'price': round(closes[-1], 2), 'ma5': ma5_val,
            'chg': round((closes[-1]/closes[-2]-1)*100, 2) if len(closes) >= 2 else 0,
        }
    except Exception:
        return None


def build_code_map():
    """获取全A股代码映射（排除ST/退市/北交所）"""
    print("[1/3] 获取A股列表...", flush=True)
    df = ak.stock_info_a_code_name()
    df = df[~df['name'].str.contains('ST|退', na=False)]
    df = df[~df['code'].str.startswith(('8', '9'))]
    code_map = {}
    for _, r in df.iterrows():
        c = r['code']
        n = r['name'].replace(' ', '')
        # 去除科创板后缀（-U/-W），保持与 interest_stock.md 命名一致
        n = re.sub(r'-[UW]$', '', n)
        fc = f"sh{c}" if c.startswith('6') else f"sz{c}" if c.startswith(('0', '3')) else None
        if fc:
            code_map[n] = fc
    print(f"  共 {len(code_map)} 只标的", flush=True)
    return code_map


def run_ranking(code_map, date_str):
    """并行计算全市场排名"""
    total = len(code_map)
    workers = 16
    print(f"[2/3] 并行计算 ({workers}线程, 共{total}只)...", flush=True)

    results = []
    done = errors = 0
    t0 = time.time()
    items = list(code_map.items())

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, code, name): name
                   for name, code in items}
        for f in as_completed(futures):
            done += 1
            try:
                r = f.result(timeout=FETCH_TIMEOUT)
                if r:
                    results.append(r)
            except FuturesTimeout:
                errors += 1
            except Exception:
                errors += 1

            if done % 200 == 0 or done == total:
                t = time.time() - t0
                rate = done / t if t > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(f"  {done}/{total} ({done*100//total}%) "
                      f"有效:{len(results)} 错误:{errors} "
                      f"{rate:.0f}只/s 剩余~{eta:.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"  完成! {len(results)}有效/{errors}错误, 耗时{elapsed:.0f}s", flush=True)

    # 排序
    results.sort(key=lambda x: x['angle'], reverse=True)

    # 保存快照
    df_out = pd.DataFrame(results)
    df_out.insert(0, '排名', range(1, len(df_out) + 1))
    path = os.path.join(RANKING_DIR, f"ranking_{date_str}.csv")
    df_out.to_csv(path, index=False, encoding='utf-8-sig')
    print(f"[3/3] 快照已保存: {path}", flush=True)

    return df_out


def load_ranking(date_str):
    """加载历史排名"""
    path = os.path.join(RANKING_DIR, f"ranking_{date_str}.csv")
    if os.path.exists(path):
        return pd.read_csv(path)
    return None


def list_snapshots():
    """列出所有快照日期"""
    files = sorted(os.listdir(RANKING_DIR))
    return [f.replace('ranking_', '').replace('.csv', '') for f in files
            if f.startswith('ranking_') and f.endswith('.csv')]


def print_top(df, n=30):
    """打印 Top N"""
    print(f"\n{'排名':<6}{'名称':<12}{'角度(°)':<10}{'最新价':<10}{'MA5':<10}{'涨跌%':<8}")
    print("-" * 60)
    for _, r in df.head(n).iterrows():
        print(f"{r['排名']:<6}{r['name']:<12}{r['angle']:<10.2f}"
              f"{r['price']:<10.2f}{r['ma5']:<10.2f}{r['chg']:<8}")


def compare_rankings(df_today, prev_date, top_n=100):
    """对比两日 Top N 排名变化"""
    df_prev = load_ranking(prev_date)
    if df_prev is None:
        print(f"无 {prev_date} 快照")
        return

    prev_ranks = dict(zip(df_prev['name'], df_prev['排名']))
    today_ranks = dict(zip(df_today['name'], df_today['排名']))

    changes = []
    for name, tr in today_ranks.items():
        pr = prev_ranks.get(name)
        if pr is not None:
            changes.append({
                'name': name, 'today': tr, 'prev': pr,
                'delta': pr - tr,  # >0 = 排名上升
                'angle': df_today[df_today['name'] == name]['angle'].values[0],
            })

    changes.sort(key=lambda x: x['delta'], reverse=True)

    print(f"\n📈 排名上升 Top 20 (对比 {prev_date}):")
    for c in changes[:20]:
        if c['delta'] > 0:
            print(f"  ↑{c['delta']:>5}  {c['name']:<10}  "
                  f"#{c['prev']}→#{c['today']}  ({c['angle']}°)")

    changes.sort(key=lambda x: x['delta'])
    print(f"\n📉 排名下降 Top 20:")
    for c in changes[:20]:
        if c['delta'] < 0:
            print(f"  ↓{abs(c['delta']):>5}  {c['name']:<10}  "
                  f"#{c['prev']}→#{c['today']}  ({c['angle']}°)")

    # 新进 / 跌出
    today_set = set(today_ranks.keys())
    prev_set = set(prev_ranks.keys())
    new_in = {n for n in today_set - prev_set
              if today_ranks[n] <= top_n}
    dropped = {n for n in prev_set - today_set
               if prev_ranks[n] <= top_n}
    if new_in:
        print(f"\n🆕 新进 Top {top_n}: {len(new_in)} 只")
        for n in sorted(new_in, key=lambda x: today_ranks[x]):
            print(f"  #{today_ranks[n]} {n}")
    if dropped:
        print(f"\n🔻 跌出 Top {top_n}: {len(dropped)} 只")
        for n in sorted(dropped, key=lambda x: prev_ranks[x]):
            print(f"  #{prev_ranks[n]} {n}")


def trend_analysis(lookback=5):
    """多日趋势：哪只股票排名在持续上升/下降"""
    dates = list_snapshots()
    if len(dates) < lookback:
        print(f"历史数据不足 ({len(dates)}/{lookback} 天)")
        return

    recent = dates[-lookback:]
    print(f"分析近 {lookback} 天: {recent}")

    # {name: [rank_day1, rank_day2, ...]}
    stock_ranks = {}
    for d in recent:
        df = load_ranking(d)
        if df is None:
            continue
        for _, r in df.iterrows():
            name = r['name']
            if name not in stock_ranks:
                stock_ranks[name] = []
            stock_ranks[name].append(int(r['排名']))

    up, down = [], []
    for name, ranks in stock_ranks.items():
        if len(ranks) < lookback:
            continue
        # 用最后2天的排名变化判断趋势
        delta = ranks[0] - ranks[-1]  # >0 = 排名上升
        if abs(delta) >= 5 and ranks[-1] <= 200:
            if delta > 0:
                up.append((name, delta, ranks))
            else:
                down.append((name, delta, ranks))

    up.sort(key=lambda x: x[1], reverse=True)
    down.sort(key=lambda x: x[1])

    if up:
        print(f"\n📈 排名持续上升 ({lookback}日):")
        for name, delta, ranks in up[:15]:
            print(f"  ↑{delta:>4}  {name:<10}  {ranks[0]}→{ranks[-1]}")
    if down:
        print(f"\n📉 排名持续下降 ({lookback}日):")
        for name, delta, ranks in down[:15]:
            print(f"  ↓{abs(delta):>4}  {name:<10}  {ranks[0]}→{ranks[-1]}")


def main():
    import argparse
    p = argparse.ArgumentParser(description='全市场5日均线角度排行')
    p.add_argument('--top', type=int, default=30, help='显示 Top N')
    p.add_argument('--compare', action='store_true', help='对比昨日排名')
    p.add_argument('--trend', type=int, default=0, help='多日趋势分析(天数)')
    p.add_argument('--show', type=str, default='', help='查看指定日期快照 (YYYY-MM-DD)')
    p.add_argument('--list', action='store_true', help='列出所有快照')
    args = p.parse_args()

    if args.list:
        dates = list_snapshots()
        print(f"已有快照 ({len(dates)} 天):")
        for d in dates:
            df = load_ranking(d)
            print(f"  {d}: {len(df)} 只标的")
        return

    if args.show:
        df = load_ranking(args.show)
        if df is None:
            print(f"无 {args.show} 快照")
            return
        print_top(df, args.top)
        return

    today = datetime.now().strftime("%Y-%m-%d")

    # 加载或计算今日排名
    df = load_ranking(today)
    if df is not None and len(df) > 0:
        print(f"今日快照已存在 ({today}, {len(df)} 条)，直接加载\n")
    else:
        code_map = build_code_map()
        df = run_ranking(code_map, today)

    if df is None or len(df) == 0:
        print("无数据")
        return

    print_top(df, args.top)

    angles = df['angle'].values
    print(f"\n📊 全市场 ({len(df)} 只): "
          f"最高{max(angles):.1f}° 最低{min(angles):.1f}° 平均{np.mean(angles):.1f}°  "
          f">80°:{sum(angles>80)} >60°:{sum(angles>60)} >0°:{sum(angles>0)} <0°:{sum(angles<0)}")

    if args.compare:
        dates = list_snapshots()
        if len(dates) >= 2:
            compare_rankings(df, dates[-2])
        else:
            print("缺少昨日快照")

    if args.trend > 0:
        trend_analysis(args.trend)


if __name__ == "__main__":
    main()
