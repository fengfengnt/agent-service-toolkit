# 小鹤生命周期与历史分页 AI/Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改上游 Toolkit/SDK 身份和执行边界的前提下，实现长期匿名预算、独立 4h 内容周期，以及真实 10 条分页和 V2 有界实时视图。

**Architecture:** Python 业务层仍唯一决定身份、归属、额度、消息、运行和文件权限；复用现有 Session/Conversation/Run、admission、claim、安全清理及事件事务。新增独立匿名预算及小型历史查询模块，REST 与 Runtime 用显式表示版本协商；V1 full 保持，V2 不先读全量再截取。门户与 SSO 由原生工程各自规划实施。

**Tech Stack:** 现有 Python 3.12–3.14、FastAPI/Pydantic、SQLAlchemy/PostgreSQL/Alembic、LangGraph、Node >=24、TypeScript、CopilotKit/AG-UI；复用锁定依赖，不新增依赖或更换 SDK。

**Spec:** [用户已批准的共同设计](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/superpowers/specs/2026-11-06-xiaohe-lifecycle-history-design.md)；唯一 wire 来源为 [共享主契约 §12](D:/Code/yunhezhiyuan-agents/docs/contracts/2026-11-03-xiaohe-integration-v1.md)。此计划不复制另一份 DTO 规范。

状态：2026-11-07，北京时间，**用户已回复“审阅通过”，三工程计划已批准，按各工程原生方式执行**。稳定基线已交接，AI 继续 Task 1；未完成的勾选和下列命令仍不代表已经执行或通过。用户追加允许按需 WSL 隔离部署；现有真人体验库、正式入口与开关保持不变，不自动短信、不建真实账号、不调用模型。

## Global Constraints

- 在 `dev-fengfeng` 开发，保护现有修改/未跟踪文件；不自动合并其他工程或工作树，不改 `src/service`、通用 Agent registry/模型工厂和上游 SDK。
- 正式开关关闭；不启动/替换现有正式或真人体验服务，不自动短信、不建真实账号、不调用模型。允许按需建立新的专用 WSL 隔离服务、可丢弃数据库和桶；不借此迁移或清空现有体验库。人工步骤另行告知用户。
- `VISITOR_MESSAGE_LIMIT=100`、`VISITOR_RETENTION_SECONDS=14400`；成功受理的新 guest 用户消息/有效动作计 1 并滑动 4h。免费欢迎、系统 controls、AI 回复、读取、拒绝、幂等重试不计数、不续期。第100条回复可完成，第101条拒绝。
- 用户 AI 会话仍为 `created_at+28800秒` 绝对截止，不滑动；complete 丢失重读不建立新身份或延长旧截止。可信认证走既有顶层 SSO 往返，错误账号不自动切换或归并。
- 同浏览器匿名预算跨4h、关闭、隔天、登录认领和退出保留；预算最小元数据不定期清零；正文/文件按原安全点清理。全部凭据丢失后无法匿名识别，不引入指纹/IP合并。
- 所有业务写入口用同一 admission 原子计数；V1/V2 不各发额度。`systemId↔tenantId` 为服务端一对一，user_id 全球唯一且属于一个业务租户；源身份 sourceTenantId 不替代业务 tenantId。
- V2 首屏/每页至多10条单条完整消息，稳定 `(createdAt,id)` keyset；parts/精确文件版本不变。activeInput 在首屏之外仅提供定位入口。
- V2 明确 opt-in，V1 full 保留；不把未知版本或混用降级成成功。历史 cursor、历史 epoch、事件 UUID cursor、单调 sequence、ownership fence 各司其职。
- 不执行真实库迁移；计划批准后的测试只用显式 `test_xiaohe_*` 库及 mock transport/受控执行器。现有 pg_factory 会 TRUNCATE，绝不能指向体验/运行库；Task 1 的稳定基线门仍须先满足。

## Review Focus

1. 相同 createdAt 的新消息在旧页上界之前排序：不能混入冻结旧页或漏掉实时新消息（Task 4 测试）。
2. 旧guest已认领、旧token仍在浏览器：不能重发额度或读取会员数据，预算自身不清零（Task 2/3 测试）。
3. 历史页响应晚于同消息SSE delta：不能回退正文，也不能用该页水位跳过其他消息事件（Task 4/6/7 测试）。
4. waiting_input/active assistant早于首屏：定位须授权，未知delta先基线，不能额外塞首屏或丢整个对话（Task 4/6 测试）。
5. 第100条受理后与4h到期、claim/cleanup同时发生：配额满不取消自身回复，到期仍取消，会员数据不被误删（Task 3/7 测试）。

