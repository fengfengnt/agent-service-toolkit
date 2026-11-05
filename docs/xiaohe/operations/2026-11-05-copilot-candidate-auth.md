# Copilot 候选会员认证准备

日期：2026-11-05。G1 完成后，按已授权 P3 范围准备候选可信认证。A5/A6 无短信适配通过；其后门户 Owner 已回报真实浏览器 G2 达标，见末尾分层补充。整体迁移收尾和正式部署仍未批准。

## 变更范围

最终仅候选 API/worker 使用 `yunhezhiyuan-aibackend-candidate:spike-0d18e81`，源码 revision `0d18e819bfe3a0b7295efc609a3289caf1392ead`，运行镜像 ID `sha256:b7b5d59ddb49bdd1d83e02c64f943d801b341e14edf68599d5af2b8ec9cb8514`。两容器保持 host network，API `127.0.0.1:18111`，原 Runtime 镜像/服务及共享 Nginx 不变。先前 `spike-fdadc8d` 镜像保留用于追溯，最终候选已包含审阅发现的配置别名修复。

原 API 容器 ID 仍为 `9a813112...`，原 worker 为 `841dbbf0...`；原门户 HTTPS 仍 200。没有修改旧数据库、SSO 登记/服务或正式开关，没有新建/轮换应用凭据。

候选库 `xiaohe_copilot_candidate_20261105` 只将 portal 的 `portal_client_id` 从 `candidate-only` 改为 `app_localportal`；`tenant_id=1`、issuer=`https://sso.lvh.me`、Origin=`https://portal.lvh.me` 保持。OAuth管理owner166、会员来源1和AI业务租户1继续分层。

## 私有注入与启动限制

- 现有合法私有来源为原 AI 配置目录的 `sso-bridge.json`，Owner 脚本仅通过只读挂载复用；密钥值只由容器进程读取，不进入 Agent 输出、候选 JSON、仓库或归档。
- 候选主配置仍为 WSL `/home/fengfeng/yunhezhiyuan/copilot-candidate/candidate.json`，模式0600，挂至 `/run/candidate/candidate.json`。显式 `sso_bridge_enabled=true` 只影响候选；未 opt-in 时，即使秘密文件已挂载也不读取、不启用。
- 既有 Bridge 文件只读挂至 `/run/candidate/sso-bridge.json`；公共 CA 只读挂至 `/run/candidate/public-ca.pem`。不复制证书私钥，不关闭 TLS 校验，不放宽 SSO 可信 peer。
- 只有现行五字段完整、issuer/application/callback 精确匹配才允许启动；基础 JSON 不接受其余 Bridge 字段覆盖。CA 必须在容器内可见且能建立严格 SSL context。
- 固定 application=`31555063-bbe3-4907-8745-c553208fff23`，callback=`https://portal.lvh.me/portal/auth/callback`。旧8443 issuer、候选自造 callback、错误app、缺失配置/CA、inline secret均拒绝。
- 基础 JSON 同时拒绝 `SSO_XIAOHE_BRIDGE_ENABLED`、`XIAOHE_SSO_*` 的受保护别名。加载器始终向 `Settings` 显式提供全部关闭值或已验证的挂载值，防止继承环境填入认证参数；未修改产品 `Settings`。
- 原数据库/桶/代理限制与受控模型图、短信中间件保持。候选主配置和 Compose 的启用前备份仅留在同机受限目录，可由 Owner 回退；不恢复或覆盖旧服务数据。

## 实际验证

最终候选启动测试与原三方启动器回归 **38 passed，19 条依赖警告**；Ruff 通过。合法 opt-in 首次 RED 后实现，原匿名资源边界仍全部通过。

真实探针在候选容器中执行，使用产品 `SsoBridgeClient` 与严格 TLS；图片、Cookie、CSRF、挑战及密钥只保存在探针进程内，不输出：

| 检查 | 实际结果 |
| --- | --- |
| 候选匿名 bootstrap、独立认证 context | 200 |
| 创建本探针的 restore operation | 201，进入 BROWSER_COMPLETION_REQUIRED，未提交 browser-completion |
| 原体重试创建 | 同一个 operation，不重复创建 |
| GET 图形验证码适配 | 产品客户端请求 `captchaType=blockPuzzle`，SSO HTTP200、业务 code0；未输出图片/挑战，未验证验证码 |
| 真实 socket | local=`127.0.0.1`，remote=`127.0.0.1:443`，在实际 HTTPS 响应的 socket 上测量 |
| 旧 Origin `https://portal.lvh.me:9444` | 403 |
| 候选 sms 路由 | 503，进入中间件即拒绝，未发短信 |
| 取消本探针 operation | 204 |
| 候选受控聊天 | 经 Runtime 完成，未调用付费模型 |

探针初稿用了错误 POST 方法请求中心 captcha，HTTP200/业务405，未当作验证码成功；按产品实际 GET 方法及参数修正后才记录 code0。各轮仅创建并取消自己的探针流程，没有真实账号登录、短信发送或会员归并。

