# 小鹤三方向留资 Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将三角色留资规则收敛为可版本化 Skill，支持口语直接提交、统一关键提示及兼容的 Artifact 表单。

**Architecture:** 三份角色包共用交互规范与现有 LangGraph 执行器。字段快照固定于草稿，交互与执行快照固定于用户 Command；服务端继续控制授权、校验、确认证据、不可变提交载荷及真实结果。

**Tech Stack:** 现有 Python、FastAPI、LangGraph、Pydantic、PyYAML、SQLAlchemy、Alembic、PostgreSQL、pytest；不新增依赖。

**Spec:** [已批准设计](../specs/2026-11-11-role-lead-skills-design.md)。执行者须先阅读设计和本计划。

## Global Constraints

- 状态：2026-11-11 用户批准设计后选择“本对话逐项实施”，授权按本计划开发与隔离测试；不包含环境迁移或部署。
- 在 `dev-fengfeng` 开发，尽量不修改上游代码；保留现有未提交工作。任务内提交仅发生于后续实施阶段，禁止 `git add .`。
- 普通表达可自然组织；必填、提交告知、受理结果由真实状态生成，不可省略或编造。
- 采购和供应商必填 `companyName`、`contactName`、`contactPhone`；组织机构另按现有要求使用 `organizationName`、`memberSize`、`coverage`、`onlineChannels`、`contactName`、`contactPhone`。附件选填；不新增或删减字段门槛。
- “好的”“可以”等按上下文语义判断提交，不用固定句式或短词黑名单代替 LLM 判断。资料齐备时直接提交，不强制汇总二次确认。
- 新草稿用新字段规则，已有草稿保留有效原字段要求；下一轮用户 Command 使用新版交互，执行中及出站请求保留原快照。
- 认证、租户／系统隔离、草稿归属、版本冲突、幂等、真实回执必须保留。登录本身不等于确认提交。
- `system_notice` 的“开始填写需求资料”保持协议兼容；新能力声明为 `X-Xiaohe-Lead-Policy: 1`，新增投影名为 `leadPolicy`。不改变 AG-UI viewVersion、Runtime 转发或正式提交请求。
- 留资 SOP 不进入答疑 topic 或知识检索；不执行配置内的代码／任意工具／任意外部地址，不改模型或知识库方案。
- 门户实现由门户 Agent 承担，按 `/handoff` 生成独立文档后交用户转交；本项目不直接修改门户或 Java 工程。
- 测试使用桩模型与隔离测试库；真实模型、短信、真实业务写入和任何环境更新均不包含在本计划的默认执行范围。

## Review Focus

1. 同一等待输入 Run 在发布新规范后收到新 Command：新轮次采用新版，前一轮恢复不换版；由 Task 3 测试固定。
2. “好的”只是同意继续咨询或登录：不得提交；同轮补资料并同意提交则按最终资料提交；由 Task 4 测试固定。
3. 模型决策完成后另一窗口修改草稿／附件：拒绝过期确认，不能静默提交新资料；由 Task 4 测试固定。
4. Java 已受理但响应丢失：原请求核对，返回待核实提示，不新增申请或编造成功；由 Task 5、8 测试固定。
5. 新旧门户交错使用且面板存在未保存输入：旧响应形状不变，新面板只在匹配草稿就绪后打开且保留编辑；由 Task 6、7 验证。

## 文件与职责

以下为目标文件，不表示这些新增资源或功能已经存在。现有函数按名称定位，避免用容易漂移的行号。

