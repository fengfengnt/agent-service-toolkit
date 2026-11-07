# 本地三方隔离联调运行记录

最新运行方式：2026-11-04 已按用户要求容器化，继续保留受控模型。API/worker/PG/MinIO 现属于 `yunhezhiyuan` 分组，PG 从 tmpfs 完整迁入持久卷并逐表核验，MinIO 原卷保留；原宿主进程、旧存储容器及旧 venv 已清理。下文 PID、私有配置路径和进程命令是历史记录，不再作为当前管理入口。当前使用 WSL `/home/fengfeng/yunhezhiyuan/aibackend/compose.sh`，细节见[运行说明](runbook.md)及 `D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/local/0.1.0/release.md`。

最新环境（2026-11-04 21:22）：Portal及AI受信Origin/callback已去9444，门户0.1.3，AI固定源b06a57e。新地址`https://portal.lvh.me`的匿名443适配及原账号/历史/会话截止保留均已核验；真人会员新地址登录尚未重验。见[Origin切换记录](2026-11-04-portal-origin-443.md)。后文原地址、PID和会员结果保留为各阶段历史。

后续环境变更：2026-11-04 21:02中心规范issuer及本AI接入均已切换443，旧SSO8443不再兼容。用户批准后，AI原地更新1条用户绑定及1条System登记，原user_id、历史及会话截止完整保留；新API/worker使用固定源码93c99b3，真实匿名443适配通过，真人会员新issuer登录尚未重验。详见[保留身份迁移记录](2026-11-04-sso-issuer-443-preserve-identity.md)。本页原8443地址、PID及结果保留为当时验收历史。

当前结论（2026-11-04）：第二轮在保留的合法浏览器context中完成可信会员绑定、访客对话/文件归并和刷新恢复，AI专用库只读交叉核对通过。本文按时间保留原失败及中间未完成状态；正式开关继续关闭、未替换部署、未调用付费模型。真实登录短信仅按用户后续授权在隔离窗口由用户主动触发。

后续Task 8仅补真实链路缺口，100/101、滚动有效期、新主体queued到期、独立双系统拒绝及已有历史追加的分层证据见[Task 8补验记录](2026-11-04-task8-boundary-evidence.md)。

用户已在门户聊天明确批准按隔离方案启动API、worker、专用数据库和bucket，继续三方协调；正式开关关闭、不替换现有部署、不发短信或调用付费模型。AI在执行前通过read_thread核验真实用户原话，未再次索取同范围确认。基线dev-fengfeng/9b79b51，本轮新增测试专用启动器，并补齐原总设计已有的首屏欢迎功能；不改上游产品源码、Windows .venv、依赖锁、共享合同或其他工程。

## 专用资源

| 资源 | 当前边界 |
| --- | --- |
| Linux运行环境 | Python3.14.7，`/home/fengfeng/.local/share/xiaohe-triad/20261104/venv`，原uv.lock冻结安装 |
| API | WSL宿主`127.0.0.1:18090`，Uvicorn通用proxy headers关闭 |
| worker | 独立Linux进程，无监听端口，复用原Procrastinate/执行器/维护 |
| PG | 原自有xiaohe-agent-test-pg17/35417；新db `xiaohe_triad_local_20261104`，新owner role `xiaohe_triad_local`，只在新库初始化业务0007及任务/原saver/store |
| MinIO | 原自有xiaohe-agent-test-minio/35418；新私有bucket `xiaohe-triad-local-20261104`，专用应用key仅可Get/Put/Delete该桶；实际head目标200、原测试桶403 |
| 本地私有配置 | Linux专用目录0700，runtime.json为0600；包含新测试应用凭据，不复制到Git或聊天 |
| 中心应用配置 | SSO Owner将既有应用私有注入同目录sso-bridge.json，精确5键、0600；AI Agent仅检查路径/权限，专用进程正常加载，未打印值、未重新登记或轮换 |

本专用库名不符合自动测试test_xiaohe_前缀，不会被既有pg_factory接受并TRUNCATE。自动化测试仍在原测试库/bucket，浏览器会话使用本新库/bucket。现有测试PG为tmpfs，本次不承诺其重启后保留联调数据；MinIO原卷保留，未删除其他库、桶、容器或卷。

