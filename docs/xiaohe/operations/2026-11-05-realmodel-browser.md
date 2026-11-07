# 门户真实模型隔离验收与人工体验窗口

日期：2026-11-05（北京时间）。**技术链路通过，三角色服务介绍未通过业务验收；人工体验窗口已准备。** 本轮沿用用户对真实模型测试和“继续下一步”的授权，不改门户正式开关，不自动发短信，会员G2继续复用历史证据。本批没有产品源码修复、构建、发布、推送或合并。

## 固定来源与实际路由

- 前端产品 `21b8337eb1f983e7136333a18a8810cc3b412a86`；使用既有true隔离构建，不替换正式静态包。本方再次核对index及146个JS/CSS摘要，index SHA256 `7870722a8d81402aa4f3464274aaea1ccd21d1941c8cfd7894711e920c4d8b93`。
- 临时Node Runtime产品 `af2e6a21d49bbc35fc2adc36dcc1fe243275f457`，镜像 `sha256:97487c6d389e18136c5c8129f843e28e77f87db6a8b389accad90262966370b4`，18112；严格TLS代理18113，marker `realmodel-011-af2e6a2`。代理复用前轮代码，仅改验收marker，不缓冲SSE。
- Node gateway与浏览器REST精确访问 `https://portal.lvh.me/ai-api/v1`，连接已运行的Python `0.1.1` / image `sha256:241633314bf9fb119ce50e8c94bfed809677a173226c34a437b0a546a1b44859`，真实 `deepseek-v4.1-flash`。没有使用仍受控的 `copilot-candidate` 前缀。
- 门户Owner原生操作Edge，合成访客数据，无既有会员会话。Runtime请求通过同协议 `route.continue`，未把业务响应替换为fixture；静态GET仅映射到上述固定构建。

## 自动验收实际结果

最多8次新业务输入，本轮实际6次，不自动重发：三方向介绍各1、采购方费用问题兼关闭/重开/刷新1、供应商办理流程询问1、独立停止1。原始脚本的6项技术PASS不等于6项业务通过。

| 范围 | 实际结果 |
| --- | --- |
| 主链路与恢复 | 新访客欢迎/三个入口可见；问题经真实后端处理；关闭不发送cancel，重开和整页刷新保持同一访客/长期对话及消息ID、顺序、时间和parts摘要 |
| 停止 | 独立访客activeRun出现后停止，浏览器恰好1次cancel；主库对应新增1个cancelled任务 |
| 三方向介绍 | 三条不含费用问题的介绍请求，均只返回“费用、分成和交付时间需顾问根据正式政策及具体需求确认。”；**业务阻塞** |
| 费用/分成/交期 | 独立费用问题未获得编造的数字承诺，得到需正式政策确认的兜底；业务口径最终仍由用户确认 |
| 留资/注册/转人工 | 问题明确“如果以后、目前只问流程、不要替我办理”，未弹卡片符合消极/假设意图规则。回复让用户通过当前未展示的认证卡片操作，指引不清晰；不据此判认证实现失败或转人工已成功，主动办理本轮未测 |

数据库只读基线：主库`completed=3`；结束后`completed=8,cancelled=1`，恰好新增5完成+1取消；候选库仍`completed=3`。这些是业务run计数，不是provider实际请求次数。浏览器记录30个Runtime POST包含connect/恢复，不能当30条输入或30次模型调用。短信、真人登录和认证complete均0；本方不额外发送付费预热请求。

浏览器SSE观测存在多网络块，`businessDeltas=0`；采购页刷新后观测数组初始化。不宣称真实模型在浏览器逐token输出。后台关闭检查没有额外保存“关闭瞬间worker正在执行”的证据，不扩大该断言。

## 已只读诊断的问题

在完全离线的注入模型探针中，`reception_outcome()`保留普通服务介绍；给同一介绍加上“具体收费和交付安排需要顾问确认。”，当前回答正则会把整段替换为费用兜底。已复现**正常介绍连同安全免责声明被一并拦截**，不调用provider、不改源码。

本轮没有保存三条真实回答在保护规则前的raw输出，所以此证据证明规则行为缺陷，不能声称重放了原始模型内容或精确确定每条的触发词。建议后续最小修复只聚焦 `src/xiaohe/graph.py` 与相应回归：区分正常说明、明确报价/数字承诺，保留越权/认证敏感信息保护。先对齐修复范围，再实现并按新版本流程部署；本轮不直接修改0.1.1。

离线还确认了假设/消极认证意图应返回None，明确“我要留资/我想注册”可分别识别lead/register。主动认证的源码识别证据不等于本轮真实浏览器认证验收；文字指引可操作性作为单独较低优先级问题。

## 用户可见窗口与生命周期

自动测试旧窗口已经结束；随后只做bootstrap和读取，另开全新访客人工窗口，未重跑6条业务输入。Node进程PID71544，标题 **小鹤真实模型人工体验｜隔离窗口｜不登录不发短信**。进程内只读状态与截图证明页面在线、输入框可编辑、新访客0条消息；人工输入会产生真实模型用量，独立计数。测试路由阻断短信、验证和认证完成请求。

窗口没有导出Cookie、CSRF、storageState、trace/HAR或身份数据。不要将直接重新打开普通门户URL等同该窗口：固定静态映射仅在此隔离context生效。

为保持该体验可用，本方两个临时容器继续保留：`yunhezhiyuan-aibackend-copilotruntime-realmodel-af2e6a2`（ID前缀`4dc49be473a1`）、`yunhezhiyuan-aibackend-copilotruntime-realproxy-af2e6a2`（`98d07f295222`），loopback18112/18113。它们不是正式部署。用户体验结束后关闭窗口并告知，再按保存的精确ID/name/owner label/image回收这两个容器；不删除测试数据、卷或镜像。

## 环境核验的实际边界

本轮基线覆盖29个既有容器；10个AI相关/共享Nginx核心容器的ID、镜像、启动时间、重启、running和挂载集合全部保持。Portal443及SSO Discovery443状态和摘要保持，门户正式HTML摘要仍 `6d0c17c0c75eb5edc3588f45e437be8c90569788f2e41d6062e4b4f1f84f4995`。本任务未重建或删除既有服务，没有改变Nginx/SSO/前端正式开关。

**不能声称整个WSL的29个容器都不变**：其中27个全字段保持；专用reception测试卷对应的额外`xiaohe-agent-test-minio`已不在，`yunhezhiyuan-serverbackend-0.1.0`在15:27:38 UTC发生容器重建，镜像、挂载、running和重启次数仍相同，ID/StartedAt变化。本任务没有操作这两个容器；重建来源未确认，不归因、不自动恢复或覆盖并行工程操作。并行接待工程确有隔离MinIO测试活动，该上下文不代替具体容器变更来源的证明。

## 证据

- [本工程公开JSON](../evidence/2026-11-05-realmodel-browser.json)。
- [门户报告](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/sso/2026-11-05-realmodel-browser.md)及[门户公开JSON](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/sso/evidence/2026-11-05-realmodel-browser.json)，初次回执提交`695824a`，最终环境边界补记提交`aaca909`。
- [当前新访客在线窗口截图](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/previews/xiaohe-realmodel-manual-ready-20261105.png)已由本方视觉核对；旧采购问答截图显示初始connecting，不能冒充这个新窗口的ready证据。

旧依赖安全告警、预算边界、真实专业Agent业务、同一文件v1→v2、会员G2重验、容量/UAT/生产放行仍不在本轮完成范围。门户和后端现有产品部署不因临时测试入口或技术PASS而被宣布业务验收通过。