| 文件 | 责任及改动边界 |
| --- | --- |
| `resources/xiaohe/lead-skills/common/policy.yaml`；`buyer/`、`organization/`、`supplier/` 各自的 `SKILL.md` 与 `policy.yaml` | 共用交互／关键模板与三个角色字段、分组、引导；不重复维护字段名单 |
| `src/xiaohe/lead_skills/{__init__,models,loader,store,notices}.py` | 严格配置类型、受限加载、不可变快照及确定性提示；无新执行引擎 |
| `src/xiaohe/{models,graph}.py`；`reception/models.py`；`migrations/versions/0013_lead_policies.py` | 快照存储、草稿／Command 引用、确认证据与兼容迁移；执行前确认迁移 head，若 `0013` 已占用则只顺延编号 |
| `src/xiaohe/workflows/{models,loader,compiler,definitions}.py` | 新流程显式角色 Skill 绑定、旧 bundle 兼容与旧有效规则适配；复用现有安全 YAML 加载约束 |
| `src/xiaohe/agents/{contracts,runtime,nodes,semantic}.py` | 每轮选版、角色装配、语义决策、同轮修改与提交；沿用现有阶段回执和写入围栏 |
| `src/xiaohe/reception/{fields,store,flow,organization,submission,contracts,forms,api}.py` | 统一字段校验、草稿、固定提示、确认提交、投影及历史兼容；只改实际调用链 |
| `resources/xiaohe/workflows/{buyer,organization,supplier}.yaml`；`resources/xiaohe/prompts/{buyer-intent,organization-intent,supplier-intent,reception-intent}.md` | 新装配引用和语义说明；移除与新设计冲突的强制汇总／短词排除 |
| `tests/xiaohe/test_lead_{skills,policy_storage,policy_versions,submission,notices,policy_api,integration}.py` | 新能力测试；复用现有 fixture、假模型与业务桩，不复制整套测试基础设施 |
| `docs/contracts/xiaohe-lead-policy-v1.md`；`docs/testing/xiaohe-lead-skills.md` | 门户可交接的精确投影契约及隔离验证／回滚边界 |

## 测试与提交约定

命令从本仓库根目录在 PowerShell 执行，Python 使用 `.venv/Scripts/python.exe`。标记 `pg` 的命令先由操作者提供**专用可清空测试库**的 `XIAOHE_TEST_DATABASE_URL`，数据库名必须以 `test_xiaohe_` 开头；现有 `pg_factory` 会迁移并清空库内测试表。禁止从生产或正在验收的环境复制 DSN。没有专用库时保留该项未验，不假称通过。

每项红灯测试须因目标行为缺失而失败，不能将连接失败当作红灯。新增类型和函数签名在下面 Interfaces 固定；可复用现有辅助函数，不为测试再造业务框架。每项结束只暂存该任务中本次新写的相关差异；文件含先前未提交工作时使用 `git add -p`，审阅 `git diff --cached` 后再提交，不能把原有工作顺带提交。

### Task 1：装配三个角色包并保留原规则

**Files:** 创建角色资源及 `lead_skills/{__init__,models,loader}.py`；修改 `workflows/{models,loader,compiler}.py`、`reception/fields.py`；新增 `test_lead_skills.py`，更新 `test_workflow_loader.py`、`test_workflow_compiler.py`。

**Interfaces:**
- 产生 `FieldPolicy`：角色、字段描述、现有校验参数／注册校验器、分组、必填与公共／申请存储归属。
- 产生 `InteractionPolicy`：三份角色引导、共用规则／模板；`LeadSkillBundle` 包含三个 `field_policies` 和一个 `interaction_policy`，ID 均为规范 JSON 内容的 SHA-256。策略内容不反向包含 workflow bundle，避免资源加载／hash 循环；执行图另在 Command 中一起固定。
- `load_lead_skills(resource_root: Path) -> LeadSkillBundle`；严格、冻结、拒绝未知键。保留现有 SafeLoader 对别名、重复键、资源越界和大小的限制。
- 新 workflow 的 `lead_skill_id` 指向稳定 ID；新定义不再重复填写 `required_fields`／`rounds`，编译时投影角色规则。旧持久化定义仍支持原结构和原 hash，不回写历史 JSON。

