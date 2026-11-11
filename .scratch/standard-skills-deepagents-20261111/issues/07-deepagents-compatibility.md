# DeepAgents 官方能力与现有运行时的接入约束

Labels: wayfinder:research
Type: research
Status: resolved
Assignee: 当前对话的研究子 Agent
Blocked by: 04

## Question

核对 DeepAgents 的标准 Skill、自定义 subagent、能力限制、版本兼容以及 checkpoint/工具副作用恢复边界，明确现有 LangGraph 业务节点接入时必须处理的约束；只调查和写文档，不安装或运行产品代码。

## Answer

官方能力研究已完成，证据集中保存在[兼容性研究](../deepagents-research.md)。

- `deepagents==0.7.32` 是符合当前冷却规则的固定候选；需要更新 core 声明及 LangChain／LangSmith 锁，现有 LangGraph 锁未发现直接最低版本冲突，完整依赖尚未求解。
- 默认 child 工具及自定义 state 传递不是只读隔离边界；首版需显式工具限制、关闭通用委派、CompiledSubAgent 输入输出投影。
- 独立根推理图使用独立 thread_id，不能依赖手填 checkpoint_ns；业务 Command／Run 和工具回执继续是业务权威。
- checkpoint 不保证外部副作用恰好一次；稳定业务动作、写栅栏和未知结果核对继续保留。
- 未安装依赖、调用模型或运行恢复测试；研究结论不代替实施验收。
