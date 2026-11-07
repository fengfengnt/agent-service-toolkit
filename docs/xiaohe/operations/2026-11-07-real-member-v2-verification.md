# 真人会员、V2 历史与文件归并：新隔离阶段

2026-11-07，北京时间。当前状态：**真人绑定、原消息/文件归并、会员浏览器原文件 GET，以及三项人工界面验收通过；刷新后同一会话与原截止只读复核通过。本阶段按人工证据收口，自动 UI 与完整浏览器生命周期仍未验。** 这不是正式发布，也不改写旧受控验收 checkpoint `03556c050380f01406596dc0ded6e64a95e9e4f0`。本轮交付只新增六份设计、计划及脱敏验收文件，业务源码仍固定于 `f7b36e7abf04ef94482e4b03166dd8f4b0c27ac8`。

按门户聊天用户真实 turn `01a1b5fd-07e6-7fc2-bb17-3a63f5bb81af` 的授权准备新隔离资源，由原工程 Owner 执行。需要用户登录或短信操作时，由门户给出具体步骤；Agent 不自动发送短信、注册、登录或调用付费模型。

## 已完成的准备与证据

| 层次 | 本轮实际结果 |
| --- | --- |
| 新服务 | Windows 原生 API `18093`、Runtime `18095`、TLS gateway `18595`，只绑定回环 |
| 数据 | 既有本轮独占 PG `35547` 中新建 `test_xiaohe_reception_real_member_20261107`，迁移到 `0010`；没有克隆真人数据或 seed 身份 |
| 身份登记 | `portal` → AI business tenant `1`；client `app_localportal`，Origin `https://portal.lvh.me`，issuer `https://sso.lvh.me`；中心管理 ownerTenant `165` 不作用户来源租户 |
| 存储 | 新容器 `xiaohe-v2-member-minio-20261107`，回环 `35549`，私有桶和独立受限应用凭据；HeadBucket 200 |
| Runtime | 逻辑 Origin 维持原登记 443，四个 AGUI POST 仅在隔离 fetch 注入点转发 `18595`；异源/越路径/重定向无旧 API fallback |
| 对话 | 实际 TLS、V2 session echo/no-store、Runtime→API→worker 受控回合通过，收到 `xiaohe.v2.ready` 及 `RUN_FINISHED` |
| 匿名文件 | 本轮独立诊断访客经原生 HTTP 创建草稿、初始化、上传并实际下载 200；68 字节 PNG 摘要一致，没有直接写数据库或伪造对象元数据 |
| 数据只读复核 | 浏览器启动前 `users=0`、`ready files=1`、`reception_submissions=0`，System/client/issuer/tenant 符合登记 |
| 新 helper 自检 | 路由 3 项、私有 loader 2 项通过；外呼 guard 2 项 RED→GREEN，通过后新鲜只读 readiness 再通过 |

公开脱敏快照见[readiness JSON](../evidence/2026-11-07-real-member-v2-readiness.json)。私有诊断在本计划 `.superpowers/sdd/2026-11-07-xiaohe-real-member-acceptance/`，包括 selfcheck、进程记录、readiness、日志和本次样本锚点；Cookie、CSRF、OAuth 参数与真实身份不导出。

Bridge 程序只从既有私有文件加载 `applicationId`、`secret`，显式覆盖旧配置中的 URL 为现443和原 callback。secret 未输出、复制进本轮 JSON 或纳入 Git。新 API 将 harness 的 FakeBridge 替换为现有 `SsoBridgeClient` 默认真实传输，`trust_env=False`，严格 CA 验证。**准备阶段只证明配置加载，上表和 readiness JSON 不作为真人通过证据；随后正常真人流程的核对另见下文。** 按 SSO Owner 确认，不为 readiness 额外 Create/Cancel 中心事务；它们会写事务并消耗预算。

模型保持 `JourneyModel`，不调用真实提供商；Python HTTP 只允许新内部进程和固定中心 Bridge 路径。没有配置真实业务写地址，没有提交业务申请。所有旧服务、原匿名 profile/数据/冻结构建保留，原 `18091/18092/18593` listener PID 核对未变；正式开关和共享反代不动。

## 真人认证后的独立只读核对

人类在新 Chrome 完成正常中心登录后，门户打开小鹤触发可信 restore，报告认证 complete 200、viewer user。Root 没有调用认证或消息接口，而是按已封存基线在新库执行 REPEATABLE READ READ ONLY，核对通过：

- 中心 verified completion 的 issuer/sub/sourceTenant 与本地 User 完全一致，client 为 `app_localportal`，中心事务关联匹配，AI 业务 scope 为 portal/tenant1，认证操作 COMPLETED。
- 原访客 source 只有一条 claim，状态 `merged`，目标对话为对应会员所有权。
- 原 41 条消息（20 条用户消息）的 ID、createdAt、role 和 parts 摘要全部保持。
- 原文件 ID、draft ID、createdAt、object key、68 字节及 SHA256 保持，归属目标对话；实际私有 S3 GET 200，内容摘要与登录前一致。
- 会员 session 建立于 `2026-11-07 21:38:59.692237+08:00`，截止 `2026-11-08 05:38:59.692237+08:00`，恰好绝对 8 小时；未撤销，toolkit_user_id 与真实绑定一致。新域业务提交仍为 0。