- [x] **Step 1：写失败测试。** `test_role_profiles_preserve_current_contract` 对三角色逐字段比较当前分组、标签、枚举、必填、长度／类型校验及存储归属；明确断言供应商附件非必填、采购电话“总台分机”仍有效。`test_skill_binding_is_separate_from_qa_topics` 断言不会进入原 topic。参数化 `test_invalid_skill_configuration_is_rejected` 覆盖未知角色／字段／动作、模板变量错误、非法 YAML／路径；`test_legacy_bundle_hash_is_unchanged` 保证历史包仍可加载。
- [x] **Step 2：验证红灯。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_skills.py -q`，预期新加载器／绑定能力缺失导致 FAIL。
- [x] **Step 3：实现上述类型和加载。** 从当前有效规则搬移数据，不趁机调整内容；普通校验参数来自字段快照，特殊校验只引用现有行为对应的固定注册实现。`SKILL.md` 用简体中文描述场景与引导，完整必填名单仅存角色配置。旧 workflow 保留兼容读取，不新增任意条件语言。
- [x] **Step 4：验证绿灯。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_skills.py tests/xiaohe/test_workflow_loader.py tests/xiaohe/test_workflow_compiler.py -m 'not pg' -q`，预期全 PASS；对照搬移前行为，不能用新配置生成自己的预期来证明等价。
- [x] **Step 5：审阅并提交任务差异。** 提交建议：`feat(xiaohe): add role lead skill resources and strict loading`。

### Task 2：持久化规则快照和可区分的确认证据

**Files:** 创建 `lead_skills/store.py`、迁移文件、`test_lead_policy_storage.py`；修改 `src/xiaohe/models.py`、`reception/models.py`、`agents/contracts.py`；更新 `test_migrations.py`。

**Interfaces:**
- `LeadPolicyRecord`：`policy_id` 主键、`kind`（`fields`／`interaction`）、不可变 `payload`、创建时间；读取重算 hash，拒绝损坏或同 ID 异内容。
- 草稿新增 nullable `field_policy_id`；Command 新增 nullable `interaction_policy_id`，均引用快照表。旧记录允许空值，经 Task 3 兼容解析。
- `persist_policy(db: AsyncSession, policy: FieldPolicy | InteractionPolicy) -> str`；`load_policy(db: AsyncSession, policy_id: str) -> FieldPolicy | InteractionPolicy`。
- `ReceptionSubmission.summary_version` 改为 nullable，新增 nullable `confirmation_evidence` JSONB。新证据 `version=1`，来源区分 `semantic_direct`、`semantic_reviewed`、`button`，记录真实 command／run／message／user／role、修订、字段／交互版本和最终载荷摘要；`button`／`semantic_reviewed` 要求真实汇总版本，`semantic_direct` 不伪造该版本。历史 null 证据保留原含义，不猜测来源。

- [x] **Step 1：写失败测试。** `test_policy_record_is_immutable` 断言去重及内容篡改拒绝；`test_expand_migration_preserves_legacy_submissions` 从旧 schema 插入样本后升级，断言用户／草稿／字段／文件／原 payload／提交键不变；`test_direct_evidence_does_not_forge_summary` 断言直接证据可无汇总，按钮缺汇总失败。
- [x] **Step 2：验证红灯。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_policy_storage.py -q`，预期表、列或证据约束缺失导致 FAIL。
- [x] **Step 3：实现扩展迁移与存储。** 不批量改历史草稿规则、状态或提交载荷。迁移降级在存在新策略绑定或新证据时明确拒绝，不能删除绑定／审计证据或为满足旧 NOT NULL 伪造汇总。新增约束保证新证据来源和汇总版本一致。
- [x] **Step 4：验证绿灯。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_policy_storage.py tests/xiaohe/test_migrations.py -q`，预期全 PASS；在专用库验证空库升级、旧库升级和无新数据时的降级／再升级。有新数据时降级应受控拒绝。
- [x] **Step 5：审阅并提交任务差异。** 提交建议：`feat(xiaohe): persist lead policy snapshots and confirmation sources`。

### Task 3：草稿固定字段、每个 Command 固定交互

**Files:** 修改 `lead_skills/store.py`、`agents/{contracts,runtime,nodes}.py`、`src/xiaohe/graph.py`、`workflows/definitions.py`、`reception/{fields,store,flow,organization,forms,contracts}.py`；新增 `test_lead_policy_versions.py`，更新 `test_workflow_versions.py`、`test_workflow_recovery.py`。

