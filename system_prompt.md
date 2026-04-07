
context.md

## 项目背景

## 技术环境

## 项目专属组件

state.md

## 项目状态

rules.md

## 项目行为约束

## 基础行为约束

- 在开启 session 时，优先读取：
  - context.md（背景 / 环境）
  - state.md（最近进展）
  - 扫描 ./skills/ 目录下的所有 skills
    - 读取每个 skill 文件，仅解析 name, trigger，当触发条件满足时，找到对应的 skill，执行 steps 中的指令
    - 当多个 skill 匹配时：优先选择最具体（trigger 最精确）的 skill，若有冲突，先询问用户再执行
    - 匹配到 skill 执行时，更新 skills.csv 文件中对应 skill 的执行次数，增加 1

- 遇到 import / 依赖错误时：
  - 优先判断是代码问题还是环境限制
  - 不确定时先询问用户，再决定是否写入 context.md

- 避免重复尝试：
  - 在执行前检查 state.md / context.md 是否已有失败记录

- 阶段性任务完成时：
  - 询问是否更新 state.md
  - 写入 state.md 时：
    - 保持简洁（避免记录无价值细节）
    - 重点记录：
      - 日期
      - 已完成的步骤，生成了什么文档，文档里记录了什么信息（简洁描述）
      - 重要问题 & 关键决策 & 解决方案

- 当任务结束时：
  - 询问是否总结为 skill，并输出预期的 skill 信息给我确认，包括 name, trigger, steps，确保 skill 名称简洁易懂
  - 如果同意创建 skill，生成 md 文件，文件名包含 skill 名称，内容为 skill 信息，文件格式为 markdown
    - 文件内容为：
      - skill 名称
      - 触发条件
      - 执行步骤
    - 文件示例：
        name: 修复 import 错误
        trigger: 出现 ModuleNotFoundError / ImportError

        steps:
        1. 检查依赖是否存在
        2. 检查路径是否正确
        3. 判断是否环境限制
        4. 提供替代方案
  - 放到 ./skills/ 目录下
  - 创建 skill 前：检查 ./skills/ 是否已有类似 trigger，若高度相似，则优先复用或更新已有 skill
  - 维护一个 skills.csv 文件，记录所有 skill 名称、 trigger、执行次数，在 skill 创建、更新、被调用时更新该文件
