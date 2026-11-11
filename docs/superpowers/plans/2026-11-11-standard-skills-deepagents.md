# 小鹤标准 Skill、DeepAgents 与 workflow 精简 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 四个业务 Agent 在精简后的 LangGraph 节点中使用 DeepAgents，迁移二十一份标准 Skill，支持可接续的 form 组合和只读 subagent。

**Architecture:** 保留客服外壳和正式业务后端，业务图固定为 `prepare_context → business_agent → finalize`。标准包、绑定和工具契约按 Command 固定；草稿字段仍按原规则固定。DeepAgents 使用独立推理线程，主 Agent 的写工具复用现有业务校验及回执，子任务只返回结果。

**Tech Stack:** Python、DeepAgents 固定候选 `0.7.32`、允许兼容升级的 LangChain／LangGraph、Pydantic、PyYAML、SQLAlchemy、Alembic、PostgreSQL、pytest；门户沿用 Vue／Quasar／Zod／Vitest／Playwright。

**Spec:** [已批准设计](../specs/2026-11-11-standard-skills-deepagents-design.md)；[固定版本研究](../../../.scratch/standard-skills-deepagents-20261111/deepagents-research.md)。执行者先读两份文档，再读本计划。

## Global Constraints

- 2026-11-11 用户批准整体设计并选择“本对话逐项实施”，授权按本计划开发、安装兼容依赖与隔离测试；不包含现有环境迁移、部署或真实模型／短信／正式业务写入。
- 复用合适的现有 checkout；执行前检查分支、staged／unstaged 和迁移 head，保留 VERSION、配置示例及其他任务的工作，不使用 `git add .`。
- 固定候选 `deepagents==0.7.32`；最低依赖 `langchain>=1.4.7`、`langchain-core>=1.7.0`、`langsmith>=0.14.12`，保留 `exclude-newer = "14 days"`。可按验证需要更新 LangGraph，不能把主线文档当成固定版保证。
- 四个业务角色及现有十八份答疑、三份留资纳入首版；接待不自动获得专业业务权限；现有字段、类型、必填和私有材料边界不扩大。
- 节点及 subagent 明确绑定，权限先于 Skill 发现和实际读取；活动留资 Skill 确定性载入，其他技能按说明按需组合。
- 首版 child 只读、只返回校验结果，无写业务工具、文件写入、Shell、再委派或内层 HITL；主 Agent 通过受控工具保存和提交。
- 旧草稿保留字段规则与资料，新轮次可换已发布引导；执行中动作、重试及出站提交保留原快照、载荷和业务键。
- “好的／可以”按语境判断；认证不是提交确认；资料齐备可直接提交；按钮继续校验实际汇总；同轮修改提交使用最终校验资料。
- `legacy`、`dsl-v1` 和原快照继续可恢复；不得放松旧 DSL 编译器或将 native bundle 塞入旧 `WorkflowBundle` 解析器。
- 关键提示、可信卡片、面板控制与受理结果来自服务端真实状态。不得把 child、模型自由文本或 checkpoint 当成业务回执。
- 门户由其维护者按配套交接实现；本仓库不直接覆盖门户或 Java 工作区，不通过工具自动向其他聊天发送任务。
- 假模型及专用临时库用于确定性验收；真实模型、短信、正式业务写入及任何环境发布另行授权。

## Review Focus

1. 同一 Command 重试时模型生成新的 tool_call_id，或同轮先后保存两次不同修改：相同业务副作用不重复，不同合法修改不被误吞；Task 5 固定。
2. 标成 isolated 的 child 返回 `files`、自定义业务 state 或伪造 `Command.update`：结果不能覆盖主状态、获得业务写权限；Task 4 固定。
3. 旧 `dsl-v1` Run 的新 Command 采用 native，而之前 Command 正在核对提交：只对新 Command 选版，旧请求不换引擎／键；Task 3、6、9 固定。
4. 用户有未保存输入时 Skill 更新 form 组合或另一窗口修改 revision：保留输入，过期操作显式冲突，不静默清空／套用新组合；Task 7、8 固定。
5. 工具写入成功后进程／模型失败，旧 worker 尚未停止而新 attempt 开始：回执可恢复、旧 fence 禁止新写、新内部 checkpoint 随生命周期清理；Task 6、9 固定。

---

