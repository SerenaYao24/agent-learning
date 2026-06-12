# 系统初始化

## 系统结构及文档配置

- 整个系统由 3 个层级组成：
  - system，维护系统级信息，包括
    - skills 相关信息
    - 系统级 context.md
    - 系统级 insight.md
  - workflows，维护所有工作流级信息，包括
    - 工作流级 context.md
    - 工作流级 insight.md
  - projects，维护所有项目信息，包括
    - 项目级 context.md
    - 项目级 state.md
    - 项目级 process_insight.md
- 整个系统 3 种关键文档类型：
  - context.md：结果信息（侧重点在怎么使用）
  - insight.md/process_insight.md：过程信息（决策点、试错点、总结）
  - state.md：状态记录

## 基础行为约束

- 在开启 session 时，优先读取：
  - 读取 system 信息（读取 `0_system/` 下所有 `.md` 文件：`工作方式.md`、`对话方式.md`、`insights.md`）
  - 当用户进入一个 project 之后，再读取 project 相关的 workflow 信息和 project 本身的信息，project 里的信息会很多，但很多都是过程信息，你都不用了解，具体了解 context.md 和 insight.md 就行
  - 加载所有 skills

- 遇到 import / 依赖错误时：
  - 优先判断是代码问题还是环境限制
  - 不确定时先询问用户，再决定是否写入 context.md

- 避免重复尝试：
  - 在规划/尝试前检查相关 context.md 是否已有失败记录，避免尝试走不通的路

- 阶段性任务完成时：
  - 询问是否更新 state.md
    - 写入 state.md 时：保持简洁（避免记录无价值细节），重点记录：
      - 日期
      - 已完成的步骤，生成了什么文档，进展到哪一步了，是否有下一步计划，如果有，是什么
  - 自行判断是否需要更新 context.md，判定规则（以下条件需全部满足，严格执行）
    - 当前任务可运行/可执行，无错误
    - 没有未完成的 feature
  - 自行判断是否需要更新 insight.md，判定规则（以下条件满足其一即可，严格执行）
    - 出现讨论
    - 出现决策
    - 出现试错
    - 实践被采纳
    - 出现约束导致的取舍
    - 未解问题需要跟踪

- 当任务结束时：
  - 创建/更新 skill：
    - 询问是否总结为 skill，并输出预期的 skill 信息给我确认，包括 name, trigger, steps，确保 skill 名称简洁易懂
    - 如果同意创建 skill，生成 md 文件
      - 文件内容为：
        - skill 名称
        - 触发条件
        - 执行步骤
    - 创建 skill 前：检查是否已有类似 trigger，若高度相似，则优先复用或更新已有 skill
    - 新建的 skill 需要在 system/skills 下维护一份，修改时需保持一致性
    - 在 system/skills 里维护一个 skills.csv 文件，记录所有 skill 名称、用途、trigger、执行次数，在 skill 创建、更新、被调用时更新该文件
  - 创建/更新 context.md
  - 创建/更新 insight.md
  - push 到远程仓库


