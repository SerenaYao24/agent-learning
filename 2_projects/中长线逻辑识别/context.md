# 中长线逻辑识别 — 项目上下文

## 项目类型（Which workflow）

文章采集与学习

## 适用场景/触发条件（When to use）

- **适用范围**：全市场 MA5 角度排行、自选股滑动窗口趋势分析、K 线可视化
- **触发条件**：
  - 获取当日全市场 5 日均线角度 Top N 排行
  - 分析自选股（interest_stock.md）近 10 日趋势及题材强弱
  - 生成自选股 K 线 + 成交额走势 HTML 页面
- **不适用/中断条件**：东方财富选股页面不可用 → MA5 排行降级到公式版 `ma5_ranking.py`

## 目标与质量标准（What good looks like）

- **目标**：每日快速获取准确排行 + 自选股趋势分析，数据与东方财富官方一致
- **验收标准**：
  - MA5 排行输出 N 条（默认 200），无 ST，角度值 0~90°
  - 自选股报告按题材分组，含滑动窗口累计涨幅和评级
  - K 线页面可正常在浏览器打开，卡片平铺布局
  - CSV 自动保存到 `.ma5_ranking/`，报告输出到 iCloud「股票分析结果」
- **质量等级/降级口径**：爬虫不可用时降级到公式版，标注 fallback

## 输入清单（Input）

- **MA5 排行**：`--query`（默认 "五日均线角度从大到小排名前 200，非 st"）、`--top`、`--page-size`
- **趋势分析**：`-i <interest_stock.md>`（必需）、`--refresh` / `--multi` / `--force-gen`（可选）
- **K 线页面**：`-i <标的文件>` 或 `--sector <题材名>`、`--days`、`-o <输出.html>`
- **输入缺失降级**：MA5 排行全部有默认值；趋势分析缺 `-i` 报错退出

## 约束条件（Constraints）

- **硬约束**：
  - `scrape_ma5_ranking.py` 需 playwright + chromium
  - CSV 文件以页面数据日期命名（如 `ranking_2026-05-22.csv`），非脚本执行日期，避免周末重复运行产生冗余文件
  - `generate_trend_page.py` 需缓存中有 OHLCV 数据
  - **所有前端变更必须在 `generate_dashboard.py` 模板中修改，禁止直接修改 `dashboard.html`**（HTML 由脚本每次重新生成，直接改 HTML 会在下次运行 `daily_run.py` 时被覆盖）
- **软约束/偏好**：MA5 排行优先爬虫版，公式版备用；风险/机会/监控数据经 API 持久化到项目根目录 JSON 文件
- **冲突处理**：缓存过期 → `--refresh` 全量刷新

## 工作指令（Workflow instructions）

### 0. 每日一键流程（最常用）

```bash
python3 daily_run.py --analyze    # 串行执行 趋势分析→筛选→MA5排行→成交额→指数→MA5趋势→异常检测→看板
python3 daily_run.py              # 全流程（含涨停板复盘提醒）
```

### 1. MA5 角度排行（主链路）

```bash
python scrape_ma5_ranking.py                    # 默认 Top 200，非 ST
python scrape_ma5_ranking.py --top 30           # 只显示前 30
python scrape_ma5_ranking.py --query "五日均线角度从大到小排名前 100"  # 自定义
```

步骤：启动 Chromium → 输入条件 → 点击「去选股」→ 调大分页 → 提取数据 → 保存 CSV

### 2. 自选股趋势分析

```bash
python stock_trend_analysis.py -i interest_stock.md              # 日常增量运行
python stock_trend_analysis.py -i interest_stock.md --refresh    # 全量刷新 API
python stock_trend_analysis.py -i interest_stock.md --force-gen  # 仅重生成报告
python stock_trend_analysis.py -i interest_stock.md --multi      # 仅多日汇总
```

### 3. 题材/标的筛选

```bash
python stock_filter.py                  # 基于最新多日报告筛选
python stock_filter.py --date 05-14     # 指定日期报告
```