**Interfaces:**
- `resolve_draft_policy(db: AsyncSession, row: ReceptionDraft) -> FieldPolicy`：首次兼容解析并固定接入时实际有效规则；后续只读该快照。
- `bind_command_policy(db: AsyncSession, command: Command, latest: InteractionPolicy, *, workflow_bundle_id: str) -> InteractionPolicy`：首次在同一事务固定策略引用及 `agentExecution.bundleId`，重试读取原版本。后者决定本轮业务子图，沿用阶段回执；不改 Run 的 `workflow_bundle_id`。历史已开始执行但没有策略引用的 Command 继续走原路径，不在恢复时补绑最新版。
- `validate_fields(direction: str, fields: dict, *, policy: FieldPolicy | None = None) -> dict` 保留原调用兼容；草稿相关调用必须传已绑定策略。模型上下文、缺项计算及提交共用同一策略；对不属于草稿的旧正式业务边界保留既有固定最低契约。
- 原独立 `application-form` 的 `definitionBundleId` 校验继续有效；同一 bundle 的表单规则可重建、不可原地替换。Task 6 投影必须等于该表单实际校验规则，不擅自增加表单写入字段或另建草稿。

- [x] **Step 1：写失败测试。** `test_old_draft_keeps_effective_fields_new_draft_uses_latest` 覆盖无 bundle 旧草稿、已有 bundle 及联系人投影；`test_next_command_in_same_waiting_run_uses_new_policy` 断言旧字段／新交互、Run bundle 未变；`test_resume_keeps_command_policy_and_cached_stages` 断言续跑不换版；`test_started_legacy_command_is_not_reclassified` 断言升级不改已开始的旧动作。`test_form_projection_matches_form_validation` 固定独立表单规则一致性。
- [x] **Step 2：验证红灯。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_policy_versions.py -q`，预期未绑定或错选新／旧版本导致 FAIL。
- [x] **Step 3：实现上述解析与选版。** 在生成 frame／语义判断前，在受写入围栏保护的事务中固定本轮版本；多实例并发只能成功固定一次。已有执行阶段／旧 in-flight Command 走原快照；后续新 Command 才进入新版。按调用点覆盖 API patch、聊天回填、legacy flow、组织兼容流程及独立表单，不只修改 `missing_required`。
- [x] **Step 4：验证绿灯。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_policy_versions.py tests/xiaohe/test_workflow_versions.py tests/xiaohe/test_workflow_recovery.py -q`，预期全 PASS。加断言政策变化不会改变已出站提交的 payload／key。
- [x] **Step 5：审阅并提交任务差异。** 提交建议：`feat(xiaohe): pin draft fields and command interaction policies`。

### Task 4：口语直接提交及同轮修改

**Files:** 修改 `agents/{contracts,semantic,nodes}.py`、`reception/submission.py`、四份 intent 提示词及三份 workflow；新增 `test_lead_submission.py`，更新 `test_agent_semantics.py`、`test_reception_submission.py`。

**Interfaces:** 保留现有 `confirm_locked_draft(..., *, evidence=None)` 入口；加入 Task 2 的证据来源分支。复用现有 `confirm_current_summary` 语义 action 标识承载“提交当前资料”意图，调整其上下文与路由，不让旧名称强迫展示汇总。按钮仍走真实 `summaryVersion` 校验；semantic_direct 允许 collecting／review，须有本轮有效语义决策及最终快照证据。

