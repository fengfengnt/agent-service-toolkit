# Runtime Zod v4 精确解析隔离验证

日期：2026-11-05。结论：**隔离探针通过后，已将精确解析纳入 Runtime 源码和正式入口；Windows 源码/编译及后续独立 Linux 测试镜像门禁通过，既有运行镜像未更新。** 根入口/v3 残留保留，不能称依赖审计全绿，也不代表既有服务已采用该方案。下文按时间保留探针、源码采用与迁移后复核；后续 Linux 结果见末节链接。

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

## 正式源码采用与迁移恢复后的复核

本阶段在 `dev-fengfeng`、基线 `07af55d` 上执行 [单任务实施计划](../../superpowers/plans/2026-11-05-runtime-zod-v4-resolution.md)。直接读取门户对话确认人类恢复指令晚于旧暂停；没有重复请求同一范围授权。

- 新增 `copilot-runtime/src/zod-v4-resolution.ts`：从当前模块解析根包 manifest，校验4.6.7，使用官方 exports 的 import/require 分支；只接管明确 `zod/v4` 公共子路径，校验实际文件留在包内。
- `src/index.ts` 先安装同步 hook，再动态导入 SDK；`tsconfig.json` 将同一批 v4 类型路径指向根包官方声明。无 skipLibCheck、SDK JS patch 或根/v3全局覆盖，无新增依赖。
- 原测试入口预加载同一产品 hook。新增四项安全/组合测试与只读 load observer；独立入口子进程显式清空继承的 resolver 参数，避免测试预加载掩盖正式入口遗漏。

RED 实测：实际 client 的 v4为4.0.0，子进程同时加载4.6.7和嵌套3.25.76内的旧v4；路径一致性断言也失败。随后 GREEN：ESM与CJS仅加载4.6.7，三元素超限样本一个 `too_big`、元素访问0；官方公共子路径可用，根/v3仍3.25.76，v3同样本仍4个issues。补充 Windows 反斜杠穿越样本后先观察到 RED，再在 wildcard suffix 拒绝反斜杠并恢复 GREEN；防止包内私有文件或v3通过路径归一化绕过官方导出。

本轮验证：**42项全部通过、零跳过**（原38项加4项），严格 `tsc --noEmit`、正式 `tsc -p tsconfig.build.json` 均 exit0；`XIAOHE_TEST_COMPILED=1` 的四项定向验证通过。源码及 `dist/index.js` 独立进程实际 health/info200；编译 Runtime factory 使用受控网关 run200、四类事件完成、run/connect各1、stop0、非支持route404。这里的业务网关是 fixture，不是真实 Python、SSO 或模型。

manifest/lock没有变化，lock SHA-256仍 `8a0d2227a4d27403556c17dfc5d2fc06795a6ee351f4c4c278151245ab491f4b`。证据保存在忽略目录 `.superpowers/sdd/2026-11-05-runtime-zod-v4-resolution/` 的 red/green/backslash-red/suite/typecheck/build/compiled 日志；未用 npm audit 结果替代实际解析断言，未重跑 audit。

SSO Owner 独占恢复并放行健康门后，本方北京时间12:15只读复核：候选 API/worker 仍为 `spike-0d18e81`、revision `0d18e819bfe3a0b7295efc609a3289caf1392ead`；候选 PG/MinIO running，分别保留 `yunhezhiyuan-copilot-candidate-pg` 和 `yunhezhiyuan-copilot-candidate-minio` 卷。原API/worker也在运行。首次旧worker使用错误名称未查到，随后通过只读容器列表纠正为 `yunhezhiyuan-aibackend-0.1.0-worker`；不把首次inspect非零当完整通过。

当前 Runtime running，仍是 **`spike-2c8132e`**、revision `2c8132ee9caaf055e7a27629deacc1cfa78fa7ee`、image ID `sha256:eb33944b55f673918a5b6bf8f5cf636e05842579e920041ec689ffeee6e241f8`；**没有采用本次源码修补**。随后Windows直接loopback `/healthz` 与 `/copilot-api/info` 均200。原API/worker的容器ID仍分别为 `9a813112...` / `841dbbf0...`。未构建、启动、重启、重建或替换任何容器，未改 .env/SSO/Nginx/凭据/数据，正式开关保持关闭。卷挂载存在仅证明资源仍在；未重复读取真实会话/文件或重跑已通过G2。

源码阶段结束时的剩余边界：当时新修补尚未通过 Linux 镜像/正式镜像入口/真实候选网络验证；运行中的旧 Runtime 不能宣称已修复。根/v3 Zod 与门户 KaTeX 风险继续列为发布阻塞，精确v4修补不等于审计全绿或正式发布验收。

## 本批最终审查

产品提交 `1510f25dc056dc626667721c7b7ec204b83ac76b`，范围 `07af55d..1510f25` 全部8文件。`task-done`再次运行42项全通过。唯一 fresh-context reviewer只读审查计划、diff、源码与调用者，另行验证42项、strict和编译入口定向4项均通过；Critical0、Important0，判定本批源码可合并，不代表部署验收。

非阻塞P3待办：`src/zod-v4-resolution.ts` 使用 `resolve()` 归一化目标，`zod/v4/locales/en.js/` 尾斜杠的错误导入在原生ESM报错，本hook可接受；空suffix的目录错误类型也不同。未发现私有文件、v3或包外越界加载，当前SDK无该导入写法。本轮按审查流程记录后暂缓；后续可拒绝空/尾斜杠suffix，并以不加载tsx的原生Node补充回归。

审查边界裁定：Linux门禁留待后续，不推断跨平台通过；旧镜像在线资源由本方只读证据证明，但不推断新补丁已运行或数据完整验收；根/v3及KaTeX继续阻塞发布；历史G2按人类要求复用，真实SSO/SMS/模型不重复执行。保留 `dev-fengfeng`、验收目录和已有并行改动，不推送、不合并到其他分支、不发布。

随后人类继续授权后，已完成[独立Linux镜像验证](2026-11-05-runtime-linux-image.md)：新 `spike-4a55b1e` 断网真实入口/ESM/CJS与最小候选HTTPS匿名只读链路均通过；原8容器身份/镜像/启动/重启/挂载集合不变，临时容器清理。此结果补齐平台门，不解除根/v3、KaTeX及正式发布门禁，不改变既有Runtime仍运行旧镜像的事实。

新一轮已授权的[残留依赖修补记录](2026-11-05-runtime-dependency-security.md)继续处理v3与许可包内嵌4.3.6，使用本地精确回移/去内嵌，独立新测试镜像 `spike-af2e6a2` 已完成门禁；不覆盖本文件的历史探针结论，也不表示既有Runtime已切换。
