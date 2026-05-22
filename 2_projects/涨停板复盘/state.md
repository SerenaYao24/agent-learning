# 涨停板复盘 - State

**日期**: 2026-05-23（本 session 初始化）

## 已完成

- 创建 project 结构（context.md + fetch_zt_data.py + 数据目录）
- 编写涨停数据抓取脚本 `fetch_zt_data.py`
- 从短线侠拉取 2026-05-22 涨停数据（115 只涨停，21 只炸板，23 只断板）
- 按 context 流程自动化采集：playwright 打开页面 → 点击涨停表现 → 全部展开 → 提取 iframe 数据 → 清洗保存
- 扩容 interest_stock.md：新增 30 只涨停标的到对应题材
- 创建 `limit-up-review` skill（含完整验证流程）

## 文档

- `context.md` —— 工作流定义（含写入验证规则）
- `fetch_zt_data.py` —— 涨停数据拉取脚本
- `数据/涨停表现_2026-05-23.md` —— 今日涨停表现

## 下一步计划

- 每个交易日运行 skill 拉取最新涨停数据
- 定期检查 keyword→sector 映射是否覆盖新增题材