- [x] **Step 1：写失败测试。** 三角色参数化 `test_semantic_confirmation_submits_without_summary`，固定桩决策表示“好的／可以”是在同意提交，断言业务调用一次且 `summary_version is None`。`test_short_reply_in_other_context_does_not_submit` 固定咨询／登录语境的不提交决策，断言零调用。`test_same_turn_patch_and_submit_uses_final_snapshot` 断言先保存明确修改、最终 revision／payload 一致。另覆盖必填缺失、非法值、过期用户、跨租户、角色不符、伪造 button 证据。
- [x] **Step 2：验证红灯。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_submission.py -q`，预期现有强制汇总或拒绝 candidates 导致 FAIL。
- [x] **Step 3：实现直接路径。** 验证原 frame revision，校验并保存有原文证据的候选字段，再生成绑定最终 revision 和不可变载荷摘要的证据。决策缓存／保存阶段回执支持原命令重试，不能二次 patch。模型决策后发生非本命令修改则返回 `DRAFT_CONFLICT`。授权、必填、正式业务最低门槛、唯一 `(draft_id,draft_revision)` 和原提交键算法继续保留。
- [x] **Step 4：补并发与恢复验证后运行绿灯。** `test_external_revision_change_rejects_semantic_submit` 覆盖字段及附件变动；`test_patch_submit_retry_does_not_apply_patch_twice` 断言最终载荷／键不变。运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_submission.py tests/xiaohe/test_agent_semantics.py tests/xiaohe/test_reception_submission.py -q`，预期全 PASS。桩测试只证明执行边界，不宣称真实 LLM 语义准确率。
- [x] **Step 5：审阅并提交任务差异。** 提交建议：`feat(xiaohe): submit complete leads from contextual consent`。

### Task 5：三个角色转化引导与统一关键提示

**Files:** 创建 `lead_skills/notices.py`、`test_lead_notices.py`；修改 common／角色 policy、`agents/{nodes,runtime}.py`、`reception/{flow,organization}.py`；复用 `test_lead_conversion.py`、`test_reception_onboarding.py`。

**Interfaces:** `render_lead_notice(policy: InteractionPolicy, notice: str, *, direction: str, missing_labels: list[str] | None = None, public_guidance: str | None = None) -> str`；notice 只接受设计 §8 的固定类别。调用方依据真实保存／提交／回执状态选择，模型不能指定成功类别。

- [x] **Step 1：写失败测试。** `test_critical_notice_uses_real_state` 逐项断言设计 §8 中文模板，缺项来自绑定规则。三角色 `test_conversion_answers_then_prepares_role_form` 覆盖会员不再邀登录、访客认证、方向不明先澄清、唯一草稿复用／多份选择。保留采购“寻源慢”“难比价”“想看效果”的明确转化。`test_login_is_not_submission_consent` 断言登录后不自动提交。
- [x] **Step 2：验证红灯。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_notices.py -q`，预期分散话术或状态判断不一致导致 FAIL。
- [x] **Step 3：接入统一渲染。** 只有真实保存成功才说已保存，只有真实回执才说已受理；超时用待核实模板并沿原键查询。答疑文本与关键块组合，模型失败不得编造答复／业务成功。保留现有语言选择及受控降级。Artifact 打开继续发稳定 system_notice，普通咨询不重复发；状态文本不得成为新的前端协议。
- [x] **Step 4：验证绿灯。** `test_lost_submit_reply_stays_unknown_until_receipt` 断言待核实→真实受理、业务创建一次；模型自由答复中伪称已受理时不能覆盖系统状态。运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_notices.py tests/xiaohe/test_lead_conversion.py tests/xiaohe/test_reception_onboarding.py -q`，预期全 PASS。
- [x] **Step 5：审阅并提交任务差异。** 提交建议：`feat(xiaohe): standardize role collection and lead status notices`。

### Task 6：兼容的 leadPolicy 投影和可交接契约

**Files:** 修改 `reception/{contracts,api,store,forms}.py`；创建 `docs/contracts/xiaohe-lead-policy-v1.md`、`test_lead_policy_api.py`；按现有方式补契约 fixture，不覆盖旧 fixture。

**Interfaces:** `LeadPolicyView` 输出 `version: 1`、`direction`、`fieldRuleVersion`、`interactionVersion`、`fields`、`rounds`、`requiredFields`、`notices`；字段描述包含原 ID、标签、支持类型、选项和可公开校验说明，不输出脚本、提示词或内部记录 ID。`notices` 按 Task 5 的类别编号提供模板及允许的占位符，状态选择仍由可信 API／事件决定。

