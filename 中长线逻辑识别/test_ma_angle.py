#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试5日均线角度计算
"""

import akshare as ak
import pandas as pd
import numpy as np

def calculate_ma_angle(close_prices, ma_period=5, fit_period=5):
    """
    计算均线角度
    
    参数:
        close_prices: 收盘价列表（按时间顺序，最早在前）
        ma_period: 均线周期，默认5日
        fit_period: 用于线性拟合的数据点数
    
    返回:
        角度（度数），正值为上升，负值为下降
    """
    if len(close_prices) < ma_period + fit_period:
        return None
    
    # 计算均线
    df = pd.DataFrame({'close': close_prices})
    ma = df['close'].rolling(window=ma_period).mean()
    
    # 去除NaN
    ma = ma.dropna()
    
    if len(ma) < fit_period:
        return None
    
    # 取最后fit_period个均线值进行线性拟合
    y = ma.tail(fit_period).values
    x = np.arange(len(y))
    
    # 计算斜率
    slope = np.polyfit(x, y, 1)[0]
    
    # 转换为角度
    angle = np.arctan(slope) * 180 / np.pi
    
    return round(angle, 2)

def search_stock_code(stock_name):
    """搜索股票代码"""
    try:
        df = ak.stock_info_a_code_name()
        for _, row in df.iterrows():
            code = row['code']
            name = row['name'].replace(' ', '')
            if stock_name == name:
                if code.startswith('6'):
                    return f"sh{code}"
                elif code.startswith(('0', '3')):
                    return f"sz{code}"
                else:
                    return code
        return None
    except Exception as e:
        print(f"搜索股票代码失败: {e}")
        return None

def get_stock_data(stock_code, days=30):
    """获取股票数据"""
    try:
        df = ak.stock_zh_a_hist_tx(symbol=stock_code, adjust="qfq")
        if df.empty:
            return None
        
        # 取最近N天的数据
        df = df.tail(days)
        return df['close'].tolist()
    except Exception as e:
        print(f"获取股票数据失败: {e}")
        return None

def main():
    # 要测试的股票
    stocks = ["水发燃气", "德龙汇能", "江特电机"]
    
    print("=" * 70)
    print("5日均线角度测试计算")
    print("=" * 70)
    
    for stock_name in stocks:
        print(f"\n【股票】{stock_name}")
        print("-" * 70)
        
        # 查找股票代码
        stock_code = search_stock_code(stock_name)
        if not stock_code:
            print(f"  ❌ 未找到股票代码")
            continue
        
        print(f"  股票代码: {stock_code}")
        
        # 获取数据
        close_prices = get_stock_data(stock_code, days=30)
        if not close_prices:
            print(f"  ❌ 未获取到数据")
            continue
        
        print(f"  获取到 {len(close_prices)} 天的收盘价数据")
        
        # 计算5日均线角度
        angle = calculate_ma_angle(close_prices, ma_period=5, fit_period=5)
        
        if angle is not None:
            # 判断趋势
            if angle > 5:
                trend = "📈 强势上升"
            elif angle > 0:
                trend = "↗️ 温和上升"
            elif angle > -5:
                trend = "↘️ 温和下降"
            else:
                trend = "📉 强势下降"
            
            print(f"  ✅ 5日均线角度: {angle}°")
            print(f"  趋势判断: {trend}")
            
            # 显示最近的价格信息
            latest_close = close_prices[-1]
            ma5 = pd.Series(close_prices).tail(5).mean()
            print(f"  最新收盘价: {latest_close:.2f}")
            print(f"  当前5日均线: {ma5:.2f}")
        else:
            print(f"  ⚠️  数据不足，无法计算角度")
    
    print("\n" + "=" * 70)
    print("计算完成")
    print("=" * 70)
    print("\n提示：您可以在股票软件中查看这3只股票的5日均线，")
    print("      观察均线走向是否与计算结果一致（角度正负、趋势方向）。")

if __name__ == "__main__":
    main()
