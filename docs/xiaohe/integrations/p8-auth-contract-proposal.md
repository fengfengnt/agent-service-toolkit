# P8 跨工程认证契约补充建议

状态更新（2026-11-04）：建议已获用户采纳并由门户归入共享v1.0.2；用户随后在本聊天确认响应全部丢失的受限恢复，唯一共享契约已增订v1.0.3。P8/P9正在实现，正式开关关闭；下文保留原建议作历史依据，当前语义以共享§9为准。ChatSnapshot/ChatPart wire仍为v1.0。

## 已确认的阻断点

当前 AI `/session` 对过期访客返回 410，对失效用户返回 401，保持不重置额度和不降级读取的已确认规则。门户 `D:/Code/yunhezhiyuan-portal/frontend/src/chat/auth-api.ts` 的 ensureChatAuthCsrf 仍调用 createChatSession，其注释明确“过期后的认证专用上下文待 AI owner 冻结”。门户计划 Task5/6 也保留了这个未完成门。因此已失效 AI 主体无法获取发起认证所需的 CSRF，不能以负向拒绝测试替代成功恢复。

共享契约 §3/§8 仍将认证专用上下文及 AUTH_NOT_READY 留待补充。认证中心的新桥接已在本地部署/登记，参见其最新[接入参数](C:/Users/70367/.codex/worktrees/sso-backend/xiaocai-admin-server/docs/sso/xiaohe-local-integration.md)；这是只读文档核验，本聊天没有读取运行 secret 或发起真实 SSO/短信请求。

## 建议公开补充

| 项目 | 建议语义 |
| --- | --- |
| POST /ai-api/v1/auth/context | body 精确 `{}`；通过当前系统/可信入口和精确 Origin 校验后返回 200 AuthContext |
| AuthContext | `{csrf:{headerName:string,token:string},expiresAt:string}`，expiresAt 为 UTC ISO；没有 viewer、对话、额度或历史内容 |
| 认证上下文能力 | 独立 opaque/摘要存储的认证专用 Cookie，建议名 `__Host-XIAOHEAUTH`，Secure/HttpOnly/Lax/Path=/；只授权本浏览器推进其认证操作 |
| 认证 CSRF | 建议 headerName 为 X-Xiaohe-Auth-CSRF，token 绑定认证上下文；认证 API 使用它，聊天和本地身份注销继续使用 ChatSnapshot 的 CSRF |
| 身份/期限 | 获取或续接认证上下文不改变原 AI 用户 8 小时绝对截止、访客 4 小时截止或计数。有效上下文可复用且 CSRF 稳定，避免新标签页无故使旧认证失效 |
| 认证上下文生命周期 | AI 实现时保留足以覆盖已绑定操作及合法 claim 重读的期限；建议以 1200 秒认证操作窗口为基础，并覆盖已绑定中心事务截止+结果读取窗口。该期限仅是认证能力，不是聊天身份续期；具体时间必须以服务端返回为准 |
| 创建/推进认证操作 | 使用认证专用上下文及 CSRF；AI 在创建时绑定来源会话提示、系统、Origin、浏览器 challenge 与原 OIDC tuple。认证成功仍由中心可信结果驱动 |
| source 已过期 | 可以重新认证；旧访客数据仍遵循过期/认领规则，不能因新上下文恢复。用户历史在可信身份建立后按既有 canonical 规则读取 |
| AUTH_NOT_READY | 409，ApiError.error.retryable=true，可返回 Retry-After:1；保持相同 operationId 及 OIDC 已换码状态，重试原 complete |
| AUTH_RATE_LIMITED | 建议 429、retryable=true、透传校验后的 Retry-After；与访客聊天额度耗尽分开处理 |
| 200/202 完成响应 | 保留既有 ChatSnapshot。202 只用于已经可信认证且归并等待，不能表示中心未换码或未认证 |

这些是本次提出的技术建议，不把 1200 秒、认证 Cookie/header 名称或新增端点描述成用户此前逐项确认的参数。门户 owner 核对后再增订共享文档为 v1.0.2，保留现有 DTO；AI 本轮没有直接修改共享合同。

## 门户 Agent 要处理的内容

1. 核对新 AuthContext 的精确 parser/fixture，在认证启动处调用新端点；聊天 createChatSession 和原 401/410 行为继续按已有协议消费。
2. 把认证 CSRF 限定在认证流程的本地 generation 中；账号/操作变化时拒绝旧响应。普通聊天、身份注销与认证操作分别使用服务端给出的证明。
3. 在 AUTH_NOT_READY 时保留原操作与“原 code 已成功兑换”事实，仅重试 AI complete，不重新换码；与 MERGE_PENDING 的已认证 202 语义分别测试。
4. 对照原计划核对 /ai-api/v1 代理及认证入口头：由受信入口生成单值 X-Xiaohe-System-Id、X-Portal-Client-IP，统一 HTTPS scheme 与可验证的原 socket peer。实际入口规则留给实施环境验证，不从浏览器透明传入可信头。

## SSO Agent 核对范围

中心 HTTP/错误/浏览器接口以[已实现的桥接说明](C:/Users/70367/.codex/worktrees/sso-backend/xiaocai-admin-server/sql/mysql/upgrade/2026-11-03-xiaohe-sso-bridge/README.md)为准，本次没有提出新中心端点或变更已确认的 600/120 秒、claim 键及六字段结果。

- 原 code 未成功兑换的 409/1002029027 映射为上述 AUTH_NOT_READY；成功换码后的重试只读同一中心结果。
- 给 AI adapter 指明现有 create/SMS 稳定幂等键及短信重发的生命周期测试，避免 AI 在响应丢失时换键重发短信；若已有覆盖，引用现有合同/用例即可。
- restore 顶层 POST 省略 credential、仍携独立 browserVerifier；手机号流程携 credential，AI 返回 fields 不包含/覆盖 verifier。沿用已实现 browser-completion→issuer continue→continuation→原 OIDC 的路线。
- 实际 AI 出站 peer、原访客 IP 和 UA 转发以运行测量及登记 allowlist 为准；SSO 提供自己的配置/fixture核对，不重新生成或在文档中复制 secret。

## 共同验收条件

- 已过期 guest 410、失效 user 401 后均能取得认证专用上下文；旧内容仍无权读取；访客计数和原截止不变。
- 已登录门户但 AI 身份到期时，通过真实中心策略完成 restore 后才恢复 AI 身份，不凭门户本地标记授权。
- 错系统/Origin/CSRF/标签页/operation 绑定拒绝；认证上下文不授予聊天/文件访问能力；双标签页及账号切换不串身份。
- 未换码时 complete 返回可重试 409，不产生 user 或假 202；换码成功后的网络重试不重复消费 code/OTP/SMS。
- 手机号、OTP、captcha、browserVerifier、credential 和应用 Bearer 不进入聊天/业务事件/checkpoint/通用日志。真实短信、浏览器和 SSO 联调分别按已有授权执行并记录，不以 fixture 代替。

两个工程确认契约补充后，本聊天继续实现 P8；P9认领、P10治理、P11部署方案及P12组合验收随后按原计划推进。