## 入口、peer与模型

继续沿用issuer `https://sso.lvh.me:8443`、public client `app_localportal`、Origin `https://portal.lvh.me:9444`和精确callback `/portal/auth/callback`。原Nginx已有/ai-api upstream指向18090，本轮未改入口配置；启动后该本地路径从502变为可达，属于已批准的隔离运行效果。GET /ai-api/v1/session返回405是方法不支持，初始化必须POST。

先保持trusted_proxy_cidrs为空，从Portal做无凭据GET，Uvicorn实际socket日志测得127.0.0.1，再仅配置127.0.0.1/32并重启自有API。没有按XFF推断peer或信任整个网段。实际HTTPS匿名初始化还带过伪造system/XFF/Proto，入口覆盖后仍正确建立portal访客。

受控DecisionModel和文件handler仅由`tests/xiaohe/integration/triad_runtime.py`装配；公开router、原lifespan、PostgreSQL saver/store、执行器与对象服务仍为真实实现。隔离进程禁用真实provider及追踪、不自动发现加载其他部署.env，不新增产品fake开关或认证成功后门。fixture发送短信路径提前返回不可重试503，即使加载真实SSO应用配置也不能经该路径外发短信。

## 已完成的真实匿名API证据

北京时间15:09起，使用严格公共CA，经Portal9444实际Nginx→API→独立worker→专用PG/MinIO：

- 依次输入“我代表供应商，想了解服务”“切换到协会组织方向”“切换到采购方方向”，每条202，最终各生成一个独立Artifact、version1、标题“隔离联调说明.md”、text/markdown。正文含对应supplier/organization/buyer及隔离说明。
- 每次精确版本下载200、private/no-store，摘要与方向正文一致；同clientMessageId重试复用run且只计一次；三条计数为1/2/3。
- 实际SSE接收到artifact.updated、message.upsert、run.updated、viewer.updated；本fixture不是付费模型逐token流。
- Cookie仅在验证进程内存中复用，重开客户端仍同conversation/原消息ID，文件可再下载；匿名下载401、另一guest403，公开internal404，隔离SMS503且未出站。

摘要：supplier `71b29beb7739dce78d3bf34dccd774ad93e0a73ac6d2178c53eca37dfa32af11`；organization `71f5130c1995566a100630f600a520369bbbcbb96a8f4a84d0c1d8bc233daa66`；buyer `d0e0043886f1755726c0236dc0de8faac1466d6ab5552d195a954cac79871ef2`。脱敏证据在本计划忽略目录`triad-anonymous-evidence.json`，不含Cookie/CSRF/secret。门户Owner收到实际输入与结果后并行做自己的隔离浏览器验证，本记录不替代其结果。

## 中心网络与后续认证

SSO Owner确认Discovery/JWKS/continuation已恢复严格TLS200；此前11:57的502是历史快照。实际WSL公开探针UTC07:10:34为127.0.0.1:46050→127.0.0.1:8443/HTTP200，后续UTC07:13:09为127.0.0.1:47046→同8443/HTTP200；使用HTTPX network_stream实际地址，连接保持供Owner只读对照。现Nginx access_log off，未为探针开启日志或重载；不能声称按query命中过日志。公开peer不等于私有bridge鉴权通过。

真实身份绑定仍要求中心受信claim；无既有中心会员会话时由用户正常登录，不能用fixture或前端成功标记冒充。正式Portal包保持false；只有门户Owner的独立浏览器context覆盖静态GET/原callback使用测试包，API/SSO走真实网络。

加载Owner私有配置后，AI实测auth/context 200、真实中心create经AI 201、未换码complete 409 AUTH_NOT_READY且无用户Cookie、浏览器顶层POST 303、中心续接页面/READY JSON 200、CSRF CONTINUE 200并限定同issuer、cancel 204。未登录会员、未换码或完成claim，未发送短信。最初探针误用browserCompletion字段及LOGIN_REQUIRED枚举，按原completion/READY修正，仅脚本变化。首个公开探针46050的四元组由Owner在edge实测匹配；第二47046未捕获，不能混用。backend peer172.24.0.1是独立诊断证据。

