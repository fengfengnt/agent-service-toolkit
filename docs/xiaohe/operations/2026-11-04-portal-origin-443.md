# Portal 443：AI受信Origin与回调同步

状态：已核验SSO聊天`01a17c42-25bb-7cb1-996b-7261a55c95c1`中用户本人要求Portal及Bridge去掉9444的原话。门户0.1.3发布就绪后，AI于2026-11-04 21:22（北京时间）完成自有Origin/callback切换、匿名真实适配及只读复核。正式开关保持关闭，不发送短信、不调用付费模型；真人新Origin会员登录尚未重验。

## 范围与执行顺序

| 本方目标 | 原值 | 新值 |
| --- | --- | --- |
| 本地AI库portal/tenant1的`systems.allowed_origin`，仅1行 | `https://portal.lvh.me:9444` | `https://portal.lvh.me` |
| 私有`sso_bridge_redirect_uri`及专用runner的精确callback | `https://portal.lvh.me:9444/portal/auth/callback` | `https://portal.lvh.me/portal/auth/callback` |
| 新匿名验证探针BASE | `https://portal.lvh.me:9444` | `https://portal.lvh.me` |

issuer保持`https://sso.lvh.me`；不改用户身份、`user_id`、来源租户1、AI业务tenant1、应用ID/密钥、会话起止、聊天或文件。SSO客户端管理归属166由SSO Owner核验保持，不能与来源/业务租户混同。不修改中心Bridge、OAuth登记、共享Nginx、Portal或真实`.env`。

1. 专用runner允许新callback并拒绝旧callback；固定SQL仅更新精确旧登记的1个Origin，调用方事务覆盖回滚，不停用任何触发器。
2. 等待Portal Owner准备无端口入口及其实际发布版本；中心Bridge已由SSO Owner修改，本方不重复写。旧version0/旧9444的未完成认证按正常规则失效，新发起流程；不改旧事务或导出Cookie。
3. 切换前复核实际库、登记和无pending任务，备份私有配置/源码指针/进程记录/目标登记及完整性摘要。按PID/启动时刻/runner所有权停止自己的API/worker，复核无其他业务连接。
4. 同一事务更新1条System Origin，核对其余全部表数据、原会话截止及用户/文件归属不变；私有配置仅改callback，固定提交源码，恢复自己的两个进程。异常回滚；提交后失败则先停止自己的进程，不能盲目重写旧身份。
5. 无短信探针验证新Origin的bootstrap/context/create/browser continuation、未登录complete拒绝和cancel；旧Origin应403。单独记录真人会员登录未重验，不把匿名适配通过当会员PASS。

## 当前证据

- RED：配置精确边界及SQL缺失6 failed/11 passed；GREEN：`test_triad_runtime.py`、`test_portal_origin_443_migration.py`、`test_identity_sessions.py`、`test_sso_bridge_client.py`共55 passed、19 warnings（19.63s）。测试只操作`test_xiaohe_agent`。覆盖原账号及会话/历史保留、新Origin恢复、旧Origin403、其他System保持、回滚及非预期登记拒绝。
- 中心公开证据`D:/Code/deploy/local-deploy/docs/deployment/portal-443-bridge-after-2026-11-04.json`：1行Bridge变更，origin/callback已443，Bridge/client版本均1、secret不变、owner166不变、无schema变更；真人登录未验。
- Portal Owner已按用户确认的0.1.3发布并给出最终就绪回执；公开归档`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-portal/local/0.1.3/verification.json`的6项HTTPS/浏览器检查通过，POST/SMS均0。本方已读归档并独立完成下述真实适配，不代其发布。

独立只读审查无Critical/Important。保留一项Minor：执行器摘要对所有System行统一排除`allowed_origin`，而不是仅目标行；SQL固定只改portal一行且测试验证other保持，实际库System数量须在本轮结果明示，不将该脚本宣传为通用多System迁移。未来若扩展到多System，应把排除条件限定目标行后再验收，本次不顺手扩展。

审查未代验的四项由本方明确接续：外部Portal/Bridge状态以Owner公开结果并结合真实探针核对；私有库/配置/进程由本方按已授权只读核验；真人会员登录保持未验，不从匿名结果推断；已完成的issuer迁移不重复全批次，仅验证复用helper及当前issuer/身份保持。若外部状态或私有状态不符则不切换；真人登录缺口不等同本次配置阻塞。

