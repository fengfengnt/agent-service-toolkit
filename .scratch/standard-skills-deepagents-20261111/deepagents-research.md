# 标准 Skill 与 DeepAgents 兼容性研究

研究日期：2026-11-11；范围：4 个业务 Agent、21 个标准 Skill（3 留资、18 答疑），外层保留 LangGraph，业务节点内使用 DeepAgents。仅检查官方资料、固定源码和本地依赖声明；未安装依赖、改锁、调用模型或启动服务。

## 结论与版本选择

- **事实：**PyPI 当前最新版为 `deepagents==0.7.35`，发布于 2026-11-09；它要求 `langchain>=1.4.9`、`langchain-core>=1.7.6`、`langchain-anthropic>=1.7.10`、`langchain-google-genai>=4.4.3`、`langsmith>=0.14.17`，Python `>=3.11,<4`。[版本元数据](https://pypi.org/pypi/deepagents/0.7.35/json)
- **事实：**仓库 `pyproject.toml` 的 `exclude-newer="14 days"` 仍生效。`0.7.33`、`0.7.34` 于 11-05 发布，`0.7.35` 于 11-09 发布，均未满 14 天；`0.7.32` 于 10-23 发布，是已核查发布序列中满足该规则的最近版本。[0.7.32 元数据](https://pypi.org/pypi/deepagents/0.7.32/json)、[0.7.33 元数据](https://pypi.org/pypi/deepagents/0.7.33/json)、[0.7.34 元数据](https://pypi.org/pypi/deepagents/0.7.34/json)
- **建议：**用户已允许升级 LangChain/LangGraph；后续先以 `deepagents==0.7.32` 固定候选实现和验收，保持 cooldown，解析完整依赖后再决定最终锁定。不要把当前滚动文档当作任意旧版的 API 保证。
- **事实：**本报告主要源码基线为 `0.7.32` 的固定 commit `e85c6b98087861512d2c5240305e16cfac68a746`；另核对 `0.7.30` 的 `42adc1bca1147715ac58fad49bc74c12fa63bb84`，下列默认工具、子 Agent 隔离和 GP 禁用 API 两版均存在。[0.7.32 标签](https://github.com/langchain-ai/deepagents/tree/deepagents%3D%3D0.7.32)、[0.7.30 标签](https://github.com/langchain-ai/deepagents/tree/deepagents%3D%3D0.7.30)

## 本仓库依赖：明确冲突与未验证项

| 项目 | 本地声明 / 当前 uv.lock | 0.7.32 最低要求 | 判断 |
| --- | --- | --- | --- |
| Python | `>=3.12,<3.15` | `>=3.11,<4` | 范围相容 |
| langchain | `~=1.4.6` / `1.4.6` | `>=1.4.7,<2` | 锁内版本需升；声明允许 |
| langchain-core | `~=1.6.12` / `1.6.12` | `>=1.7.0,<2` | **声明无交集，必须调整** |
| langchain-anthropic | `~=1.7.6` / `1.7.6` | `>=1.7.6,<2` | 直接约束相容 |
| langchain-google-genai | `~=4.4.2` / `4.4.2` | `>=4.4.2,<5` | 直接约束相容 |
| langsmith | `~=0.14.11` / `0.14.11` | `>=0.14.12` | 锁内版本需升；声明允许 |
| langgraph | `~=1.2.7` / `1.2.16` | 由 LangChain 间接约束 | 未见明显冲突 |
| langgraph-checkpoint | 间接依赖 / `4.3.0` | 间接约束 | 未完整解析 |

直接约束来源：[DeepAgents 0.7.32 PyPI](https://pypi.org/pypi/deepagents/0.7.32/json)。其最低 `langchain==1.4.7` 要求 `langgraph>=1.2.12,<1.3`，当前 `1.2.16` 满足；并非引入 DeepAgents 就必须升级 LangGraph。[LangChain 1.4.7 PyPI](https://pypi.org/pypi/langchain/1.4.7/json)

**推断：**最小升级是 core 的声明和锁、LangChain/ LangSmith 的锁，加 DeepAgents 及新增传递依赖；其他 provider、checkpoint 插件仍需实际 resolver 和离线测试验证。`0.7.30` 的最低依赖与当前上述锁定版本一致，可作为能力基线备选，但不据此承诺完整锁一定可解。[0.7.30 PyPI](https://pypi.org/pypi/deepagents/0.7.30/json)

## 原生 API、继承与只读子 Agent

**事实：**`create_deep_agent` 返回已编译 LangGraph，支持 `skills`、`subagents`、`backend`、`middleware`、`permissions`、`interrupt_on`、`response_format`、`context_schema`、`checkpointer`。`SubAgent` 声明可指定 `name/description/system_prompt/model/tools/middleware/skills/permissions`；`CompiledSubAgent` 可接预编译图。[固定 graph.py](https://github.com/langchain-ai/deepagents/blob/e85c6b98087861512d2c5240305e16cfac68a746/libs/deepagents/deepagents/graph.py)

**事实：**以下边界不能依靠提示词：

- `tools=` 是追加业务工具，不会移除默认文件工具。声明式子 Agent 未填 `tools` 时继承主 Agent 业务工具，填 `tools=[]` 才明确不继承；它仍默认附带文件中间件。[固定 graph.py](https://github.com/langchain-ai/deepagents/blob/e85c6b98087861512d2c5240305e16cfac68a746/libs/deepagents/deepagents/graph.py)
- `general-purpose` 默认自动添加并继承主 Agent skills；自定义 isolated 子 Agent 不自动继承 skills，必须单独指定。配置 `HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False))` 并通过 `register_harness_profile` 注册到实际模型所用 profile，可禁用 GP，仍保留显式注册的自定义子 Agent。[固定 profile 源码](https://github.com/langchain-ai/deepagents/blob/e85c6b98087861512d2c5240305e16cfac68a746/libs/deepagents/deepagents/profiles/harness/harness_profiles.py)
- `FilesystemMiddleware(backend=backend, tools=["ls", "read_file", "glob", "grep"])` 可替换同名默认中间件，移除模型可调用的 `write_file/edit_file/delete/execute`；此名单必须包含 `read_file`。主 Agent 和声明式 child 都需配置，不会因主 Agent 替换而自动继承。[固定 filesystem.py](https://github.com/langchain-ai/deepagents/blob/e85c6b98087861512d2c5240305e16cfac68a746/libs/deepagents/deepagents/middleware/filesystem.py)
- isolated 只代表对话隔离：原生 `task` 仍传入非排除自定义 state，并把 child 非排除变更合并回父 state；`files` 也不能假定完全隔离。child 不是天然“只返回文字”的安全边界。[固定 subagents.py](https://github.com/langchain-ai/deepagents/blob/e85c6b98087861512d2c5240305e16cfac68a746/libs/deepagents/deepagents/middleware/subagents.py)

**建议：**首版 child 用 `CompiledSubAgent` 包装最小只读 Agent/图，显式投影输入、只返回 `messages` 或 `structured_response`；工具仅查询授权业务数据，杜绝保存/提交工具和返回业务 `Command.update`。业务写权限仅主 Agent 受控工具持有，工具逐次检查服务端用户、角色和 write fence。禁用 shell 同时落实在文件工具白名单及无执行能力 backend，不接 Sandbox/LocalShell 执行后端。依赖 `_permissions` 等私有入参的方案暂不采用。

**事实：**声明式 child 默认继承主 `interrupt_on`，child 自己指定时整体覆盖；`CompiledSubAgent` 不继承此配置，需在其内部显式设置 HITL。公共导出路径包括 `from deepagents import create_deep_agent, CompiledSubAgent, HarnessProfile, GeneralPurposeSubagentProfile, register_harness_profile`；`SkillsMiddleware` 从 `deepagents.middleware.skills` 导入。[固定公共导出](https://github.com/langchain-ai/deepagents/blob/e85c6b98087861512d2c5240305e16cfac68a746/libs/deepagents/deepagents/__init__.py)、[固定 skills.py](https://github.com/langchain-ai/deepagents/blob/e85c6b98087861512d2c5240305e16cfac68a746/libs/deepagents/deepagents/middleware/skills.py)

## 标准 Skill 可移植格式及授权目录

**事实：**标准单元是 `skill-name/SKILL.md`，包含 YAML `name`、`description` 和 Markdown 正文；可带 `scripts/`、`references/`、`assets/`。名称须符合规范且匹配目录。`metadata` 可放字符串版本；`allowed-tools` 仍属实验字段，各客户端支持不同，不应作为后端授权依据。相对资源路径可移植，绝对磁盘路径与项目 Python 模块路径不可移植。[Agent Skills 规范](https://agentskills.io/specification)

**事实：**DeepAgents `skills=["/skills/role/"]` 的 source 指向包含技能子目录的父目录，不能直接指向某个技能根；使用 POSIX 路径、按 backend root 解析。同名技能由后面的 source 覆盖前面。运行时按 name/description 暴露目录、按需读取正文，允许单轮读取多个 Skill。skill 元数据会缓存在 thread state，不能把刷新 active 目录视为已经固定了在途内容。[官方 Skills 文档](https://docs.langchain.com/oss/python/deepagents/skills)、[固定加载实现](https://github.com/langchain-ai/deepagents/blob/e85c6b98087861512d2c5240305e16cfac68a746/libs/deepagents/deepagents/middleware/skills.py)

**事实：**`metadata.include_tools` 是 DeepAgents 扩展，引用应用预先提供或 resolver 返回的工具名称；它不是标准 Skill 的 Python import 声明。技能正文引用脚本并不等于平台自动执行脚本；执行仍依赖 shell 等工具能力。[固定 skills.py](https://github.com/langchain-ai/deepagents/blob/e85c6b98087861512d2c5240305e16cfac68a746/libs/deepagents/deepagents/middleware/skills.py)

**建议：**以标准 Skill 文件为可迁移内容，工具名称由服务端白名单绑定，不加载 Skill 提供的任意模块/脚本。`prepare` 先按 Agent 显式绑定、用户授权、启用版本筛选，再生成仅含允许内容的 `/skills/<bundle_hash>/<role>/<skill-name>/` 快照；backend 不挂整个技能仓库，避免 `read_file` 绕过目录索引读取其他角色技能。只显示过滤后的 description 不构成访问隔离。

**建议：**发布时对排序后的相对路径及文件原始字节计算 bundle hash，覆盖正文和所有引用文件；Command 记录 bundle hash、binding revision、tool policy revision，恢复复用同一不可变快照和工具契约。旧草稿字段由草稿 schema 固定；新轮可换引导版本，在途 Command 不换。hash/冻结/授权均是本项目设计，并非 Agent Skills 标准自动提供。

## 外层 LangGraph、独立推理 checkpoint 与业务权威

**本地事实：**`src/xiaohe/graph.py` 的 `respond` 调用 `run_agent_turn`；`src/xiaohe/workflows/compiler.py` 将业务图编译为 `checkpointer=False`。CodeGraph 未收录这些新增路径，已回退直接读取。当前不能假设这层业务图已经提供原生嵌套 checkpoint 恢复。

**事实：**真正嵌套的子图可继承父 checkpointer；`checkpointer=None` 为每次调用隔离、支持 interrupt，`False` 不支持恢复，`True` 跨调用累积且同一实例并发会产生 namespace 冲突。函数内调用的子图 interrupt 恢复时，父调用节点及子图中断节点均从头执行。[官方子图指南](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)、[官方 Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

**关键事实：**当前锁定 LangGraph `1.2.16` 的 `_loop.py` 在非 nested 根调用遇到非空 `checkpoint_ns` 时，会清空 namespace 和 checkpoint id。因此“独立根 DeepAgent + 手填 checkpoint_ns”不能当作可靠隔离方案。[固定 LangGraph 1.2.16 源码](https://github.com/langchain-ai/langgraph/blob/1.2.16/libs/langgraph/langgraph/pregel/_loop.py#L379)

**建议：**允许复用同一个 Postgres saver 实例，但独立推理图用服务端派生的独立 `thread_id`（如 Command ID + 业务 Agent ID），根 namespace 保持默认；保存推理 thread 与 Command 的关系。不要复制父配置中的内部 task/checkpoint 字段。或者完整采用原生嵌套恢复模式，勿混用两种所有权。异步调用接兼容的 AsyncPostgresSaver；saver 生命周期由现有服务管理。

**推断与限制：**独立推理 checkpoint 只记录消息、模型/工具循环进度，不接管 Command/Run/草稿/提交流程权威。业务完成状态只由受控工具与 `finalize` 根据数据库事实确认；child 建议、模型 JSON、checkpoint 结果都不能直接把 Run 标成提交成功。同一业务 Command 重试必须命中同一个推理 thread，且要防止两个 worker 同时推进它。

**事实：**LangGraph 重试是至少一次节点执行；checkpoint 不保证外部副作用恰好一次。调用成功后、写 checkpoint 前崩溃，仍可能重做副作用。应用应使用稳定幂等键；没有收到外部确认时结果是未知，不能当作未执行后盲目重发。[官方故障恢复指南](https://docs.langchain.com/oss/python/langgraph/fault-tolerance#retry-boundaries-and-idempotency)

**建议：**复用稳定 effect ledger，业务副作用键依据业务 operation/Command（以及有意义的槽位）生成，不能依据新的模型 tool_call_id、attempt 或本次 checkpoint id。写入前校验当前 ownership epoch；外部服务支持同一键去重，或按业务 reference 查询对账。不确定结果进入已有恢复/人工处理流程。同一 Postgres 并不让 saver 事务与业务事务自动原子化。

## prepare → deepagent → finalize 的兼容验收点

1. `prepare`：认证/授权与显式 binding 预过滤；冻结 bundle 与工具策略；建立 Command→推理 thread 映射。重入读取已冻结记录，不再读取 active 版本。
2. `deepagent`：仅消费业务只读快照和授权工具；将 child 结果作为建议；按首次调用、故障继续（`None`）和 interrupt 恢复（LangGraph `Command(resume=...)`）区分输入，恢复不能重复追加原始用户消息。
3. `finalize`：再次校验 fence/业务版本，读取工具与 ledger 结果，生成现有 `AgentOutcome`；仅成功完成的推理输出才进入此步骤，未完成/interrupt 不得当普通答复完成 Command。
4. **独立图限制：**不能假定内层 interrupt 会自动恢复现有外层业务等待；适配层须持久化映射、将内层暂停转成当前业务等待并在下一次正确恢复。首版若继续由外层统一收集留资和确认，应限制内层 HITL 使用范围，避免两个等待状态机争夺用户输入。
5. 必测但本轮未执行：禁止 child 写业务/读越权 Skill/执行脚本；两技能组合；同 Command 重试及 worker 竞争；副作用后崩溃；暂停后进程重启；发布新 bundle 后旧 Command 恢复仍用旧内容；取消/fence 失效时不再写入；现有 SSE/AG-UI 不泄漏 child 内部提示词。

未完成依赖求解、fake-model 集成、真实 checkpoint/interrupt/业务副作用测试；研究证明有实现路径，不代表已满足运行验收。升级 LangGraph 后须针对最终固定版本复核 namespace/恢复语义，不能沿用本文对 1.2.16 的内部实现断言。
