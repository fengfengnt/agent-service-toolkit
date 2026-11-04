# 小鹤组合验收记录

本记录区分受控隔离集成、真实外部服务、浏览器及部署。基线 P0–P7 与本批 P11/P10独立部分/P12已有能力均在 dev-fengfeng；受控模型不代表真实模型调用，HTTPX中心fixture不代表SSO或短信已联通。

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
| 已登录Portal、到期AI恢复、受信claim与canonical归并 | 等P8/P9接入共享v1.0.2 | 未执行 |
| 真实短信/会员验证/OIDC换码/跨站浏览器 | 三方服务和相应授权，独立证据 | 未执行 |
| 真实付费模型、正式部署、备份恢复 | 实际供应商/目标环境及相应授权 | 未执行 |

新增旅程2项通过；P10及相邻24项通过；P11及装配11项通过。完整仓库回归：426通过、4跳过、1失败（33条警告，105.67秒）；失败为原 tests/app/test_streamlit_app.py::test_app_simple_non_streaming 的 Streamlit内部session key缺失，单独重跑同样失败，未修改其源码/fixture/依赖。四项跳过为上游要求外部部署的docker用例。本批业务测试在该次全跑无失败，Ruff/Alembic通过；不能称全仓全绿。

## 接续门

门户已冻结共享 v1.0.2，SHA256为647faa1b0f79b23fa2847225437433fb6e93ddeeafe73c3e07a28879f997ebac，并在原聊天获得用户授权继续P8/P9。后续补齐中心受信fixture、全局身份/会话、认领安全点与清理竞争后，才能运行访客→已有用户的完整旅程。正式 SSO_XIAOHE_BRIDGE_ENABLED 保持false；不升级现有数据库或重新生成密钥。
