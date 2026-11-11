# DeepAgents 与主子 Agent 的执行职责

Labels: wayfinder:grilling
Type: grilling
Status: resolved
Assignee: 当前对话（标准 Skill 与 DeepAgents 接入设计）
Blocked by:

## Question

DeepAgents 在现有 LangGraph 节点内可以决定和执行哪些动作；主 Agent 与 subagent 分别能否保存草稿、提交申请及更新业务状态，如何复用现有权限、幂等和真实结果机制？

## Answer

2026-11-11，用户确认：首版 subagent 只返回处理结果，由主业务 Agent 统一调用工具保存和提交；后续按需给特定 subagent 开放写入权限。继续讨论设计，暂时不要写代码。

- DeepAgents 承担业务节点内的技能使用与任务处理；主业务 Agent 通过受控工具保存草稿和提交申请。
- 首版 subagent 返回处理结果，不直接保存草稿或正式提交；主业务 Agent 接收结果后仍由现有工具与服务端校验落实权限、字段要求、幂等及真实状态。
- 将来给特定 subagent 开放写入是显式扩展，不因使用某份 Skill 或被父 Agent 调用就自动获得写入权限。
- 本决策不决定首版必须落地哪些具体子任务；相关范围由后续决策票确定。
