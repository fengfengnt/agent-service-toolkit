# SSO issuer 443：保留身份原地迁移记录

后续变更：2026-11-04 21:22已完成[Portal Origin/callback去9444](2026-11-04-portal-origin-443.md)，当前Portal为`https://portal.lvh.me`、callback为`https://portal.lvh.me/portal/auth/callback`、运行源b06a57e。下文原9444配置、93c99b3快照和PID保留为本次issuer迁移当时的事实，不再代表最新运行配置。

状态：用户明确批准后，已于2026-11-04 21:01（北京时间）完成本地迁移及API/worker恢复，21:02补完真实匿名适配和只读核验。当前issuer为`https://sso.lvh.me`；原账号、历史和原会话截止保留。正式开关关闭，不发送短信、不调用付费模型。真人新issuer登录尚未重验，不能把匿名适配通过等同会员登录通过。

## 已核验的授权与范围

已通过read_thread核验“本地启动后端”聊天中用户要求去除8443并通知已接入Agent同步；随后又核验SSO聊天中用户明确“不再兼容8443”。后者覆盖此前临时保留兼容入口的安排。

- 规范issuer为 `https://sso.lvh.me`，不以8443作回退。
- Portal自身Origin `https://portal.lvh.me:9444`、原callback `/portal/auth/callback`、client `app_localportal`保持；New API自身9443也不属于本AI修改范围。
- 中心已由其Owner完成backend切换，SSO Owner随后移除旧8443并校验reload；其最终公开证据为 `D:/Code/deploy/local-deploy/docs/deployment/issuer-443-no-8443-2026-11-04.json`。本AI不修改共享Nginx、不重启中心、不动签名密钥或实际部署.env。
- 本方仅处理自己的独立AI接入配置、精确issuer校验和已存在身份的连续性；不新增通用别名兼容机制、不重做Task8设计。

## 只读证据

UTC2026-11-04 10:47:18，在专用运行库 `xiaohe_triad_local_20261104` 的显式READ ONLY事务中确认：

| 项目 | 结果 |
| --- | --- |
| 现行System登记 | portal↔tenant1，issuer为旧8443，Origin及client如上 |
| 旧issuer用户绑定 | 1条 |
| 新issuer用户绑定 | 0条 |
| 新旧issuer下同subject/sourceTenant但不同user_id的冲突 | 0条，仅在库内比较，不输出subject |
| 受影响用户关联 | 1个canonical、2个claim，当时5个有效AI会话；不输出个人信息 |
| 旧issuer操作 | 当时1个尚有效且未完成、6个已完成；切换前须重新核对，不能把旧操作凭据改签为新issuer |

原始脱敏元数据位于本计划忽略目录 `issuer-443-preflight-audit.json`。未查询手机号、OTP、Cookie、CSRF、display_name或subject正文。

当前私有接入文件公开字段仍指向旧base URL，callback及公共CA路径有效。实际新443的Discovery/JWKS均200，Discovery issuer及相关端点为443；公钥sorted compact JSON SHA256为 `95e08466aa1697674540419be3d535fc92a33b004d839576b849f3120a3b2b6a`，与中心Owner保留值一致。HTTP原始字节摘要不同源于序列化方式，不能据此宣称换钥。对应 `issuer-443-public-readiness.json`、`issuer-443-jwks-normalization.json`；早先探测到旧入口仍返回新issuer是该时点历史，不构成继续兼容8443的方案。

## 推荐的一次性原地迁移

只修改身份键中的issuer，保留原user_id。产品代码本来按配置精确核验issuer，不需要放宽校验或增加别名查询。

| 目标 | 预期动作 | 必须保持 |
| --- | --- | --- |
| xiaohe.systems中portal/tenant1的登记 | issuer旧8443→新443，预计1行 | system_id、tenant_id、client、Origin |
| xiaohe.users中该旧issuer/portal/tenant1绑定 | issuer旧8443→新443，预计1行 | user_id、subject、source_tenant_id及全部归属 |
| AI私有sso-bridge.json | base URL改为443 | applicationId、应用secret、callback、CA；不轮换任何凭据 |
| 当前专用test runner及其校验用例 | 精确canonical常量改为443，拒绝旧base URL | 原资源隔离guard、短信阻断、受控模型 |
| 当前观察/探针脚本 | 现行调用使用443 | 旧验收JSON、历史URL、原始hash和历史测试记录不批量改写 |

