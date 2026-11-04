# 小鹤组合验收记录

本记录区分受控隔离集成、真实外部服务、浏览器及部署。P0–P9核心、P11配置及P10/P12独立部分均在 dev-fengfeng；受控模型不代表真实模型调用，HTTPX中心fixture不代表SSO或短信已联通。

## 当前已有能力

| 用例 | 环境及证据 | 当前结果 |
| --- | --- | --- |
| API/worker共用镜像配置、存储私有、非业务路径拒绝 | test_deploy_contract.py、integration/test_deploy_proxy.py；真实临时Nginx/独立证书，Compose解析 | PASS；未构建业务镜像/启动正式Compose |
| socket peer保留、HTTPS只从受信入口采纳 | test_deploy_contract.py；真实ASGI请求、伪造XFF与未知peer | PASS；实际门户入口链待联调 |
| 到期停止queued/running/waiting_input及队列取消 | test_visitor_expiry.py；真实PG及框架任务、取消与业务同事务 | PASS |
| 活动图停止事实、迟到回复/上传拒绝 | test_visitor_expiry.py及P7真实MinIO用例 | PASS；不代表不能中止的外部供应商动作已回滚 |
| 清理认领中的资料保护/安全物理删除 | 依赖P9实际认领记录与所有权 | 未执行；只保留cleanup_pending，不删对象或checkpoint |
| 供应商→组织方向、关闭后恢复、独立worker对象接续 | integration/test_backend_journey.py；真实PG队列/saver/store、受控结构化模型、重建ASGI实例 | PASS；不把实例重建称为实际进程硬杀 |
| 文档ready、下载摘要、匿名及跨系统拒绝 | 同上；真实私有MinIO，应用最小bucket权限 | PASS |
| 幂等重试不多扣、事件按原ID重放 | 同上；真实PG消息/命令/事件、HTTP写入 | PASS |
| 第100条允许/101拒绝，刷新与到期不重建guest | 同上及test_admission.py | PASS |
| 实际进程三窗口硬杀恢复 | 原integration/test_runtime_crashes.py、execution-recovery.md | 已有P4证据；本批完整回归结果另行记录 |
| 认证专用上下文、过期主体认证、可信claim与canonical归并 | test_auth_operations.py、test_guest_claim.py、test_claim_races.py、integration/test_claim_storage.py；真实PG/MinIO、受控中心 | PASS；实际Portal/SSO未联通 |
| 响应连Cookie全丢恢复、并发完成、错账号与退出拒绝 | 同上及test_batch_review_regressions.py；同一窗口、首次8小时绝对截止 | PASS；原绑定上下文恢复获用户明确批准 |
| SMS未决同key、不同captcha证明、冷却、迟到旧动作 | test_auth_operations.py及中心适配测试；HTTPX fixture | PASS；不证明供应商与中心DB之间恰好一次 |
| API不可用不耗执行预算、恢复后执行、等待期仍到期 | test_batch_review_regressions.py、test_deploy_contract.py；真实PG/任务框架 | PASS；未启动实际业务Compose |
| 真实短信/会员验证/OIDC换码/跨站浏览器 | 三方服务和相应授权，独立证据 | 未执行 |
| 真实付费模型、正式部署、备份恢复 | 实际供应商/目标环境及相应授权 | 未执行 |

新增旅程2项通过；P10及相邻24项通过；P11及装配11项通过。完整仓库回归：426通过、4跳过、1失败（33条警告，105.67秒）；失败为原 tests/app/test_streamlit_app.py::test_app_simple_non_streaming 的 Streamlit内部session key缺失，单独重跑同样失败，未修改其源码/fixture/依赖。四项跳过为上游要求外部部署的docker用例。本批业务测试在该次全跑无失败，Ruff/Alembic通过；不能称全仓全绿。

最终修复后业务、真实PG/MinIO与原client/threads共288项通过，19条既有依赖警告，171.52秒；无skip。专项15项通过，Ruff与Alembic check通过。独立审查两项Important及关联退出漏洞均先RED后GREEN，见[整批审查与裁定](2026-11-04-auth-deployment-review.md)。该结果不覆盖或消除上文全仓Streamlit失败。

审查修复提交`bc62a53`后再次硬杀真实worker，在after-checkpoint窗口恢复142.40秒，目标180秒；实际测试1通过/2个其他窗口未选（154.32秒）。使用正式heartbeat10秒、stalled120秒、scan30秒，没有缩短参数；其余两个窗口已有本次完整业务回归中的受控时序证据，不把未选项当作失败或全窗口正式时序重测。

## 接续门

公开共享合同文档已更新为v1.0.3，wire仍v1.0，最终SHA256为`0f7592698bb9b45e3d81bd5364ebff59fabb384bf74d420c9940a270c32583f8`。v1.0.2为门户此前冻结版；v1.0.3补入本聊天用户批准的原绑定上下文响应全丢恢复。需另两工程同步此文档基线，不重新定义字段。

2026-11-04约10:10（北京时间）只读探测：SSO Discovery严格TLS200，Portal根302后/portal/严格TLS200；Portal GET /ai-api/v1/session返回502，AI18090未监听。只读公共CA；未读取运行密钥、未创建真实中心事务。中心应用已登记不代表AI/门户全链路运行就绪，浏览器实际包版本/入口链仍须owner核对。

下一步按OS Temp交接准备受控三方联调。P10物理清理、真实SSO/会员/OIDC浏览器与P12最终产品验收仍待办。正式 SSO_XIAOHE_BRIDGE_ENABLED 保持false；不升级现有数据库或重新生成密钥，不部署。