---

## 基线、所有权和文件地图

2026-11-07 稳定基线：嵌套仓库 HEAD `41598965d0633924ffcb6ff9cb80a92d58466887`，分支 `dev-fengfeng`，与 origin 同步。收口 Owner 已完成受控快进并交回源码写入权；当前仅 `.codegraph/` 未跟踪，保留。主契约在父目录，父目录不是 Git 仓库；仓库内同名文档是可分发镜像，不能冒称父目录已提交。CodeGraph 索引不包含 xiaohe 新增部分，源码核对使用 rg。

接待、system_notice、controls 与认证修补已由对应 Owner 整合；源树9个产品文件与稳定基线逐字一致。实际迁移唯一 head 为 `0009_workflow_definitions.py`（down_revision=`0008`）。本任务新增 `0010_guest_budget_history.py`，down_revision=`0009`；不改已存在迁移。下段保留此前阻塞记录，不能作为当前未整合状态。

审批后前置核对：主树 `7e0f2f6` 仍至0007；接待树 `e4f8a79` 已含 `0009_workflow_definitions.py`（revision=0009/down_revision=0008），并有正在实施的未提交认证、admission、schema、工作流和接待修改。**因此下文原预留0009文件名不可直接使用**；待 Owner 确认可整合的稳定提交及整合责任后，依据真实 head 修订编号再实现。此次批准不包含自动合并他树或代提交其脏文件，已通知门户协调前置。

| 文件 | 最小职责/改动 |
| --- | --- |
| `src/xiaohe/contracts_v2.py`（新增） | 只定义 §12 DTO/事件、严格校验；引用原 Viewer/Message/Part |
| `src/xiaohe/view_version.py`（新增） | 解析版本头及回显，统一拒绝未知/重复值 |
| `src/xiaohe/guest_budget.py`（新增） | 持久匿名额度凭据、存量承接、周期建立与幂等最小账本 |
| `src/xiaohe/history.py`（新增） | 最近10/旧页/按ID定位、cursor签验、epoch和一致水位 |
| `src/xiaohe/models.py`、`migrations/versions/0010_guest_budget_history.py`（新增迁移） | 预算/凭据/幂等元数据；Session预算关联、Conversation.history_epoch、Message.membership_sequence、AuthOperationRecord.expected_identity及索引 |
| `identity.py`、`admission.py`、`expiry.py`、`claim.py` | 原业务接入预算，保留原期限/清理/归并 fence；complete 按视图加载 |
| `conversations.py`、`events.py`、已有接待消息 writers | 消息加入序号；V1读取不改语义；V2事件含序列，归并使目标历史epoch递增 |
| `api.py`、`authentication.py` | 现有入口统一选表示，新增只读分页/定位路由、安全错误映射 |
| `agui.py`、`agui_projection.py`、`agui_projection_v2.py`（新增） | V1维持；V2有界投影、旧assistant基线和ready |
| `copilot-runtime/src/config.ts`、`business-client.ts`、`context.ts`、`runner.ts`、`server.ts` | 固定版本配置、严格新schema/错误及事件转发、info/SSE/错误回显；沿用现有Runner |
| `copilot-runtime/contracts/xiaohe-agui-v2.schema.json`（后续生成） | 唯一主契约未来V2机器块的生成副本，不手写第二套规范 |
| `tests/xiaohe/*`、`copilot-runtime/tests/*` | 各任务列明的安全/并发/表示回归；fixture为虚构身份 |
| `docs/xiaohe/operations/runbook.md`、`deploy/xiaohe/env.example`、`copilot-runtime/.env.example` | 仅在实施后记录新配置/承接回滚，不改现用环境文件/compose部署 |

统一基线已核对的持久消息创建点：`admission.py`（用户及 system_notice）、`conversations.py`（欢迎）、`worker.py`（assistant）、`events.py`（流式 assistant）。`claim.py` 移动既有消息并重发 upsert；`runs.py`、`artifacts.py`、`expiry.py` 另有状态/产物/到期事件。新增成员序号须覆盖上述入口，不能只修改 REST；LangChain 内部消息不属于业务 Message 持久化。

