# P2 持久任务验证门

日期：2026-11-03。用户已要求持续按现有计划实施；本阶段基线 `5ee3b56`，分支 `dev-fengfeng`。

## 选择及边界

锁定 Procrastinate 3.10.0，Python 3.14.7、SQLAlchemy 2.1.5、psycopg 3.3.4、PostgreSQL 17.11。新增依赖只有 Procrastinate 及其 asgiref，已有包版本保持。版本和外部连接能力见[官方发布记录](https://procrastinate.readthedocs.io/en/stable/changelog.html)、[受调用方管理的连接](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html)。

`defer_run(db, run_id, lock_key)` 先 flush 业务行，再由当前 AsyncSession 的 connection → raw connection → driver_connection 取得真实 psycopg AsyncConnection；拒绝无事务或 autocommit。任务插入复用该连接，函数不提交、不关闭、不修改调用方连接，不用框架连接池代替外部事务。任务参数只有 `{run_id}`，不包含身份、聊天、Cookie 或认证材料。

业务表仍在 xiaohe，框架管理 public.procrastinate_*；不复制框架 DDL。测试只在空的专用隔离库按框架 SchemaManager 安装，已有运行环境的升级留给部署步骤使用框架迁移能力，不用 apply_schema 覆盖已有数据。

执行互斥使用框架 `lock`，不是 queueing_lock。相同会话锁下第二个 worker 真正完成一次无可执行任务的轮询并退出时，第二条任务仍为 todo；放开第一个任务的进程屏障后两条依次完成。

`build_job_app(database_url, execute_run)` 注入可信执行函数。P2 只有框架适配，测试子进程使用隔离探针；P4 才装配实际业务 worker，不把探针当正常业务执行器。

## 失败和恢复

默认 heartbeat 10 秒、stalled 阈值 120 秒、扫描 cron 每分钟的 0/30 秒、普通轮询 5 秒。只使用框架 get_stalled_jobs/retry_job/finish_job API；不以已运行时长猜测僵死，不自建任务队列。参考[官方失联任务恢复](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html)。

ServiceError.retryable=true 才允许重试；最多三次总执行（框架 attempts 初始 0，允许两次重投），间隔 1 秒、2 秒。权限、取消、过期及未分类异常不自动重试。硬杀重投也受同一预算；耗尽时保留 failed 记录。

恢复扫描维护任务使用 queueing_lock 限制积压；不能使用永久依赖 doing 状态的执行锁，否则扫描任务本身僵死会阻塞后续扫描。扫描体用 PostgreSQL **事务级 advisory mutex**，进程断开/事务结束即释放；它只防并发扫描重复重试，不保存任务、不承担队列职责。僵死的旧扫描通过框架 API 标为 failed，新的周期扫描继续工作。

多副本/负载上线时必须为维护队列保留可用消费能力，不能让所有槽长期被模型任务占满；P11 做进程/队列装配与容量验证。heartbeat 失联可能是活进程停顿或网络分区，P4 仍须业务 attempt/ownership fencing；本门不证明旧外部工具已经停止或恰好执行一次。

## 已执行证据

隔离容器由 `deploy/xiaohe/compose.test.yml` 创建，名 xiaohe-agent-test-pg17，绑定回环 35417，临时 tmpfs，库 test_xiaohe_agent。镜像固定 `postgres@sha256:3b0cda93be7c8dba2ed484d19393d93845dc3dafb88973e248ba8e6d99d784e1`。未访问或改动现有业务库、其他容器或卷。

- 初始测试 RED：xiaohe.jobs 尚未实现。
- 业务行/job 同一事务：提交同时可见、提交前另一连接均不可见、回滚均不存在。
- 受理子进程提交后立即 os._exit(91)，没有返回响应；重启 worker 仍消费原 job，一次完成探针效果。这是数据库/进程证据，不是尚未装配的 HTTP 端点验收。
- 禁用 LISTEN/NOTIFY 后，worker 轮询仍消费任务。
- 真实子进程被 TerminateProcess 硬杀，doing 状态被 heartbeat 恢复并完成；不是捕获异常后的正常退出。
- 同会话执行锁、三次预算、有效 heartbeat 不重试、僵死维护任务自恢复、并发扫描去重均有断言。
- 首次正式时间参数硬杀测试实测 141.20 秒，低于 180 秒目标；补充维护恢复边界后再次执行正式参数验证，实测146.13秒；两次均在目标内。

快速回归对已经确认死亡的探针进程显式使用 seconds_since_heartbeat=0，以验证状态转换而无需每次等待两分钟；不改产品默认值。正式时间验证设置 `XIAOHE_PROBE_REAL_TIMING=1`，使用默认周期扫描，不人工重试、不改 heartbeat 时间。

```powershell
# 显式指定 XIAOHE_TEST_DATABASE_URL，库名必须以 test_xiaohe_ 开头。
uv run --frozen --python 3.14.7 pytest tests/xiaohe/integration/test_job_transactions.py tests/xiaohe/integration/test_job_recovery_probe.py -q -m pg
# 单独设置 XIAOHE_PROBE_REAL_TIMING=1 后重跑 hard-kill 用例，验证真实默认时间。
```

尚未实现 P3 业务受理与 fencing、P4 checkpoint 崩溃恢复、P6 公开 REST/SSE。因此当前只能声明任务框架验证通过，不能宣称门户已可靠受理消息或原生 /stream 已具备恢复能力。

最终 P2 11 项快速测试通过；P0/P1/上游相邻回归合计127项通过，19条既有依赖警告；Ruff通过。默认时间实测额外执行，不用快速阈值代替。P2门已通过，可以继续P3；这不是运行环境SLA或真实模型/门户验收。