### 4. K 线走势可视化

```bash
python generate_trend_page.py                         # 默认读 interest_stock.md
python generate_trend_page.py --sector 光通信          # 题材模式
python generate_trend_page.py -i 自选.md --days 30    # 指定文件 + 天数
```

### 5. 大盘指数数据

```bash
python index_data.py                     # 指数 m1 + ETF m5 + 日线
python index_data.py --no-minute         # 仅日线
python index_data.py --no-etf            # 跳过 ETF
```

覆盖上证/深证/创业板/科创综指 + 6只ETF。指数分钟线 m1，ETF 分钟线 m5。上涨/下跌/涨停/跌停家数从短线侠 qxlive 获取。成交额：上证/科创综指用 stock_zh_a_daily 真实数据，深证/创业板用 Sina 实时比校准。总成交额 = (上证+深证) * 1.01。

### 6. MA5 角度排名趋势

```bash
python3 _gen_ma5_trend.py              # 基于近5日 ranking_*.csv 生成 ma5_trend.json
```

产出 `.index_data/ma5_trend.json`，包含近5日排名变化对比、题材 top3 平均排名。供看板「5日线角度」Tab 消费。

### 7. MA5 公式计算（备用）

```bash
python3 ma5_ranking.py --top 50      # 全市场扫描（慢，5~8min）
python3 ma5_ranking.py --compare     # 对比昨日排名
python3 ma5_ranking.py --trend 5     # 近 5 日趋势
```

### 8. 数据看板

```bash
python3 generate_dashboard.py        # 写数据文件到 .index_data/ + 生成瘦身 dashboard.html
```

7 个 Tab：指数宏观 / ETF宏观 / 板块数据 / 5日线角度 / 个股数据 / 大宗交易 / 重点监控。
数据不再内嵌到 HTML，改为写入 `.index_data/` 下的 js/json 文件，由 `dashboard_server.py` 的 `/api/data/*` 端点动态服务，HTML 约 55KB。
行情指标栏全局可见。上证日K固定关键位线+3万亿量能线。ETF惰性渲染。tooltip智能切换亿/万亿。

**5日线角度Tab**：近5日 MA5 排名变化对比（两日对比差，绿色+上升/橙色-下降），按题材标签筛选（top3 平均排名），列：排名变化/名称/今日涨幅/今日排名/所属题材/备注。
**大宗交易Tab**：全量数据保存到 `block_trades_*.json`，树状表格按题材分组展示自选股交易，加权折溢率从大到小排列，默认收起。
**重点监控Tab**：手动录入股票监管信息，字段含名称、类型（重点监控/触发严重异动）、触发规则（30天200%/10天100%）、起止日期（结束默认+10交易日剔除周末），按开始时间从近到远排列，过期灰显。
**风险标签**：数据驱动（涨跌家数分析 + 上证均线压制 MA5/10/20/30/60/120）+ 手动添加，橙色标签。
**机会标签**：数据驱动（涨跌家数分析 + 上证均线支撑 MA5/10/20/30/60/120）+ 手动添加，黄绿色标签，与风险标签平行运作。
**题材统计**：使用 `top_list.md`（近10日涨幅前15）作为股票池，排除僵尸股对板块整体判断的影响。多日报告中 d1~d4 从历史每日报告的题材表获取，d5 从当前 top_list 缓存计算。

### 9. 看板服务（统一入口）

```bash
python3 dashboard_server.py          # 启动统一服务（端口 8977）
```

替代 `python3 -m http.server`，提供静态文件 + 数据 API + 持久化 API：
- `GET /api/data/*` — 图表数据端点（stock-map, index-chart, etf-chart, ranking, block, sector, ma5-trend），数据文件由 `generate_dashboard.py` 写入 `.index_data/`
- `GET/POST /api/risk-tags` — 风险标签持久化 → `risk_tags.json`
- `GET/POST /api/opp-tags` — 机会标签持久化 → `opp_tags.json`
- `GET/POST /api/monitor` — 重点监控持久化 → `monitor.json`