## 任务依赖和验证前置

Task 1（统一契约/基线）→ Task 2（数据/身份）→ Task 3（公共admission/到期）。Task 4（分页）依赖1/2，Task 5（各读取入口）依赖3/4；Task 6（投影/Runtime）依赖4/5，Task 7组合验证依赖全部及门户/SSO各自批准后的交付。

未来命令工作目录均为嵌套仓库；用已安装环境 `uv run --no-sync`，不借验证更新依赖。PG命令需先在**批准的可丢弃隔离库**中注入 `XIAOHE_TEST_DATABASE_URL`，名字必须 `test_xiaohe_`，各 fixture 可能清表；不在计划中写秘密。未来PG命令形式为 `uv run --no-sync pytest <files> -v`。Runtime命令加 `npm --prefix copilot-runtime ...`。不运行真实三方/浏览器/MinIO/provider探测来补本轮计划的“通过率”。

### Task 1: 冻结消费边界和统一基线

**Files:** 主契约§12；`tests/xiaohe/test_view_v2_contracts.py`（新增）、`tests/xiaohe/contracts/view_v2.json`（新增）；新增 `contracts_v2.py`、`view_version.py`；V2机器schema的主契约块及生成副本；`copilot-runtime/tests/view-v2-contracts.test.ts`（新增）。

**Interfaces:** `parse_view_version(request: Request, *, v2_only: bool=False) -> Literal[1,2]`；`set_view_version(response: Response, version: Literal[1,2]) -> None`；导出 `ChatSnapshotV2/HistoryPageV2/MessageBaselineV2/PublicSnapshotV2/EventEnvelopeV2/BootstrapInputV2/CreateAuthOperationV2/ExpectedIdentity`，字段以§12为准。

- [x] **Step 1:** 用户审阅本计划后，核对已整合接待基线和 migration head，列出所有 Message writers；门户/SSO确认§12消费及恢复边界。未满足不写代码、不真实迁移。
- [x] **Step 2:** 写 `test_v2_contract_rejects_unknown_fields_and_invalid_watermarks`：version只能2、messages<=10、pageSize=10、hasMore与olderCursor一致、空流cursor=null/watermark=0；sequence是1..9007199254740991整数，epoch/watermark是0..该上界，字符串/浮点小数/越界拒绝；parts沿现有完整union。`test_view_header_is_explicit` 覆盖无头/1/2/空值/重复/3；V2专属入口无2拒绝。restore必有expectedIdentity，其他intent禁止；V1不放宽。
- [x] **Step 3:** 运行 `uv run --no-sync pytest tests/xiaohe/test_view_v2_contracts.py -v`，预期新增实现缺失而FAIL；无外部请求。
- [x] **Step 4:** 新增最小DTO/版本辅助；在唯一主契约新增V2机器schema块，生成Runtime副本和固定fixture。原V1块字节及解析边界不放宽。
- [x] **Step 5:** 同命令预期PASS；`node copilot-runtime/tests/run.mjs view-v2-contracts` 预期PASS且无网络；核对主契约块与副本一致。
- [x] **Step 6:** 仅提交本任务文件，`git commit -m "feat(xiaohe): define opt-in v2 history views"`；父目录契约单独记录内容摘要/文件状态，不冒称已进Git。

### Task 2: 持久预算、可信承接和内容周期

**Files:** 新增 `guest_budget.py`、`0010_guest_budget_history.py`、`test_guest_budget.py`、`test_guest_budget_migration.py`；修改 `models.py`、`identity.py`、`test_identity_sessions.py`、`test_migrations.py`。

**Interfaces:** `bootstrap_guest_episode(db: AsyncSession, request: Request, response: Response, system: TrustedSystem) -> Actor`；`load_guest_budget(db: AsyncSession, session: Session, *, lock: bool) -> GuestBudget`；`adopt_legacy_guest(db: AsyncSession, session: Session) -> GuestBudget`。预算只承载额度，不输出预算ID或cookie摘要。