脱敏结果见[真实归并只读证据](../evidence/2026-11-07-real-member-claim.json)。这是数据库身份、记录归并和物理对象字节证据，**不替代会员浏览器验收**。门户首屏数量断言暂未通过，正核对测试驱动是否同时计入 SDK wrapper 与自定义 article；尚未据此判断产品分页故障或修改产品。

门户随后只读复核源码：原 driver catch 只保存错误 name，未保存 stack、message 或实际 count，不能锁定本次 AssertionError 行；SDK 默认 wrapper 是具名 slot 的 fallback，而门户已有自定义 assistant/user slot，现有证据不能证明双计。`open` 只等 input visible、`reopen` 不等待 QDialog 完全消失，存在测试时序缺口，但没有原 DOM/栈，仍不能认定为本次根因。没有修改产品、driver 或冻结构建。后文新增 5 条发生于后续动作，不用于倒推解释先前 UI 断言失败。

## 会员浏览器原文件下载与界面检查阻塞

Root 随后读取门户 `member-result.json`，确认 `member-browser-readonly-get` 为真实浏览器 GET 200，SHA256 与已封存的原访客文件一致；Runtime 写入仍 20、remoteStops 为 0、认证 complete 仅 `[200]`，未导出 storageState。`bound-member-reopen` 只证明关闭/重开聊天弹窗后同会员和 canonical、额外 complete 为 0，不等于关闭浏览器 context 后用同 profile 重开。原始 API 页长数组跨多个操作累计，也不作为 UI 分页通过证据。脱敏中间结果见[浏览器进度证据](../evidence/2026-11-07-real-member-browser-progress.json)。

门户的 CUA 入口先返回 `Browser is not available: chrome`；之后原 Computer Use 工具的窗口状态读取因无法可靠确认 Windows 当前浏览器 URL 被安全机制停止，未得到该次 AX 树或截图。没有换通道绕过拒绝。协调线程人类 turn `01a1b640-fe5f-7c30-bdcc-a45653006fd5` 已明确回复“专用窗口已置前，位于门户页面”，Root 只读核实了原始回答；门户只能在平台允许的新轮先确认可信 URL，再补剩余界面验收，再次被拒仍停止。本轮不要求重新登录、发短信或重放认证/归并。用户要求通过后才提交推送，当前 UI 条件未满足，尚未提交本阶段。

在上述人类确认后，门户于新轮沿原 `sky.get_window_state` 尝试合法恢复，仍在返回 AX/截图前被同一 URL 安全检查拒绝，已再次停止。门户给出当前窗口人工核验：关闭/重开小鹤观察最近消息及更早入口，加载第 1～20 条测试消息检查完整无重复，再刷新门户检查历史恢复。当时尚未收到人类结果，未提交推送；后续人工回复与只读核对见下一节。没有再次询问置前状态、切换通道、重新登录或重放认证/归并。

## 三项人工验收与刷新后只读核对

Root 已读取协调聊天原始人类 turn `01a1b640-fe5f-7c30-bdcc-a45653006fd5` 中对 `call_f7db19cf89d94559911f1c4ca7aa8f15` 的回复 **“三项正常（可补充最早序号）”**。这是小鹤弹窗关开、完整有序无重复的第 1～20 条测试对话、普通刷新后会员/历史/原文件保留的人工证据。未给最早可见序号，首屏精确条数保持未知，不将人工反馈写成自动分页通过。

随后 Root 不触 UI、不消费或导出 Cookie，只读复核新库及私有对象：

| 刷新后检查 | 结果 |
| --- | --- |
| 全库会员会话 | 总共 1 条，ID 与归并前后封存的原 targetSession 相同；不只检查旧行仍存在 |
| 身份 | 全库 User 1，与原绑定一致，没有另一个绑定用户 |
| 截止 | 原建立时间和 `2026-11-08 05:38:59.692237+08:00` 截止均不变，仍为绝对 8 小时 |
| 认证操作 | 总数 1，COMPLETED 1；原会话建立后新增操作 0 |
| 归并 | 全库仅 1 条 claim，仍为 merged，原 source/target/operation 关联保持 |
| 原消息 | 原 41 条 ID、createdAt、role、parts 摘要全部保持 |
| 当前消息总数 | 46：原 41，加既有 `save_round`、`skip_optional` 两条动作记录和 3 条助手反馈（包含归并续接）；额外 5 条的 message.upsert 事件均唯一并关联原 continuation_run |
| 原文件 | 原 ID/draft/创建时间/object key、68 字节及 SHA256 均保持，实际 S3 GET 200/字节一致 |
| 业务提交 | 仍为 0，无测试申请提交 |