- 更新 context.md 信息流程：
  - 把关键信息更新到正确的层级与正确的文档中，并保持字段间一致性与可执行性。
  - 层级为：
    - system
    - workflows
    - projects
  - 内部结构为：
    - 项目类型（which workflow）
    - 适用场景/触发条件（When to use）
    - 目标与质量标准（What good looks like）
    - 输入清单（Input）
    - 约束条件（Constraints）
    - 工作指令（Workflow instructions）
    - 失败模式（Failure modes）
    - 复用方式（Reuse）
    - 证据/示例（Examples）
  - 注意：不同层级写法应遵循边界：
    - system 层：写抽象纪律/通用规则，避免具体工具链细节
    - workflow 层：写“通用工作流（某任务形态）导致的失败/偏差”的抽象处理与可复用判断框架
    - project 层：写本项目具体链路的追问字段、工具失败兜底、偏差落点与落地流程
  - 联动更新规则（核心）
    - 当你检测到任务结果中包含以下变化时，必须同时更新相关区块，且保证字段一致性：
      - 如果新增/修改了 Input（例如新增一个输入字段、输入格式变化、输入来源变化）：
        - 更新 Input
        - 同步更新 Failure modes：加入“输入缺失/格式错误/语义冲突/来源不可用”等失败表现及征兆
        - 同步更新 What good looks like：把输入相关的验收项写清楚（质量标准与验收点）
        - 同步更新 Constraints：补充“不允许什么/必须满足什么”
        - 同步更新 Judgment：增加关键决策点（何时追问、何时降级/中断）
        - 同步更新 Workflow instructions：把输入相关步骤补进执行顺序（或更新分支条件）
      - 如果发现“输出偏差”或“失败处理”被修正：
        - 更新 Failure modes
        - 更新 What good looks like（偏差的定义/纠正标准/验收阈值）
        - 更新 Judgment（发生时选择哪条路径）
        - 更新 Workflow instructions（对应修复/兜底步骤）
      - 如果新增了可复用策略或通用块：
        - 更新 Reuse（说明可贴用的段落范围与使用边界）
    - 在 system/workflow 层记录更抽象的部分；在 project 层记录具体实例化方式
    - 确保所有区块与层级边界一致
    - 根据项目类型判断，抽象信息需要更新到哪个 workflow
  - 分步实施要求
    - 请输出三部分结果：
      - A. 更新计划（Update Plan）：简要列出你将更新的层级与区块（例如：workflow 层更新 When to use / Failure modes / Judgment 等），每个区块说明“为什么要改”。
      - B. 区块级补丁（Patch）：以“区块为单位”给出每个层级主文档的更新内容。格式如下（必须保持标题一致）：system/context.md：When to use：新内容（或补丁）、What good looks like：新内容（或补丁）...; workflow/<workflow_type>/context.md：同上; projects/<project_name>/context.md：同上
        - 要求：只改与本次任务相关的条目，不要无关重写整个文档; 保持原有区块标题与层级边界
      - C. 一致性自检（Consistency Check）:列出你自检的 5-8 条检查项，例如：
        - Input 的每一项是否在 Failure modes 中有对应失败表达/征兆？
        - Failure modes 是否和 What good looks like 的验收偏差定义一致？
        - Judgment 是否明确了追问/降级/中断的触发条件？
        - Workflow instructions 的步骤是否覆盖了新增的输入处理逻辑？
        - system/type 中是否避免引入项目级工具细节？
    - 用户确认后方可修改文件
  - 行为约束
    - 不要让用户再次解释；所有抽取与归纳必须只基于 task_result_summary（本次任务完成摘要） 与 changes_observed（实际发生/新增/修正点）
    - 如果信息不足以更新某区块：在该区块用“缺少信息”条目标注，并提出最少的补问项（不超过 3 个）
    - 不要新增与当前变化无关的大段内容
  - 条目模板
    - 项目类型（which workflow）：枚举值，表明当前项目使用哪种 workflow
    - 适用场景/触发条件（When to use）
      - 适用范围（一句话）
      - 触发条件（列出 3-7 条短句）
      - 不适用/中断条件（列出 1-3 条短句，指向失败模式时更好）
    - 目标与质量标准（What good looks like）
      - 目标（一句话）
      - 验收标准（列出 3-6 条可检验口径）
      - 质量等级/降级口径（如适用，1段即可，和Failure modes联动）
    - 输入清单（Input）
      - 必需输入（清单）
      - 可选输入（清单）
      - 输入缺失时的降级策略引用（指向 Failure modes 里的某类条目，而不是自己重复一套逻辑）
    - 约束条件（Constraints）
      - 硬约束（必须满足）
      - 软约束/偏好
      - 冲突处理规则（1段：冲突时谁优先）
    - 工作指令（Workflow instructions）
      - 开始指令
      - 步骤总览（3-7步）
      - 每步产出/检查点（每步一句或两句）
      - 停止与重跑条件（与Failure modes挂钩）
    - 失败模式（Failure modes）
      - 触发条件 / Trigger（何时发生）
      - 模式类型 / mode_class（区分两类）
        - 阻断型 blocking：无法满足质量标准，需要追问/中断/升级
        - 降级型 degradation：允许兜底默认值，但要降置信/降质量并标注风险
      - 严重程度 / severity：低 low / 中 medium / 高 high
      - 失败表现 / Failure manifestation（会看到什么）
        - 缺失：XX 输入未提供/无法解析
        - 冲突：同一字段语义不一致
        - 不可用：来源不可达/超时
        - 格式错误：与预期结构/单位/类型不匹配
      - 征兆 / Signs（错的信号）：例如：解析失败次数、字段为空比例、置信度低于阈值、上下游校验失败等
      - 影响范围 / Quality impact（质量会偏到哪里）：例如：输出结论偏差类型、决策方向可能受影响、可用性下降程度
      - 判定依据 / Judgment link（对应哪个判断点）：指向本层级文档的判断规则（例如“允许默认值的条件”或“关键字段判定”那条）
      - 处置策略 / Action（怎么做）
        - ask_more 追问更多信息
        - halt 中断并升级
        - use_default 使用默认值（fallback）
        - rerun 重跑（带兜底参数/换策略）
      - 默认值策略（仅当 mode_class=degradation 或 Action=use_default 时填写）
        - 默认值来源：固定常量 / 历史统计 / 类型级建议值
        - 默认值可接受的理由：为什么在缺失时仍"够用"
        - fallback 标记方式：输出是否要带 fallback=true / 风险标签 / 置信度降级
      - 质量验收口径（与 What good looks like 对齐）
        - 合格条件：例如"允许降级但必须满足 X/Y，并标注风险"
        - 不合格条件：例如"默认值导致偏差超过阈值 → 必须中断"
    - 复用方式（Reuse）
      - 复用边界（哪些可以直接复用，哪些只可抽取片段）
      - 参数化点（要变化的字段/变量）
      - 版本/兼容性口径（一句话：更新如何影响旧任务）
    - 证据/示例（Examples）
      - 正例（1-2个短例）
      - 反例/边界例（1个短例，说明失败模式触发与处理）

