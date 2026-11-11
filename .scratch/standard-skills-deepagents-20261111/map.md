# 标准 Skill 与 DeepAgents 业务节点接入

Labels: wayfinder:map

## Destination

形成在现有 LangGraph 业务节点内接入 DeepAgents 的设计，支持标准 Skill 导入、节点绑定、按身份权限筛选及后续 subagent 使用；以现有字段自由组合的留资场景明确迁移路线，供后续制定实施计划。

## Notes

- 使用 superpowers:brainstorming、mattpocock-skills:wayfinder、grilling、domain-modeling；使用本地 Markdown tracker。
- 用户已选择：业务节点内接入 DeepAgents，便于后续扩展 subagent；不采用自行实现完整 Skill 执行器的路线。
- 用户已限定：首阶段自由组合系统已有字段；Skill 更新可配合后端接口和前端界面一起修改发布，不要求所有变化仅编辑 Markdown。
- 用户补充确认：后续实施可以更新 langchain、langgraph 及必要关联依赖；当前设计阶段仍不安装或修改依赖。
- 标准 Skill 采用 Agent Skills 格式。业务角色、可信身份权限、Skill、LangGraph 节点及 subagent 分别讨论，不将说明文字当成授权。
- 三方向留资实现正在另一个聊天推进；该已批准设计与实施是本次迁移基线。保留旧草稿字段要求、后续轮次更新交互规范、在途动作和提交固定原请求的约定。
- 关联聊天：[规划Agent 实现方案](thread://01a18a8c-d9c9-7320-986b-0083f7718ffd?hostId=local)，本聊天已通过 read_thread 读取其近期状态；该聊天所报测试结果不等同于本聊天复验。
- 官方核对来源：[Skill 支持](https://docs.langchain.com/oss/python/deepagents/skills)、[subagent](https://docs.langchain.com/oss/python/deepagents/subagents)、[Agent Skills 规范](https://agentskills.io/specification)。自定义 subagent 的 skills 需显式配置；工具及业务权限另行核验。
- 当前仅讨论和记录设计；不修改产品代码、安装依赖、迁移、部署、调用真实模型或发送短信。
- 调研与审阅仅写本地图下的研究记录及设计文档，复用现有 checkout，避免与已提交留资基线及其他工作发生分支切换或推送干扰。
- 已完成[定稿设计](../../docs/superpowers/specs/2026-11-11-standard-skills-deepagents-design.md)及[独立审阅](builder-review.md)；用户以“定稿”整体批准，接续制定实施计划，不开始编码。

## Decisions so far

- [DeepAgents 与主子 Agent 的执行职责](issues/01-execution-authority.md)：首版 subagent 只返回结果，主业务 Agent 统一通过工具保存和提交；以后按需显式开放特定 subagent 的写入权限。
- [标准 Skill 的导入绑定与权限筛选](issues/02-skill-binding.md)：明确绑定并筛选权限后按说明选择；一轮可组合多份 Skill，由当前留资 Skill 统一推进同一草稿。
- [留资组合调整后的执行状态与版本兼容](issues/03-state-and-version.md)：保留已有资料与完成记录，重新判断新版待办；旧草稿字段要求不变，在途动作及提交保持原请求。
- [外部 Skill 的依赖与脚本启用](issues/05-import-capabilities.md)：导入后由 Codex 检查并绑定，执行能力通过授权工具接入；缺少必要能力的 Skill 适配后再启用。
- [多份 Skill 的指令冲突处理](issues/06-skill-conflicts.md)：系统约束有效，当前留资 Skill 推进流程；配置冲突由维护者修正，运行中无法判定时暂停受影响动作并保留草稿。
- [首版迁移与 subagent 接入范围](issues/04-migration-scope.md)：四 Agent 接入、二十一份 Skill 标准化、必要前后端适配及只读 subagent 配置验证；设计包含 workflow 精简。
- [DeepAgents 官方能力与现有运行时的接入约束](issues/07-deepagents-compatibility.md)：固定候选与最小升级、只读 child 的工具／状态边界、独立推理线程及业务回执恢复均已核对；实际安装与集成未验证。
- [标准 Skill 接入与 workflow 精简设计整体确认](issues/08-design-approval.md)：用户已定稿，后续制定实施计划；未授权产品编码、安装、迁移或部署。

## Not yet specified

本地图的首版设计决策已全部关闭，完整设计已定稿。后续开发拆解见[九项实施计划](../../docs/superpowers/plans/2026-11-11-standard-skills-deepagents.md)，不继续在此创建实现任务；当前尚未开始实施。

## Out of scope

- 本轮实施、安装依赖、数据迁移、生产发布及真实模型验收。
- Skill 自行创造新业务字段、绕过后端校验或因描述文字获得权限。
- 首版通用脚本执行环境及其隔离方案；本版需要执行的能力通过授权业务工具适配。
- 首版新增具体生产子任务、跨轮长任务及子任务专用前端进度交互；已有流程的等待、取消和真实状态继续保留。
