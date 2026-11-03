# P0 接口与边界冻结记录

日期：2026-11-03。本文件保留 P0 冻结事实；用户随后明确授权的 P1 进展见[业务身份与会话](identity-sessions.md)。P1 增量不改本文件对应的公共合同及内部执行协议，也不代表 AI 后端已经可以运行接待业务。

## 基线与证据

- 仓库：`D:/Code/yunhezhiyuan-agents/agent-service-toolkit`，分支 `dev-fengfeng`。
- 上游基线：`836649674568320c0cc994553a8a930ed82398d0`。
- 设计与计划在父目录 `../docs/superpowers/`，不属于本仓库，不复制成第二份设计。
- 公共契约：`../docs/contracts/2026-11-03-xiaohe-integration-v1.md`，文档 v1.0.1 / wire DTO v1.0；执行期间只读。
- 该文件 SHA256：`68ce8cc2da56093caf9b90a98761cf85f782653837e0d3e477e11cfc8441ed5d`。
- `tests/xiaohe/contracts/*.json` 全部是模拟 fixture；其中标识、凭据、时间、文本均不代表真实记录。

## 已实现的类型边界

`src/xiaohe/contracts.py` 定义公开 camelCase DTO、六类 ChatPart、七类事件及对应 payload、全部 RunStatus/AuthStage、认证操作与产出物视图。未知字段/类型拒绝；消息、选项、建议 ID 在对应列表中唯一。输入按 Unicode 码点计数且保留原文，动作恰好提供非 null 的 optionId/text 之一。merge 非 null 只允许 user viewer；delta 和 run.updated 要求非 null runId，后者还须匹配 payload.run.id。

事件 ID 与 snapshot.cursor 使用同一不透明字符串格式。P0 只验证形状和稳定传递，不证明数据库水位一致、原子事件写入或回放去重；这些归 P3/P6。MessageAccepted 不含 cursor；重试读取当前 viewer/status 的行为归 P3。

AuthActionBody 是内部按 action 判别的容器，实际公开 HTTP body 是其 body，不能给门户增加 action 包装。认证字段采用 SecretStr：repr/Python dump 保留脱敏对象，**显式 JSON 序列化会恢复必要的真实 wire 值**，只能用于受信认证传输或对应浏览器表单，不能用于日志、消息、事件、模型上下文或 checkpoint。校验错误不应通过 errors()/原始 body 写通用日志，后续 API 必须转换成安全 ApiError。browserVerifier 只能由原浏览器加入表单，服务端 completion.fields 拒绝包含它。

URL 字段的类型校验不等于授权：issuer、Origin、callback、完成端点及浏览器表单目标仍须在 P8 对照受信登记精确核验。P0 没有开放任何 HTTP 业务入口。

## 身份与会话

中心 VerifiedCompletion 只有 issuer、sub、sourceTenantId、authenticatedAt、portalClientId、transactionId 六字段；没有 AI userId、authorizationId 或 session_valid_until。BoundCompletion 在内部附加 TrustedSystem，不改变中心 DTO。

AI 首次可信登录生成全局 UUID user_id，原子绑定 `(issuer, sub, sourceTenantId)`，以后重试/并发复用。sub 是不透明值，不解析；AI user_id 与中心会员 ID 不同。一个 user_id 只属于一个 tenant_id，每个 tenant_id 只对应一个 systemId，冲突拒绝。唯一约束、映射事务、真实权限检查在 P1/P8/P9 实现，P0 fixture 不证明这些行为。

已确认的本地登记为 `systemId=portal → tenantId=1`，client `app_localportal`；中心 issuer `https://sso.lvh.me:8443`，Portal Origin `https://portal.lvh.me:9444`，callback `/portal/auth/callback`。桥接 applicationId `31555063-bbe3-4907-8745-c553208fff23` 只是预留值，不表示真实登记。

访客配置默认 100 条/14400 秒；AI 会话规则是创建后绝对 28800 秒、不滑动。P0 仅实现访客配置和身份类型，没有会话服务、Cookie、计数器、续期或过期清理。

## 服务端配置

`Settings` 只在显式创建时读取环境，不在 import 时加载 secret 或启动 provider。

| 环境变量 | 内容 |
| --- | --- |
| `TOOLKIT_BASE_URL` | 必填，由部署方控制的内部 HTTP(S) origin；不含用户信息、路径、query、fragment。实际网络隔离由 P11 落实 |
| `AUTH_SECRET` | 必填，非空 ASCII Bearer；与上游共用同一部署值，禁止下发浏览器 |
| `VISITOR_MESSAGE_LIMIT` | 正整数，默认 100 |
| `VISITOR_RETENTION_SECONDS` | 正整数，默认 14400 |
| `XIAOHE_DATABASE_URL` | 可选业务连接引用，SecretStr；P0 不连接数据库 |

未新增模型供应商、API base URL 或模型客户端。后续直接使用上游 `schema.models.AllModelEnum`、`core.settings` 与 `core.get_model`，本次未加载/验证真实模型配置。没有生成或复制运行 secret，未改 `.env`。SSO 每应用 Bearer 与 AUTH_SECRET 是独立秘密。

## 内部执行协议

客户端 `ToolkitClient.stream_execution(ToolkitExecution)`：

```text
POST {TOOLKIT_BASE_URL}/internal/xiaohe/runs/{run_id}/stream
Authorization: Bearer <AUTH_SECRET>
Accept: application/x-ndjson
Content-Type: application/json
```

