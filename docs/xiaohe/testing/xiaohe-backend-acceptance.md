# 小鹤组合验收记录

最新环境（2026-11-04 21:22）：Portal Origin/callback亦已去9444，门户0.1.3、AI固定源b06a57e，55项相关测试及新Origin真实匿名适配通过，旧Origin403，原用户及会话原截止保持。详见[Origin切换记录](../operations/2026-11-04-portal-origin-443.md)；新地址真人会员登录仍未重验。

后续环境变更（2026-11-04 21:02）：本AI按用户批准完成SSO issuer 8443→443的原身份迁移，账号、历史和原会话截止保持，53项相关回归及真实匿名适配通过；新issuer真人会员登录尚未重验。见[迁移记录](../operations/2026-11-04-sso-issuer-443-preserve-identity.md)。下方三方会员证据仍对应原验收时点。

本记录区分受控隔离集成、真实外部服务、浏览器及部署。P0–P12通用底座及本地隔离接入范围已完成，代码仍在 dev-fengfeng。真实Portal HTTPS/API/worker/SSO/PG/MinIO、正常中心手机号页会员验证、首次归并与已有历史追加、文件及刷新恢复均有后续真实证据；模型保持受控，不代表付费模型或三个专业业务已验收。正式开关关闭，部署/备份等发布门另列。父目录AI计划已同步65/65步骤；门户最终提交3318438、计划50/50。

## 当前已有能力

| 用例 | 环境及证据 | 当前结果 |
| --- | --- | --- |
| API/worker共用镜像配置、存储私有、非业务路径拒绝 | test_deploy_contract.py、integration/test_deploy_proxy.py；真实临时Nginx/独立证书，Compose解析 | PASS；未构建业务镜像/启动正式Compose |
| socket peer保留、HTTPS只从受信入口采纳 | test_deploy_contract.py；真实ASGI请求、伪造XFF与未知peer；后续真实Portal Nginx链 | PASS；实际socket peer127.0.0.1已测量，仅信127/32，真实HTTPS伪造头覆盖另有记录 |
| 到期停止queued/running/waiting_input及队列取消 | test_visitor_expiry.py；真实PG及框架任务、取消与业务同事务 | PASS |
| 活动图停止事实、迟到回复/上传拒绝 | test_visitor_expiry.py及P7真实MinIO用例 | PASS；不代表不能中止的外部供应商动作已回滚 |
| 清理认领中的资料保护/安全物理删除 | test_cleanup_races.py、integration/test_cleanup_storage.py；真实PG/MinIO、认领及写锁 | PASS；pending/merged资料排除，未认领过期正文/checkpoint/对象回收 |
| 清理失败、硬杀及迟到对象 | 同上；真实清理进程在对象删除后硬杀、DB回滚、上传取消、迟到存储写入 | PASS；保留最小随机key记录巡检，原生S3版本控制失败关闭 |
| 供应商→组织方向、关闭后恢复、独立worker对象接续 | integration/test_backend_journey.py；真实PG队列/saver/store、受控结构化模型、重建ASGI实例 | PASS；不把实例重建称为实际进程硬杀 |
| 文档ready、下载摘要、匿名及跨系统拒绝 | 同上；真实私有MinIO，应用最小bucket权限 | PASS |
| 幂等重试不多扣、事件按原ID重放 | 同上；真实PG消息/命令/事件、HTTP写入 | PASS |
| 跨方向→私有文件→硬杀API/worker→恢复→已有用户202归并→继续对话 | integration/test_backend_journey.py::test_guest_to_existing_user_with_process_restart_and_artifact；真实内部HTTP API及worker进程/框架/PG/MinIO、中心fixture | PASS；公共适配ASGI实例重建，不是实际Portal浏览器或公开API进程验收；原消息ID/时间和对象key/版本保留 |
| 第100条允许/101拒绝，刷新与到期不重建guest | 同上及test_admission.py | PASS |
| 实际进程三窗口硬杀恢复 | 原integration/test_runtime_crashes.py、execution-recovery.md | 已有P4证据；本批完整回归结果另行记录 |
| 认证专用上下文、过期主体认证、可信claim与canonical归并 | test_auth_operations.py、test_guest_claim.py、test_claim_races.py、integration/test_claim_storage.py；真实PG/MinIO、受控中心 | 历史受控PASS；后续实际Portal/SSO的常规绑定及两来源归并亦PASS，异常专项保留原层级 |
| 响应连Cookie全丢恢复、并发完成、错账号与退出拒绝 | 同上及test_batch_review_regressions.py；同一窗口、首次8小时绝对截止 | PASS；原绑定上下文恢复获用户明确批准 |
| SMS未决同key、不同captcha证明、冷却、迟到旧动作 | test_auth_operations.py及中心适配测试；HTTPX fixture | PASS；不证明供应商与中心DB之间恰好一次 |
| API不可用不耗执行预算、恢复后执行、等待期仍到期 | test_batch_review_regressions.py、test_deploy_contract.py；真实PG/任务框架 | PASS；未启动实际业务Compose |
| 真实中心手机号页短信/会员验证/OIDC换码/本地浏览器 | 隔离Portal/原中心/独立API与worker，用户手动输入及触发短信 | PASS；不代表聊天卡片真实SMS、新客注册或不同可注册域部署 |
| 真实付费模型、正式部署、备份恢复 | 实际供应商/目标环境及相应授权 | 未执行 |

