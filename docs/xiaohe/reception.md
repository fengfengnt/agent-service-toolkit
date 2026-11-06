# 小鹤接待与独立专业 Agent

新任务默认通过四个独立 LangGraph 执行：接待负责理解服务方向并交接；采购、社群组织、供应商各自持续接待和处理业务。Skill 是专业 Agent 使用的受控资料，不再代替 Agent。实现使用现有 LangChain／LangGraph，没有引入 DeepAgents。

## 流程和提示词位置

| Agent | 实际流程定义 | 提示词 |
| --- | --- | --- |
| 小鹤接待 | `resources/xiaohe/workflows/reception.yaml` | `resources/xiaohe/prompts/reception-{intent,answer}.md` |
| 采购 | `resources/xiaohe/workflows/buyer.yaml` | `resources/xiaohe/prompts/buyer-{intent,answer}.md` |
| 社群组织 | `resources/xiaohe/workflows/organization.yaml` | `resources/xiaohe/prompts/organization-{intent,answer}.md` |
| 供应商 | `resources/xiaohe/workflows/supplier.yaml` | `resources/xiaohe/prompts/supplier-{intent,answer}.md` |

YAML 是实际运行定义。`schema_version` 是 DSL 格式版本，`workflow_version` 是人工可读版本；`agent_id` 固定角色；`entry` 指向起点。`prompts` 引用角色提示词，`skills` 列出本方向允许的资料。`rounds` 按顺序定义采集轮次、名称及字段，`required_fields` 定义流程的最终必填项。正式业务接口另有不能被 YAML 降低的最低要求。

`nodes` 支持六种节点：`understand` 调用 LLM 得到结构化理解；`decide` 由代码校验后选择分支；`answer` 结合受权上下文与冻结材料生成答复；`operation` 调用固定允许的业务操作；`handoff` 交给允许的目标 Agent；`result` 生成结果并决定是否继续等待。节点 `name` 用于阅读；`next`、`routes`、`fallback` 表达执行关系。修改时同时检查分支目标和节点名称，不加入任意 Python、表达式、工具名或 URL。

Pydantic 在 `src/xiaohe/workflows/models.py` 定义格式；`loader.py` 安全加载文件、提示词及资料并生成摘要；`compiler.py` 校验节点、可达性、循环和业务守卫，然后编译 LangGraph。启动时加载四份图，非法定义阻止启动，不静默采用旧文件。

## 修改与恢复

本期修改文件后需重启服务，没有热更新、编辑界面或导出功能。每次加载形成不可变 bundle，数据库保存四份定义、提示词和资料快照。新任务绑定当前 bundle；既有 run、等待输入、重试及草稿继续使用其绑定版本，不能在恢复时换成最新文件。缺失或校验失败的旧定义阻止恢复，不重新猜测。旧无绑定任务走兼容路径；旧草稿通过原规则摘要绑定最早匹配的快照，不将其解释为当前新规则。

新服务返回 `DraftView.fieldLayout`，仅含绑定版本的轮次与必填信息，门户据此显示表单。没有该字段的旧服务保留原布局兼容显示。提示词和整份流程不传给门户。复用正式资料时，`fields`里可能保留新布局不再采集的旧值；汇总会额外只读展示并明确说明它们将一并保留提交，不静默删除。如需修改，先取消提交并向服务方核对。

## 语义与代码的分工

意图、表达、否定、条件、指代、多意图与字段候选由 LLM 结构化理解，没有关键词猜测回退。字段候选必须有本轮原文证据，字段类型、枚举和数值区间规范化由代码校验。表达不清时澄清；模型超时、非法结果或不可用时提示重试，不据此推进方向或草稿。

代码控制身份、系统与租户范围、当前任务所有权、草稿版本、必填、当前汇总确认及幂等。按钮同样走这些守卫。明确自然语言可以确认已展示的当前汇总；单独“好的”、否定、条件或修改加确认不能直接提交。后台结果不确定时保存原提交键并查询，不另造一次申请。

专业答复允许标注一般建议；公司报价、政策、案例、效果承诺必须有已确认资料，否则说明待确认。模型不能声明认证或提交成功。只有真实业务回执才能形成成功状态。

## 入口与宿主边界

原消息／动作入口保留；专业 Agent 还有 `POST /ai-api/v1/agents/{agent_id}/conversations/{conversation_id}/messages` 和对应 `/actions`。它们使用现有认证、CSRF、系统范围与消息／动作协议，不接受浏览器自报的可信身份或租户。专业动作入口必须对应当前持久 Agent，不能借入口切换来确认别的草稿。

`Actor`／`TrustedSystem` 与 `src/xiaohe/reception/business_client.py` 的 `BusinessBackend` 的认证／业务边界允许其他宿主提供自己的受信身份与业务后台；当前门户只是默认装配。四个图目前共用执行外壳和部署，未来独立部署的装配位置已注释预留，本期没有单独包、远程调用框架或新部署服务。

交接仅包含必要摘要、近期可见文本与目标方向自己的草稿。返回原方向恢复原草稿；认证凭据、内部推理、私有附件原文及其他方向整份草稿不进入交接。

验收证据见 [本次记录](../reception/2026-11-06-independent-agents-acceptance.md)。有限模型评测、受控业务回归、联合真实服务及浏览器结果应分别读取，不能相互替代。
