# 中长线逻辑识别 — 过程洞察

## 关键决策（Key decisions）

### 决策 1：MA5 角度数据获取方式选型
- **决策**：使用 Playwright 爬取东方财富条件选股页面，而非逐只调 API 计算。
- **理由**：
  - 全市场 5000+ 只股票逐只调 API 需 5~8 分钟，爬虫版约 25~35 秒
  - 角度值来自东方财富官方，无需担心公式偏差
  - 查询条件灵活（加"非 st"、改排名数量），一次输入即可
- **约束**：需要安装 Chromium（约 500MB），page structure 随查询条件变化

### 决策 2：角度计算公式确认与保留
- **决策**：确认东方财富同款公式 `ATAN((MA5今/MA5昨-1)×100)×180/π`，写入 `ma5_ranking.py` 备用
- **理由**：爬虫是主链路，公式版保留以应对页面不可用或自定义标的场景
- **约束**：公式版需逐只调 akshare API，速度慢，仅做兜底

### 决策 3：列映射用表头核心名精确匹配，放弃子串回退
- **决策**：`_build_column_index` 只用核心名（第一段文字）精确匹配，不用子串回退
- **理由**：子串回退导致"5日均线"误匹配"日线周期5日均线角度"，MA5 列取值错误
- **约束**：表头文本格式变化时需同步更新关键词

### 决策 4：上涨/下跌家数数据源选型
+- **决策**：从乐股网 HTML 改为 短线侠 qxlive 页面（Playwright 渲染后正则提取）
+- **理由**：乐股网数值不准确（total≠A股总数），短线侠数据与交易所一致
+- **代价**：每次运行多 ~8s（启动 Chromium）

### 决策 5：看板分时图布局
+- **决策**：分时图上下分栏（股价+量能），去掉右轴涨跌幅，日K线同理
+- **理由**：ECharts 双轴对齐调试成本高（刻度取整、等分），去掉右轴大幅简化
+- **替代**：分时卡片右上角直接显示当日涨跌幅数字

### 决策 6：持久化方案从 localStorage 改为服务端 JSON API
- **决策**：前端不再只用 localStorage，改为通过 `dashboard_server.py` 提供的 REST API 读写 `.index_data/*.json`
- **理由**：
  - localStorage 绑定浏览器 + origin（端口/协议），换个方式打开数据全丢
  - `generate_dashboard.py` 重新生成 HTML 时无法感知 localStorage 中的数据
  - 服务端 JSON 可被 Python 和前端同时读写，数据流形成闭环
- **约束**：需要额外启动 `dashboard_server.py`，不能直接双击 HTML 文件使用

### 决策 7：新增机会提示标签
- **决策**：在风险提示下方独立展示机会提示区域，黄绿色系，操作逻辑与风险标签完全一致
- **理由**：风险/机会分开管理视角更清晰，独立 JSON 存储（`opp_tags.json`）避免语义混淆
- **约束**：当前无数据驱动的自动机会标签，仅支持手动添加

## 关键试错（Key trials）

### 试错 1：公式探索过程
- **尝试**：先后测试了 5 种公式——(A) 斜率/均价×100 arctan, (B) 5日总涨幅 arctan, (C) 日均涨幅×5 arctan, (D) 斜率/首MA5×100 arctan, (E) 回归首尾涨幅 arctan
- **结果**：方法 A 偏差 mean=-3.8°、max=8.8°，低股价偏差尤其大（大众交通误差 8.6°）
- **结论**：正确公式只看单日 MA5 变化率，不需要 5 日线性回归

### 试错 2：名称表数据被误判为角度表
- **尝试**：用 `10 < v < 90` 范围检测角度值
- **结果**：名称表中的最新价（如 52.87）也在该范围，导致 name 表数据被写入 data 表
- **结论**：`hasStockCode`（6 位数字代码）必须优先于 `hasAngleValues` 判断

### 试错 3：空列表头过滤导致列索引偏移
- **尝试**：`.filter(t => t)` 过滤掉空的 th
- **结果**：dataCols 缺少前 6 个空列，索引从 0 开始偏移 6 位，角度列取到空字符串
- **结论**：保留空列以保证 dataCols 与 dataRows 索引对齐

### 试错 4：Vue contenteditable 文字修改
- **尝试**：用 `innerText = ''`、`document.execCommand`、`fill()` 等方式修改查询条件
- **结果**：`fill()` 能成功写入文字，但需配合「去选股」按钮触发实际查询
- **结论**：预存条件 ID 固定了参数，修改文字后必须重新执行查询流程，不能仅改文本

### 试错 5：ECharts candlestick tooltip 数据偏移
+- **尝试**：tooltip formatter 中用 `p.data[0]` 取开盘、`p.data[1]` 取收盘
+- **结果**：显示数值全部错位，开/收/高/低与实际不符
+- **根因**：ECharts 传给 formatter 的 candlestick data 会在原始数组前插入内部 `dataIndex`，变成 `[dataIndex, open, close, low, high]`（5 元素），而非原始传入的 `[open, close, low, high]`（4 元素）
+- **结论**：正确索引为 `data[1]=O, data[2]=C, data[3]=L, data[4]=H`，与 `generate_trend_page.py` 已有处理一致
+
+### 试错 6：HTTP API 直调失败
- **尝试**：直接调 `push2.eastmoney.com`、`xuangu.eastmoney.com/api/*` 等接口
- **结果**：全部返回 HTML 页面（SSR），数据由客户端 JS 动态加载
- **结论**：纯 HTTP 请求无法获取数据，必须用浏览器渲染

