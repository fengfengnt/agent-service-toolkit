# Portal 443：AI受信Origin与回调同步

状态：已核验SSO聊天`01a17c42-25bb-7cb1-996b-7261a55c95c1`中用户本人要求Portal及Bridge去掉9444的原话。AI代码与隔离测试准备完成，实际运行配置及登记尚未切换；门户Owner已接续0.1.3发布，等待其新入口准备完成。正式开关保持关闭，不发送短信、不调用付费模型。

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
- Portal Owner已完成其新版本确认，按0.1.3接续构建；本方不代其发布，等实际入口就绪后同步AI切换。

独立只读审查无Critical/Important。保留一项Minor：执行器摘要对所有System行统一排除`allowed_origin`，而不是仅目标行；SQL固定只改portal一行且测试验证other保持，实际库System数量须在本轮结果明示，不将该脚本宣传为通用多System迁移。未来若扩展到多System，应把排除条件限定目标行后再验收，本次不顺手扩展。

审查未代验的四项由本方明确接续：外部Portal/Bridge状态以Owner公开结果并结合真实探针核对；私有库/配置/进程由本方按已授权只读核验；真人会员登录保持未验，不从匿名结果推断；已完成的issuer迁移不重复全批次，仅验证复用helper及当前issuer/身份保持。若外部状态或私有状态不符则不切换；真人登录缺口不等同本次配置阻塞。

原[issuer迁移记录](2026-11-04-sso-issuer-443-preserve-identity.md)及原9444验收证据保留为历史，不批量修改任意可信issuer/Origin测试fixture。`20261104_local_issuer_443.sql`是已执行的一次性历史脚本，不因本轮Origin变化而重写。