- 更新 insight.md 信息流程：
  - 把关键信息更新到正确的层级与正确的文档中，并保持字段间一致性与可执行性。
  - 层级为：
    - system
    - workflows
    - projects
  - 内部结构为：
    - 关键决策（Key decisions）：做了什么决策 + 决策理由和收益是什么 + 决策约束是什么。
    - 关键试错（Key trials）：尝试的方向 + 观察到的结果/信号 + 结论/教训。
    - 权衡与约束触发点（Tradeoffs & constraint triggers）：权衡对立 + 触发约束/条件 + 最终取舍。
    - 拒绝方案（Rejected considered）：已被拒绝的方案 + 为什么不选。
    - 备选方案（Alternatives considered）：已决策的问题中，未明确拒绝但尚未尝试/实施的方案
    - 不确定性与后续验证（Open questions）：尚未决策的问题 + 不确定点 + 计划验证手段 + 预期判据

---

## 模型质量提醒

| 模型 | 状态 | 说明 |
|------|------|------|
| `deepseek-v4-flash` | ✅ **可用** | 主力模型，质量可靠，性价比高 |
| `glm-5.1` | ✅ **可用** | 备选模型，质量可靠，价格偏贵 |
| `hy3_preview` | ❌ **不推荐** | 质量较差，不要因贪图便宜或速度而使用此模型，最终浪费的是双方时间 |

> 用户明确提醒：不要贪便宜选差模型，全程 wx 时间内推荐的好模型，没时间也没兴趣试错。

