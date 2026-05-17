---
name: stock_trend_update
overview: 更新股票趋势分析代码，替换目标标的列表并优化趋势判断逻辑
todos:
  - id: update-stock-list
    content: 更新 interest_stock.md 为用户指定的10只标的
    status: completed
  - id: optimize-trend-logic
    content: 优化 analyze_trend() 趋势判断：用线性回归斜率替代首尾窗口对比，并在告警信息中展示斜率值
    status: completed
  - id: run-analysis
    content: 运行脚本生成最新趋势分析报告
    status: completed
    dependencies:
      - update-stock-list
      - optimize-trend-logic
---

## 产品概述

针对用户指定的10只目标标的，自动采集近10日涨跌幅数据，以5日滑动窗口计算6个累计涨跌幅数值，并根据趋势规则输出红/绿灯告警。

## 核心功能

- 更新目标标的列表为：利通电子、维科技术、中钨高新、国城矿业、蓝色光标、法狮龙、雄韬股份、大元泵业、柏诚股份、长飞光纤
- 收集每只标的近10日每日涨跌幅，以5日滑动窗口计算6个累计涨跌幅
- 最后一个窗口累计涨跌幅低于3%时，红灯告警提示回调风险
- 6个窗口值呈涨幅扩大趋势时，绿灯告警提示启动迹象
- 6个窗口值呈涨幅缩小/跌幅扩大趋势时，红灯告警提示走弱迹象
- 优化趋势判断逻辑：从简单的首尾窗口对比改为线性回归斜率，更准确反映整体趋势方向

## 技术栈

- 语言：Python 3
- 数据源：akshare
- 输出格式：Markdown 报告

## 实现方案

### 核心改动

1. **更新 `interest_stock.md`**：将内容替换为用户指定的10只标的
2. **优化 `analyze_trend()` 趋势判断逻辑**：当前仅用 `last_window - first_window` 判断趋势方向，只看首尾两个点容易被中间波动干扰。改用线性回归斜率（6个窗口值对窗口序号做最小二乘拟合），斜率>0表示整体涨幅扩大，斜率<=0表示涨幅缩小或跌幅扩大。斜率计算无需引入numpy，手动公式即可：

```
slope = (n*sum(x*y) - sum(x)*sum(y)) / (n*sum(x^2) - (sum(x))^2)
```

3. **增强报告输出**：在告警信息中展示趋势斜率值，让用户直观感知趋势强度

### 边界情况处理

- 当分母为0（6个窗口值完全相同）时，slope=0，归入走弱红灯
- 保留原有数据不足时的告警逻辑

## 目录结构

```
/Users/heloise/Desktop/agent/中长线逻辑识别/
├── interest_stock.md          # [MODIFY] 替换为用户指定的10只标的名称
├── stock_trend_analysis.py    # [MODIFY] 优化 analyze_trend() 趋势判断：用线性回归斜率替代首尾对比；输出中增加斜率信息
└── stock_trend_report.md      # [GENERATE] 运行脚本后自动生成的最新报告
```