- [x] **Step 1:** 写 `test_budget_survives_close_next_day_and_content_purge`：用20→服务器时钟到期→新guestId/conversation→count20/limit100；断言旧消息不可读且欢迎只1条。`test_legacy_adoption_uses_retained_count_once` 覆盖正文已清但Session count有效、重试不重复预算；已撤销/归属冲突/计数缺失不补0；空白首次正常建立。
- [x] **Step 2:** 写 `test_budget_cookie_is_not_content_authority`：预算不能读取旧conversation/会员，已知坏/撤销预算凭据不当首次；内容Cookie缺失但旧有效周期仍在时AUTH_REQUIRED。`test_budget_renewal_and_rotation_never_reset_count` 检查31,536,000秒、Secure/HttpOnly/Lax/Path=/、无Domain、只存摘要；模拟更换受控凭据仍归同预算，旧凭据可识别撤销。没有自动定期轮换功能。
- [x] **Step 3:** 在隔离库运行 `uv run --no-sync pytest tests/xiaohe/test_guest_budget.py tests/xiaohe/test_guest_budget_migration.py -v`，预期FAIL。
- [x] **Step 4:** 增加 GuestBudget（scope/count/limit/current_session_id）、GuestBudgetCredential（摘要/scope/budget_id/撤销元数据）、GuestBudgetReceipt（最小幂等摘要及原周期）、Session.budget_id；保留旧计数字段作兼容镜像。同一迁移一次增加Task4所需history_epoch=0、membership_sequence=0/索引和Task5 nullable expected_identity，不在后续任务重写已提交/已应用迁移。约束scope和计数非负，budget总数不递减；Receipt按预算/种类/客户端键摘要唯一，不存正文/手机号/请求JSON。迁移只新增结构，不批量猜测绑定旧预算。
- [x] **Step 5:** guest内容Cookie设置有限持久Max-Age；user Cookie保持原策略。bootstrap有效guest复用；过期且剩余才建一个新周期，满额已过期返回429；预算仅代表未来额度。现有Session/Cookie可证明且无冲突才lazy承接，预算Cookie缺失但Session已关联时重领同预算凭据，不能另发额度。认领/注销后的合法匿名重开保留该预算，先验证旧内容授权已结束，不重开旧线程。
- [x] **Step 6:** 并发锁保留既有 Session→按ID排序Conversation 的顺序，再取预算行锁；不得出现持预算锁反等已有Session/Conversation的路径。bootstrap先无锁读指针，再按该顺序锁并重读；指针变化则释放本次事务重新读取，不在预算锁内追锁另一Session。唯一current_session指针+事务确保同预算只发一个周期/欢迎；测试用两连接竞争、锁超时断言无死锁，不新增通用锁框架。
- [x] **Step 7:** 同命令及 `uv run --no-sync pytest tests/xiaohe/test_identity_sessions.py tests/xiaohe/test_migrations.py -v` 预期PASS。迁移测试仅隔离副本，核对旧users/messages/artifact versions/user expires指纹不变；不降级真实库。
- [x] **Step 8:** 仅提交任务文件，`git commit -m "feat(xiaohe): persist guest budgets across content episodes"`。记录回滚边界：启用预算后不能退回会独立初始化Session计数的旧应用；结构回退不能当安全业务回退。

### Task 3: 所有业务入口共用预算与到期安全点

**Files:** 修改 `admission.py`、`identity.py`、`conversations.py`、`expiry.py`、`claim.py`、统一基线已有reception controls/writers；新增 `test_guest_budget_admission.py`、`test_guest_budget_races.py`；复用原 `test_admission.py`、`test_cleanup_races.py`、`test_guest_claim.py`。

**Interfaces:** `charge_guest_message(db: AsyncSession, session: Session, *, kind: str, client_key: str, payload_digest: str) -> GuestBudget`；`load_viewer(db, actor)` 从关联预算读当前总计，V1 Viewer字段不变；原 `_accept`/complete签名仅增加可选视图参数，原调用缺省不改。

