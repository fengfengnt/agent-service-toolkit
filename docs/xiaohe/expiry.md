# P10 独立部分：到期失效和主动停止

本次在 P8/P9 尚未实现时，只实施匿名到期停止，不开始物理删除。同步访问权限仍由会话/写入 fence 的数据库时间强制，不等后台扫描，也不把刷新、回复、SSE 或幂等重试视为续期。

`expire_guests(db, now, limit)` 复用调用者事务，按 session→conversation→run 既有顺序锁定；session 分批、SKIP LOCKED，最终重查实际数据库时间。有效访客和现有 merge_pending 对话跳过；扫描参数给未来时间也不能提前结束未到期会话。

到期后 queued/running/waiting_input 统一变为 expired 并释放 slot，完成未结命令、标记 attempt 状态和未完成产物的 cleanup_pending，持久化 run.updated 与一次 viewer.expired。原会话的过期状态保留，所以再次 /session 仍是410，不清计数、不创建新 guest。框架 todo 取消使用 Procrastinate 的正式 API 和同一 psycopg 事务连接；doing 通过原 worker watcher/fence 中断，不能取消的外部结果依然不得提交。

运行维护接入原30秒恢复任务，独立 PG 扫描互斥。扫描失败回滚业务与队列取消，下一周期重试，不增加新调度框架。到期测试复现了 ASGI 取消会中断 graph_stopped_at 写入的问题，内部流 finally 改为 shield 收尾；保留图停止事实供后续安全清理使用。

已验证：三种运行状态、批次上限、重扫不重复事件、未来 cutoff 不提前失效、既有 pending 标志排除、队列取消原子回滚、正式维护任务实际执行、活动图 finally/停止时间、迟到上传拒绝。现有 graph/事件/worker 相邻24项通过；不是认领竞争或完整物理清理通过。

待 P9 联合实现/验证：guest_claims 的 pending/merged 保护、确定顺序的认领与到期竞争、等待全部原 namespace 停写、重查对象所有者后删除、checkpoint/事件/消息删除及保留 tombstone、迟到上传孤儿回收，以及硬杀/DB/MinIO删除失败重试。没有暴露或启用一个会返回假成功的 purge 接口；对象和历史仍保留，意味着存储空间尚不会自动回收。