门户Owner独立真实浏览器已验证三方向消息/文件摘要、SSE、重开和整页刷新不重复计数；fresh context还验证顶层POST、中心续接、authorize至正常手机号登录表单，pageerror和SMS均为0。其结果是门户Owner提供的证据，AI不冒充自己执行。完整会员认证、换码、身份绑定及归并仍待合法会员会话。

## 首屏欢迎漏项与修复

真实浏览器首次初始化返回messages=[]，保留为失败证据。总设计§2规则5要求方向未知时展示欢迎及采购方、社群组织、供应商选择，故补齐既有需求，不增加专业业务流程。

服务端在bootstrap及认证完成后的空会话中，持久化一条assistant欢迎消息，复用suggestions和message.upsert；行锁与固定消息ID防止并发重复。无run、command或任务、额度消耗及会话续期；点击三个入口才走正常/messages。已有非空历史不补插欢迎。归并保留源欢迎消息的ID、内容和创建时间；来源过期时不迁移，目标空会话只生成自己的新欢迎。裸“社群组织”与既有alias一致，确定性识别为organization，无需模型猜测。

欢迎4项专项先RED（空首屏），再暴露裸社群组织路由错误，修复后GREEN。9个旧测试因欢迎改变空历史或数量预期而失败，按实际消息身份/内容调整，保留原隐私、过期、并发及8小时锚点检查；相关47项回归通过。完整业务回归、独立审查和切换后的浏览器复测结果后续追加，不以旧314项通过覆盖新修复。

提交173dd40后完整所选业务/原client及threads回归326 passed、无skip，19条既有依赖警告、216.92秒；Alembic无差异。一次独立只读审查9b79b51..173dd40：0 Critical、1 Important、0 Minor；确认欢迎、归并、过期及领域夹具改动未削弱原安全断言。Important是专用启动器仅检查URL主体，psycopg的query可覆盖实际host/dbname。两个覆盖用例先RED；最小修复拒绝全部非空url.query，无此参数的实际专用配置无需更改。修复后的最终结果后续追加。

审查范围裁定：正式发布/短信/付费模型继续保持未执行；完整真实会员仍须合法会话，不由夹具替代；新欢迎在线行为须切换后另取浏览器证据；历史批次/上游问题不重审，进程脚本只证明本次本地隔离，不提供生产运维保证。这些边界的代价分别是尚不可正式上线、会员闭环未验收、浏览器未复测前不能宣布首屏通过，以及原有问题仍保留。无延期Minor，不做第二轮审查。

运行源码使用Git tracked文件的私有固定快照，未复制.env、untracked或凭据，避免工作区开发期间Python延迟import混用版本。欢迎修复经验证后在门户已确认的无活动窗口，切换自有API及worker到同一新快照；不操作其他进程。

## 最终回归与切换后实测（北京时间15:55）

- 提交：欢迎173dd40、隔离query修复b370bfb。最终完整所选328 passed、无skip、19条既有依赖警告，216.62秒；Alembic无差异，Ruff通过。原上游Streamlit失败仍是此前全仓结果，不声称全仓全绿。
- 审查问题一次修复：host/dbname query覆盖两用例RED→GREEN，启动器10 passed（5.93秒），不派第二次审查。当前正式参数未改，唯一共享合同SHA256仍为0f7592698bb9b45e3d81bd5364ebff59fabb384bf74d420c9940a270c32583f8。
- 在门户确认的无活动窗口，仅停止旧自有API/worker并启动新进程：API PID3257486、worker PID3257487，均从source-b370bfb633c1固定快照启动，不从开发工作区动态导入。
- 真实严格TLS探针UTC07:55:26：新访客首屏一条assistant欢迎，三个suggestions标签精确匹配，count0/activeRun=null；4次并发重开保持消息全文/ID/创建时间及原绝对截止。依次裸供应商、社群组织、采购方，经正常/messages均202，文件方向及三个SHA与前述一致，计数1/2/3。SSE四类、同clientMessageId去重、重开同conversation/全部消息ID、文件下载恢复通过；401/403/internal404及短信503保持。
- 脱敏证据：计划忽略目录triad-welcome-evidence.json。旧首屏失败及旧匿名文件证据保留，未放宽验收。门户Owner已获READY通知，其真实快捷按钮/刷新复测另记，不能用本API探针代替浏览器。
- 同快照真实认证探针UTC07:55:41再次通过：context200/create201/未换码complete409 AUTH_NOT_READY/顶层POST303/续接READY及CSRF CONTINUE200/匿名仍409/cancel204。memberAuthenticated=false、SMS=0；只测合法未认证边界，没有会员换码或归并成功证据。

