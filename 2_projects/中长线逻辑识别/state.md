# 中长线逻辑识别 — 状态记录

## 2026-05-24

- **已完成**：
  - 创建 `dashboard_server.py` 统一服务，提供静态文件 + API 持久化端点（risk-tags / opp-tags / monitor）
  - 风险/机会/监控数据改为服务端 JSON 持久化（`.index_data/*.json`），解决 localStorage 丢数据问题
  - 修改 `generate_dashboard.py` JS 模板，前端 fetch API 替代 localStorage
  - 重点监控新增"触发规则"字段（30天200% / 10天100%）
  - 新增机会提示标签区域（黄绿色 UI），操作逻辑与风险提示一致，数据持久化到 `opp_tags.json`
  - 修复风险标签 × 删除按钮因 `getRiskState()` 被替换导致无法点击的 bug
- **当前状态**：`dashboard_server.py` 需手动启动（`python dashboard_server.py`），看板功能完整
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
