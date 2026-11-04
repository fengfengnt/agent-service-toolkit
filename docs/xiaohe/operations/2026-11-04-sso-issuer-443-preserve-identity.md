# SSO issuer 443：只读核验与待确认迁移范围

状态：中心已切换443，旧SSO8443已由其Owner关闭；本AI尚未修改运行配置、身份记录或进程。已有旧issuer用户绑定，现先给出保留身份的一次性方案，待用户确认数据修改范围。正式开关关闭，不发送短信、不调用付费模型。AI新登录/绑定的443适配尚未生效，不能将中心完成回执当作本方已完成。

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

当前没有需要挑选目标账号的冲突。唯一待用户决定的是：是否批准对这1条既有用户身份键和1条System登记执行上述保留原user_id的一次性迁移。该数据修改不同于仅更换连接地址，确认前不改绑定或使新配置生效。