- [x] **Step 1：写失败测试。** `test_legacy_read_shape_is_unchanged` 对 GET `/ai-api/v1/reception/conversations/{conversation_id}`、`/ai-api/v1/reception/application-form` 无 header 的响应与原 fixture 深比较；`test_capable_client_gets_effective_policy` 带 `X-Xiaohe-Lead-Policy: 1` 获得顶层 `leadPolicy`，无 activeDraft 时为 null，有草稿时按其字段版本投影。未知／重复能力 header 按未声明处理，不改变原返回或授权。
- [x] **Step 2：验证红灯。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_policy_api.py -q`，预期缺投影导致 FAIL，原响应形状测试须始终通过。
- [x] **Step 3：实现 opt-in 序列化并写精确契约。** 投影只加到两类读取响应；新门户仍使用旧 create／patch／actions／form submit 请求。独立表单投影严格匹配其 definitionBundleId 的实际规则；资料面板展示交互版本不替换在途 Command 快照。不得改变共享 WireModel 或 Runtime view，避免让所有旧调用方被动接收新字段。
- [x] **Step 4：验证绿灯。** 加跨用户／跨租户读取拒绝、旧草稿新交互、旧后端缺字段的消费示例；运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_policy_api.py tests/xiaohe/test_reception_contracts.py -q`，预期全 PASS。
- [x] **Step 5：审阅并提交任务差异。** 提交建议：`feat(xiaohe): expose opt-in effective lead policy views`。

### Task 7：门户交接与独立验收边界

**Files:** 本项目提供 Task 6 契约／fixture；交接文档按 `/handoff` 保存到 OS Temp。门户 Agent 修改 `D:/Code/yunhezhiyuan-portal/frontend/src/chat/XiaoheWidget.vue` 及 `reception/{contracts.ts,store.ts,fields.ts,ReceptionFieldEditor.vue,ReceptionSummaryPanel.vue,presentation.ts}`，按实际依赖扩展测试；本对话不跨工程编码。

**Interfaces:** 门户显式声明能力、消费 `LeadPolicyView`，缺投影时沿用原展示；草稿／角色／owner 仍从可信状态读取。既有按钮请求与系统通知协议不变。

- [x] **Step 1：生成具体交接文档。** 引用定稿设计、Task 6 契约、固定后端 SHA 和旧／新 fixture；列明 UI 工作、依赖顺序及未授权操作。不传凭据／个人资料，不向其他 chat 自动发消息。
- [x] **Step 2：交用户转交并暂停依赖门户的部分。** 后端独立测试可继续；不将“交接完成”记为“门户实现完成”。Java／Runtime 契约没有变化则不新增它们的开发任务。
- [ ] **Step 3：由门户 Agent 先写失败测试。** `old_backend_without_projection_falls_back`；`new_policy_drives_role_fields`；`collection_notice_waits_for_matching_draft`；`replayed_notice_preserves_unsaved_input_and_focus`；`owner_change_does_not_reuse_another_users_form`。分别断言兼容、三角色字段、事件／读取乱序、重连去重与隔离。
- [ ] **Step 4：由门户 Agent 实现并运行绿灯。** 表单集中填写；同草稿已有未保存修改时保留本地输入并提示状态更新，不自动覆盖或换草稿。使用门户项目现有测试／typecheck／build 命令，返回实际命令、结果和固定 SHA。
- [ ] **Step 5：核对交接回执。** 确认无新请求协议、无伪造受理状态、无前端强制增加确认步骤；缺回执则标门户部分未验，不阻止记录后端已完成项。

### Task 8：隔离组合验证、迁移演练与实施收口

**Files:** 创建 `test_lead_integration.py`、`docs/testing/xiaohe-lead-skills.md`；仅在必要时补现有测试 fixture，不增加新运行服务。

**Interfaces:** 使用实际三份资源、桩模型、隔离 PG、假业务客户端，走现有 admission→Command→graph→draft→submission→receipt；组合结果按后端自动化／门户自动化／人工 UI／真实模型四类分别记录。