## 门户Owner真实浏览器复测回执

门户Owner在同一b370bfb运行快照上使用真实Edge复测，报告整体exit0、requirementGaps为空，原首屏失败记录保留：

- 初始messages.length=1，assistant欢迎及三个suggestions可见，count0。
- 实际依次点击供应商、社群组织、采购方按钮，各POST/messages 202，三个v1 Artifact预览/下载200，SHA逐一匹配前述方向文件。
- 关闭重开及整页刷新后同conversation、全部消息ID保持，count3不增加；pageerror=0、SMS=0。API/SSO/PG/MinIO均未mock。
- 新快照浏览器restore再次到正常/sso/ssologin手机号表单：context200/create201/顶层POST303/continue200/continuation GET和CSRF POST200/authorize302/loginContext200；没有输入手机号、发短信或完成会员认证。

证据由门户Owner执行并回传，本AI未代为操作浏览器。文件位于门户worktree `C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/.superpowers/sdd/2026-11-03-xiaohe-portal/triad-browser/anonymous-result.json`，认证边界见同目录restore-result.json。欢迎漏项的真实浏览器门已通过，不重复运行这些已通过用例。

后续仅待真实会员闭环：门户Owner提供可操作的隔离窗口及正常认证入口，由用户使用合法会员身份登录，再验证换码、绑定、对话/文件归并和固定绝对8小时。fresh隔离context不会自动共享普通浏览器的SSO Cookie；不通过导出Cookie/storageState或伪造身份绕过。无短信边界继续有效，如没有已有合法会话或其他已支持认证方式，则停留登录页并报告，不擅自发短信。正式包false、自有隔离进程继续保留。

## 无短信会员登录的现有能力核对

SSO Owner只读核对并回传：当前公开会员首登只有正常手机号短信验证码，密码/企业公开入口已退役，没有另一种现成免短信首登方式。AI随后只读查看当前`SsoLoginController.java`，确认POST /sso/login/password直接返回HTTP410及SSO_PUBLIC_FLOW_RETIRED，正常公开认证为/sso/login/sms/send和/sso/login/sms。本段是源码与Owner既有测试的证据，没有为此发起登录或HTTP410探针。

因此，无短信恢复必须是同一浏览器context已持有有效中心会员Session。普通浏览器已有状态不自动进入fresh测试context；管理员后台或旧App MEMBER令牌不能代替中心会员Session。当前fresh窗口不能完成会员首次登录，不要求用户输入账号密码或导出Cookie。

用户进一步要求准备可手动操作的隔离窗口；门户Owner在其现有测试harness中准备可见窗口，静态测试构建仅覆盖该context，API/SSO仍真实。窗口还须阻断中心/sso/login/sms/send及小鹤operation/sms发送请求，避免手工误点外发；不改产品、正式包或部署。窗口就绪情况及用户步骤以Owner回执为准，未就绪前不宣布已可操作。

门户Owner后续回执确认窗口已实际打开并保留：真实Edge headed/fresh内存context，标题“小鹤隔离联调｜禁止短信”，地址https://portal.lvh.me:9444/portal/，小鹤已展开，黄色条为“隔离测试窗口·短信发送已阻断·使用受控模型”。现有browser.mjs只新增测试manual模式；helper PID38180，由门户Owner保持运行，不由AI另开或并行操控。仅该context覆盖静态GET及原callback，正式包仍false。

