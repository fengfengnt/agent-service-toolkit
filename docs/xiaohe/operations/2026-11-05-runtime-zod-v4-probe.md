# Runtime Zod v4 精确解析隔离验证

日期：2026-11-05。结论：**Windows Node 隔离进程验证可行；未修改产品包、SDK、lockfile、候选镜像或服务。** 根入口/v3 残留保留，不能称依赖审计全绿，也不代表 Linux 容器已采用该方案。

## 范围与制品

分支 `dev-fengfeng`，本轮开始 HEAD `b89fa7b`。用户在门户会话明确恢复，并要求先核迁移环境、复用 G2，再以独立 probe 验证依赖最小处理。原并行的部署、运维及未跟踪改动保持。

忽略目录：`D:/Code/yunhezhiyuan-agents/agent-service-toolkit/.superpowers/sdd/2026-11-05-runtime-zod-v4-probe`。

- `setup.mjs`：只在此目录建立到现有 Runtime 依赖的 junction，生成独立 TS 配置、复制同摘要合同、保存原文件指纹；不执行 npm install。
- `register.mjs`：通过 Node 24.21.0 的公开 `registerHooks`，仅在 probe 进程精确重定向 `zod/v4` 及其官方公共子路径，按官方 exports 区分 import/require。不重定向 `zod`、`zod/v3` 或其他根别名，不修改 SDK JS。
- `compat.test.mjs`、`baseline-red.txt`、`alias-tests.txt`：版本、三元素有界样本、真实 AG-UI schema 与残留断言。
- `typecheck-trace.txt`、`build.txt`、`runtime-tests.txt`：严格编译、独立 outDir 构建与现有 Runtime 单元输出。
- `compiled-smoke.mjs`、`result.json`：编译产物实际 loopback Express 与受控网关验证；绝非真实 Python/SSO/G2。

## RED → GREEN 与实际解析

| 项目 | 原解析 | probe 精确解析后 |
| --- | --- | --- |
| 实际 AG-UI client 的 `zod/v4` | 嵌套 zod3.25.76 内 v4.0.0 | 根包4.6.7的 v4.6.7 |
| `.array(z.string()).max(2)` 解析 `[1,2,3]` | 4 个 issues，版本断言 RED | 1 个 `too_big` |
| 超限有效元素的 refine 访问计数 | 3 | 0 |
| 实际 AG-UI1.0.2 输入与公开事件 schema | 原基线通过 | 同样通过，core/client 实际重定向记录均为4.6.7 |
| 根/v3依赖 | 各自原包 | 不变；旧 v3 同样本仍4个 issues |

首次 Node 原型只覆盖了 ESM：CommonJS 仅改 resolver 的 parentURL 仍选中 client 嵌套旧包，被路径守卫拒绝，未计通过。最终按安全包官方 exports 显式区分 import/require，两个入口均验证，不以单一模块路径推断整个 Node 运行时。

3 项 probe 测试全部通过；原 Runtime **38 项单元通过、零跳过**，包含受理、重连、停止与断订阅、初始作用域和23路由拒绝矩阵。只使用虚构凭据、loopback HTTP 与受控网关，不连接运行中的候选服务。

严格 `tsc` 和隔离构建均 exit0，无 `skipLibCheck` 或类型放宽。trace 明确将 `zod/v4` 类型解析到 `copilot-runtime/node_modules/zod/v4/index.d.cts`；不是只改 TS 或只改 JS。编译产物实测 health/info/run 均200、未支持 route404，run/connect各调用一次、无 stop，依次完成 RUN_STARTED/MESSAGES_SNAPSHOT/STATE_SNAPSHOT/RUN_FINISHED。

原 Runtime 的17个源码、测试、manifest、lock、TS配置及合同文件 SHA-256 完全不变；原 lock 摘要 `8a0d2227a4d27403556c17dfc5d2fc06795a6ee351f4c4c278151245ab491f4b`。未自动纳入产品启动入口，未构建或替换 Docker 镜像。

## 残留与可采用边界

原 lock 仍有4处嵌套 zod3.25.76：`@ag-ui/client`、`@ag-ui/proto`、`@copilotkit/runtime`、`@copilotkit/shared`。本 probe 改的是明确 v4 子路径的运行时解析，不改这些包的版本或其根/v3 消费；旧 v3 解析行为仍在。未重跑 npm audit，不宣称告警消失或已覆盖所有调用路径。

[官方修复](https://github.com/colinhacks/zod/commit/a0ebf97)针对 v4 带前置上限的集合提前拒绝；[维护者 PR](https://github.com/colinhacks/zod/pull/6652)明确无上限集合行为未变。这个三元素测试不能证明任意无限数组或嵌套负载已有资源上限。Runtime 现有64KiB入口限制继续有效，但不替代依赖治理。

后续若选择采用，应把精确解析及 TS 路径作为单独源码变更，确保 Node 启动时在 SDK 导入前注册；再验证 Linux 镜像、条件导出、启动入口和既有安全回归。当前不直接改服务/包，不全局 force override，也不借类型放宽使结果变绿。门户 Vite/TS alias 的独立结果由门户 Owner 维护，不能拿本 Node probe 代替其浏览器门禁。

## 迁移后在线状态与复用证据

本方有界只读检查中，`wsl --list --verbose` 返回 Ubuntu Stopped；后续 `docker ps` 15秒未返回，只终止本次客户端，没有重启服务。Windows loopback 对 Runtime18110、候选API18111、候选MinIO35428未取得成功响应，尚不能核实迁移后容器/卷/数据保留；不可达不等于数据丢失。

随后 SSO Owner 报告 `Wsl/Service/E_UNEXPECTED`，本方停止进一步 WSL 调用。用户已在 SSO 会话允许其独占恢复，当前在线健康门仍未放行；本报告不把启动阶段瞬时 Portal200或历史 G2 当作当前在线通过。

已保存的 [G2分层证据](2026-11-05-copilot-candidate-auth.md)继续复用，不重跑真实会员、短信、G2或付费模型。迁移健康门通过后，再只读核实原部署及候选的容器、镜像、卷和专用库/桶；本方此轮未执行 up/recreate/restart、数据变更或正式开关变更。