- [x] **Step 1：写失败集成测试。** `test_three_roles_complete_collection_and_direct_submit` 参数化三方向；`test_policy_upgrade_and_lost_receipt_keep_original_request` 先出站再换配置并模拟 worker 重启／响应丢失，断言相同 payload、key、policy ID 且业务创建一次；`test_existing_draft_next_command_uses_new_interaction` 用真实等待 Run 验证 Task 3，不能仅测选版 helper。
- [x] **Step 2：验证红灯并修复接线。** 运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe/test_lead_integration.py -q`，预期首次只因跨层接线问题 FAIL；修复范围限本设计，不用重写外围架构解决。
- [x] **Step 3：验证绿灯及相关回归。** 先运行该集成文件；再运行 `.venv/Scripts/python.exe -m pytest tests/xiaohe -m 'not minio' --ignore=tests/xiaohe/test_conversion_signals.py -q`，须提供专用 PG。不传真实模型评测开关；报告实际 PASS／FAIL／SKIP。使用 `.venv/Scripts/python.exe -m ruff check` 加本次变更的 Python 文件列表，不能顺带格式化全仓库。
- [x] **Step 4：记录迁移／恢复演练。** 在隔离样本中验证迁移前后的旧草稿、旧提交、在途 Command 和文件引用。旧版二进制只承诺在未产生新版绑定／证据前回退；产生新版记录后须保留兼容读取能力的版本，不能直接回到不理解 nullable summary／新证据的旧代码。任何正式迁移、停流排空或生产切换另行安排，不在测试命令里执行。
- [ ] **Step 5：汇总门户组合证据及未验项。** 等 Task 7 回执后核对旧后端／新前端、新后端／旧前端和新新组合；无真实窗口／模型授权时只记录未验。真实 LLM 对短口语的判断仍需后续独立评测；本阶段不声称已达到真实模型准确率。
- [x] **Step 6：审阅并提交本任务差异，更新完成状态。** 提交建议：`test(xiaohe): verify lead policy upgrades and submission recovery`。检查所有任务状态，留下固定 SHA、前端交接状态、测试结果、迁移限制及剩余未验项；未经后续指令不推送、不打包、不部署。

## 计划自检与实施入口

设计 §5／§6 对应 Task 1、3；§7 对应 Task 4、5；§8 对应 Task 5；§9 对应 Task 2、3、8；§10 对应 Task 6、7；§11 对应 Task 1、2；§12 的十类验收均落入各任务及组合验证。版本存储、证据来源、函数命名和投影属性在上下游任务一致，未计划新增必填、知识检索或通用工作流平台。

建议后续在本对话逐项实施：任务之间共享字段、证据和版本接口，顺序实现更容易保持一致；每项有独立测试和提交边界。也可选分任务子 Agent 实施及独立审阅。**用户已选择本对话逐项实施；后端 Task 1—6 完成，Task 7 已形成门户交接并等待其回执，Task 8 后端独立部分完成（918 passed、5 skipped、10 deselected），门户组合部分待回执。未授权推送或部署。**

## 实施中的调整

- 新 Command 在 admission 中固定交互版本及 `leadWorkflowBundleId`，恢复时不猜测是否升级；Run 外壳绑定保持。
- 已部署但未提交的三角色留资修复因本次依赖单独提交为 `02c0b18`；VERSION、域名及部署文件未纳入。
- 无 Skill 的历史 workflow 使用固定 `legacy-lead-fields-v1.json` 适配格式校验，避免第一次读取发生在配置修改后而提高原门槛；旧分组之外的正式资料保留校验定义。
- 标准 `tests/xiaohe` 全目录在基线已有 integration/test_deploy_proxy.py 同名导入冲突；独立服务集成另需专用 HTTP/MySQL/MinIO 环境。本轮完整业务回归排除 integration 目录及真实模型评测文件，不使用现有三方环境。

## 本轮实施结果

实现与验证基准：`0fa7a1659e335b8d45cc2f19908e2ddff78731b4`。独立审阅两项重要问题已修复并通过完整顶层业务回归，详情见[验证记录](../../testing/xiaohe-lead-skills.md)。Task 7 的门户实现／回执及 Task 8 的门户组合验收仍未完成；暂停这些依赖项。本轮不推送、打包或部署。