## 文件边界与依赖顺序

本计划是一条前后依赖的交付链，前端以接口契约为输入，可独立交接及验收；不为共用状态拆出平行的业务系统。

| 位置 | 责任 |
| --- | --- |
| `pyproject.toml`、`uv.lock` | 固定候选及兼容依赖；不修改部署版本。 |
| `src/xiaohe/deep_runtime/models.py` | 本计划新增的严格数据类型；沿用现有 Actor、WriteFence、AgentId、FieldPolicy、AnswerDraft。 |
| `src/xiaohe/deep_runtime/bundles.py` | 标准包、宿主绑定及不可变资源的加载、hash、持久化；不实现另一套 Skill 执行器。 |
| `src/xiaohe/deep_runtime/factory.py` | 原生 DeepAgents 装配、只读技能文件投影、CompiledSubAgent 输入／输出边界；不新增自定义文件系统框架。 |
| `src/xiaohe/deep_runtime/tools.py` | 现有业务服务的授权适配和稳定副作用回执；不复制角色流程。 |
| `src/xiaohe/deep_runtime/runtime.py` | 三节点图、本轮准备／执行／收尾、内部 checkpoint 驱动与线程记录。 |
| `src/xiaohe/deep_runtime/forms.py` | form 组合、引导完成依据及 v2 投影；不改变旧字段快照。 |
| `resources/xiaohe/skills/<role-prefixed-name>/`、`resources/xiaohe/deep-runtime.yaml` | 二十一份标准包及四角色宿主绑定。旧嵌套目录、catalog、lead-skills 只作兼容基线保留，不同时维护为新真源。 |
| `src/xiaohe/{models,admission,graph,main,settings,worker,expiry}.py`；`reception/{models,contracts,api,store,submission}.py` | 仅接入实际调用链、版本选择、进度及清理；保留旧行为。 |
| `migrations/versions/0014_deep_runtime.py` | 增量快照表、native Run 引用／约束、草稿引导信息；若 head 已变化则顺延编号，不覆盖。 |
| `tests/xiaohe/deep_runtime_support.py`；`test_deep_*.py` | 复用现有业务桩及临时数据库，补原生工具循环脚本模型；不修改生产 fake model 迎合测试。 |
| `docs/contracts/xiaohe-lead-policy-v2.md`；`docs/handoffs/2026-11-11-standard-skills-portal.md`；`docs/testing/xiaohe-deepagents.md` | 精确接口、门户任务及验证记录。 |

任务依赖：1 → 2 → 3；4 依赖 1—3；5 依赖 3；6 依赖 4—5；7 依赖 3、5—6；8 依赖 7；9 在后端独立检查可先执行，前后端组合部分等待 8 的回执。

## 执行与测试约定

命令默认从 toolkit 仓库根目录在 PowerShell 执行，使用 `.venv/Scripts/python.exe`。每项先用目标行为缺失得到红灯，再实现最小改动；连接失败不是红灯。首次运行已通过的依赖检查无需人为制造失败。

`pg` 测试必须显式提供专用可清空库的 `XIAOHE_TEST_DATABASE_URL`，库名以 `test_xiaohe_` 开头，禁止使用现有业务库 DSN；`pg_factory` 会迁移并清空测试表。没有专用库时保留未验项。

每项结束检查 `git diff --check`、相关测试和 staged diff，只提交该项明确列出的文件。本计划中的提交属于未来实施，不在规划阶段执行。

### Task 1：固定依赖与原生能力基线

**Files:** 修改 `pyproject.toml`、`uv.lock`；新增 `tests/xiaohe/deep_runtime_support.py`、`tests/xiaohe/test_deep_native.py`。

**Interfaces:** 测试用 `DeepScriptModel` 能按脚本返回真实 `AIMessage.tool_calls` 并记录收到的工具，不访问网络；`make_script_model(messages: list[AIMessage]) -> BaseChatModel`。生产代码继续用现有 `get_model`／`reception_model`。

- [x] **Step 1：写能力测试。** `test_native_skill_read_and_tool_loop` 实际调用固定版 `create_deep_agent`，断言先读取命中的 SKILL.md 再调用提供的测试工具，最终返回结果；`test_supported_native_exports` 核对 CompiledSubAgent、profile、文件中间件和异步 saver 的公开接口。

  该场景的调用轨迹及答复固定断言：`assert trace == ["read_file", "probe"]`；`assert answer == "probe-result"`。