## 整批审阅与修复

一次独立只读审阅发现 1 项 Important/P2：`Settings` 的别名与环境入口可绕过省略开关时的默认关闭检查。新增六别名和环境隔离/优先级回归，实际观察 6 项 RED；只在候选加载器修复后整套 38 项 GREEN。新镜像还在 Linux `--network none`、无真实秘密挂载条件下验证默认关闭与别名拒绝，并再次通过上表真实无短信探针。没有以原先正常路径通过代替边界修复，也未安排重复审阅。

审阅列出的真人与回调完整性继续留给 G2；当前部署状态由实施 Owner 另行实查。无待修复的本批 Important、无延期 Minor。保留 `dev-fengfeng`、原镜像/配置备份和验证记录，不合并、推送或清理并行改动。

## 门户后续接线与限制

Runtime URL 与 REST base 继续分别为 `https://portal.lvh.me/copilot-candidate/copilot-api`、`https://portal.lvh.me/copilot-candidate/ai-api/v1`。门户在独立浏览器 context 中保留已登记精确 callback `/portal/auth/callback`，由测试网络分流确保相关 `/ai-api` 回调续接到候选18111原 operation，不能改成未登记的候选 callback、复制旧 Cookie 或重放 code。

上述 A5/A6 准备探针没有提交 browser-completion、verify、claim 或真实登录，未验证八小时会员会话、账号绑定、访客归并或新旧会员文件连续性。当时 G2 保持待验；后续门户真实浏览器结果单独记录如下，不能用准备探针代替。不自动重发短信，不得据此替换正式门户部署或启用正式开关。

G1 的匿名专项 A4 测试仍要求关闭候选 SSO；认证准备阶段只执行了上表新增验证，未把此前匿名专项冒充本模式完整回归。

## G2：门户真实浏览器验收补充

证据来源是门户 Owner 会话 `01a19f12-7f6d-74a0-b853-95fafb351220` 的本轮执行回执，及其隔离控制器 `member-live-commands.js`、真实浏览器 helper `frontend/tests/browser/copilot-real.mjs`；代码位于 `C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal`。本方读取了控制器及保留断言，但没有接管浏览器、取得 Cookie 或重新执行真人流程。门户公开总报告 `docs/sso/2026-11-05-copilotkit-validation.md` 由门户 Owner 继续收尾，不将尚未提交的 G2 回执冒称其已有提交内容。

### 门户执行与回报

- 保留原隔离 Edge context，认证 complete 成功 200；超时定位为验收控制器 close 后立即 open、QDialog hide 尚未结束，仅修控制器等待 hidden，没有产品或后端变更。
- 同一 context 重开后，旧访客与会员两份精确文件版本实际浏览器下载 200，SHA-256 与原值一致。
- 随后只结束候选 AI 会话，保留中心登录；新增第二段供应商访客消息与文件后，经正常门户登录再次 complete 200，没有自动发送新短信。
- 门户输出 `G2_RESULT`：`existingHistoryPass=true`、`sameIdentityAndCanonical=true`、`preservedOldFiles=2`、`addedFiles=1`；累计精确浏览器下载 10 次。旧新消息按原 ID 顺序、时间及 parts 完整保持，三个精确文件版本的下载摘要保持。
- 隔离测试只接管该 context 的静态 GET/精确 callback 接线；API/SSO 使用真实网络，不是成功响应 fixture，也不是以 MinIO 直读替代浏览器下载。

### 本方独立只读佐证

只在候选 PG 的 READ ONLY 事务中聚合合成标记，不输出真实身份、手机号、Cookie、CSRF、授权码或对象路径。按控制器实际第二标记 `我代表供应商，Copilot已有历史追加第二段` 核对：

| 项目 | 只读结果 |
| --- | --- |
| 第一段访客 / 会员输入 / 第二段访客标记 | 各 1 条，均在同一 canonical |
| canonical 匿名摘要 | `65cbe9b971c7`；仍为会员 owner |
| 已完成归并 | 2 条，均匹配 canonical 同一会员 owner |
| `mergePending` / 活跃 run | false / 0 |
| 用户消息 / completed run | 3 / 3 |
| 文件 / ready version | 3 / 3 |

此前第一轮只读检查还核实旧访客与会员两份对象实际存在、大小和内容摘要匹配；该对象检查只作为存储层证据。上文浏览器下载及第三文件连续性来自门户实际浏览器回执，两个层次不混算。Runtime 从该次测试起点没有安全码日志行，不能据此宣称所有请求均未被拒绝。

**阶段结论：门户 G2 真实浏览器达标已有执行回执及后端只读佐证。** 门户继续旧 SDK 清理、完整回归及整批审查，G2 不代表最终迁移收尾或正式发布批准。本轮本方仅补文档和只读核对，没有重跑 G1、修改数据、重启服务或改变正式开关；现有部署和正式 false 保留。