- [x] **Step 1:** 写 `test_all_paths_share_one_budget_at_99_100_101`：REST和AGUI交叉成功计同一预算，100受理、101无Message/Run、重试/拒绝不扣不续。`test_system_controls_and_welcome_are_free` 明确受控系统动作不是用户发言。
- [x] **Step 2:** 写 `test_hundredth_reply_finishes_until_real_expiry`、`test_claim_logout_and_expiry_do_not_reset_budget`、`test_old_episode_retry_after_command_purge_is_not_redirected`、`test_concurrent_bootstrap_admission_claim_cleanup`；验证旧CSRF/文件/流失效、已认证目标内容不清理，最小账本保留但正文清理。
- [x] **Step 3:** 隔离库运行 `uv run --no-sync pytest tests/xiaohe/test_guest_budget_admission.py tests/xiaohe/test_guest_budget_races.py -v`，预期FAIL。
- [x] **Step 4:** 原幂等判定优先于扣减；新有效用户消息才同事务条件更新预算+兼容Session镜像+4h截止。预算并发原子拒绝第101；所有消息/动作/接待写入口核对分类，controls不走收费路径。旧周期已无授权的重试直接拒绝，不用Receipt恢复已过期正文。
- [x] **Step 5:** 清理继续先撤销访问、fence运行，再安全删除正文/对象；排除预算/凭据/最小Receipt，不破坏claim保护、上传advisory锁。claim只移动有权内容，源session撤销不清预算；logout不删预算cookie/元数据。禁止给旧guest权限重新赋成会员读取权。
- [x] **Step 6:** 同命令加 `uv run --no-sync pytest tests/xiaohe/test_admission.py tests/xiaohe/test_cleanup_races.py tests/xiaohe/test_guest_claim.py -v` 预期PASS；新时钟测试替代真实等待4h，不调用模型/中心。
- [x] **Step 7:** 仅提交任务文件，`git commit -m "feat(xiaohe): enforce shared guest budget admission"`。

### Task 4: 真分页、冻结成员与授权定位

**Files:** 新增 `history.py`、`test_history_v2.py`、`test_history_v2_races.py`；修改 `conversations.py`、`events.py`、`claim.py` 及统一基线所有消息创建点。结构字段/索引已由Task2一次增加，不重写历史迁移。

**Interfaces:** `load_snapshot_v2(db: AsyncSession, actor: Actor, conversation_id: UUID, *, cursor_secret: SecretStr) -> ChatSnapshotV2`；`load_history_page(db, actor, conversation_id: UUID, before: str, *, cursor_secret: SecretStr) -> HistoryPageV2`；`load_message_baseline(db, actor, conversation_id: UUID, message_id: UUID) -> MessageBaselineV2`；`encode_history_cursor(payload: HistoryCursor, secret: SecretStr) -> str` / `decode_history_cursor(token: str, secret: SecretStr) -> HistoryCursor`。内部HistoryCursor明确含scope_digest/principal_digest/conversation_id/history_epoch/ceiling_sequence/before_created_at/before_id，不输出为公共DTO；secret由调用点传当前Settings.auth_secret，不从全局环境另读。

- [ ] **Step 1:** 写 `test_35_messages_page_10_10_10_5`：相同createdAt亦无重复遗漏、保持ID/parts/精确version。`test_append_with_same_timestamp_cannot_enter_frozen_older_page` 在上界前后随机UUID，新增消息只走实时，不进入旧页。
- [ ] **Step 2:** 写 `test_merge_invalidates_target_history_not_ordinary_append`、`test_cursor_cannot_cross_owner_system_or_episode`、`test_active_input_locates_old_card_without_eleventh_initial_message`、`test_late_page_baseline_is_not_global_event_progress`；恶意cursor不回显内部身份；源guest只FORBIDDEN/过期，不披露target。
- [ ] **Step 3:** 隔离库运行 `uv run --no-sync pytest tests/xiaohe/test_history_v2.py tests/xiaohe/test_history_v2_races.py -v`，预期FAIL。
- [ ] **Step 4:** Conversation新增history_epoch=0；Message新增membership_sequence=0用于存量。新Message在其首个upsert前设置为即将分配的Event.sequence，后续正文upsert不改；welcome/用户/assistant/system_notice等创建点全部覆盖。存量0归入初始视图，新seq必须严格更大。归并按目标新upsert序号重置被移动消息的membership_sequence并递增目标history_epoch，ID/createdAt不变。
- [ ] **Step 5:** 按scope/canonical/epoch及 membership_sequence<=初始watermark筛成员，createdAt/id keyset取11判断hasMore、返回10正序；复用Conversation短事务锁取得page/body水位及activeRun/activeInput，不查询全量。使用Task2的 `(conversation_id,created_at,id)` 索引；用查询记录断言LIMIT和有界读取，避免实现为Python切片。记录事件/epoch递增和V2序列化均检查安全整数上界，不截断；增加 `test_sequence_and_epoch_overflow_fail_closed`。
- [ ] **Step 6:** cursor用标准库HMAC-SHA256签名，密钥由现有AUTH_SECRET以固定上下文 `xiaohe.history-cursor.v2` 派生，不新增基础设施或输出密钥；内部含scope/主体绑定摘要/canonical/epoch/成员上界/before键，定长校验+常时验签，最大2048字节。密钥变化导致旧cursor失效后重取视图，不变更身份或配额。消息定位与页读取都独立鉴权/CSRF，不靠cursor授权。
- [ ] **Step 7:** 同命令及 `uv run --no-sync pytest tests/xiaohe/test_event_recovery.py tests/xiaohe/test_guest_claim.py tests/xiaohe/test_welcome.py -v` 预期PASS；用页水位只比较同消息基线的模型断言覆盖迟到页，不能声称已验证门户滚动。
- [ ] **Step 8:** 仅提交任务文件，`git commit -m "feat(xiaohe): add authorized keyset history views"`。

