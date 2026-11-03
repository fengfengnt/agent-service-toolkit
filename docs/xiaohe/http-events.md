# P6 REST、事件恢复与应用装配

入口为 `uvicorn xiaohe.main:create_app --factory`，与 `python -m xiaohe.worker` 分进程运行；具体端口、代理和网络隔离留待 P11。业务库引用 XIAOHE_DATABASE_URL；原 saver/store 继续使用上游 POSTGRES_* 配置和连接池，要求 DATABASE_TYPE=postgres。原 AUTH_SECRET 与业务共享密钥必须一致且非空，不采用 FakeModel 代替生产模型配置。

## 当前公开操作

| 路径（前缀 /ai-api/v1） | 行为 |
| --- | --- |
| POST /session | 建立或恢复会话，返回 ChatSnapshot 和 CSRF |
| GET /conversations/{id} | 权限保护的一致快照 |
| POST /conversations/{id}/messages | 原子受理后返回 202 MessageAccepted |
| POST /conversations/{id}/actions | 校验持久问题后续接原 run，返回 202 |
| GET /conversations/{id}/events | 游标回放及实时 SSE |
| POST /runs/{id}/cancel | 显式取消，返回 RunRef |
| POST /auth/logout | 204，仅注销 AI 用户会话，不重置访客额度 |

产出物读取和认证操作由 P7/P8 追加，本阶段没有用假端点返回成功。普通聊天不能提交认证字段。系统来源、Origin、Cookie、CSRF 和对象权限继续使用 P1/P3 规则，内部 Bearer 不能替代用户认证。输入错误和未知异常返回固定安全 ApiError，不回显请求或原始异常。业务响应统一 `Cache-Control: private, no-store`。

DTO 继续使用父工程共享合同 v1.0.1/wire v1.0，未增加第二套协议。浏览器仍按已有约定携 Cookie/CSRF，不发送内部共享密钥；没有新增需要门户改变的字段。

## 快照和游标

正文、状态和事件在同一数据库事务提交。快照持对话锁读取消息、活动 run、viewer 和最后 eventId，worker 也按该对话锁写入，因此快照效果与游标没有缺口。游标是持久 UUID，通过受权对话中的事件行定位 sequence，不排序 UUID。

`snapshot.cursor`、SSE id、ChatEvent.eventId、下次 after 使用同一个值。after 和 Last-Event-ID 同时出现必须一致；外部/未知/未来游标返回 409 EVENT_CURSOR_INVALID，已淘汰或回放范围有缺口时返回 410 EVENT_CURSOR_EXPIRED。以上初始检查在发送 SSE 响应头之前完成，错误响应为普通 JSON。

event_tombstones 只保存已淘汰事件的 ID、归属和 sequence，用来区分过期和无效游标；本阶段实现查验，没有开放删除或自动淘汰接口。后续清理必须在对话锁下同事务保存 tombstone 再删除事件，保留最新水位事件供快照定位，不能静默留下回放缺口。P10 决定实际清理时机。

事件 ID/payload 不可修改。`persist_reply_chunk` 以 run+attempt+chunk_key 幂等记录增量，同键改内容拒绝；已有卡片保留，增量追加最后一个 text part。重建使用同 ID message.upsert 整体替换，前端不能把恢复后的完整正文追加到旧残片。

## SSE 消费

```text
id: <持久 eventId>
event: message.upsert
data: {"eventId":"...","conversationId":"...","runId":"...","type":"message.upsert","payload":{"message":{...}}}

```

消息以 UTF-8 JSON 编码，SSE 字段结束用 LF，事件结束用空行；正文换行由 JSON 转义。心跳是无 id 的注释，不推进水位。订阅先严格回放 after 之后的持久事件，再以 PostgreSQL 轮询接续；响应关闭缓冲，不能把 HTTP 分包等同事件边界。

每批及发送前重验会话、归属和 ownership_epoch，旧 epoch 的流关闭。访客在订阅中到期时持久记录 viewer.expired 后关流；已失效的初始订阅返回 410。用户注销/失效后停止旧流读取，后续 API 返回 401。关闭 SSE 没有取消或完成任务的含义，显式取消使用 cancel 操作。

门户继续先安装一致快照，再从其 cursor 订阅；先检查当前身份/本地 epoch，再按 eventId 去重，upsert 替换同 ID 消息。410 游标过期时重取快照，不重发用户消息；HTTP 202 的 MessageAccepted 没有 cursor，不能借它推进水位。上述消费规则与已交接合同一致。

## 原应用复用及启动

新增入口在原 lifespan 前注册 xiaohe 图，显式委托原 app 的 lifespan 完成 saver/store setup、Agent 装载和关闭；没有复制上游初始化或新建生产 checkpoint 池。原 app 挂载 `/internal/toolkit`，新增执行路由在 `/internal/xiaohe`；原 native Schema、AG-UI 和已有路径处理函数保持原样。顶层文档端点关闭。

跨进程初始化使用专用连接的 autocommit 会话锁，非阻塞尝试取得锁；原初始化进入后立即解锁，再开始服务。不能持普通事务包裹初始化：真实全新库测试复现了它阻塞原 `CREATE INDEX CONCURRENTLY` 的问题。异常/取消时受保护地解锁，解锁失败销毁连接，避免将持锁连接归还池。

同一 Python 进程只允许一个活动小鹤装配实例拥有上游全局 registry；多副本使用独立进程。公共反代必须仅允许 /ai-api/v1，内部 mount 的 health、文档及原生路由不得通过通配代理暴露；该网络门仍由 P11 落实，当前内部监听本身不等于公网隔离。

## 已验证范围

- REST 状态/别名/安全错误、Cookie/CSRF 与 Bearer 分离、到期不重置访客。
- 游标同一体系、外国游标拒绝、淘汰游标 410、旧 epoch 关闭、持久过期事件、增量幂等和最终 upsert。
- ASGI 真实断开订阅后，独立 worker 继续完成并能通过快照取回最终正文；这是 ASGI/数据库证据，未冒充浏览器或公网网络测试。
- 原 lifespan 开关各一次并绑定新增图；原 info 协议和原 client/threads 受控兼容检查。
- 两个真实子进程在本次创建的全新 test_xiaohe_startup_* 库并发启动，使用原 memory 工厂及连接池完成初始化，验证 checkpoints/store 及测试角色读写权限，然后删除本次临时库；不改既有业务数据库。探针仅加载小鹤，不代表所有上游示例 Agent 的外部依赖都已验收。

真实模型、SSO/短信、浏览器、生产数据库角色最小权限和代理部署尚未验收。

本阶段完整业务回归及原 client/threads 基线合计 206 项通过，19 条既有依赖警告；Ruff、Alembic 无差异检查通过。共享合同 SHA256 保持 68ce8cc2da56093caf9b90a98761cf85f782653837e0d3e477e11cfc8441ed5d，上游保护路径无改动。