body 仅包含服务端确定的 `run_id,user_id,thread_id,attempt,ownership_epoch,mode`，mode 为 start/recover/resume。不接收 agent_config、configurable、checkpoint_id/checkpoint_ns 或额外配置。没有浏览器 body 透明转发功能；调用方必须先完成业务授权并构造可信命令。当前类型不能证明命令归属正确，P4 服务端还必须查询业务库核验。

响应必须 HTTP 200、`application/x-ndjson`，每行 UTF-8 JSON，行尾 LF/CRLF；允许空行。内部字段保留 snake_case，parts 内仍为公开 camelCase。kind 判别如下：

| kind | 其余字段 |
| --- | --- |
| delta | message_id:UUID、delta:string |
| outcome | outcome:{parts,wait_for_input,artifact_version_ids:UUID[]} |
| error | code:string、retryable:boolean |

每次流只有一个显式 outcome/error 终结；终结后事件、无终结 EOF、无行尾帧、未知/损坏/超过 1 MiB 的帧均是内部协议错误。小块 delta 立即交付，不等待凑满缓冲区。不把 EOF、原生 `[DONE]` 当业务成功；不自动重发请求或切换执行模式。等待输入由 outcome.wait_for_input 表达。

内部 URL 来自服务端配置；客户端不跟随重定向、不使用环境代理。连接等超时 30 秒，读取空闲超时 120 秒；异常转换成无原始响应/请求上下文的 ServiceError。HTTP 状态失败用 UPSTREAM_UNAVAILABLE，损坏协议用内部 UPSTREAM_PROTOCOL_ERROR，后者不是新公开错误码。P4/P6 决定安全的持久失败与公开映射。

**执行服务端尚未实现，真实调用会失败。** P0 只以 HTTPX MockTransport 验证客户端；P4 增加该内部路由并复用上游 verify_bearer，P6 装配入口，P11 完成网络隔离。公开 SSE 仍按共享合同，不改成 NDJSON。

## SSO 交接差异与后续门

已只读核对认证源隔离 worktree：`C:/Users/70367/.codex/worktrees/sso-backend/xiaocai-admin-server` 的 `docs/sso/backend-integration.md`、`sql/mysql/upgrade/2026-11-03-xiaohe-sso-bridge/README.md` 及交接文件。中心代码完成不代表已经部署或可调用。

P8 需按该 README 实现 `/internal-api/system/sso-bridge` 私有 HTTP 适配：独立应用 Bearer 与 applicationId、TLS/受信 socket peer、可信 IP/UA 转发；create/SMS 各自操作级 Idempotency-Key，claim 固定为 transactionId；CommonResult 与语义 HTTP 同时核验。中心 stage 与公开 AuthStage 是不同状态机，不能直接共用枚举。事务 600 秒、credential 120 秒、claim 结果至 exchangeAt+600 秒。

两处待 P8 明确，不修改本次公共合同：

1. 中心实现的 restore 使用顶层 POST `/sso/bridge/browser-completion`，省略 credential 但仍提供独立 browserVerifier；不能照旧规划将 restore 简化成无 verifier 的 OIDC GET。现有 BrowserCompletion 可表达 POST，但具体阶段/前端行为需与 Portal 对齐。
2. 未成功换码的 claim 是 HTTP 409 / Java code 1002029027（AUTH_NOT_READY），不能映射成已认证的 MERGE_PENDING。公开安全错误语义在 P8 对齐；不能重换已消费 code。

尚未落实：真实应用登记及安全密钥交付、AI 实际出站 peer/代理头链测量、SSO 私有入口、到期后新认证上下文/CSRF、数据库和运行服务、真实不同 site 浏览器续接及人工验收。已验通的 New API OAuth2 通路不改 provider/client/端点/UserInfo/sub 映射。

## 验证

Python 3.14.7，`uv sync --frozen --python 3.14.7` 成功；uv.lock 未重新解析。Windows 缓存重命名及硬链接回退警告后正常完成。

```powershell
uv run --frozen --python 3.14.7 pytest tests/xiaohe/test_contracts.py tests/xiaohe/test_toolkit_boundary.py -q
uv run --frozen ruff check src/xiaohe tests/xiaohe
uv run --frozen --python 3.14.7 pytest tests/client/test_client.py tests/service/test_threads.py -q
```

受控上游基线实施前 22 项通过。新增测试先因业务模块不存在而 RED；凭据异常回显及小块流缓冲分别由断言复现后修复。独立只读审阅发现两项重要问题：可选字段默认输出 null，以及时间类型接受数字时间戳；均补充断言观察 RED，再针对具体字段修复。默认序列化省略可选值，快照的必填 null 保留；时间仅接受 UTC ISO 字符串或内部 aware datetime。

最终执行上述新增与上游测试的合并命令：**89 项通过（新增 67、上游 22），19 条既有依赖警告；Ruff check/format check 通过**。上游依赖警告包括 LangGraph 迁移、langchain-community 停更和 Google gRPC 未来版本提示，本次未更换依赖。保护路径、pyproject、uv.lock、原测试配置均无 diff；共享合同 hash 不变。未留下待修的重要审阅问题。

未执行真实 SSO、短信、模型、数据库、MinIO、浏览器或部署验收；未实施 P1–P12。