### Task 5: bootstrap、complete 和快照统一 V2

**Files:** 修改 `api.py`、`identity.py`、`authentication.py`、`claim.py`、`conversations.py`；新增 `test_view_v2_api.py`；复用 `test_auth_operations.py`、`test_http_api.py`、`test_auth_privacy.py`。

**Interfaces:** `bootstrap_session(request: Request, response: Response, db: AsyncSession, *, view_version: Literal[1,2]=1, expected_identity: ExpectedIdentity | None=None) -> ChatSnapshot | ChatSnapshotV2`；`complete_auth_operation(factory: async_sessionmaker[AsyncSession], request: Request, response: Response, operation_id: UUID, *, view_version: Literal[1,2]=1) -> ChatSnapshot | ChatSnapshotV2`；`require_restore_identity(operation: AuthOperationRecord, identity: VerifiedCompletion, trusted_source: User | None) -> None` 在bind_user前执行；`require_session_csrf(request: Request, session: Session) -> None` 在identity.py复用既有常时校验供V2 GET使用。拒绝约束不授予身份，鉴权和已提交认证结果只执行一次。

- [ ] **Step 1:** 写 `test_bootstrap_complete_snapshot_share_v2_shape`、`test_complete_v1_v2_reread_keeps_same_identity_and_deadline`；202保留MERGE_PENDING/Retry-After；原绑定上下文恢复响应丢失后仍同8h，不重复claim/换码。写 `test_v2_get_rejects_stale_csrf_after_cookie_account_change`。
- [ ] **Step 1a:** 写 `test_restore_expected_identity_is_frozen_before_redirect`、`test_restore_other_account_rejected_before_bind_or_merge`：可信旧AI绑定A与客户端约束冲突时create拒绝；AI Cookie全失时预期A只作拒绝约束，claim B的complete即409且User/Session/Conversation/GuestClaim行数不变。直接调用complete不绕过、同operation异约束不覆盖、相符claim才可绑定；预期A不能自行造出用户A或跳过中心claim。新增 `test_logged_in_bootstrap_guard_precedes_private_snapshot`：POST/session预期A+Cookie B直接409，缺/过期/guest Cookie401且不新建匿名；匹配A才返回有界快照。匿名仍空body。
- [ ] **Step 2:** 写 `test_live_full_guest_is_readable_but_expired_full_guest_has_no_new_episode`、`test_unknown_view_cannot_mutate_session_or_auth_operation`、`test_v1_contract_is_unchanged`；不向V1严格schema塞新字段。
- [ ] **Step 3:** 隔离库运行 `uv run --no-sync pytest tests/xiaohe/test_view_v2_api.py -v`，预期FAIL。
- [ ] **Step 4:** 在任何变更前解析版本；V2 bootstrap新增严格body `{}` 或 `{expectedIdentity}`，有预期时先比当前受权User绑定，不向已登录门户闪现guest/另一账号私有快照。V2 create使用CreateAuthOperationV2，将expected_identity冻结在原Operation/请求摘要内，可信旧User绑定优先作拒绝约束。中心六字段claim验证后调用require_restore_identity，再允许bind_user/新Session/目标对话/claim；不靠complete后的前端隐藏。全Cookie丢失时客户端约束不升级为可信身份。相同operation重读同约束/身份/截止，不新增中心参数或协议。
- [ ] **Step 4a:** 以同一已验证actor/canonical/完成事务调用Task4 loader，传显式cursor_secret，新增两个只读路由；将原聊天CSRF比较提取为require_session_csrf，V2 GET显式调用（当前_request_session对GET跳过，不能仅传csrf_required=True冒充已校验）；V1 GET策略保留。回显版本和no-store，统一新错误安全提示。auth/context/create/advance响应仍原非snapshot DTO，错误及流前错误回显已解析版本，非法头406不反射。complete重试不能先拿V1全量body再另开事务裁剪为V2。原同事务快照/水位边界保留。
- [ ] **Step 5:** 同命令加 `uv run --no-sync pytest tests/xiaohe/test_auth_operations.py tests/xiaohe/test_http_api.py tests/xiaohe/test_auth_privacy.py -v` 预期PASS，中心仅MockTransport。顶层SSO协议复用以SSO Owner计划为依赖，不新增iframe/popup/BFF或扩大frame权限。
- [ ] **Step 6:** 仅提交任务文件，`git commit -m "feat(xiaohe): negotiate bounded v2 session views"`。

