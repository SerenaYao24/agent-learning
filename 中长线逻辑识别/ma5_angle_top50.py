#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日获取5日均线角度排名前50的股票
优化策略：先筛选再计算，使用并行处理
"""

import akshare as ak
import pandas as pd
import numpy as np
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
import time
import os

# 缓存目录
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ma5_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def calculate_ma_angle(close_prices, ma_period=5, fit_period=5):
    """计算均线角度"""
    if len(close_prices) < ma_period + fit_period:
        return None
    
    df = pd.DataFrame({'close': close_prices})
    ma = df['close'].rolling(window=ma_period).mean()
    ma = ma.dropna()
    
    if len(ma) < fit_period:
        return None
    
    y = ma.tail(fit_period).values
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    angle = np.arctan(slope) * 180 / np.pi
    
    return round(angle, 2)

def get_stock_data(stock_code, days=30):
    """获取股票数据"""
    try:
        df = ak.stock_zh_a_hist_tx(symbol=stock_code, adjust="qfq")
        if df.empty:
            return None
        df = df.tail(days)
        return df['close'].tolist()
    except:
        return None

def process_stock(stock_info):
    """
    处理单只股票（用于并行处理）
    
    参数:
        stock_info: (stock_code, stock_name)
    
    返回:
        (stock_code, stock_name, angle, latest_price) 或 None
    """
    stock_code, stock_name = stock_info
    
    try:
        close_prices = get_stock_data(stock_code, days=30)
        if not close_prices:
            return None
        
        angle = calculate_ma_angle(close_prices, ma_period=5, fit_period=5)
        if angle is None:
            return None
        
        latest_price = close_prices[-1]
        
        return (stock_code, stock_name, angle, latest_price)
    except Exception as e:
        return None

def filter_stocks(stock_df, min_price=3, max_price=500, min_volume=10000):
    """
    预筛选股票
    
    参数:
        stock_df: 股票列表DataFrame
        min_price: 最低价格过滤
        max_price: 最高价格过滤
        min_volume: 最低成交量过滤（手）
    
    返回:
        筛选后的股票列表
    """
    print(f"开始预筛选，初始股票数: {len(stock_df)}")
    
    # 排除ST股票
    stock_df = stock_df[~stock_df['name'].str.contains('ST|退', na=False)]
    print(f"排除ST后: {len(stock_df)}")
    
    # 获取实时行情进行筛选
    try:
        print("正在获取实时行情...")
        # 使用东方财富接口获取实时数据
        df_realtime = ak.stock_zh_a_spot_em()
        
        # 合并数据
        df_merged = pd.merge(
            stock_df, 
            df_realtime[['代码', '最新价', '成交量', '涨跌幅']], 
            left_on='code', 
            right_on='代码',
            how='left'
        )
        
        # 价格过滤
        df_merged = df_merged[
            (df_merged['最新价'] >= min_price) & 
            (df_merged['最新价'] <= max_price)
        ]
        print(f"价格过滤后({min_price}-{max_price}元): {len(df_merged)}")
        
        # 成交量过滤（去除僵尸股）
        if '成交量' in df_merged.columns:
            df_merged = df_merged[df_merged['成交量'] >= min_volume]
            print(f"成交量过滤后(>{min_volume}手): {len(df_merged)}")
        
        # 排除跌停股（可选）
        if '涨跌幅' in df_merged.columns:
            df_merged = df_merged[df_merged['涨跌幅'] > -9.5]
            print(f"排除跌停股后: {len(df_merged)}")
        
        result = [(row['full_code'], row['name']) for _, row in df_merged.iterrows()]
        print(f"\n✅ 筛选完成，最终候选股票数: {len(result)}")
        
        return result
        
    except Exception as e:
        print(f"⚠️  实时行情获取失败，使用基础筛选: {e}")
        # 降级方案：只排除ST
        result = [(row['full_code'], row['name']) for _, row in stock_df.iterrows()]
        return result

def get_top50_ma5_angle(use_cache=True, max_workers=None):
    """
    获取5日均线角度排名前50的股票
    
    参数:
        use_cache: 是否使用缓存
        max_workers: 并行工作进程数，None为自动
    
    返回:
        DataFrame with columns: ['排名', '代码', '名称', '5日均线角度', '最新价']
    """
    print("=" * 70)
    print("5日均线角度 Top 50 计算程序")
    print("=" * 70)
    
    start_time = time.time()
    
    # 1. 获取所有A股列表
    print("\n[1/5] 获取A股列表...")
    stock_df = ak.stock_info_a_code_name()
    
    # 添加市场前缀
    def add_prefix(code):
        if code.startswith('6'):
            return f"sh{code}"
        elif code.startswith(('0', '3')):
            return f"sz{code}"
        else:
            return code
    
    stock_df['full_code'] = stock_df['code'].apply(add_prefix)
    
    # 2. 预筛选
    print("\n[2/5] 预筛选股票...")
    candidate_stocks = filter_stocks(stock_df)
    
    if not candidate_stocks:
        print("❌ 没有符合条件的股票")
        return None
    
    # 3. 并行计算均线角度
    print(f"\n[3/5] 并行计算 {len(candidate_stocks)} 只股票的5日均线角度...")
    print(f"    使用进程数: {max_workers or mp.cpu_count()}")
    
    results = []
    calculated = 0
    total = len(candidate_stocks)
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_stock, stock): stock for stock in candidate_stocks}
        
        for future in as_completed(futures):
            calculated += 1
            if calculated % 50 == 0:
                elapsed = time.time() - start_time
                print(f"    进度: {calculated}/{total} ({calculated*100//total}%), 耗时: {elapsed:.1f}s")
            
            result = future.result()
            if result:
                results.append(result)
    
    print(f"    计算完成: 成功 {len(results)}/{total}")
    
    # 4. 排序取前50
    print(f"\n[4/5] 排序并取前50...")
    results.sort(key=lambda x: x[2], reverse=True)  # 按角度降序
    top50 = results[:50]
    
    # 5. 生成结果DataFrame
    print(f"\n[5/5] 生成结果...")
    df_result = pd.DataFrame(
        [(i+1, item[0], item[1], item[2], item[3]) for i, item in enumerate(top50)],
        columns=['排名', '代码', '名称', '5日均线角度', '最新价']
    )
    
    elapsed_total = time.time() - start_time
    print(f"\n✅ 完成！总耗时: {elapsed_total:.1f}秒")
    
    return df_result

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='获取5日均线角度排名前50的股票')
    parser.add_argument('--no-cache', action='store_true', help='不使用缓存')
    parser.add_argument('--workers', type=int, default=None, help='并行进程数')
    parser.add_argument('--output', type=str, default=None, help='输出文件路径')
    
    args = parser.parse_args()
    
    # 获取Top50
    df_top50 = get_top50_ma5_angle(use_cache=not args.no_cache, max_workers=args.workers)
    
    if df_top50 is None:
        return
    
    # 打印结果
    print("\n" + "=" * 70)
    print("Top 50 股票（按5日均线角度排序）")
    print("=" * 70)
    print(df_top50.to_string(index=False))
    
    # 保存到文件
    if args.output:
        output_path = args.output
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        output_path = f"ma5_angle_top50_{today}.csv"
    
    df_top50.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"\n💾 结果已保存至: {output_path}")
    
    # 显示统计信息
    print(f"\n📊 统计信息:")
    print(f"  最高角度: {df_top50['5日均线角度'].max():.2f}°")
    print(f"  最低角度(Top50): {df_top50['5日均线角度'].min():.2f}°")
    print(f"  平均角度: {df_top50['5日均线角度'].mean():.2f}°")
    print(f"  角度>30°的股票数: {(df_top50['5日均线角度'] > 30).sum()}")

if __name__ == "__main__":
    main()