- [x] **Step 2：记录红灯或既有能力。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_deep_native.py -q`；缺库或目标公开 API 不存在是可用红灯，记录实际原因。
- [x] **Step 3：更新兼容组合。** 加入固定候选，调整 core 声明及必要关联锁版本，运行 `uv lock`、`uv sync --frozen`；保持冷却规则。更换候选需先复核研究中默认工具／状态传递与恢复约束，不静默采用浮动 latest。
- [x] **Step 4：验证。** 重跑能力测试及 `tests/agents`；核对 `uv lock --check`。预期全 PASS，锁内版本满足约束；不把仅导入成功当成原生工具循环通过。
- [x] **Step 5：审阅并提交。** 仅依赖和能力测试，建议提交 `build: add verified DeepAgents dependency set`。

### Task 2：二十一份标准包与受限绑定

**Files:** 新增 `deep_runtime/{__init__,models,bundles}.py`、标准包目录、`resources/xiaohe/deep-runtime.yaml`、`tests/xiaohe/test_deep_bundles.py`；保留旧资源。

**Interfaces:** `load_runtime_bundle(resource_root: Path) -> RuntimeBundle`；`select_binding(bundle: RuntimeBundle, actor: Actor, agent_id: AgentId) -> RuntimeBinding`；`skill_files(bundle: RuntimeBundle, binding: RuntimeBinding) -> dict[str, dict]`。`SkillSpec` 表达标准 name／description、入口与资源引用；`RuntimeBundle.skills` 是以标准名称为键的 SkillSpec 映射，另含排序资源、宿主绑定、角色字段配置和交互模板，bundle_id 的内容 hash 覆盖全部资源与配置。`RuntimeBinding` 包含 agent_id、允许 Skill 名称、活动留资 Skill、工具名称及 child 绑定，未授权身份得到相应受限集合。

- [x] **Step 1：写失败测试。** `test_twenty_one_standard_packages` 断言 manifest 中恰有 21 个标准包、name 与目录一致、三方向和答疑内容保持；`test_denied_skill_absent_from_listing_and_read` 断言未授权包不在投影及直接读取结果中；参数化重复 name、路径越界、符号链接逃逸、缺引用／依赖、错误 YAML 和同职责冲突；`test_reference_bytes_change_hash` 断言引用内容变化也改变 hash。

  固定断言：`assert len(bundle.skills) == 21`；`assert changed.bundle_id != original.bundle_id`。角色来自可信上下文，缺少授权不得由业务方向名称推断。
- [x] **Step 2：验证红灯。** `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_deep_bundles.py -q`，预期目标加载器缺失。
- [x] **Step 3：实现加载与资源迁移。** 复用现有严格 YAML／资源大小限制。标准名使用 `buyer-lead-capture`、`buyer-security` 等唯一短横线名称；旧 dotted ID 用宿主兼容映射。三份 `FieldPolicy.skill_id` 保留既有内部 ID，不强改标准包格式。快照使用可持久化表示并对资源原字节求 hash；遇到首版不支持的必要资源编码或执行能力时明确不可用，禁止从 metadata import 任意模块。
- [x] **Step 4：验证绿灯及旧基线。** 重跑新测试和 `test_lead_skills.py`、`test_workflow_loader.py`；旧 bundle hash、字段基线及旧 catalog 路径保持。
- [x] **Step 5：审阅并提交。** 建议 `feat(xiaohe): add standard skill bundles and explicit bindings`。

### Task 3：不可变快照、Command 选版与数据库兼容

**Files:** 修改 `models.py`、`reception/models.py`、`admission.py`、`settings.py`、`deep_runtime/{models,bundles}.py`；新增 `0014_deep_runtime.py`、`tests/xiaohe/test_deep_storage.py`、`tests/xiaohe/test_deep_versions.py`；更新 `test_migrations.py`。

**Interfaces:** `persist_runtime_bundle(db: AsyncSession, bundle: RuntimeBundle) -> str`；`load_runtime_bundle_record(db: AsyncSession, bundle_id: str) -> RuntimeBundle`；`bind_deep_command(db: AsyncSession, run: Run, command: Command, bundle: RuntimeBundle) -> DeepExecution`。`DeepExecution` 固定 runtime=`deepagent-v1`、bundle_id、选版策略、各角色内部 thread 引用、工具 effects 与本轮结果；可信 Actor／WriteFence 保留在执行上下文，不能由模型覆盖。

- [x] **Step 1：写失败测试。** 验证快照不可修改／缺失不回退最新版；Run 的 legacy／dsl-v1 行仍合法；native 新 Run 合法且不会进入旧快照解析器；等待中的旧 Run 新 Command 可选新版，前一 Command 不换版。`test_existing_run_new_command_pins_new_bundle` 同时断言旧字段 policy_id 不变。

  选版固定断言：`assert old_execution.bundle_id == old_bundle.bundle_id`；`assert new_execution.bundle_id == new_bundle.bundle_id`；`assert draft.field_policy_id == original_field_policy_id`。
- [x] **Step 2：验证红灯。** `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_deep_storage.py tests/xiaohe/test_deep_versions.py -q`，目标是新模型／约束缺失，不能是测试库不可连接。
- [x] **Step 3：实现增量存储。** 在 models.py 新增 `DeepRuntimeBundleRecord`，映射独立 `xiaohe.deep_runtime_bundles` 内容快照表，避免旧 `WorkflowBundle.model_validate` 读取 native payload；给 Run 增加 nullable `deep_runtime_bundle_id` FK，扩展约束为 legacy 两引用皆空、dsl-v1 仅旧引用非空、deepagent-v1 仅新引用非空。草稿新增 nullable JSONB `guidance_progress`，旧值不回填、不改字段规则。
- [x] **Step 4：实现选版。** `Settings.deepagents_enabled=False`，alias `XIAOHE_DEEPAGENTS_ENABLED`，仅影响后续 Command 选择。新 native Run 绑定新列；旧等待 Run 不修改 runtime／原引用，新 Command 在 `payload.deepExecution` 固定 native 快照。旧 Command 无该记录则按原 Run 恢复；关闭开关也必须恢复已固定 native Command。原 `interaction_policy_id` 继续固定真实交互模板，native 不伪造旧 `leadWorkflowBundleId`。
- [x] **Step 5：验证并提交。** 运行新测试及 `test_migrations.py`、`test_lead_policy_storage.py`、`test_lead_policy_versions.py`；在临时库演练 upgrade，并验证存在新记录时 downgrade 明确拒绝。建议 `feat(xiaohe): persist native runtime snapshots per command`。

### Task 4：原生 Agent 装配及只读 child 边界

**Files:** 新增 `deep_runtime/factory.py`、`tests/xiaohe/test_deep_factory.py`、`tests/xiaohe/test_deep_children.py`；扩充 `deep_runtime/models.py`。

**Interfaces:** `build_business_agent(bundle: RuntimeBundle, execution: DeepExecution, binding: RuntimeBinding, *, model: BaseChatModel, tools: list[BaseTool], checkpointer: BaseCheckpointSaver) -> CompiledStateGraph`。`ChildTask` 只含授权任务文本和只读资料快照；`ChildResult` 只含 summary、候选资料和缺项建议；`build_readonly_child(bundle: RuntimeBundle, binding: RuntimeBinding, *, model: BaseChatModel) -> CompiledSubAgent`。`DeepTurnResult` 含校验后的 AnswerDraft 候选和等待／交接建议，不能含可信受理状态。

- [x] **Step 1：写失败测试。** 真实 native 图执行两 Skill 组合，活动留资 Skill 必定载入，其余正文未命中不读取。`test_child_cannot_merge_business_state` 让 child 尝试返回 files、自定义状态、Command.update 和伪造受理，断言被拒绝且父状态不变；写工具、文件写工具、execute、task 再委派实际不可调用。

  固定断言：`assert parent_business_state == before`；`assert not {"save_lead", "submit_lead", "write_file", "execute", "task"} & child_tool_names`。
- [x] **Step 2：验证红灯。** `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_deep_factory.py tests/xiaohe/test_deep_children.py -q`，预期装配边界缺失。
- [x] **Step 3：实现最小装配。** 复用原生 `StateBackend`，仅在首次输入放入 Task 2 产出的允许 Skill 文件；恢复不替换 files。显式配置只读 FilesystemMiddleware、关闭默认 general-purpose；通过公开 profile API 应用到实际使用的模型。不要添加自定义文件系统或整仓库挂载。
- [x] **Step 4：实现 child 投影。** CompiledSubAgent 外包装只接收 ChildTask，建立其自己的技能 files／只读工具集合，输出严格解析为 ChildResult，再转为允许结果；丢弃或拒绝任意业务 state 合并。业务工具的授权上下文不进入模型可改写的 state；无 child 内层 interrupt。
- [x] **Step 5：验证并提交。** 重跑上述测试与 Task 1 native 能力测试，预期全 PASS；建议 `feat(xiaohe): constrain DeepAgents and readonly subagents`。

### Task 5：授权业务工具与稳定副作用记录

**Files:** 新增 `deep_runtime/tools.py`、`deep_runtime/forms.py` 的组合核心、`tests/xiaohe/test_deep_tools.py`、`tests/xiaohe/test_deep_effects.py`，扩充 `deep_runtime/models.py`；按需对 `reception/{store,submission}.py`、`agents/nodes.py` 做小范围共用服务提取，保留全部调用者。

**Interfaces:** `make_business_tools(context: ToolContext) -> list[BaseTool]`；`execute_effect(context: ToolContext, operation: str, *, draft_id: UUID | None, expected_revision: int | None, payload: dict) -> ToolResult`。`ToolContext` 包含服务端 factory、Actor、WriteFence、Command ID、原用户消息、当前绑定、字段／交互快照及 business_client；不作为 LLM 参数。`ToolResult` 含确定性结果、当前 revision、可空 receipt_id 回执引用及可公开错误。

工具名称固定为 `read_lead`、`prepare_lead`、`compose_form`、`save_lead`、`submit_lead`、`query_progress`、`request_handoff`，按绑定／授权只返回子集。prepare_lead 复用现有认证、唯一／多草稿、新建／恢复行为。`compose_form(context: ToolContext, field_groups: list[list[str]], *, prompt: str) -> FormViewV2`；FormViewV2 在 models.py 定义 version=2、scope（draft／application）、direction、fieldRuleVersion、interactionVersion、formId、draftRevision、applicationRevision、fields、groups、pending、actions；两个 revision 依 scope 互斥，groups 为含 id／name／fields 的列表。核心仅组合可编辑字段并签发服务端 formId，Task 7 扩展其读取投影与进度。

- [x] **Step 1：写失败测试。** 覆盖匿名认证、角色隔离、原文证据、字段保留、过期修订、取消 fence、口语语境确认、同轮改资料提交、实际汇总按钮和结果未知。显式加入 Review Focus 1 的 `test_same_effect_new_tool_call_id_returns_receipt` 与 `test_two_distinct_saves_in_one_command_both_apply`。

  固定断言：`assert repeated.receipt_id == first.receipt_id`；`assert business_calls == 1`。连续合法修改场景另断言 `assert saved_revisions == [base_revision + 1, base_revision + 2]`，不能用同一断言吞掉第二次修改。
- [x] **Step 2：验证红灯。** `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_deep_tools.py tests/xiaohe/test_deep_effects.py -q`，预期 native 工具适配／effects 缺失。
- [x] **Step 3：实现授权适配。** 将准备／恢复草稿、读取、form 组合、保存、确认提交、真实进展与交接接入现有服务。prepare_lead 创建草稿时显式使用本轮 bundle 的字段 policy 并持久化其引用，不能重新读取后来变化的 live 文件；旧调用者仍沿原路径。共用提取保持旧校验及报错；接待不提供专业写工具，child 不提供写工具。语义确认候选绑定可信 Command／原消息及最终资料，不能把 submit 调用本身当确认证据。
- [x] **Step 4：实现幂等记录。** `payload.deepExecution.effects` 在现有锁序和短事务内持久化动作。稳定身份覆盖 Command、角色、操作、草稿、预期修订及规范化业务载荷，不使用 tool_call_id／attempt。相同动作先返回原回执，其他载荷按当前 revision 校验，避免吞掉合法连续修改。正式提交继续复用原 submission 键和不可变载荷，未知结果查询原请求。
- [x] **Step 5：验证并提交。** 新测试以及 `test_agent_operations.py`、`test_lead_submission.py`、`test_reception_lock_order.py` 全 PASS；检查无模型／网络等待持数据库事务。建议 `feat(xiaohe): expose guarded business tools with stable receipts`。

### Task 6：三节点运行时、checkpoint 与旧引擎并存

**Files:** 新增 `deep_runtime/runtime.py`、`tests/xiaohe/test_deep_runtime.py`、`tests/xiaohe/test_deep_recovery.py`；修改 `graph.py`、`main.py`、`admission.py`、`toolkit_runtime.py`、`expiry.py`，必要时只扩展 `agents/runtime.py` 的既有上下文准备入口，不改旧编译器语义。

**Interfaces:** `run_deep_turn(factory: async_sessionmaker[AsyncSession], fence: WriteFence, *, execution: DeepExecution, model: BaseChatModel, business_client, checkpointer: BaseCheckpointSaver) -> AgentOutcome`；`deep_thread_ids(command: Command) -> set[str]`。业务图固定三个节点，无新的路由 DSL。原 `run_agent_turn` 和 legacy handler 保留。

- [ ] **Step 1：写失败测试。** 三角色答疑／资料／确认、接待交接和按钮路径；断言三个自定义业务节点、按钮不额外依赖模型判断、每轮最多一次交接。旧 Command 和新 Command 分派分别命中原／新执行器；独立 thread_id、默认根 namespace、首次加消息与恢复 `None` 不重复输入。

  固定断言：`assert business_node_names == {"prepare_context", "business_agent", "finalize"}`；`assert user_message_occurrences == 1`；`assert handoff_count <= 1`。
- [ ] **Step 2：验证红灯。** `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_deep_runtime.py tests/xiaohe/test_deep_recovery.py -q`，目标是新运行时未接入。
- [ ] **Step 3：实现调度与装配。** `graph.respond` 先找到当前 Command 的固定 runtime，再分派，native 分支不能先调用旧 `bundle_for_run`。admission 的专业 Agent 入口按当前 Command 的可信 active 角色检查，扩展现有只识别 dsl-v1／agentExecution 的条件，测试不能进入他人或错误方向。通过 `build_graph` 返回图实例的既有 checkpointer 生命周期，在 respond 执行时显式传给 native runner；启动未就绪则受控拒绝，不另开 saver 或进程。启动核对所有未完成 native 快照，缺版不回退。
- [ ] **Step 4：实现恢复与收尾。** 复用既有互斥及 fence，内部线程关系记入 DeepExecution；换 attempt 需隔离时换物理线程并保留业务 effects 键。工具成功后模型失败可由回执生成关键提示。意外内层 interrupt 受控报错，不结束 Command 或抢占外层 await_input；方向交接及最终输出保持原 AgentOutcome。
- [ ] **Step 5：验证清理并提交。** 加入 Review Focus 5 的崩溃／竞争测试及 `test_native_threads_deleted_with_guest`，覆盖取消、过期、访客正文清理和 child namespace。运行新测试与 `test_workflow_recovery.py`、`test_visitor_expiry.py`、`test_agent_handoff.py`。建议 `feat(xiaohe): run skills in compact durable business graphs`。

### Task 7：自由 form、引导进度与规则投影 v2

**Files:** 扩展 Task 5 的 `deep_runtime/forms.py`；新增 `tests/xiaohe/test_deep_forms.py`、`tests/xiaohe/test_deep_progress.py`、`tests/xiaohe/test_lead_policy_v2.py`、`tests/xiaohe/contracts/lead-policy-v2.json`、`docs/contracts/xiaohe-lead-policy-v2.md`；修改 `reception/{api,contracts,store,forms}.py`、`lead_skills/views.py`、`worker.py`。

**Interfaces:** 消费 Task 5 的 compose_form／FormViewV2；产生 `lead_policy_v2(db: AsyncSession, actor: Actor, draft_id: UUID) -> FormViewV2`、`application_policy_v2(db: AsyncSession, actor: Actor, view: ApplicationFormView) -> FormViewV2`、`record_published_guidance(db: AsyncSession, command: Command, message: Message) -> None`。展示及进度持久化到 `guidance_progress`，不改变字段 policy_id；旧 DraftView.step 保留旧协议值，新引导不再复用 round1—round3 枚举。

- [ ] **Step 1：写失败测试。** 三步改四步／合并和重排既有字段，不新增固定枚举；旧字段门槛不变，只读保留字段不能编辑。`test_nonfield_step_requires_published_evidence` 断言未落库的解释候选不是完成记录，已发布消息或真实回执才可作为依据。两个原有 GET 的 v1、重复头、未知值继续旧形状；独立申请表单的 v2 读取不新建聊天草稿或调用模型。

  固定断言：`assert old_draft.field_policy_id == original_policy_id`；`assert len(view.groups) == 4`；`assert not unpublished_step.completed`；`assert legacy_response == original_v1_fixture`。
- [ ] **Step 2：验证红灯。** `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_deep_forms.py tests/xiaohe/test_deep_progress.py tests/xiaohe/test_lead_policy_v2.py -q`，预期 v2 投影与进度缺失。
- [ ] **Step 3：实现数据投影。** 两个原有 GET 恰好一个 `X-Xiaohe-Lead-Policy: 2` 才返回 v2；1 仍 v1，其他保持原兼容。聊天无活动草稿时仍 null；独立表单按现有 ApplicationFormView 和规则确定性投影，不在 GET 调模型。formId 由服务器绑定 scope、主体、修订、交互及展示组合；读取／校验不得产生新的业务草稿。
- [ ] **Step 4：实现显式 v2 保存边界。** 聊天采用 `PATCH /ai-api/v1/reception/drafts/{draft_id}/form`，输入 `formId`、`expectedRevision`、`fields`。独立表单原 POST `/application-form` 在声明 v2 时采用扩展请求模型，在原请求上增加 formId，仍校验原 definitionBundleId／expectedApplicationRevision 后复用现有服务。旧请求不新增必填键、正式业务后端载荷不变。formId 失效返回 ACTION_STALE，修订冲突返回 DRAFT_CONFLICT；会话、CSRF 和归属检查保持。
- [ ] **Step 5：实现接续及完成依据。** 读取同草稿已有资料和已完成事项，在新 Command 按新 Skill 判断待办；新步骤不机械沿用旧 round 编号。最终解释由 worker 的既有消息落库事务调用 record_published_guidance；引用真实 Message／回执而非模型自报。保存修订变化后旧 form 返回冲突，保留客户端输入；完整精确 JSON 与错误码写进契约及 fixture。
- [ ] **Step 6：验证并提交。** 新测试及 `test_lead_policy_api.py`、`test_reception_drafts.py`、`test_event_recovery.py` 全 PASS；对比旧 fixture，未知字段、清空、缺项与部分保存不丢资料。建议 `feat(xiaohe): project flexible forms and evidence based guidance`。

### Task 8：门户配套交接与兼容验收

**Files:** 本仓库新增 `docs/handoffs/2026-11-11-standard-skills-portal.md`。门户维护者修改 `D:/Code/yunhezhiyuan-portal/frontend/src/chat/reception/{contracts,api,fields,store,presentation,application-form}.ts`、`ReceptionFieldEditor.vue`、`ReceptionRoundCard.vue`、`ReceptionSummaryPanel.vue`、`ApplicationFormDialog.vue`；新增门户 fixture、单元与浏览器测试，按实际消费链缩小文件集合。

**Interfaces:** Task 7 的已实现 v2 fixture／契约、formId 冲突语义及原保存／正式提交边界。门户只呈现服务端已有字段组合，Skill 名称、工具策略和内部 checkpoint 不作为用户操作项。

- [ ] **Step 1：编写可转交文档。** 附精确响应、v2 保存请求、旧后端回退、真实状态提示和验收命令。保留门户当前 v1 适配与未提交工作；交用户转交，不自动发消息或跨仓编码。
- [ ] **Step 2：由门户维护者写红灯。** `reception-lead-policy-v2.test.ts` 固定协议解析与 3→4 组合；`reception-form-rebase.test.ts` 固定 owner／角色变化和未保存输入；浏览器 `reception-lead-policy-v2.spec.ts` 验证聚焦、冲突后保留输入及面板去重。

  门户场景固定断言：`expect(editor.values).toEqual(unsavedValues)`；`expect(serverSubmissionCount).toBe(1)`；`expect(activeFieldGroups).toHaveLength(4)`。
- [ ] **Step 3：由门户维护者实现。** 声明支持 v2，缺 v2 时回退 v1／旧展示；复用现有字段组件，去除新路径对 round1—round3 的限制，不删旧分支。新组合不覆盖脏输入；用户核对后再更新，过期 form 重新读取。
- [ ] **Step 4：获得验证回执。** 门户运行 `npm exec vitest run tests/unit/reception-lead-policy-v2.test.ts tests/unit/reception-form-rebase.test.ts`、`npm run typecheck`、`npm run build`，并在隔离 fixture 服务运行对应 Playwright；未完成前标记“后端已验、门户／组合待验”，不得把交接发出视为前端完成。
- [ ] **Step 5：分别提交。** 本仓库只提交交接文档；门户维护者独立提交其变更并返回 commit／验证及未验项。Java 正式接口不变时不创建额外 Java 改造；确需变更再提供精确交接。

### Task 9：组合恢复、迁移与回退验收

**Files:** 新增 `tests/xiaohe/test_deep_journeys.py`、`tests/xiaohe/integration/test_deep_runtime_crashes.py`、`docs/testing/xiaohe-deepagents.md`；按需扩充已有 crash worker fixture，不修改部署脚本／版本号。

**Interfaces:** Tasks 1—7 的固定 bundle、runtime、工具与 v2 API；Task 8 回执是前后端组合结论的前置条件。继续复用旧业务客户端桩和专用临时 PostgreSQL，不调用真实模型／短信／正式后台。

- [ ] **Step 1：写失败恢复场景。** 三方向完整留资、同轮答疑加留资、只读 child；旧 Run 新 Command 选 native、旧 Command 在途提交仍旧引擎；副作用后 checkpoint 前崩溃、模型后续失败、取消和 worker 竞争；发布新包后旧 Command 使用旧内容；新版关闭后在途 native 仍可恢复。

  固定断言：`assert accepted_application_count == 1`；`assert retried_submit_key == original_submit_key`；`assert resumed_execution.bundle_id == original_bundle_id`。
- [ ] **Step 2：验证这些场景确有覆盖。** 未通过的目标场景先红灯；能通过的既有回归保留对照。对每个故障注入点明确断言唯一正式申请、原提交键和正确缺项／受理／未知结果，不只检查 HTTP 200。
- [ ] **Step 3：完成集成缺口。** 只修本次接入缺失，不扩大成旧代码重构。复核 SSE／AG-UI 不泄漏 child 内部消息、系统提示及授权快照，确认所有新推理线程被生命周期清理枚举覆盖。
- [ ] **Step 4：运行验收。** 先跑各 Task 相关测试，再运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe --ignore=tests/xiaohe/integration -m 'not minio' -q` 与本次 crash integration；依赖升级另跑 `tests/agents`、相关 service streaming 测试。MinIO 项须有独立测试存储后另验，不能连接现有 bucket。既有同名模块收集问题如仍存在单独记录，不能将忽略后的集合描述为整个仓库通过。
- [ ] **Step 5：演练迁移和回退并提交。** 仅专用库验证 upgrade、旧行不改写、有新记录时拒绝不兼容 downgrade、只切后续新请求的回退。记录依赖锁、commit、测试集合、门户回执、未做真实模型／部署及其验收入口。建议 `test(xiaohe): verify native skills recovery and migration`。

## 设计覆盖与自审记录

本节是计划自审，不是运行结果：

| 定稿设计 | 实施任务 |
| --- | --- |
| 标准格式、二十一包、绑定／权限、按需读取 | 1、2、4 |
| 三节点 workflow、确定性按钮、一次交接及旧引擎保留 | 5、6 |
| 留资自由组合、非字段完成证据、旧草稿下一轮接续 | 3、7 |
| 子任务只读、state／files 合并边界、无再委派 | 4 |
| 真实确认、同轮改资料、稳定回执及未知结果 | 5、6、9 |
| 固定快照、独立推理线程、竞争／取消／清理 | 3、6、9 |
| v1／v2、动态 form、门户脏输入与旧后端回退 | 7、8 |
| 依赖升级、临时库迁移、回退、真实模型分开验收 | 1、3、9 |

五项 Review Focus 均在所属任务设置具名测试。接口类型由 2—4 定义，后续任务只消费；未定义的业务类型优先复用现有模型。文件、步骤和命令按当前基线核对，执行前再检查迁移编号与包 API 漂移。

用户已选择本对话逐项实施，按任务记录开发与隔离验证；部署、真实模型及正式业务写入继续单独授权。