### Task 6: 有界投影、流基线和 Runtime 版本门

**Files:** 新增 `agui_projection_v2.py`、`test_agui_v2.py`、`copilot-runtime/tests/view-v2.test.ts`；修改 `agui.py`、`events.py`、`contracts_v2.py`、Runtime的 `config.ts/business-client.ts/context.ts/runner.ts/server.ts`、既有Runner/安全测试；生成V2机器schema副本，不改V1副本。

**Interfaces:** `project_snapshot_v2(snapshot: ChatSnapshotV2, attempt_id: str | None) -> list[dict]`；`project_event_v2(event: EventEnvelopeV2, context: ProjectionContextV2) -> list[dict]`；`ProjectionContextV2` 保持最大10窗口及最多一个当前assistant内部基线；`RuntimeConfig.viewVersion: 1|2`；原XiaoheBusinessClient签名不变。

- [ ] **Step 1:** 写 `test_v2_projection_does_not_read_or_emit_all_history`、`test_hidden_active_assistant_has_baseline_before_delta`、`test_ready_allows_idle_eof_but_not_active_run_eof`；初始10条+额外独立baseline仍不是第11条首屏消息。
- [ ] **Step 2:** 写 `test_page_baseline_then_older_delta_does_not_duplicate_or_skip_other_message`、`test_epoch_or_ownership_change_terminates_old_stream`。Runtime新测试：配置默认1/显式2/非法失败；每个业务请求固定版本头，V2回显缺失/V1流/未知CUSTOM均拒绝，浏览器伪造头不能改上游配置；V1模式可接原严格V1响应而无回显，但不能接显式2。info/SSE/过滤错误均回显，info body不变；Number.isSafeInteger拒绝字符串和越界，不能把epoch/sequence转字符串。
- [ ] **Step 3:** 运行 `uv run --no-sync pytest tests/xiaohe/test_agui_v2.py -v` 和 `node copilot-runtime/tests/run.mjs view-v2`，预期FAIL；Python涉及PG者用隔离库。
- [ ] **Step 4:** agui selected/connect改成版本分派，V2直接Task4有界查询。V2事件附真实sequence/订阅epoch，重连从受权一致快照开始；raw events续传校验客户端historyEpoch。旧assistant只受控读一条完整基线并标水位，缓冲后续事件，先baseline再delta，对已包含delta按消息水位过滤；不能为了补基线全量重读或误跳别的消息。
- [ ] **Step 5:** V2 STATE保持当前10条窗口/权威控制，CUSTOM只发v2.event/message-baseline/ready，原运行成功/失败事件不换含义。ready仅证明通道受权快照就绪，活跃EOF仍异常；无run空闲正常ready后退出。不新增SDK消息历史数据库。
- [ ] **Step 6:** Runtime固定配置、严格V2schema、safe errors及事件白名单按§12；V1路径保留。server在info/SSE/错误统一回显固定版本，info不表示Python探测通过；不放开浏览器控制头/上游URL。配置示例仅文档列出默认1，不改运行env或代理。SDK的messages window不拥有门户history cache；将事件顺序和模型fixture交给门户Owner实现缓存，不代写门户组件。
- [ ] **Step 7:** 同命令与 `uv run --no-sync pytest tests/xiaohe/test_agui_api.py tests/xiaohe/test_agui_projection.py -v`、`npm --prefix copilot-runtime run typecheck`、`npm --prefix copilot-runtime test` 预期PASS；无实际模型/SSO/浏览器。无需build镜像或启动服务证明类型检查。
- [ ] **Step 8:** 仅提交任务文件，`git commit -m "feat(xiaohe): project bounded v2 views through runtime"`。

