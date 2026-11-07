# Bridge恢复后的小鹤接入核对与协议验证

日期：2026-11-06，北京时间。**配置与无短信匿名协议验证通过，无需修改配置或重启；完整真实会员登录未验证。**

## 授权与范围

已读取来源聊天“数据处理”中的真人直接指令：“把 Bridge 恢复结果同步给负责小鹤 AI 的 agent，让他核对现有接入配置并验证链路，不要发送短信或创建测试账号。”本轮仅在本工程记录结果，不另向其他聊天发消息。

配置核对只读。协议验证只创建本轮自己的匿名访客、认证上下文和三个匿名认证事务，三个事务均已取消；不调用短信、验证码验证、同意确认、真实浏览器认证完成或模型接口。不创建/重置账号，不导入会员历史，不合并身份，不延长既有用户会话，不改Bridge/client/tenant/Nginx/正式开关。

参考[中心恢复记录](D:/Code/deploy/local-deploy/docs/sso/sso-bridge-recovery-2026-11-06.md)、[基础恢复记录](D:/Code/deploy/local-deploy/docs/sso/sso-base-config-recovery-2026-11-05.md)及受限目录 `/home/fengfeng/yunhezhiyuan/backups/bridge-restore-20261106-133512/result.json`。只摘录公开配置及验证布尔结果，不输出secret或secret hash。

## 当前两套Bridge均有独立使用方

| 小鹤使用方 | 实际入口 | applicationId | 模式 |
| --- | --- | --- | --- |
| 0.1.1主API/worker | `https://portal.lvh.me/ai-api/v1` | `31555063-bbe3-4907-8745-c553208fff23` | 真实模型配置；本轮不调用模型 |
| 旧candidate API/worker | `https://portal.lvh.me/copilot-candidate/ai-api/v1` | 同上，原Bridge | 受控候选 |
| 新受控人工验收环境 | `https://portal.lvh.me:18116/ai-api/v1`，API18094 | `reception-manual-20261106` | 独立controlled环境 |

原Bridge私有文件是 WSL `/home/fengfeng/yunhezhiyuan/aibackend/config/sso-bridge.json`，主服务生效模型/业务环境文件是同目录 `runtime-0.1.1.env`。主API及worker进程中的Bridge applicationId和secret与这两个私有文件在内存逐项比对一致；candidate只读挂载同一原Bridge文件。主服务和候选内部桥接开关原本有效，本轮未改变。

人工环境读取 `/home/fengfeng/yunhezhiyuan/manual-reception-20261106/manual.json`，其Bridge独立，不因原Bridge恢复而被覆盖。对应的人工Java服务、私有文件和端口也未被本轮替换。

两套issuer/base URL均为 `https://sso.lvh.me`，Origin均为 `https://portal.lvh.me`，callback均为 `https://portal.lvh.me/portal/auth/callback`。主/候选CA是已有本地公共CA文件，人工Windows进程使用该文件的UNC路径。三条接入均以各自期望applicationId的现有凭据成功创建匿名中心事务，并通过只读captcha获取验证鉴权；未轮换或传递明文secret。

中心恢复回执中的Portal `registeredClientId=3d539fbf-d418-4bc5-aaa3-aec666f76e31`、管理归属165、authVersion1是本次新登记值。本方分别只读核对主业务库和人工业务库：`systemId=portal → tenantId=1`，client为`app_localportal`，Origin/issuer精确匹配且system启用。没有把管理租户165当成AI业务租户，也没有改回旧管理租户166。

## 实际网络与TLS

主容器及候选容器内部使用原有CA和HTTPX，`trust_env=False`、严格TLS、无重定向：Discovery200，issuer正确；`sso.lvh.me`解析为127.0.0.1，实际socket为127.0.0.1到127.0.0.1:443。两者host网络保持。

人工Windows配置的UNC公共CA可读，原生严格TLS Discovery200且主机解析为127.0.0.1。浏览器侧人工入口18116的严格TLS也通过。没有关闭证书校验、读取/复制私钥、修改hosts、容器网络或共享Nginx。

## 三条接入各17项协议检查

三条入口分别使用全新的内存CookieJar，相互不复用身份；Cookie、CSRF、OIDC state/nonce、浏览器完成ticket及图形挑战均未写入公开证据。

| 分支 | 实际结果 |
| --- | --- |
| 无认证上下文complete | 403 `CSRF_INVALID` |
| 旧9444 Origin | 403拒绝 |
| 新匿名session | 200，guest，业务租户1、system portal |
| 新auth/context及重复读取 | 200，同一未过期上下文读取不滑动续期 |
| 错误认证CSRF | 403 `CSRF_INVALID` |
| AI→中心创建事务 | 201，`BROWSER_COMPLETION_REQUIRED`，中心browser-completion action为canonical443 |
| 同体重试 | 201，同一operation及相同绝对截止 |
| 未完成真实认证就complete | 409 `AUTH_NOT_READY`，没有用户登录成功 |
| 图形挑战获取及期望Bridge凭据鉴权 | 200，只读取、不解图、不发送短信 |
| 另一新匿名上下文取消本事务 | 403 `FORBIDDEN` |
| 自有事务取消及重复取消 | 204 / 204 |
| 已取消事务再次complete | 409 `AUTH_REPLAY` |
| 重新读取访客session | 仍为同一guest/对话，消息数0，原访客截止保持 |

另直接验证中心无Bridge凭据访问返回401。首次中心负向探针错误带入浏览器Origin，先被CORS以403纯文本拒绝；诊断确认后按真实后端客户端去掉Origin，才计入401鉴权结果。不是中心或产品配置故障，没有为此改CORS。

## 过期与真实会员的验收边界

完全离线的 `httpx.MockTransport` 验证中心410/1002029025映射为产品 `AUTH_OPERATION_EXPIRED` / 410，网络请求0，仅使用合成凭据。首次探针误用异常属性`.status`，修正为现有`.http_status`后通过；产品源码未改。

未等候新上下文真实20分钟到期，没有改数据库期限、系统时钟或伪造真实认证来补齐。因此本轮不能声称真实20分钟过期链路已重验。

没有取得本轮可复用且获准使用的有效真实会员登录态，也没有搜索/导出浏览器Cookie来绕过这一限制。未验证真实会员SSO登录、browser-completion、OIDC回调、账号绑定、会话领取、访客聊天/文件归并、会员进展及新建用户会话绝对8小时期限。历史G2只作历史证据，不能覆盖本次中心数据恢复后的会员闭环。

若后续需要完整会员验收，需要用户确认可使用的现有真实会员登录窗口；仍不为测试创建账号、发短信或更改会员分类/激活/协议记录。本轮无需用户重新填Key、切换Bridge、修改业务tenant或重启服务。

## 保持与记录

配置核验前后，五份相关私有文件在同一探针进程内逐字节比较保持；没有持久化或输出这些文件的内容/摘要。13个受保护运行容器的ID、image、StartedAt、RestartCount、running和挂载集合均保持。没有发布、重启、迁库、恢复身份或开启正式功能。

本地结果为 **PASS_SCOPED_NO_MEMBER_LOGIN**；[公开JSON](../evidence/2026-11-06-bridge-recovery-verification.json)区分真实协议检查、原生TLS、离线过期映射与尚未执行的会员认证。工作分支仍为 `dev-fengfeng`，原有并行修改保持，未提交、推送或合并。