不新建、合并或覆盖另一账号，不搬迁canonical，不改消息/文件ID、版本或对象key，不清会话，不延长原created_at/expires_at，不改已存认证结果和旧操作的issuer。通用业务fixture里的任意可信issuer样例不等于现行环境配置，不做全仓字符串替换。AI代理模板内部listen8443也不是SSO issuer，保持不动。

不采用“允许新旧issuer并存并在bind_user自动择一”的方式：它会增加身份别名及长期信任逻辑，超出本次配置迁移目标。创建新用户则违反保留原身份的要求。

## 获确认后的执行门

1. 在切换前重新只读确认预计行数、新旧冲突和认证操作状态；任何非预期行数或冲突均停止，不自动合并或选择账号。
2. 仅在本AI安全窗口，按原有PID/启动时刻/runner所有权检查暂停自己的API和worker，避免旧进程在身份键变更期间并发首次绑定。中心及共享Nginx不动。
3. 私有备份当前配置与两类目标的最小回滚元数据，不输出凭据/个人信息；在同一受保护事务内锁定目标System及用户行、再次检查冲突，更新两处issuer。以库内主键/关联集合核对原user_id、canonical、claims、消息、文件和全部原会话时间不变。
4. 同步私有base URL及新的固定源码快照，恢复本AI两个进程。签名密钥、应用凭据、AUTH_SECRET不变，不回退8443。
5. 只验证服务/新Discovery及无短信的适配边界，确认用户映射仍唯一且没有新增账号。已有登录会话继续沿原有效期；若需要真人新issuer重新认证，另记实际RP登录证据，不能从配置正确推断登录通过。

未完成的旧认证保留原记录，不能从数据库提取Cookie/credential或改写可信结果；需要继续认证时经新issuer正常重新发起。进行中旧事务、配置备份、角色以及当前用户数以执行前实测为准。

## 回滚与当前决策

更新过程中出错则数据库事务回滚。若提交后启动验证失败，先保持自己的进程暂停，以备份的精确主键集合核对后撤销本次两字段修改；若已经有新的真实认证或其他变化，先重新审计，不做盲目反向批量更新。恢复旧配置并不保证旧SSO可用，因为用户已要求关闭旧8443入口；必要时保持AI认证停用并报告，不能擅自让中心恢复旧监听。

当前没有需要挑选目标账号的冲突。用户已针对这1条既有身份键及1条System登记明确批准，保持原user_id与历史，不再重复索取同范围确认。

## 实施检查清单

采用writing-plans/executing-plans执行单个本地迁移任务；本文件为用户已批准的范围，不另外创建一份冲突方案。

实施检查发现0001迁移的guard_user_identity会拒绝issuer变化。一次性SQL须在自己的API/worker停机后、独占users/systems表的同一事务内，仅临时停用这一条用户身份触发器，修改已批准字段并在提交前恢复。唯一/FK/会话/事件保护均保持；触发器定义不变，异常或外层事务回滚时数据及启用状态一并回滚，不新增永久兼容开关。

- [x] 配置与迁移先RED：新增443允许/旧8443拒绝，原身份保留并复用、冲突与行数拒绝、事务回滚及保护恢复用例。
- [x] GREEN：只改专用runner canonical常量，新增固定本地库的一次性SQL；相邻身份/中心客户端检查通过，不重跑328。
- [x] 备份私有配置、源快照指针及受影响资料的非秘密完整性摘要；一次独立只读安全审查后执行。
- [x] 仅停止自己的API/worker，复核无其他使用该库的业务连接；独占事务更新1User+1System，恢复触发器并验证原主键、canonical、会话时间及消息/文件元数据保持。
- [x] 私有base URL及固定源码快照生效，重启自己的两个进程；验证真实443适配/匿名安全边界，记录未完成的真人新issuer登录证据，不触发短信或模型。

