# Copilot Runtime 隔离验证记录

## A2：逐请求 Runtime

分支 `dev-fengfeng`。复用 A0 的 CopilotKit Runtime 1.84.1、AG-UI 1.0.2；锁文件固定版本及完整性。Python A1 提交 `3d2807f`，主合同 v1.0.4 SHA-256 `752a6365665a6eab3a1cfd7ccf0d064005b4625ad8e2113f6d7a5a4e07ba5a0c`，派生 schema SHA-256 `2c51b56490026013b10aec9b6d92917dad63aaaf1381e2a80e67d6bf95009b83`。

Runtime 仅注册一个 `default`，使用自定义 Runner。Python 受理成功后才附着事件流；重连只读，解绑只终止订阅，显式 stop 才调用业务取消。进程不维护业务历史、不启动模型。

每个请求独立保存经筛选的 AI Cookie、聊天 CSRF、Origin 和 AbortSignal。固定同源 HTTPS 网关禁止客户端覆盖，TLS 校验开启。初始消息暂存至作用域正确的公开状态快照通过校验，避免错误主体或失效流先输出消息。业务错误保留合同错误码，屏蔽上游原文和凭据。

### 已执行

- `npm run typecheck` 严格检查和 `npm run build` 通过；无 `skipLibCheck` 或 SDK 源码补丁。
- 受控上游与真实 Express HTTP：受理一次、waiting_input、断订阅不取消、显式取消一次、未受理不产生 RUN_STARTED、异常 EOF、安全错误、失效前不泄露初始消息。
- 并发两个虚构主体凭据隔离；过滤 SSO Cookie、Authorization 和伪造系统/代理头；跨主体 403、缺 CSRF/错误 Origin/重复 Cookie 拒绝。
- 23 个禁止路由逐个 404：threads 列表/消息/事件/状态/清理/修改/删除/归档/订阅，transcribe，inspector 两种，suggest，trajectory，memories 五种，annotate，debug，以及未知 agent 和前缀别名。拒绝发生在业务调用前。
- 编译后的 `dist/server.js` 实际启动，GET `/copilot-api/info` 无 Origin 返回 200；仅公开 SDK 元数据，私有 POST 仍要求 Origin/Cookie/CSRF。

以上为 Runtime 单元/受控传输层证据，不等于真实 Python/PG/worker/MinIO/HTTPS 链路，也不构成 G1 放行。A3/A4 结果须另行填写。正式开关保持关闭，未自动发短信、未调用付费模型。

## A3：隔离候选环境与 HTTPS

Runtime 镜像 `yunhezhiyuan-aibackend-copilotruntime:spike-2c8132e` 已实际多阶段构建；revision `2c8132ee9caaf055e7a27629deacc1cfa78fa7ee`，image/manifest-list `sha256:eb33944b55f673918a5b6bf8f5cf636e05842579e920041ec689ffeee6e241f8`。容器使用 UID 1000，未包含 `.env`、测试目录或 TypeScript 开发依赖，注入公共 CA 并保持 TLS 验证。服务/容器分别为 `aibackend-copilotruntime` / `yunhezhiyuan-aibackend-copilotruntime`，project=`yunhezhiyuan`，仅映射 `127.0.0.1:18110:3000`。

专用候选 API/worker 使用受控接待图和原产品 worker，SSO 关闭、短信入口拒绝。新 PG 容器端口 35427、数据库 `xiaohe_copilot_candidate_20261105`；新 MinIO 端口 35428、bucket `xiaohe-copilot-candidate-20261105`。两者使用新卷和新凭据，不接原库或 pytest 清理库。候选 Python 启动器拒绝旧库、旧桶、连接参数覆盖和 SSO 启用（7 项通过）。配置仅保存在 WSL 私有目录 `/home/fengfeng/yunhezhiyuan/copilot-candidate`，未写入仓库/归档。

门户 Owner 增加并验证两个候选 location 及 Docker host 接口监听，原 `/ai-api/` 和静态页面保留。实际地址：

- `https://portal.lvh.me/copilot-candidate/copilot-api/info` → Runtime，仅公开 SDK 元数据。
- `https://portal.lvh.me/copilot-candidate/ai-api/v1/` → 候选 Python，Runtime 固定使用此上游。