### 试错 7：ETF 分钟线文件名 `_m5` 后缀不一致
+- **尝试**：dashboard 用 `m5=True` 加载，构造路径 `etf_minute_m5_上证50ETF_*.csv`
+- **结果**：全部找不到文件，分时图和5日分时均为空
+- **根因**：`index_data.py` 保存 ETF 分钟线时未加 `_m5` 后缀（ETF 只有 m5 一种精度）；指数 m5 文件根本未生成
+- **结论**：`load_all_minutes_m5()` 加 `m5=False` 回退逻辑；指数5日分时用 m1 文件兜底

### 试错 8：ECharts markLine formatter 中动态参数不可靠
+- **尝试**：5日分时 markLine label 用 `formatter: function(p) { ... dates[p.name] ... }`
+- **结果**：`Cannot read properties of undefined (reading 'slice')` → 页面只渲染第一行
+- **结论**：markLine data 构建时直接内联 `dates[di+1].slice(5)`，不依赖 formatter 运行时参数

### 试错 9：ECharts 多 grid 模式下偶发 init 错误
+- **尝试**：5日分时用双 grid（价格 66% + 量能 22%），数据含 null 断点
+- **结果**：偶发 `getBoundingClientRect` null 报错
+- **结论**：`makeChart()` 内 `echarts.init` 包裹 try-catch，失败静默跳过

### 试错 10：5日分时从重叠线改为连续拼接
+- **尝试**：5天各画一条线重叠在同一时间轴上（5 条线共用 X 轴）
+- **结果**：用户期望从左到右 5 天接连展示，非重叠比对
+- **结论**：单条折线，5 天首尾相接，天间 null 断点 + 灰色实线分隔，标签标日期

### 试错 11：ETF 图表在 `display:none` Tab 内无法渲染
+- **尝试**：页面加载时 `buildEtfGrid()` 同步初始化所有 ETF 图表
+- **结果**：ETF Tab 的 `.tab-pane{display:none}` 导致容器高度为 0，ECharts 无法获取元素尺寸 → `getBoundingClientRect` null
+- **结论**：惰性渲染——初始化只建指数图表，ETF Tab 首次切换时才 `buildEtfGrid()`

### 试错 12：ETF m5 量能数据为非累计值，差分导致柱消失
+- **尝试**：ETF m5 的 `amount_yi` 与指数 m1 一样做 `v-amounts[i-1]` 差分
+- **结果**：ETF 量能柱频繁消失，偶发有值（diff 后大量 0/负值）
+- **根因**：TX mkline m5 返回的 `amount_yi` 是每 5 分钟独立值（非累计），波动上升/下降
+- **结论**：ETF 量能用原始值（不差分），通过 `isETF` 参数区分；同时 ETF 分时缺 09:30 点，补开盘价

### 试错 13：深证/创业板历史成交额数据源探索
+- **尝试**：`stock_zh_a_daily`（Sina源）获取深证(sz399001)、创业板(sz399006) 日线成交额
+- **结果**：仅上海交易所代码有效（sh000001/sh000688），深圳代码全部报错 "No value to decode"
+- **替代方案**：深市指数用当日 Sina 实时成交额 ÷ 成交量计算比例，应用到历史日估算

## 权衡与约束触发点（Tradeoffs & constraint triggers）

| 权衡对立 | 约束条件 | 最终取舍 |
|---------|---------|---------|
| 爬虫速度 vs 全量覆盖 | Chromium 启动 3s + 页面渲染 8s + 分页调整 5s + 提取 2s ≈ 25~35s | 接受 ~30s，换取官方数据准确性 |
| 列结构稳定 vs 查询灵活 | 不同查询条件导致不同列出现/消失 | 按表头核心名精确匹配，无匹配列静默跳过 |
| 分页 vs 一次性加载 | 页面默认 50 条/页，最多 200 条/页 | 自动检测并调大分页，上限 500 条内一次拉完 |

## 拒绝方案（Rejected considered）

1. **纯 API 方案**（push2 / xuangu API）：数据 JS 动态渲染，纯 HTTP 拿不到 → 拒绝
2. **全市场逐只调 akshare 计算**（ma5_ranking.py 原方案）：太慢（5~8min），且需维护公式 → 降为备用
3. **直接修改 contenteditable 后点"更新选股结果"**：预存条件 ID 固定参数，改文本不生效 → 改为走"去选股"完整流程

## 备选方案（Alternatives considered）

- 如果东方财富页面改版导致 Playwright 爬虫失效：回退到 `ma5_ranking.py` 公式计算版本

## 不确定性与后续验证（Open questions）

| 未解问题 | 不确定点 | 验证手段 | 预期判据 |
|---------|---------|---------|---------|
| 多日排名变化分析工具 | 是否值得单独写脚本，还是用 pandas 手动对比就够了 | 先用手动方式跑几天看效果 | 如果需要每天对比则做工具 |
| 题材分类与 5 日线角度的交叉分析 | interest_stock.md 中的题材标签能否关联到角度排行 | 手动查几只自选股在排行中的位置 | 覆盖面 >30% 则值得做 |
| 页面改版风险 | 几个月后 el-table 结构可能变化 | 每次运行失败时检查页面结构 | 如果无法修复则降级到公式版 |
