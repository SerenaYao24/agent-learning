# 中长线逻辑识别 — 状态记录

## 2026-05-27

- **已完成**：
  - 个股数据 tab 题材标签按日均涨跌排序 + 显示平均涨幅（正红负绿）
  - 个股卡片涨跌幅着色（正红负绿）、股票名点击跳转东方财富个股页（sh/sz）
  - `stock_filter.py` 新增 `--all --save-tags` 批量模式，`daily_run.py` 自动调用两个筛选策略
  - 板块数据表 + 个股题材标签展示筛选结果（加强题材/抗跌题材标签、绿点 hover）
  - `daily_run.py` / `scrape_ma5_ranking.py` / `scrape_amount_ranking.py` 新增 Python3 版本检查
  - 板块数据表新增 d1-d5 逐日涨跌列
  - `interest_stock.md` 去重：92 只跨题材重复标的 → 0 重复
- **当前状态**：`stock_filter.py --all --save-tags` 生成的 `filter_tags.json` 被看板消费显示

## 2026-05-25

- **已完成**：
  - 删除同花顺全A指数：移除 `index_data.py` 中三个函数和 main() 调用，总量能改为 `(上证+深证)*1.01`
  - 修复 `generate_dashboard.py` 中 `index_data[4]` 引用 → `(上证+深证)*1.01`
  - 更新 `anomaly_detection.py`：`check_index_volume` 和 `check_panic_sell` 用新总成交额公式替代同花顺全A
  - 更新 `context.md`、`代码使用方法.md`、`异常检测需求.md` 同步删除同花顺全A引用
- **已完成**：
  - 集成 trend_view.html 到 dashboard：板块数据 tab（解析多日报告题材表格，可点击跳转）+ 个股数据 tab（K线卡片 + 题材 pills 筛选，按5日涨幅降序，默认展示最强板块，切换题材时懒加载 ECharts 图表）
- **当前状态**：看板生成、异常检测、每日编排脚本均已适配新总成交额公式

## 2026-05-24

- **已完成**：
  - 创建 `dashboard_server.py` 统一服务，提供静态文件 + API 持久化端点（risk-tags / opp-tags / monitor）
  - 风险/机会/监控数据改为服务端 JSON 持久化（`.index_data/*.json`），解决 localStorage 丢数据问题
  - 修改 `generate_dashboard.py` JS 模板，前端 fetch API 替代 localStorage
  - 重点监控新增"触发规则"字段（30天200% / 10天100%）
  - 新增机会提示标签区域（黄绿色 UI），操作逻辑与风险提示一致，数据持久化到 `opp_tags.json`
  - 修复风险标签 × 删除按钮因 `getRiskState()` 被替换导致无法点击的 bug
- **当前状态**：`dashboard_server.py` 需手动启动（`python dashboard_server.py`），看板功能完整
  - 优化指数宏观 Tab 5 日分时：数据源从 m1 改为 m5，处理方式与 ETF 保持一致（补 09:30 开盘价、量能不差分+左移）
  - `index_data.py`：`get_minute_mkline` 新增 `date` 字段，m5 按日期分组保存（覆盖 05-14~05-22 共 7 个交易日）
  - 创建 `daily_run.py` 每日流程编排脚本（--analyze 模式串行执行趋势分析→MA5排名→指数数据→异常检测→看板），更新 `代码使用方法.md` 置顶入口
- **下一步计划**：实现 `异常检测需求.md` 中的自动化检测策略（ETF 异动、指数量能、震荡区间）

## 2026-05-23

- **已完成**：
  - 确认东方财富同款 MA5 角度公式：`ATAN((MA5今/MA5昨-1)×100)×180/π`，11 只验证股偏差 <0.05°
  - 开发 `scrape_ma5_ranking.py`（Playwright 爬虫版），从东方财富条件选股直取全市场排行
  - 处理多项稳健性坑：弹窗遮挡、列结构变化、股价误判为角度、分页限制、Vue 组件文字修改
  - 更新 `代码使用方法.md`，新增爬虫版和公式版两个章节
  - 创建项目规范文档：`context.md`、`process_insight.md`、`state.md`
- **当前状态**：`scrape_ma5_ranking.py` 可正常运行，`ma5_ranking.py` 公式已修正备用
- **下一步计划**：
  - 每日运行爬虫积累历史数据
  - 视需要开发多日排名变化对比工具
