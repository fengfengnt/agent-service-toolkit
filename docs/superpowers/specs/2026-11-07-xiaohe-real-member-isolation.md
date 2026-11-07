# 小鹤真人会员 V2 隔离补验设计

用户在门户聊天 `01a19f12-7f6d-74a0-b853-95fafb351220` 的真实指令（turn `01a1b5fd-07e6-7fc2-bb17-3a63f5bb81af`）已由 Root 只读核验：继续按隔离方案补真人会员恢复、历史分页、文件归并；各工程原生执行，需登录/短信时通知用户，正式开关关闭、不替换现有部署、不调用付费模型。此授权允许准备和启动新的隔离服务，不再次请求启动许可。

旧受控阶段固定为 AI `03556c050380f01406596dc0ded6e64a95e9e4f0`；其中 729 个主目录用例为分批覆盖，匿名 Chrome、10 物理接待文件、6 Compose 已验。新阶段独立记录，不能把 FakeBridge 或合成 claim 改称真人成功。

## 执行范围与选择

Root 选择独立新服务、新数据库和新私有存储，避免改动已验的 18593 匿名栈。可复用本轮独占 PG 35547 的**服务器资源**，新建数据库 `test_xiaohe_reception_real_member_20261107`；不能运行 `pg_factory` 或 TRUNCATE 预览库，不读取/克隆旧真人体验库。初始化只建立迁移、System 登记元数据及已有 workflow/saver/store 定义，不 seed User、聊天、文件或认证结果。

拟用新端口 API 18093、Runtime 18095、TLS gateway 18595、MinIO 35549；启动前核占用与资源，不覆盖已用端口。进程方式沿用已验证的 Windows 原生 Python/Node＋WSL Docker 独占 PG/MinIO，不能标成整套 WSL 容器发布。

公网逻辑 Origin 固定 `https://portal.lvh.me`，callback 固定 `/portal/auth/callback`，真实中心与 BFF 使用现有登记和网络。门户只在全新专用 Chrome context/profile 内替换静态 GET/精确 callback，并将 AI/Runtime 请求引到新服务。不能改共享 Nginx、hosts/DNS、真实应用注册或正式静态包；原 18593、18090、18110/18111、原会员窗口和新旧证据/profile 均保留。

Runtime 配置继续使用逻辑同源 HTTPS；新测试进程通过现有 `createRuntimeApp(..., {fetch})` 注入，将逻辑 443 的 `/ai-api/v1/agui/{run,connect,status,stop}` 定向新 TLS gateway。同主机名 SNI/CA 校验保留，只允许精确源与路径，不回落原 443 API、不降低产品校验。此映射只在隔离进程，产品/SDK 无需改动。

## 真实 Bridge 与模型

新 API 继续复用已有受控 `JourneyModel` 图/worker/harness，但必须把 SSO `MockTransport` 替换为现有 `SsoBridgeClient` 的真实传输。实际协议为 `/internal-api/system/sso-bridge`、Bearer 应用凭据及现有 application/IP/UA headers；不新增中心协议或接口。

Issuer、portalClientId、callback、System/businessTenant、受信 socketpeer/IP/UA 规则及既有私有注入路径由 SSO Owner 只读确认。进程只白名单加载必需的 Bridge 字段，不继承旧数据库、旧存储、业务写地址或其他秘密。secret 值不进入模型上下文、控制台、公开 JSON、Git 或交接；不注册应用、不轮换凭据、不改中心开关。

新进程模型固定为受控替身，禁用 tracing/telemetry，真实 HTTP 仅允许新内部端点和已确认的中心路径。不能把“已加载配置/中心 TLS 可达”写成“真人 claim 已通过”。SSO Owner 已确认 Create/Cancel 会写中心事务并消耗预算，本轮不做额外预认证事务；配置/TLS准备完成后，等门户正常登录流程实际验证凭据，不绕过认证。

登录和短信由门户告知用户在隔离窗口操作，不自动获取/重发验证码、不创建真实账号、不收集或导出 Cookie/storageState。真实身份只能来自中心返回并通过现有校验，不使用 DB seed、MockTransport 或伪造用户状态。

## 文件与历史的验收语义

沿原生接待上传链使用本轮无敏感测试文档，实际写入新私有 MinIO。先匿名下载取得 200 和 SHA256，再正常登录/claim。归并后原 file ID、字节数、SHA256、可下载字节、原聊天 message ID/createdAt 保持；刷新、真实关闭重开和分页后仍可下载。必要的旧匿名/异系统拒绝优先消费既有具名用例，追加现场只读验证不得导出旧 Cookie 或影响真实资料。

原生 `FileRef` 没有数字 `version`，其身份由固定 file ID 与不可覆盖的 ready 对象/摘要构成；`Draft.revision` 和 `ReceptionView.version` 不能冒充文件版本。若使用原有产出物链，则按实际 `artifact_ref.artifactId + version` 核验。不能为验收新增版本字段、正式接口或伪造 artifact。

聊天/文件均通过真实产品 API 与浏览器产生，不靠 seed 补历史或补对象。不得向真实业务后端提交测试申请；本轮目标是新 AI 域内所有权、恢复和字节链，不是修改原申请、分派专员或业务受理。

## 证据与完成条件

1. Root 给出精确端口、作用域、PG/bucket、V2/受控模型/存储实际健康与真实 Bridge 配置层级，证明所有旧资源未改；门户提供仅新 member-dist 的 true 测试包，正式 false 包不变。
2. 用户在门户准备的新窗口完成既有账号认证后，Root/门户分别验证可信身份与本地绑定、claim、分页和真实文件字节。仅共享公开脱敏证据，真实身份必要字段用不可逆摘要或私有记录，不公开手机号、Sub、凭据和 OAuth 参数。
3. 记录 HTTP 状态、文件/消息身份与摘要、绝对 8h 截止及恢复结果；没有留存的状态不补造。所有新结果写新阶段 JSON/Markdown，旧 `03556c0` 不改。
4. 普通提交/推送 `dev-fengfeng` 并核远端。无真人步骤时先完成所有准备；等待用户输入属于有明确操作步骤的交接，不以假身份替代。未验证的真实 24h、运行中停止、负载和正式发布继续单列。

## 人工补验后的本阶段交付边界

工具在用户置前窗口后仍无法合法确认 URL，因此停止自动 UI 检查，没有换通道绕过。协调聊天真实人类 turn `01a1b640-fe5f-7c30-bdcc-a45653006fd5` 对问题 `call_f7db19cf89d94559911f1c4ca7aa8f15` 回复“三项正常（可补充最早序号）”：小鹤弹窗关开、1～20 测试对话完整有序无重复、普通刷新后会员/历史/原文件保留。Root 已读取原始回答。没有提供最早序号，不补造首屏精确数量。

本阶段据这份人工反馈与刷新后独立只读核对收口并正常提交推送；自动首屏/分页、真 browser context 同 profile 关闭重开、刷新后再次 HTTP 下载不在本阶段宣称通过，继续列为剩余未验。原文件会员浏览器 HTTP GET 的既有 200/SHA 证据保持单列。该取舍不修改产品或绕过 URL 安全检查，不把人工验收升级为自动化结果。