### Task 7: 隔离组合验证与交接证据

**Files:** 新增 `tests/xiaohe/test_lifecycle_history_journey.py`、`docs/xiaohe/operations/2026-11-06-lifecycle-history-verification.md`、`docs/xiaohe/evidence/2026-11-06-lifecycle-history.json`；仅必要修改 `runbook.md`、`deploy/xiaohe/env.example`、`copilot-runtime/.env.example`（如基线无该文件则在已有README配置节记录，不为凑模板新建）。

**Interfaces:** 只组合前述公共REST/AGUI DTO；证据分别记录 source/test/fixture、真实服务/浏览器/模型/部署及人工验收，后几项没有执行则明确未验证。

- [ ] **Step 1:** 写 `test_offline_lifecycle_history_journey`：匿名20→模拟4h→余80→mock可信登录/claim→35条10/10/10/5→旧card授权定位→同8hcomplete重试；所有身份/文件为隔离fixture，无真实手机号。另覆盖第100回复/第101、claim/清理并发、V1/V2共预算和迟到页，任何自动外网调用立即fail。协议fixture另覆盖缓存旧窗口后离线新增25条再重连：新olderCursor能补齐间隔，不能从本地最老ID跳过；CUSTOM delta与标准TEXT事件只记一次。
- [ ] **Step 2:** 隔离库运行 `uv run --no-sync pytest tests/xiaohe/test_lifecycle_history_journey.py -v`，预期FAIL后仅补组合缺口，不扩大业务功能；同命令预期PASS。
- [ ] **Step 3:** 运行本计划所列新测试与已有受影响身份/admission/expiry/claim/event/AGUI/文件授权回归及Runtime类型/测试一次；只在新失败或新改动时重跑相关项。物理对象清理如需MinIO，另用批准的可丢弃实例，未有则列为待验收，不偷用当前服务。
- [ ] **Step 4:** 与门户Owner交换生成fixture/契约版本、历史/事件水位序列和activeInput定位例子，待其在批准隔离环境完成滚动锚点/账号错配/顶层SSO/重开浏览器验证。SSO只复用现有框架；AI不代部署三工程，不把旧G2算作新分页通过。
- [ ] **Step 5:** 写证据及runbook：Cookie有限期限、预算与正文的分离、识别边界、旧计数承接筛选、混合版本风险、回滚限制。**真实库承接/部署为独立后续动作，不在本计划自动执行**；新运行时回退也必须保留公共预算控制，不能退回发新100的老admission。
- [ ] **Step 6:** 仅提交本任务文件，`git commit -m "test(xiaohe): verify isolated lifecycle and history flows"`；不push/merge/release。报告准确通过项/待验证项并交用户审阅，部署开关仍关闭。

## 计划自查与执行门

已按共同设计§4/§5/§6/§7逐项映射：通道ready/EOF→Task6；SSO恢复/原8h→Task5及SSO依赖；长期预算/存量/过期→Task2/3；真分页/卡片/水位→Task4/6；V1兼容→Task1/5/6；组合证据→Task7。五项Review Focus各有具名测试；未把门户滚动、打开意图、前端发送守卫或中心认证实现算成AI代码任务。

用户已批准三工程计划，原生执行方式不再重新询问，使用 `superpowers:executing-plans`。开工检查尚未满足接待基线前置，目前未执行产品测试/迁移/服务命令、未修改上述产品文件；等对应 Owner 确认可整合稳定基线并处理归属后，重新核对迁移编号，再按任务执行。每项结果写入本计划专属 ledger，不把审批记录视为 Task 1 或新增功能已完成。