## 本地真实接入验收收尾（2026-11-04）

门户Task8已完成最终验收并提交3318438。完整原始矩阵见[门户最终验收](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/sso/2026-11-04-task8-acceptance.md)，本AI的范围、时间线、资源与只读交叉证据见[三方运行记录](../operations/2026-11-04-isolated-triad.md)和[边界补验](../operations/2026-11-04-task8-boundary-evidence.md)。

- 真实浏览器验证首屏欢迎、三个方向、SSE、文件及重开/刷新；中心正常手机号页认证后，可信complete200和MERGED、原消息/文件身份保留，数据库唯一映射/canonical/claim交叉核对通过。
- 真实HTTPS逐条100次受理/第101次429；独立只读100个viewer.updated事件证明累计1..100且截止严格递增。新专属guest受控推进截止，真实queued→expired、五项410/不复活、原周期维护及安全清理均通过。
- 独立新库/桶/临时双HTTPS入口实际两个不同system/tenant，双向八项403且正常读/下载200；实际DB映射补证排除同系统不同guest，临时进程和凭据已回收，原环境未变。
- 用户在原会员窗口完成已有历史canonical的第二次访客追加；数据库证明旧3+新3消息及新旧文件保留、来源各一次claim、身份/canonical唯一。最后GET-only人工触发校验明确PASS，两个文件实际下载200/SHA一致，保留浏览器观测另证快照及两下载200；回门户后正常新可信认证complete200，无新增短信。此为人工触发断言与真实浏览器观测，不冒充重新运行整个自动矩阵。

真实运行源码固定b370bfb；最终所选328项无skip、Ruff/Alembic通过均为已有验证，本次仅复用。当前无阻塞本地隔离接入验收的问题；这不等同首版专业接待业务完成或正式可发布。

## 尚未验证与后续边界

| 项目 | 当前证据和缺口 | 本次Task8是否阻塞 |
| --- | --- | --- |
| 聊天专用卡片真实SMS、新客注册 | 卡片交互/适配有专项与fixture；真实会员走中心正常手机号页，未实测卡片发短信与新注册完整链路 | 不阻塞本地接入收尾；正式启用相应能力前另验 |
| 真实中心异常重演 | complete全响应丢失、换号、重放、并发和202等已有PG/MinIO/受控中心专项，未全部在真实中心浏览器重复 | 不阻塞已通过常规链路；保留证据层级并按发布范围补验 |
| 真实时间和活动图停止 | 本轮HTTP到期为提前新记录截止、实捕queued；未等待满4h/8h，running图取消及正式时序硬杀复用框架/历史证据 | 不冒充本轮实测；不阻塞已接受的本地范围 |
| 正式运行与发布 | 不同可注册域部署、正式包启用、业务镜像实际发布、备份恢复及容量尚未验收 | 属于正式发布前范围，当前开关继续关闭 |
| 模型与专业业务 | 未调用付费模型/embedding，采购方、组织机构、供应商具体业务流程及知识/业务效果后续设计 | 不阻塞底座接入；不能据此宣布专业Agent上线 |
| 已知维护/上游事项 | 已认领旧匿名namespace专门回收仍待办；上游Streamlit既有单项失败保留 | 不阻塞本地接入；不称全仓全绿，不在本轮顺手修复 |

