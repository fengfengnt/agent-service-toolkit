# Copilot 候选会员认证准备

日期：2026-11-05。G1 完成后，按已授权 P3 范围准备候选可信认证；**无短信适配通过，真实会员与 G2 尚未验收**。

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

本次没有提交 browser-completion、verify、claim 或真实登录，没有验证八小时会员会话、账号绑定、访客归并或新旧会员文件连续性。真人验证码仍由用户输入，不自动重发短信。G2 保持待验；不得据此替换正式门户部署或启用正式开关。

G1 的匿名专项 A4 测试仍要求关闭候选 SSO；当前切换到认证准备模式后只执行了上表新增验证，未把此前匿名专项冒充本模式完整回归。
