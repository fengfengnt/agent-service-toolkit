# 小鹤生命周期与历史 V2：隔离验证记录

日期：2026-11-07。实现基线 `41598965d0633924ffcb6ff9cb80a92d58466887`，业务代码固定到 `f7b36e7abf04ef94482e4b03166dd8f4b0c27ac8`；本记录随 Task 7 提交。分支 `dev-fengfeng`。本轮只新增隔离测试资源，未发布、切换现有服务、迁移真实业务库或开启正式功能。

## 证据边界

| 层次 | 当前证据 | 不代表 |
| --- | --- | --- |
| 源码/契约 | V2 显式协商、有界 SQL、独立预算、迁移 0010、固定 Runtime 版本 | 已升级任何运行库 |
| 组合测试 | `test_lifecycle_history_journey.py` 三项通过，使用独立 PG、ASGI、MockTransport 中心和受控图 | 真实会员认证或真实对象文件可下载 |
| Runtime | 类型检查、71 项 Node 测试及构建通过；未更换依赖 | 浏览器交互验收 |
| 新进程网络 | 新 HTTPS → Runtime → API → PG → worker → 受控图 → `RUN_FINISHED`；验证 CA、主机名和 V2 回显 | 真实模型/短信/SSO、正式 readiness 或业务受理 |
| 浏览器 | 由门户原生工程在新隔离 context 验证，待固定回执 | 旧 G2 或用户既有“我的进展”证据可替代新 V2 |
| 对象存储 | 本轮预览尚未配置；文件版本引用为合成 fixture | 已验证物理上传、下载、归并或删除 |
| 正式环境 | 未变更 | 已上线 |

机器可读摘要见 [验收证据](../evidence/2026-11-06-lifecycle-history.json)。原始日志在本计划独有、Git 忽略的 `.superpowers/sdd/2026-11-06-xiaohe-lifecycle-history-ai-runtime/`；不提交配置、Cookie、认证响应或密钥。

## 组合验证与复跑

只允许新建、可丢弃的测试库。**名字以 `test_xiaohe_` 开头不等于可以清空**：`test_xiaohe_reception_manual_20261106` 是既有真人数据，禁止用于 `pg_factory`。该 fixture 会 TRUNCATE；浏览器预览库必须与 pytest 库分开。

本轮 pytest 使用 `test_xiaohe_lifecycle_20261107`，独立测试 PG 的回环端口 35547；预览另用 `test_xiaohe_reception_v2_preview_20261107`。没有对其他库执行迁移或清理。

```powershell
# XIAOHE_TEST_DATABASE_URL 由本次隔离资源配置注入，禁止指向体验/业务库。
$env:PYTHONPATH = (Get-Location).Path + ';' + (Join-Path (Get-Location) 'src')
$env:USE_FAKE_MODEL = 'true'
$env:DEFAULT_MODEL = 'fake'
uv run --no-sync pytest tests/xiaohe/test_lifecycle_history_journey.py -v
npm --prefix copilot-runtime run typecheck
npm --prefix copilot-runtime test
npm --prefix copilot-runtime run build
```

三项组合断言：匿名 20 条通过实际 admission 和受控 worker 回复；模拟内容到期并调用原安全清理；新周期额度仍为 20/100、可用 80；Mock 可信完成/归并；35 条按 10/10/10/5 读取；首屏外待办定位与精确文件版本 7；丢失首次用户 Cookie 后原绑定上下文重试，账号及原绝对 8h 截止不变。另验证缓存旧窗口后离线新增 25 条，必须用重连快照的 `olderCursor` 补齐；CUSTOM 事实与标准 TEXT 镜像不会在服务投影中追加两遍。前端去重仍由门户测试负责。

组合文件的 HTTP 外呼默认失败，显式 ASGI/MockTransport 不走真实网络。第 100/101、claim/cleanup 竞争、V1/V2 共预算、旧页冻结和未来 baseline 水位等边界复用前六项具名回归，不复制一套业务实现。

首轮广泛回归为 707 通过、6 失败、6 错误、10 个 MinIO 用例未选。一个旧测试仅改 Session 镜像计数、未改新的共享预算，已同步 fixture 并定向通过；原启动隐私测试 15s 超时后原样复跑通过，没有放宽断言。四个一次性 SQL 迁移正例因测试库名被既有保护拒绝；在本轮独占 35547 PG 新建 `test_xiaohe_agent` 后，两个文件共 8 项全部通过，未改原 SQL allowlist。`test_xiaohe_agent` 只指这台专用 PG 的新库，不能据同名去连接旧服务。

6 个 Compose 解析测试在 WSL `wslpath` 子进程输出解码时报错；另一次只读资源门检查超时。本轮保留环境阻塞证据，不重启 WSL/共享服务或改编码掩盖原因。后续可移植回归排除这 6 项及已单独验证的 8 项 SQL 用例，物理 MinIO 仍未运行；不能把可移植全绿说成部署全绿。