下一步先确认官网文案/产品PPT知识来源与三方向服务边界，定义答疑、留资、转人工及会员进展工具的业务规则；收费、分成、交付时间由用户确认。本轮仅更新计划/文档，不开始业务实现、接入付费模型或启用正式开关。下文保留较早批次的原始测量与边界，不替换成新通过数。

新增旅程2项通过；P10及相邻24项通过；P11及装配11项通过。完整仓库回归：426通过、4跳过、1失败（33条警告，105.67秒）；失败为原 tests/app/test_streamlit_app.py::test_app_simple_non_streaming 的 Streamlit内部session key缺失，单独重跑同样失败，未修改其源码/fixture/依赖。四项跳过为上游要求外部部署的docker用例。本批业务测试在该次全跑无失败，Ruff/Alembic通过；不能称全仓全绿。

P8/P9批次最终业务、真实PG/MinIO与原client/threads共288项通过，19条既有依赖警告，171.52秒；无skip。专项15项通过，Ruff与Alembic check通过。独立审查两项Important及关联退出漏洞均先RED后GREEN，见[上一批审查与裁定](2026-11-04-auth-deployment-review.md)。该历史结果不覆盖或消除上文全仓Streamlit失败；P10/P12接续批次结果见下节。

审查修复提交`bc62a53`后再次硬杀真实worker，在after-checkpoint窗口恢复142.40秒，目标180秒；实际测试1通过/2个其他窗口未选（154.32秒）。使用正式heartbeat10秒、stalled120秒、scan30秒，没有缩短参数；其余两个窗口已有本次完整业务回归中的受控时序证据，不把未选项当作失败或全窗口正式时序重测。

## P10/P12 接续最终结果

314项完整业务/真实PG与MinIO/原client和threads通过，无skip，19条既有依赖警告，210.25秒；Ruff及Alembic check通过。一次独立审查0Critical/1Important/0Minor，Important的慢存储占住单执行槽问题已RED→GREEN修复，未增加框架并发。清理和迟到key巡检各5秒预算，实际单槽测试证明新聊天与两轮恢复可推进；大guest按独立提交的对象进度跨预算续做。详见[本批验收与审查](2026-11-04-cleanup-journey.md)。正式参数142.40秒是上一批测量，本轮未重新执行该长时测量；不把受控组合或定时故障注入说成真实三方/生产验收。

## 接续门

公开共享合同文档已更新为v1.0.3，wire仍v1.0，最终SHA256为`0f7592698bb9b45e3d81bd5364ebff59fabb384bf74d420c9940a270c32583f8`。v1.0.2为门户此前冻结版；v1.0.3补入本聊天用户批准的原绑定上下文响应全丢恢复。需另两工程同步此文档基线，不重新定义字段。

2026-11-04约10:10（北京时间）只读探测：SSO Discovery严格TLS200，Portal根302后/portal/严格TLS200；Portal GET /ai-api/v1/session返回502，AI18090未监听。只读公共CA；未读取运行密钥、未创建真实中心事务。中心应用已登记不代表AI/门户全链路运行就绪，浏览器实际包版本/入口链仍须owner核对。

P10/P12接续已补匿名物理清理与完整受控组合，下一步仍按原OS Temp交接准备受控三方联调。真实SSO/会员/OIDC浏览器与P12最终产品验收仍待办。正式 SSO_XIAOHE_BRIDGE_ENABLED 保持false；不升级现有数据库或重新生成密钥，不部署。
