# P4 执行与恢复

业务入口仍待 P6 装配；本阶段不代表门户、真实模型或 SSO 已联通。上游 service、registry、saver/store、模型模块均未修改。

## 执行关系

`python -m xiaohe.worker` 作为独立进程消费 Procrastinate。通过 `ToolkitClient` 携内部共享密钥调用新增 `POST /internal/xiaohe/runs/{run_id}/stream`，原 `verify_bearer` 负责验证；装配前额外确认上游与业务 AUTH_SECRET 非空且一致，避免原验证器的空密钥放行语义。浏览器不接收此密钥，也不能指定原生 thread、namespace、user_id 或恢复状态。

业务 run 是公开结果的权威。每次输入只有一个未完成 Command，每个输入最多执行三次；run.attempt 跨多轮动作单调递增。内部端点再次核验 run/user/thread/attempt/ownership_epoch/mode，且同一 attempt 只能开始一次图调用。原生 graph_run_id、物理 thread 和停止证据存入 run_attempts，不能用原生 UUID 取代业务 ID。

worker 持事务级 advisory guard，阻止相同 run 的多个活执行者；每次正文/事件/终态提交均经过业务 fence。guest 检查原会话、独立执行截止和权限 epoch；user 检查启用状态、独立截止和权限 epoch，本地 logout 不自动取消已受理任务，8 小时原截止仍有效。取消、过期、旧 attempt 的迟到正文不再发布。

框架恢复到三次上限时，先通过持有业务互斥锁的幂等回调结束业务 run、释放占用，再将框架 job 标为失败；当前仍有活 worker 持锁时推迟处理。该回调失败不提前结束框架恢复记录，下一次扫描可重试。

## checkpoint 规则

每个 run 使用独立 LangGraph thread，避免已暂停的旧任务被新请求覆盖。同一输入使用稳定 HumanMessage ID，回复 ID 由 run+command 确定；重试先同 ID upsert 清除旧残片，最终整体替换，不把重新生成的文本追加到旧文本。

Command.delivered_at 仅表示开始交付。恢复还检查 checkpoint 内的 command_id 和持久 `__resume__` 写：未接受时交付一次，已接受未完成时用 `None` 继续，完成/暂停结果可从 checkpoint 重建。当前动作的恢复回执存在、节点尚未完成时，不能把前一个问题误当成本次动作结果。

图运行持物理 thread guard。无法确认旧图已停止且状态未完成时，创建隔离 thread，从受权业务历史重建；旧调用只保留审计，不能提交当前结果。动作重建使用受理时复制的服务端 question，不采纳浏览器伪造问题。HTTP 取消使用独立图流任务和受保护的收尾，等待图清理后才释放执行资源，避免 ASGI 重复取消打断 LangGraph 清理。

ORM、任务、checkpoint 分别管理事务。checkpoint 的 durability 使用 sync；saver/store 由原应用 lifespan 绑定，P6 才装配业务应用，本阶段测试显式绑定真实 PostgreSQL saver/store。`build_graph()` 的无 handler 默认安全失败，测试回复仅通过测试注入，P5 接真实接待节点。

## 验证和边界

测试覆盖重复投递、权限/身份/thread/mode、旧 fence、8 小时独立截止、本地 logout、取消活执行、耐久等待和动作不重放。真实子进程探针经 HTTP 和持久队列运行，在以下窗口硬杀，再由框架扫描接管原 run：

1. 交付标记提交后、图接受前，硬杀 API 和 worker。
2. checkpoint 完成后、业务结果提交前，硬杀 worker。
3. 动作 checkpoint 完成后、业务动作回执前，硬杀 worker。

快速测试只在确认死亡后设置扫描阈值 0，不改产品默认 10 秒 heartbeat / 120 秒失联 / 30 秒扫描。设置 `XIAOHE_RUNTIME_REAL_TIMING=1` 单独运行 after-checkpoint 用例会使用正式扫描参数，并记录实测时间；180 秒是本机探针目标，不是生产 SLA。

本机正式参数完整测试通过，实测恢复 132.52 秒；该测试同时断言原业务 run 完成、模型节点没有重跑、人类消息 ID 不重复。此前一次实测 137.03 秒，业务恢复通过但测试把维护任务计入执行任务总数而失败；修正统计条件后已完整重跑。相邻业务/上游 core/schema 回归 188 项通过，原 client/threads 基线 22 项通过；新增迁移回填和最终受影响测试另行核验。19 条警告来自既有依赖。

```powershell
# XIAOHE_TEST_DATABASE_URL 必须明确指向 test_xiaohe_ 前缀的隔离库。
uv run --frozen --python 3.14.7 pytest tests/xiaohe/test_worker_recovery.py tests/xiaohe/test_run_fencing.py tests/xiaohe/test_toolkit_execution.py tests/xiaohe/integration/test_runtime_crashes.py -q -m pg
```

未完成节点可能再次调用模型或工具。外部副作用必须由工具的业务幂等契约控制；本实现不承诺网络副作用恰好一次。P9 再接入真实 pending claim 的持久协调任务；本阶段没有用空协调任务冒充归并实现。真实模型、SSO/短信、公开 REST/SSE、浏览器和生产部署尚未验收。