原[issuer迁移记录](2026-11-04-sso-issuer-443-preserve-identity.md)及原9444验收证据保留为历史，不批量修改任意可信issuer/Origin测试fixture。`20261104_local_issuer_443.sql`是已执行的一次性历史脚本，不因本轮Origin变化而重写。

## 执行结果与复核

- 固定运行源码`b06a57e843904c76955d6c451cdde3fab8b7dd8c`，仍在`dev-fengfeng`。无产品/上游源码、依赖锁或schema修改。
- 执行前只读确认实际专用库`xiaohe_triad_local_20261104`：System总数1、用户总数1、目标旧Origin登记1、无pending Run/claim。备份`/home/fengfeng/.local/share/xiaohe-triad/20261104/portal-443-backup-20261104T132147Z`目录0700、文件0600；保留旧私有配置、源码指针、进程记录和目标登记/摘要。
- 仅停止本AI旧API/worker 3773382/3773383，确认目标库无其他业务连接；同一事务只更新1条System的`allowed_origin`。18张表的其余摘要和身份触发器摘要完全一致，包括原用户、27条既有session全部字段与原截止、22条conversation、209条message、102条artifact/版本、2条claim等受控联调资料。
- 私有bridge仅改`sso_bridge_redirect_uri`，base issuer、applicationId、secret、CA不变；runtime文件字节不变。用户身份未更新、未新建/合并账号、不改旧认证事务。实际System总数仅1，故本轮排除的Origin字段恰为唯一目标；前述多System摘要Minor作为后续边界保留。
- 新API/worker PID为3824226/3824227，均存活、对应固定源码、API405就绪；受控fake模型和原短信拦截仍生效。未重启或修改共享Nginx、中心、Portal，不操作旧用户窗口或Cookie。
- `21:22:12`实际`https://portal.lvh.me`严格TLS匿名探针：bootstrap/context 200、旧Origin请求403且无Cookie、create 201、顶层POST 303、continue页面/READY/CONTINUE 200；目标issuer精确443，匿名complete前后均409 `AUTH_NOT_READY`、无用户Cookie，cancel 204。探针不发短信、不执行模型。
- `21:22:27`切换后只读核验：用户数仍1、用户整行摘要与原值一致，System仅Origin变化，private bridge仅callback变化，runtime原样、私密权限/触发器/固定源/受控模型核对通过。匿名探针产生新的访客和认证记录，因此全表完整性比较在迁移提交前完成，不要求探针后总行数不变。

聚合证据位于本仓库忽略目录`.superpowers/sdd/2026-11-03-xiaohe-ai-backend/`：`portal-443-cutover-result.json`、`portal-443-adapter-evidence.json`、`portal-443-postcheck.json`。本方已向门户Owner回传公开状态及限制，不升级为会员443验收。

下一步如验证真人会员恢复，应在当前443入口正常新发起流程；旧Bridge版本0或旧9444的在途认证已失效，不改写或重放。原AI会话仍沿各自原8小时截止，不因地址切换重算；已经自然到期者不复活。本次未重跑真人登录、真实短信、付费模型、原窗口恢复或旧issuer整批验收。

## 旧Portal端口关闭回执

2026-11-04 22:00:26（北京时间），SSO Owner提供`D:/Code/deploy/local-deploy/docs/deployment/portal-443-no-9444-2026-11-04.json`。本方已读取并核对9项检查及Windows字段：旧Portal9444不再兼容或重定向，WSL无旧监听、Windows9444及原SSO8443拒绝连接；Portal443页面/回调页、SSO Discovery严格TLS均200，Portal HTML及公钥保持。NewAPI9443和容器内部443保留，不属于本次关闭范围。

这是共享入口Owner的实测回执，本方未重复操作共享Nginx、重启AI或发起认证。AI此前已使用无端口Origin/callback，无需再改配置；21:22的AI匿名/身份保持证据与22:00的入口关闭证据分别保留。该回执自身未验证真人登录、RP发布或AI身份迁移，不覆盖本方已有独立证据，也不补足尚未执行的真人会员登录。