隔离验证记录：`test_triad_runtime.py`、`test_issuer_443_migration.py`、`test_identity_sessions.py`、`test_sso_bridge_client.py`，先RED 6 failed/9 passed，再GREEN 53 passed（19 warnings，13.29s），Ruff通过。测试仅连接`test_xiaohe_agent`，不操作真实联调库。执行器`--check`仅只读访问真实联调库，确认1旧/0新用户、无queued/running/waiting_input Run和pending GuestClaim，且443 Discovery 200、公钥摘要一致；未停机或改数据。

独立只读审查结论：SQL及数据保留方案无Critical/Important问题；发现临时进程管理脚本在“API已启动、worker启动失败”时，尚未保存新API的PID。已用隔离复现观察RED，修复为每次启动立即原子保存所有权记录、记录失败停止刚启动进程，并在读取进程信息失败时清理刚启动进程。GREEN检查确认API记录0600保留，后续stop能定位该PID。只修改被忽略的本地联调脚本，不改产品或上游代码；无待处理Minor，Declined to judge为空。

## 实际执行结果

- 实施代码提交`3874539`，运行固定源码`93c99b356b84995b3f8b375e9db163b9fbb057e1`；仍在`dev-fengfeng`，未合并或推送。审查修复后相同53项测试再次通过（19 warnings，17.02s）。
- 备份目录`/home/fengfeng/.local/share/xiaohe-triad/20261104/issuer-443-backup-20261104T130055Z`为0700，配置、旧源码指针、旧进程记录及精确目标行/摘要为0600；不把私有备份内容放入Git或聊天。
- 仅停止旧API/worker 3257486/3257487。确认目标库没有其他业务连接、无执行中Run或pending归并，再在同一事务更新1条User及1条System的issuer。原user_id保留；旧issuer用户0、新issuer用户1，没有新增或合并账号。
- 提交前核对`xiaohe`全部18张表的行数及库内聚合摘要，除User/System的issuer外完全一致；涵盖26条既有session的全部字段和原截止、21条conversation、208条message、102条artifact/版本以及2条claim等测试资料。正文、token等只在库内参与摘要，不返回执行器或报告。身份触发器启用状态及定义摘要与原值一致。
- 私有bridge仅修改base URL；复核applicationId、secret、callback、CA及整个runtime配置字节不变。旧auth_operations记录未改写，旧未完成认证不得继续走8443，应正常新发起。已有会话的原截止未延长，已自然到期者不会因迁移复活。
- 新API/worker PID为3773382/3773383，均存活且对应固定源码；API就绪，仍为受控fake模型。只重启本AI进程，不修改共享Nginx、中心、Portal或真实`.env`。
- 真实HTTPS匿名适配：auth context 200、create 201、顶层完成表单303、continue页面及READY/CONTINUE均200，目标精确443；未登录complete仍409 `AUTH_NOT_READY`、无用户Cookie，cancel 204。没有发送短信、没有调用模型，探针后只读确认原账号仍唯一且精确复用，未新建用户。

公开聚合证据在忽略目录`.superpowers/sdd/2026-11-03-xiaohe-ai-backend/`：`issuer-443-cutover-result.json`（successful=true）、`issuer-443-adapter-evidence.json`、`issuer-443-postcheck.json`；旧验收JSON保留。首次执行因WSL Git的CRLF检查差异在停机前拒绝，按Windows checkout的`core.autocrlf=true`只作该次diff核验后继续，未改仓库配置或业务文件。最终结果覆盖执行结果文件中的早先失败状态，旧RED日志保留。

## 尚未重验与下一步

本次不操作用户原浏览器Cookie，也未重新走会员登录/换码/归并；没有声称新issuer真人会员登录已验收。下一步可由用户在隔离窗口按正常入口进行一次443会员恢复验证；若需要短信，应另按当次明确授权执行，不自动重发。原会话仅在其原截止前继续有效，切换不会重算8小时。正式发布、付费模型及三个专业业务的原后续范围不变。
