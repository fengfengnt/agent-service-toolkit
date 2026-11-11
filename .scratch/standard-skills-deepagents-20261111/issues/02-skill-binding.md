# 标准 Skill 的导入绑定与权限筛选

Labels: wayfinder:grilling
Type: grilling
Status: resolved
Assignee: 当前对话（标准 Skill 与 DeepAgents 接入设计）
Blocked by:

## Question

导入的标准 Skill 如何绑定到业务节点及自定义 subagent；同一节点有多个 Skill 时，如何按可信身份权限限定可见集合，再按适用说明选择并组合使用？

## Context

- 用户已提出：外部导入标准 Skill 后，由 Codex 指定业务节点使用；同一节点可有多个 Skill，根据角色、权限和适用说明使用。
- 已确认的职责边界见[DeepAgents 与主子 Agent 的执行职责](01-execution-authority.md)。
- 待讨论建议：节点及 subagent 显式绑定候选 Skill，程序按可信身份与权限过滤后，再让模型按说明选择并按需读取正文、引用资源。
- 本票讨论的场景：用户同一轮既咨询采购问题又准备留资时，主业务 Agent 是否可同时使用多个已授权且适用的 Skill，以及同一草稿的流程推进归属。
- 导入资源的兼容检查和脚本执行范围尚未确定，不因同意上述建议而视为批准。

## Answer

2026-11-11，用户对“同一轮组合使用多份已授权 Skill，同时由当前留资 Skill 统一推进该草稿的引导流程”明确回答“同意”。

- 延续用户最初指定的方式：由 Codex 将标准 Skill 绑定到指定节点；自定义 subagent 也显式配置其候选 Skill。
- 候选技能先经过可信身份、角色和权限筛选，再按名称与 description 选择，按需加载正文及引用资源；技能说明不是授权来源。
- 同一轮允许组合使用多份适用且已授权的 Skill，例如资料安全答疑和采购留资。
- 同一份草稿由当前留资 Skill 推进，其他技能可提供辅助，不能各自发起相互独立的留资流程。
- 原问题中外部依赖、脚本和指令冲突部分已分别拆到[外部 Skill 的依赖与脚本启用](05-import-capabilities.md)和[多份 Skill 的指令冲突处理](06-skill-conflicts.md)，不将其记为已获同意。