复跑结果为 **704 项通过、1 项启动隐私测试超时**；后者在整批高负载下再次超过原 15s，故仅将测试子进程上限改为 45s，原断言和产品启动器不变，随后该项 11.27s 通过。这是「704 项整批通过 + 1 项定向恢复」，不是一次 705 项整批全绿。未因测试环境问题改动真实 Compose、WSL 或运行服务。

## 新隔离网络入口

- 公开 Origin：`https://portal.lvh.me:18593`；API `127.0.0.1:18091`，Runtime `127.0.0.1:18092`，新 TLS gateway `127.0.0.1:18593`。
- Runtime 固定 `XIAOHE_VIEW_VERSION=2`、同源 HTTPS gateway；只在新进程注入公开 `local-ca.pem`，不关闭 TLS 校验、不改系统信任、不改共享 Nginx 或原 443 路由。
- Python 真实 HTTP 只放行回环 18091/18092，中心为 `SsoBridgeClient` 的 MockTransport。Node fetch 只放行新 Origin 的 `/ai-api/v1/agui/`。受控 `JourneyModel` 复用已有隔离 harness，无模型服务凭据。
- 新 System/issuer/client 均为合成 fixture，未登记真实 SSO 应用或回调。不能用该 Origin 做真实会员认证验收。
- 启动脚本、进程 PID、私有配置和原始日志均位于上述 ledger 目录的 `preview/`；服务只绑定回环。不要把私有配置发给门户；仅交付 Origin、公开 CA、CSRF 使用规则及固定版本。
- `/internal/xiaohe/health/ready` 在隔离 harness 中只是存活替身；数据库/worker 能力由成功受理与完成的真实 HTTP 回合另证，不能把它当生产依赖健康。

门户只在全新 context 访问该入口。无 Cookie 时 `POST /ai-api/v1/session`，带新 Origin 和 `X-Xiaohe-View-Version: 2`；随后按响应 `csrf.headerName/token` 发送受保护请求。不复用旧窗口的真人 Cookie，不访问真实短信/中心端点，不重复提交原申请。

## 契约、事件与分页交接

唯一主契约为父目录 `docs/contracts/2026-11-03-xiaohe-integration-v1.md`，仓库同名文件是逐字镜像；当前 raw SHA256：`3435d508e3401ba42359e191ff0b90a96f18c05102274a78e9dcce1fa45d6ca9`。原 V1 机器块 SHA256 仍为 `a1f5b073149da2d9fa33f5174f9c11821ef0f47a4b239377bb869b6252a14aaa`。旧 raw `6446…` 只保留为早期记录。

共享 fixture：`tests/xiaohe/contracts/view_v2.json`；Runtime 派生 schema：`copilot-runtime/contracts/xiaohe-agui-v2.schema.json`。fixture 中已有 `history`、`sync`、`activeInput`、单消息 baseline，不能另造私有字段。

事件消费顺序：初始 snapshot 确定全局水位，必要隐藏正文 baseline 先于 ready/后续 delta；单消息 `watermarkSequence=9` 可以覆盖该消息旧 delta 8，但不能推进全局 ACK 或跳过另一条消息的事件 9。只按 `xiaohe.v2.event` 维护业务历史；标准 TEXT 是 SDK 镜像。SDK 初始及持续窗口不超过 10 条，首屏外待办只用授权 locator 加载，不能塞第 11 条。

## 运维边界与回滚

匿名预算与 4h 正文周期分开：受理用户发送才计费/滑动正文截止，欢迎、答复、系统通知、重试及读取不计次数；4h 清理不清预算和幂等回执。内容 Cookie 与预算 Cookie 有有限 Max-Age（当前一年），`Secure/HttpOnly/SameSite=Lax/Path=/`，无 Domain。浏览器删除全部凭据后无法无损识别旧匿名人，不以 IP/指纹合并。

存量只承接可以由有效归属及保留计数证明的预算，不把未知、冲突、已撤销或认领身份猜成 0。迁移 0010 需要先在副本验证，再独立批准真实库承接；本轮只在新测试库执行。会员始终是建立后绝对 8h、不滑动续期，恢复或 complete 重试不能改原锚点。

V2 是部署进程的显式协商，默认仍为 V1。三端版本不配套应失败，不能偷偷降级成功；旧 V1 也必须继续使用共享预算。回滚代码不能退到旧版“新 Session 重发 100”的 admission，不自动 downgrade/drop 0010，不删除预算/回执来换取旧版启动。先保留备份，在新副本演练完整业务 schema、任务/checkpoint/store 与对象的同一时间点恢复，再考虑切换。

真实 SSO 注册/回调、真实会员与文件闭环、物理对象清理、旧数据承接、部署及正式开关均属后续独立验收；未执行项不得写为通过。

执行中的裁定、理由及误判代价按顺序保留在 [执行裁定](../evidence/2026-11-06-lifecycle-history-rulings.md)，独立审查结果另行追加，不抹去首轮失败。