三类经过人工参与的数据文件位于项目根目录，纳入 git 推送。`.index_data/` 和 `.ma5_ranking/` 下的原始数据不推送。

前端所有图表数据通过 `/api/data/*` 动态加载，标签/监控操作通过 fetch API 与后端通信。`generate_dashboard.py` 生成数据文件 + 瘦身 HTML，两端通过 `dashboard_server.py` 形成闭环。

### 7. 题材涨幅排行（按近10日涨幅筛选前15）

该功能已内嵌到 `stock_trend_analysis.py`，数据获取完成后、报告生成前自动更新 `top_list.md`，无需单独执行。

步骤：读取 interest_stock.md → 按题材分组 → 从缓存计算各标的近10日涨幅 → 每题材保留涨幅前15名 → 输出 top_list.md（格式与 interest_stock.md 一致）。
- 无数据标的放末尾并标注 `# ⚠️ 无近10日数据`
- 代码映射支持硬编码修正（`HARDCODED_MAP`），覆盖名称映射错误

## 失败模式（Failure modes）

| 模式 | 触发条件 | 类型 | 表现 | 处置 |
|------|---------|------|------|------|
| 弹窗遮挡 | 东方财富页面弹窗 | blocking/low | 按钮被 shadow 拦截 | 自动清除遮罩（已内置） |
| 列结构变化 | 查询条件不同 | degradation/medium | MA5/是否ST 列增减 | 按表头核心名匹配，无匹配跳过 |
| 页面不可用 | 东方财富改版/维护 | blocking/high | 表格结构无法识别 | 降级到 `ma5_ranking.py` |
| 缓存无 OHLCV | 旧缓存缺少 K 线字段 | blocking/medium | 走势图生成失败 | `--refresh` 重新拉取 |
| API 超时 | akshare 网络波动 | degradation/low | 部分标的数据缺失 | 缓存兜底，not_found 记录 |
| 看板 API 不可用 | dashboard_server.py 未启动 | degradation/low | 前端 fetch 失败 | 静默兜底（catch 空回调），内存数据可操作但不持久化 |

## 复用方式（Reuse）

- `_classify_tables` / `_build_column_index` 可用于其他东方财富页面爬取
- `stock_trend_analysis.py` 复用同一缓存层（`.stock_cache/stock_data.json`）
- 公式版 `ma5_ranking.py` 不依赖东方财富页面结构，作为兜底

## 组件模式（Component patterns）

### 树状表格（Tree Table）

默认应用方式：
- **外层（分组行）**：`<td>` 数量与内层完全一致，空白列填空 `<td></td>`，保证列对齐
- **内层（数据行）**：默认隐藏 `style="display:none"`，点击分组行展开/折叠
- **分组行**：`cursor:pointer` + 深色背景，用 `<b>` 加粗显示分组名
- **关键数值列**（如折溢率、总金额）：外层显示加权汇总值，内层显示明细值
- **toggle 函数**：遍历 `nextElementSibling` 直到不再是 `.trade-row`，切换 `display`

```javascript
// 分组行模板 - 与内层同列数
<tr class="theme-row" style="background:#111827;cursor:pointer" onclick="toggleTheme(this)">
  <td><b>分组名</b></td><td></td><td></td><td></td><td>汇总值</td><td>汇总值</td><td></td>
</tr>
// 数据行模板 - 默认隐藏
<tr class="trade-row" style="display:none">
  <td>名称</td><td>代码</td><td>日期</td><td>价格</td><td>折溢率</td><td>金额</td><td>备注</td>
</tr>
```

## 证据/示例（Examples）

- **MA5 排行**：`python scrape_ma5_ranking.py --top 10` → Top 10 排行 + CSV
- **趋势分析**：`python stock_trend_analysis.py -i interest_stock.md` → 每日 + 多日报告
- **K 线页面**：`python generate_trend_page.py --sector CPO --days 30` → 题材卡片页面
- **降级例**：爬虫失败 → `python ma5_ranking.py --top 200`（慢但可用）
