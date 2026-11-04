# P8 SSO 适配与联调输入

唯一公开合同为父工程 `docs/contracts/2026-11-03-xiaohe-integration-v1.md` 文档v1.0.3，原wire仍v1.0。v1.0.2已由门户owner冻结，v1.0.3记录用户在本聊天追加确认的响应全部丢失恢复；当前SHA256为0f7592698bb9b45e3d81bd5364ebff59fabb384bf74d420c9940a270c32583f8。不再把旧“待确认”段落作为阻塞。

中心 DTO、原有600/120秒及结果读取期限，以[SSO桥接合同](C:/Users/70367/.codex/worktrees/sso-backend/xiaocai-admin-server/sql/mysql/upgrade/2026-11-03-xiaohe-sso-bridge/README.md)和[SSO回复](C:/Users/70367/AppData/Local/Temp/xiaohe-sso-p8-adapter-reply-20261104.md)为准。中心应用登记已由SSO owner完成，本聊天没有重新登记、生成/读取运行密钥或修改该工程。

## 实现与复用

- auth/context产生独立opaque Cookie/摘要及稳定认证CSRF，不查询/复活聊天内容，不新建guest。所有卡片动作验证当前受信系统、Origin、认证Cookie/CSRF和操作归属。普通聊天及logout仍使用聊天CSRF。
- 新操作固定基础窗口1200秒；公开阶段使用中心更短截止，完成凭据还受自身120秒截止。上下文可延长到已有绑定的合法中心结果窗口，聊天期限不延长。
- 在出站前提交稳定operation UUID，创建重试复用相同中心key及原OIDC/browser challenge。恢复流程返回固定issuer顶层POST，无credential；手机号完成只把短期credential交本次响应，verifier由原浏览器补入。
- 验证短信/协议和captcha均走中心既有接口，不复制会员开户、短信供应商或OIDC换码。captcha外层CommonResult成功还须内层repCode=0000。原访客IP/UA在AI操作创建时绑定，后续仅转发该已验证上下文。
- 未决短信持久key及手机号HMAC；新captcha证明可沿同手机号、同未决key恢复，不自动变成另一条短信。已成功发送的60秒冷却内恢复同challenge，过冷却后的新证明才作为用户主动重发；退休请求HMAC拒绝旧动作迟到重放。保守有效期从首次发送记录计算，最多300秒且不晚于事务截止，不把事务600秒当短信期限。
- 只从中心claim接收六字段可信结果，校验issuer/client/transaction及时间。409/1002029027为可重试AUTH_NOT_READY；429为AUTH_RATE_LIMITED并给安全Retry-After。应用Bearer401与来源Session401区分；不返回原数字诊断，不伪造202。
- 可信结果先持久化，再由P9原子建立AI账号/固定8小时会话和认领。后续本地失败不重新消费code/OTP/SMS。业务消息、checkpoint和普通日志不接收认证请求体。

短信供应商已经发出但中心尚未提交的崩溃窗口，中心自身不保证恰好一次外发；AI保留原key，不声称跨系统exactly-once。未知结果期间更换手机号被拒绝，需明确取消并新建操作。

## 配置项

| 键 | 来源与用途 |
| --- | --- |
| SSO_XIAOHE_BRIDGE_ENABLED | 默认false；正式门户及部署配置继续false。隔离测试仅在自己的app settings开启，不更改运行环境 |
| XIAOHE_SSO_BASE_URL | 登记的固定HTTPS issuer origin；不接受浏览器提供的任意服务地址 |
| XIAOHE_SSO_REDIRECT_URI | 原Portal精确callback；必须与原OIDC tuple及登记一致 |
| XIAOHE_SSO_APPLICATION_ID | 已登记独立bridge applicationId |
| XIAOHE_SSO_APPLICATION_SECRET | 服务端独立应用Bearer；与AUTH_SECRET/模型/MinIO凭据分离，不进浏览器/Git/诊断 |
| XIAOHE_SSO_CA_FILE | 可选受信公共CA文件，用于本地严格TLS；不能关闭验证或复制私钥 |
| XIAOHE_TRUSTED_PROXY_CIDRS | 实测的最小Portal入口socket peer；不能默认信整个Docker网段 |

业务System登记继续显式映射portal→tenant1，源sourceTenantId保留中心含义。AI生成自己的全局user_id，只绑定issuer/sub/sourceTenantId，不解析sub或要求中心增加userId。

## 验证和剩余输入

HTTPX MockTransport验证固定路径/头、禁止重定向、错误转换、六字段白名单；真实隔离PG验证上下文/CSRF/阶段、稳定出站key、身份并发和归并；真实MinIO验证认领前后私有文件。它们不等于真实中心、短信或浏览器联通。

联调前仍需只读核对实际Portal/AI进程、严格TLS、入口头链及AI出站peer。正式开关继续false，既有数据库不升级、凭据不重生、服务不替换。本聊天不修改Portal/SSO工程；需要动作时给用户具体交接。实际SSO/短信、不同site浏览器、真实模型、目标部署/备份恢复均单独留证，不以fixture数量冒充。