窗口发送保护阻断POST /sso/login/sms/send及Portal /ai-api/v1/auth/operations/*/sms，context/续接不受影响。Owner报告3条纯路由断言通过、2个空载保护探测在转发前abort、pageerror0，没有记录请求体/手机号/Cookie/trace/storageState。脱敏就绪文件为上述门户scratch triad-browser/manual-ready.json。本AI仅核对回执/脱敏文件，没有代为操作可见窗口。

用户操作顺序：在带黄色提示条的Edge窗口点击任一方向，体验受控聊天与文件；再点小鹤右上角×收起，在门户页头点“登录”，到正常手机号表单停止。本fresh context无合法中心会员Session；禁止短信条件下无法完成首次会员认证，发送按钮即使误触也会阻断。不能把普通浏览器已有登录态、管理员身份或本匿名体验当作会员换码/归并验收。窗口及隔离API/worker保持运行，等待用户手工操作；未来会员认证范围需另行明确。

## 后续新增授权：真实登录短信

用户随后明确允许本次真实登录短信，手机号及验证码由用户在隔离窗口输入；正式开关继续关闭、不替换部署、不调用付费模型。该授权覆盖前述本次登录发送限制，不扩大到自动预发短信、其他窗口、注册或其他短信功能。正常表单认证后继续验证可信账号绑定、访客对话及文件归并；授权不等于实际发送或验收已完成。

门户Owner准备新的可见短信授权context，保留旧禁止短信窗口，仅在新窗口放行精确issuer https://sso.lvh.me:8443 的正常POST /sso/login/sms/send，由用户主动操作。AI测试启动器原小鹤operation/sms提前503保持，不需要为中心正常登录流程修改/重启API或worker。正式包false、b370bfb受控运行和既有私有配置保持。

准备工作：先建立可核对的匿名消息和文件基线，再从小鹤真实认证operation经中心表单登录，由用户输入手机号/验证码并正常完成回调；后续核对complete/可信claim、canonical、原消息ID/创建时间、原文件版本/摘要及首次绝对8小时。仅门户页头登录成功不能替代聊天身份绑定/归并证据。SSO Owner只读检查现有渠道/模板/租户，不发送预演短信。窗口就绪、渠道条件及用户操作以新的回执续记，本段不提前标注会员成功。

SSO Owner一次发送前只读核验回执：配置层面READY，登录模板sso_personal_login与真实ALIYUN渠道启用，所需渠道值仅作非空检查；来源租户1正常、现有全局portal手机号流程适用，非debug/mock，敏感短信日志按脱敏分支处理。Owner没有预发短信、调用供应商或修改配置/库/部署。本结论是配置/源码证据，不能证明本次渠道受理或运营商送达；这些结果须由用户实际操作后核实。OAuth应用的管理归属与本手机号流程的来源租户分别解释，不依据管理owner推断用户tenant_id。

门户后续已实际打开新窗口“小鹤会员联调｜本次允许登录短信”，helper PID44476，当前停在真实 https://sso.lvh.me:8443/sso/ssologin。Owner已生成一段访客对话及一个文件作为归并基线，正常认证操作已建立；原禁止短信窗口仍保留。新窗口短信例外仅适用于精确issuer的正常发送路径，由用户操作；AI operation/sms保护保持。

AI只读核对门户scratch triad-browser/member-ready.json的脱敏就绪字段，确认mode=member、标题/当前页面/helperPID及pageErrors=0，不读取Cookie/验证码或请求体。用户在新窗口填测试手机号、点击获取验证码并填收到的验证码；如出现协议，由用户自行确认，保持窗口打开。门户helper在用户完成后继续正常回调、绑定/归并检查；当前仅就绪，不提前宣布短信送达、会员身份绑定或归并成功。

## 首次会员验证后的测试窗口中断

用户确认已在窗口完成验证；门户Owner随后定位测试harness缺陷：登录后某Portal document未命中独立构建的映射，返回正式旧index，其引用asset在测试bundle不存在；route回调未捕获ENOENT，Node44476退出，用户确认窗口关闭或空白。该事实不能推导为中心登录失败或AI绑定成功。门户修复测试映射，保留失败证据，不修改正式部署、不自动重发短信。

AI于UTC08:36:58在专用库执行显式READ ONLY事务，仅查询本基线的非敏感元数据：

- 原conversation615535df-9792-479e-b5c0-cf7bbcc3dc61仍是portal/tenant1的supplier访客会话，count1、未撤销、merge_pending=false。
- 三条原欢迎/用户/assistant消息ID及创建时间仍保留在原会话。artifact c2f782ce-c6c2-4c6c-8af1-a392ecf72070的v1仍ready、111bytes，content_digest与verified_digest均匹配原SHA71b29beb7739dce78d3bf34dccd774ad93e0a73ac6d2178c53eca37dfa32af11。
- 对应operation c203983a-08cd-4a7b-b9e9-3f69da47af62仍BROWSER_COMPLETION_REQUIRED、verified_completion为空、bound_user为空、首次认证anchor为空，未创建GuestClaim；没有本操作的AI用户绑定或归并证据。
- 脱敏审计位于本计划忽略目录triad-member-interruption-audit.json，包含READ ONLY=on与精确检查时刻；未查询Cookie、CSRF、手机号、OTP或凭据字段，未修改数据。中心事务进展由SSO Owner另做只读核对，不能从AI尚未complete推定中心未验证。

原浏览器临时认证上下文若随窗口退出丢失，不通过DB或已知ID重造会话。后续在修好的测试窗口通过正常认证重试，新基线和新结果单独记载；原访客记录与自有服务保持，原中断不能被新成功覆盖。

## 第二轮：中心已有验证记录，回调兑换未完成

门户修好资料页document映射及资源异常处理后，在同一授权下准备第二轮新窗口helper69592。用户再次完成验证，当前窗口仍保留在/portal/auth/callback，页面空白；门户继续用无认证公开探针排查跨域重定向后的静态拦截，不复制或回放用户提供的一次性URL参数，也不再关闭合法context。

第二轮基线conversation50bcaedc-b8bf-4455-8e2a-5647dca053bf，三条消息于UTC08:40:17–19建立，artifact a972e4fa-d9fb-4b33-8b40-b75fb5ede9b9 v1。AI于UTC08:45:27显式READ ONLY核对：消息ID/时间仍在源会话，文件ready/原SHA一致；op f9f1f237-df37-4655-880a-fe4d68b7b650仍BROWSER_COMPLETION_REQUIRED、verified/bound/首次anchor均空，GuestClaim和对应userSessions均空。脱敏证据triad-member-retry-audit.json；尚未进行AI账号绑定/归并，不能验收刷新后的会员恢复。

SSO Owner于UTC08:46:28（北京时间16:46:28）只读核对对应中心tx8c41752d-4269-4976-88c4-40873f9a450f：AUTHORIZING，身份验证/source identity/中心Session/authorization关联布尔均true；code_exchange_recorded=false、trusted_result_materialized=false，兑换和结果截止时间为空。中心验证码阶段已有记录，卡点在授权码兑换后才会产生的可信完成结果之前；不把用户确认或callback页面停留当作完成。

中心契约600秒，按该操作UTC08:40:22创建推导预期截止约UTC08:50:22，AI自身09:00:22的截止不能延长中心期限。SSO SQL原expires_at时区解释有疑点，Owner没有把它直接标作UTC事实，本记录也仅将上述截止标作契约推导。保留当前合法浏览器context，若事务到期应经正常新restore复用该context的有效中心Session，不自动重发短信或从库提取凭据。

随后AI于UTC08:49:09仅只读核对该operation的center_expires_at（真实中心create响应经适配器接收解析后存储的timestamptz），得到UTC08:50:22.803410，与AI created_at相差600.236323秒。这是适配器实际观察的中心UTC截止证据，与600秒契约吻合；不再仅依赖前述创建时刻推导，也不把原SQL客户端的时区显示差异当成已证实的应用期限错误。证据triad-member-center-deadline-audit.json，已同步双方，无需修改时间配置。

门户继续修复跨域重定向后document/asset映射，在同一存活窗口让用户按正常“刷新／重新登录”入口恢复，优先复用已有中心Session。AI不并行操控窗口；截至本节证据时刻，尚无可信完成、用户绑定、归并或刷新后会员恢复的通过结果。

## 最终通过：同context恢复、可信绑定与归并

门户Owner回执：无认证的302探针RED→GREEN，证实SSO重定向目标HTML落到正式旧入口名，而测试静态目录没有该入口。仅在忽略scratch的测试UI中，将该入口名映射至当前测试main JS，既有依赖/CSS保持；资料页映射遗漏与异常退出保护也已修复。没有修改正式包、部署或产品源码。历史staticMissing=1保留，不把过程描述为一次无错。

用户在仍保留的helper69592窗口按正常刷新及“重新登录”恢复，复用此前短信建立的中心会话，没有第三次短信。没有复制/回放一次性URL或提取Cookie。第二轮成功context记录一次由用户触发的短信发送；这不是对首轮中断期间发送次数的统计。

门户真实浏览器结果文件member-result.json：stage=MEMBER_LOGIN_AND_GUEST_MERGE_PASSED、portalAuthenticated=true、pageErrors=0。两次正常可信complete均200，首次恢复产生MERGED，整页刷新后的恢复仍为同一账号；原消息ID/时间及文件版本保留，归并后与刷新后下载均200、SHA一致。此为门户Owner执行的浏览器证据，AI另做以下独立只读数据核对。

AI于UTC08:54:10在专用库显式READ ONLY交叉核对，并用本地断言逐字段比较门户基线与先前retry-audit：

- 外部issuer匹配、subject存在、source_tenant_id=1，唯一映射至user79b8e008-fbb1-479d-b140-f27007a2a4a9及systemId=portal/tenantId=1。身份映射数1、个人门户canonical数1，canonical为3f7e9399-8897-4ff4-8d1f-b671b9f40ba9；未查询subject正文或用户明细。
- 源50bcaedc-b8bf-4455-8e2a-5647dca053bf仅一条GuestClaim，status=merged，对应可信op f38e9412-2c1a-4102-a324-49157128ad3d；原guest Session已撤销。刷新后的新可信op为4e0f0296-1e1b-451b-ab95-f6131c6c6ec4，绑定同一user，没有重复认领。较早失败f9f1f237仍未兑换，不覆盖其历史状态。
- 三条原消息691b846a-8bbc-58cc-9f47-c854e6002089、85ba27b1-8cbd-437a-8ec4-8b65c42bbad2、7ef9cabe-4618-5cf9-a47f-8ed5dccca482的创建时间分别仍为UTC08:40:17.914183、08:40:18.342571、08:40:19.378912，归属同一canonical。
- artifact a972e4fa-d9fb-4b33-8b40-b75fb5ede9b9迁入canonical，原version_id310d888d-1476-4dcc-8b81-d32277470f7d、object_key指纹及v1不变；版本总数/latest均1、ready、111bytes，content/verified/download三者SHA与基线一致。没有重建文件或增加版本。
- 两个COMPLETED且可信的认证操作分别建立两个会话：UTC08:48:39.618511→16:48:39.618511、UTC08:48:43.622717→16:48:43.622717，均精确+8小时，并分别匹配对应操作首次anchor。后一次是刷新验证中的新可信认证操作，前一个会话截止保持原值；不将这两个不同认证锚点混称普通请求滑动续期，也不声称现场等待满8小时。
- 首轮615535df-9792-479e-b5c0-cf7bbcc3dc61的GuestClaim仍为0；其临时context丢失后的原资料没有被强行迁移，原中断记录保留。

证据：AI计划忽略目录triad-member-final-audit.json、triad-member-crosscheck-summary.json；门户同目录member-result.json保留实际浏览器结果。断言全部通过，无手机号/OTP/Cookie/CSRF/secret导出，无数据库写入、服务重启或额外短信/模型测试。当前API3257486、worker3257487及b370bfb源码快照保持，只有文档提交更新。部署备份/生产发布及此前上游Streamlit问题仍不属于本次通过结论。

## 自有进程操作

从Windows运行以下命令，脚本校验PID、内核启动时刻和专用runner路径，只操作自有API/worker；不改Nginx/SSO/存储服务：

```powershell
wsl.exe -d Ubuntu -- /home/fengfeng/.local/share/xiaohe-triad/20261104/venv/bin/python /mnt/d/Code/yunhezhiyuan-agents/agent-service-toolkit/.superpowers/sdd/2026-11-03-xiaohe-ai-backend/triad_processes.py status
```

将最后的status改为stop可停止两个自有进程，start可再次启动；restart-api仅在协商安全点加载新的专用配置。私有目录内api.log/worker.log及processes.json用于本地排查，不向聊天导出请求体或认证材料。停止API后现有/ai-api会再次不可达，但不会删除数据或改变正式开关。再次建环境必须按已批准的新资源范围操作，不对原库/bucket执行重置。
