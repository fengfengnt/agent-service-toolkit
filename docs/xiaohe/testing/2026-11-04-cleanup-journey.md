# P10/P12 接续验收与裁定

用户在两份原交接转交后授权“继续”，执行范围是P10清理、P9/P10竞争、P12受控组合与文档。基线`ca424a8`，仍在dev-fengfeng，原生保护目录、原Docker/Compose、既有迁移和共享合同不改。真实Portal/SSO/短信/模型、正式运行配置和部署不在本轮执行范围。

## 当前验证

完整业务/真实PG与MinIO/原client和threads：309通过、无skip，19条既有依赖警告，232.64秒。随后补充真实事务重叠的两种认领/删除屏障顺序，2项通过、9项未选，2.99秒；代码未改，仅增加测试。Ruff与Alembic check通过。独立审查及其必要修复后的最终结果在本文件末尾续记，不把此前结果冒充修复后结果。

| 证据 | 覆盖与边界 |
| --- | --- |
| test_cleanup_races.py | 官方checkpoint删除同PG事务回滚、原run/thread写锁、live拒绝、到期权限、最小cursor与SSE到期记录、撤销访客维护、认领竞争、批次失败轮转、已清理run的失联job恢复 |
| integration/test_cleanup_storage.py | 真实私有对象删除失败、上传/清理取消、真实进程硬杀、pending/merged文件保护、迟到存储写入回收、VersionId失败关闭；中心为HTTPX fixture，不发短信 |
| integration/test_backend_journey.py | 新完整旅程使用真实内部HTTP API和worker进程：两个方向、文件、硬杀/重启、checkpoint重放、已有用户202认领、框架协调、保持ID/时间/key/版本、用户继续对话、跨系统拒绝 |

P12在同一旅程里实际硬杀内部API及worker；公共适配层重建ASGI实例，中心为可信fixture。它不证明真实Portal浏览器、反代网络、OIDC换码或外部模型。硬杀后确认原进程已死才在隔离测试调用0秒失联扫描，证明恢复机制；本次完整旅程不作正式120秒失联阈值的时长验收，上一批142.40秒正式参数测量仍是历史独立证据。

## 执行裁定

1. 保留Session/空Conversation、无正文cursor及唯一安全到期事件，删除匿名正文/运行载荷/文件元数据与checkpoint；保证旧Cookie和SSE不重建主体。代价是保留必要关联及计数元数据，不是删除账号或全部审计痕迹。
2. 使用原PG任务与锁，不增加清理框架；官方saver删除复用原事务。业务图未写共享Store正文，不盲删其他namespace。已认领guest的旧匿名namespace保留撤销保护，其专门空间回收另列后续项。
3. 单靠活跃上传锁无法撤回进程死亡后存储端已接收的请求，因此新增0007最小随机key清理记录，原维护分批持续巡检。代价是元数据和巡检成本随历史增长；没有未经证据支持的定时淘汰。
4. 仅支持当前无原生版本控制的私有桶，遇VersionId失败关闭，不改IAM或用delete marker冒充物理删除。代价是将来启用原生版本控制需另补历史版本清理。
5. 已清理run的迟到内部job安全结束，失联最终回调视为已收尾，避免一个旧UUID破坏全局恢复；公共接口的未知run鉴权行为不变。
6. 组合旅程重建公共ASGI适配时需复用原隔离认证配置；首次503是新fixture恢复默认关闭开关所致，修正fixture，没有放宽产品开关。

P10新迁移仅应用自有`test_xiaohe_agent`；当前MinIO仅自有隔离bucket，未读取运行secret或改其他项目。原全仓Streamlit AppTest失败仍保留，不用本批业务通过覆盖；原四项外部docker测试也不在本批选择范围。