Root 刷新后私有只读脚本首次把当前总数误断言为恰好 41，检查失败；这是另一条断言，不是门户先前的 UI 断言。随后只读核对源码 `ACTION_TEXT`、Command 的 `reception.action` 与事件关联，确认上述差异，再将私有核验脚本改为验证原 41 保持并逐项溯源额外记录，结果转绿。没有改消息、业务状态或产品代码，也没有由 Root 新发消息。刷新后结果纳入[归并证据](../evidence/2026-11-07-real-member-claim.json)的 `postHumanRefreshReadOnly`；人类反馈纳入[浏览器证据](../evidence/2026-11-07-real-member-browser-progress.json)的 `manualUiAcceptance`。其中只读检查自身的 browser 字段为 false，既有浏览器 GET 200 证据在另一份记录，不是互相替代。

## 浏览器准备记录与后续限制

门户随后在新的专用 headed Chrome 完成 20 条 UI 消息，捕获 41 条历史，并通过原生 guest HTTP 文件链实际上传和下载另一个 68 字节测试文件。Root 已独立在新库执行 REPEATABLE READ READ ONLY：该浏览器 subject 仍属 guest、scope 为 portal/tenant1，共 41 条消息（20 条 user），尚无 claim；私有对象 S3 GET 200、字节数和 SHA256 与门户报告相符。归并前 message ID/createdAt/parts 摘要和文件元数据已保存为私有不可覆盖基线，不保留 Cookie/CSRF 或消息正文。它与前述 readiness 独立样本分开；仍未标记真人绑定或归并通过。

门户 Owner 已获新端口和就绪回执，使用独立 Chrome/profile 及固定 `member-dist`。只在该 context 覆盖门户静态 GET/精确 callback 文档及 AI/Runtime 网络，中心登录/授权/BFF 保留真实网络和原登记。Playwright APIRequestContext 不受 context route 拦截，禁止据此向未定向的逻辑443发 AI 请求。

真实绑定、独立归并核对及原文件会员浏览器 GET 已通过；三项人工验收与刷新后原会话/截止保持亦已通过。readiness 诊断样本不冒充该浏览器 subject。guest 原界面没有自动上传入口，文件按原生受权 HTTP 链验收，不能伪称 guest 点击上传界面已验。

材料 `FileRef` 无数字 `version`，校验实际 file ID、bytes、SHA256 和下载字节；消息核对 ID/createdAt。`Draft.revision` 和 `ReceptionView.version` 不作为文件版本。

剩余未验：自动 UI 首屏精确 10 条/完整分页及其断言失败的最终原因；真正关闭 browser context 后同 profile 重开；刷新后再次 HTTP 下载（人工只确认原文件保留，不能补成再次下载）；会员材料 UI 完整上传链；自然 8h/24h 到期、运行中停止、压力及正式发布。以上不阻止本次按已核实人类反馈交付记录，但不得宣称自动 UI 或整个生命周期全部通过。

## 本阶段裁定和代价

1. 不额外创建/取消认证事务作为就绪探针，留正常人类流程验证。代价：准备时只能证明配置/TLS；本次正常认证后来已通过。
2. 材料按真实 file ID/字节/SHA 验收，不新增数字 version。代价：不宣称 DTO 未提供的版本保证。
3. guest 文件走原生受权 HTTP，和会员 UI 分开。代价：匿名 UI 上传及会员 UI 完整上传不计为已验。
4. 复用已验 provisioning 源代码，显式改为新独占资源，私有 helper/日志保留本机且只公开指纹。代价：本次不是可移植生产部署包。
5. 保留全部活跃资源、私有证据和工作树；不合并、不建 PR、不重跑旧 729 或更改正式部署。代价：隔离资源继续占用，本次仅为 Git 文档交付。
6. 接受已核实三项人类反馈完成本阶段验收，自动/真 context 项继续未验。代价：不能把本阶段称为完整自动浏览器验收。
7. 区分保留原 41 条与当前 46 条，并溯源额外动作/反馈。代价：当前总数不能作为原访客消息保存的替代指标。

## 最终审阅与交付

独立只读审阅范围 `03556c0..0e46ab4`，未发现 Critical、Important 或 Minor 问题；公开 JSON 可解析，所抽核 helper/脱敏日志指纹匹配，产品及旧 checkpoint 没有修改。候选 `0e46ab48753784abd5891f3ff3ca817b1c5468ec` 已正常推送并核远端一致；随后仅追加交付勾选和本段审阅结论，最终固定 SHA 及六文件 Git LF SHA256 由交付回执提供。没有合并、创建 PR、删除工作区或更改正式部署；所有新旧隔离服务、数据库、bucket、profile 和私有证据继续保留。

用户另次确认“我的进展能看到本次需求和进展”，属于旧原采购申请的人工验收，已在[原申请闭环记录](2026-11-07-original-application-acceptance.md)留存，不重复提交，也不当成本轮新会员/文件已通过的证据。