真实匿名 bootstrap、Cookie/CSRF、受控消息、PG/worker/MinIO 与 HTTPS SSE 冒烟通过：14 帧首帧约 0.356 秒、后续帧约 1.428–1.600 秒逐批到达，业务最终 RUN_FINISHED，快照 3 条消息、1 个私有文件引用。未使用成功响应 fixture。TLS 校验和主机名验证保持开启。容器约束 3 项通过；完整 G1 权限/重启/到期测试仍待 A4。

Runtime 可加载镜像已归档到 `D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend-copilotruntime/local/spike-2c8132e/`，457881600 字节，SHA-256 `b50b51cc21b303cda986fe489a511b2572f8815cd0d69cfe2fba61d2fd314b29`。目录含 `release.md` 与 `SHA256SUMS`，不含运行秘密或业务数据。

## A4：核心链路验证

实际执行 `tests/xiaohe/integration/test_copilot_runtime.py`：9 项通过、零跳过（78.57 秒）。这些测试读取 Owner 显式指定的 WSL 私有候选配置，使用真实 HTTPS、Python、PG、持久队列/worker 和 MinIO；不调用 `pg_factory`，不清理运行库，不模拟成功认证或业务响应。

| 项目 | 实际断言与边界 | 结果 |
| --- | --- | --- |
| V1 受理/持久化 | 真匿名会话；1 用户输入、1 Command、1 job、1 计次，事件已入 PG；受控回复与私有文件实际可下载 | 通过 |
| V2 幂等/响应丢失 | 真实 HTTP 受理响应被客户端主动丢弃后重试；Command/job/用户消息/额度/截止不重复；同键不同输入 409；伪造 assistant 历史/state 不成为业务事实 | 通过 |
| V2 待输入 | waiting_input 只结束交互；新 attempt 恢复同一业务 run；新动作有新 job；重复动作不重复受理；陈旧 stop 不取消后续动作、陈旧 requestId 被拒绝 | 通过 |
| V3 断开/重启 | 运行中关闭真实 HTTP 订阅、只重启本 Runtime 容器；只读等待就绪后 connect，worker 完成；原消息 ID/完整内容/时间及文件 SHA-256 保持；无第二次受理 | 通过 |
| V3 游标 | 在专用库模拟既有事件淘汰并保留 tombstone；旧 REST cursor 410，Runtime 从权威快照恢复且不重新输入；快照订阅水位竞态另由本轮 A1 真实 PG 回归覆盖 | 通过 |
| V4 停止 | 关闭订阅后业务仍 queued/running；显式 stop 后 PG cancelled；重连无 activeRun、无伪 RUN_FINISHED；重复 stop=false | 通过 |
| V5 权限 | 两真实匿名主体并发，互相 run/connect/stop/REST/文件拒绝；另一个真实登记 system 的匿名会话不能接入 portal Runtime；伪造代理/system 头不改变身份 | 通过 |
| V5 默认路由 | 23 个 SDK 管理/未支持路径经 HTTPS 全部 404；缺 CSRF、错 Origin、未知 thread 拒绝；路由拒绝无新业务记录 | 通过 |
| V8 额度/到期 | 仅候选 API 临时设 2 条/5 秒；读取和幂等不续期，新受理滚动；第三条 429；真实流中自然到期发安全错误，随后消息/文件/session/connect/run 均拒绝，不自动新建访客 | 通过 |

短期限测试在 `finally` 恢复候选 API 的 100/14400 并等待 ready；不是修改现有部署。默认 100 条/14400 秒通过新会话数据库事实与原配置核对，没有等待四小时。

本轮相邻 Python 回归 55 项通过、19 条依赖警告；Runtime 最终严格 typecheck/build 及 38 项测试通过，Ruff 通过。没有执行全仓测试，不将既有上游 Streamlit 问题称为已解决。

### 后续门禁与限制

G1 的技术范围为 V1–V5/V8；最终整批审阅后交门户继续 P1–P3。此时尚未完成门户产品 UI 替换与浏览器完整交互（V6）、新 UI 文件交互（V7）及真实会员登录/绑定/归并（V9）。会员真实链路不能用匿名主体或 fixture 替代。候选 Python 入口强制 SSO 关闭，不能直接注入现有私有 SSO 参数来绕过此限制；后续会员准备应由相关 Owner 在明确隔离范围内单独处理，验证码由用户输入。

现有 `aibackend-0.1.0`、原数据库/桶、旧 `/ai-api/` 和门户静态部署保留。正式开关继续关闭，不自动发短信，不调用付费模型，不发布 UAT。
