# Zod v3 残留与许可包内嵌 Zod 的上游研究

日期：2026-11-05。范围：官方公告、官方源码、npm registry 元数据及本机已安装的官方发布文件；只做研究，不安装、修改依赖或运行服务。版本与 dist-tag 是本次在线查询快照，后续可能变化。

## 结论

1. **`npm audit` 给出的 `zod >=4.6.6` 不能解释为包中所有入口均已修复。** 公告按 npm 包版本标记 `<4.6.6` 受影响；实际修复提交仅改 `src/v4`。`zod@4.6.7` 仍导出 `./v3`，该入口的数组、Set 超限后仍继续解析元素。将四个嵌套 v3 包强制改成根 v4，既不能证明兼容，也不能证明 `/v3` 安全。[GitHub 官方公告](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5)、[修复提交](https://github.com/colinhacks/zod/commit/a0ebf97)、[4.6.7 元数据](https://registry.npmjs.org/zod/4.6.7)、[4.6.7 自带 v3 源码](https://github.com/colinhacks/zod/blob/v4.6.7/packages/zod/src/v3/types.ts)
2. **本次未找到官方已发布的 v3 修复版。** registry 中最新稳定 v3 为 `3.25.76`，最新稳定 v4 为 `4.6.7`。官方修复 PR 的目标明确为 v4，因此不能把本地回移称为“已升级到官方修复版 v3”。[Zod registry](https://registry.npmjs.org/zod)、[PR #6652](https://github.com/colinhacks/zod/pull/6652)
3. **存在依赖树版本之外的旧实现。** `@copilotkit/license-verifier@0.5.0` 发布文件内嵌 `zod@4.3.6/v4`，并非 v3。替换包外 `zod` 或 `zod/v4` import 不能替换已经复制进 bundle 的函数。该包最新稳定版仍为 `0.5.0`，仅有根导出；未发现可直接切换的官方无内嵌入口。[许可包 0.5.0 元数据](https://registry.npmjs.org/@copilotkit/license-verifier/0.5.0)、[官方发布 tarball](https://registry.npmjs.org/@copilotkit/license-verifier/-/license-verifier-0.5.0.tgz)

## 上游修复实际覆盖什么

公告 GHSA-mrhx-c7xc-m3w5 当前页面于 2026-10-23 更新，标记 `<4.6.6` 受影响、`4.6.6` 已修复。公告文字提及无长度上限的数组；但对应修复的实际能力是**有上界集合在解析子项前拒绝超限输入**，不是给任意无界 schema 自动增加限制。[公告](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5)、[修复 PR](https://github.com/colinhacks/zod/pull/6652)

`a0ebf97` 共改六个文件，均在 `packages/zod/src/v4`：三个 array/map/set 测试，以及 `core/{schemas,util,compile}.ts`。`util.sizeCaps()` 收集前置 size checks 中的上界，`schemas.ts` 的 `capSize()` 在分配输出、遍历元素前执行这些检查；编译路径也提前检查。[提交文件清单](https://github.com/colinhacks/zod/commit/a0ebf97)、[util.ts](https://github.com/colinhacks/zod/blob/a0ebf97/packages/zod/src/v4/core/util.ts)、[schemas.ts](https://github.com/colinhacks/zod/blob/a0ebf97/packages/zod/src/v4/core/schemas.ts)、[compile.ts](https://github.com/colinhacks/zod/blob/a0ebf97/packages/zod/src/v4/core/compile.ts)

该修复有意只使用前置且安全的检查。例如 `.overwrite(a => a.slice(0, 2)).max(2)` 不能在 overwrite 前按原始长度判失败；官方测试保留该行为。不能把任意下游 refinement、transform 或 overwrite 的限制全部提到输入之前。[官方 array 回归测试](https://github.com/colinhacks/zod/blob/a0ebf97/packages/zod/src/v4/classic/tests/array.test.ts)

由此推断：无 `.max()` / `.length()` / `.size()` 的集合、巨大 record、合法但过大的输入，仍需要调用方已有的请求体/结构预算限制；仅升级 v4 不是通用资源预算。该推断基于修复的触发条件，不是上游宣称的全面 DoS 防护。[修复源码](https://github.com/colinhacks/zod/blob/a0ebf97/packages/zod/src/v4/core/schemas.ts)

## v3 可最小回移的具体位置

本机 `zod@4.6.7/src/v3/types.ts` 与官方 tag 源码核对；下表行号采用该 tag。v3 的问题不是“完全没有检查长度”，而是检查后 `status.dirty()`，仍继续分配、解析。[官方 v3 源码](https://github.com/colinhacks/zod/blob/v4.6.7/packages/zod/src/v3/types.ts)

| 集合/函数 | 官方源码位置 | 当前行为与最小建议 |
| --- | --- | --- |
| `ZodArray._parse` | `src/v3/types.ts:2236`；exactLength 2256 附近，maxLength 2287 附近 | 已有 `too_big` issue，随后 sync 的 spread/map 或 async 的 `Promise.all` 继续走全部子项。仅对 `length > exactLength`、`length > maxLength`，保留原 issue 字段与消息，改为在分配前返回 `INVALID`。不要因 exactLength 的 too-small 分支一并提前退出。 |
| `ZodSet._parse` | `src/v3/types.ts:3688`；maxSize 检查位于元素 spread 前 | 对 `size > maxSize` 保留原 issue 后提前 `INVALID`；`.size(n)` 本来组合 min/max，因此超大时也覆盖。minSize 保持原行为。 |
| `ZodTuple._parse` | `src/v3/types.ts:3397` | 无 rest 且输入超过固定项数时，已有 `too_big`，却仍 spread 全部输入。可在原分支提前拒绝，避免大数组复制；有 rest 的 tuple 是无界集合，不能按固定项数拒绝。此项是额外本地加固，未列在该上游提交中。 |
| `ZodMap._parse` | `src/v3/types.ts:3594` | v3 没有原生 `maxSize` / `.max()`，会先 spread entries 再解析 key/value。不能照搬 v4 map gate 假装已经存在上界。需要调用方明确的大小约束或通用输入预算，不能为保持表面“修复”擅自发明阈值。 |
| `ZodRecord._parse` | `src/v3/types.ts:3505` | 没有原生条目上界，`for...in` 将每项 key/value 压入 pairs。若必须限量，需调用方明确预算并在解析之前拒绝；不要只在解析后的 `.refine()` 检查数量。 |

表中回移是本地维护建议，**不是已经实施或经测试的补丁**。不建议拷贝 v4 `capSize()` 到 v3：内部定义、issue 格式和状态模型不同。最小 v3 补丁仅需使用既有 `addIssueToContext` / `INVALID` 与现有 `_def` 字段。[v3 parse 状态实现](https://github.com/colinhacks/zod/blob/v4.6.7/packages/zod/src/v3/helpers/parseUtil.ts)

保持 validation 强度的要点：超限输入必须失败，禁止截断、只验证前 N 项、丢弃额外键、返回原值或替换为 `z.any()`。类型判定、错误 path、上下文 errorMap、自定义 message、`inclusive` / `exact` 标志均沿用原实现。因为 `INVALID` 会改变错误聚合与外层 `.catch()` / effects 行为，即便合法输入不变，也不能承诺所有无效输入的错误列表、refinement 副作用完全一致；需加入回归检查。[v3 effects/catch 与数组实现](https://github.com/colinhacks/zod/blob/v4.6.7/packages/zod/src/v3/types.ts)

## 最新 CopilotKit / AG-UI 能否直接收口

以下为在线 registry 查询结果，明确区分 npm tag 与 GitHub release；网页搜索缓存曾仅显示旧 CopilotKit `1.80.0`，本次以直接 release/API 和 registry 为准。

| 包 | 本次最新稳定/候选 | Zod 或许可依赖结论 |
| --- | --- | --- |
| `@copilotkit/runtime` | `latest=1.84.1`；`next=2.0.0-rc.29` | 稳定版 `zod:^3.25.18`、shared `1.84.1`、license-verifier `~0.5.0`；next 改 `zod:^4.2.0`，仍依赖 license-verifier `~0.5.0`。 |
| `@copilotkit/shared` | `1.84.1` | `zod:^3.23.3`，同时依赖 `zod-to-json-schema:^3.23.5` 和 license-verifier `~0.5.0`；强制转 v4 要核对转换器与 v3 schema 兼容。 |
| `@copilotkit/license-verifier` | `0.5.0` | 元数据没有外部 `dependencies.zod`，但发布 JS 内有旧 v4 实现。 |
| `@ag-ui/client` | `1.0.3` | 仍依赖 `zod:^3.25.76`、core/proto/encoder `1.0.3`。 |
| `@ag-ui/proto` | `1.0.3` | 仍依赖 `zod:^3.25.76`。 |

逐项原始证据：[runtime 1.84.1](https://registry.npmjs.org/@copilotkit/runtime/1.84.1)、[runtime rc.29](https://registry.npmjs.org/@copilotkit/runtime/2.0.0-rc.29)、[shared 1.84.1](https://registry.npmjs.org/@copilotkit/shared/1.84.1)、[license-verifier 0.5.0](https://registry.npmjs.org/@copilotkit/license-verifier/0.5.0)、[client 1.0.3](https://registry.npmjs.org/@ag-ui/client/1.0.3)、[proto 1.0.3](https://registry.npmjs.org/@ag-ui/proto/1.0.3)。

CopilotKit 最新 GitHub release 为 `v1.84.1`（2026-11-03）；AG-UI 最新 release 页为 `release/2026-11-03`，该页发布的是 Python 包，不能据此推导 client/proto npm 版本。[CopilotKit release](https://github.com/CopilotKit/CopilotKit/releases/tag/v1.84.1)、[AG-UI release](https://github.com/ag-ui-protocol/ag-ui/releases/tag/release/2026-11-03)

因此，此时升级 client/proto `1.0.2 → 1.0.3` 或切到 CopilotKit v2 候选，**均不能单独证明全部旧解析器消失**；后者还是跨版本迁移，超出保持功能不变的最小修复范围。AG-UI 官方说明 core 主入口不加载 Zod、schema 单独导出；但 client 自身仍需要 Zod，不能用只导入 core 类型替代真实校验。[AG-UI 1.0 迁移说明](https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/migrating-to-1-0.mdx)

## 内嵌许可包：证据与维护路径

本地只读核对的官方文件位于 `copilot-runtime/node_modules/@copilotkit/license-verifier/dist/{index.mjs,index.js}`。ESM 文件第 4 行起的 region 标记明确为 `../../node_modules/.pnpm/zod@4.3.6/node_modules/zod/v4/...`；约第 1018 行为版本 region，第 1444 行为 `$ZodArray`，约第 3998 行才开始 `../../libs/license-catalog/src/index.ts`。`src/verify.ts` 与 `src/organization-entitlement.ts` 使用其中的 schema；后者导出 `organizationEntitlementSchema` 和 `parseOrganizationEntitlement`。这些是发布物源码证据，不是仅凭 `ZodArray` 字样的猜测。[相同版本官方 tarball](https://registry.npmjs.org/@copilotkit/license-verifier/-/license-verifier-0.5.0.tgz)

该包导出 `verifyLicense`、`createLicenseChecker`、key attestation、feature/limit 检查等完整功能，schema 参与实际许可解析。尤其 feature record 当前无大小上界；即使外置到安全 v4，仍需保留入口处现有资源预算，不能宣称新版自动为 record 限量。打包内出现 `$ZodArray` 也不单独证明本项目通过许可路径可利用数组漏洞：实际导出的 schema 与调用可达性需由实施者验证。[许可包发布 JS 与声明](https://registry.npmjs.org/@copilotkit/license-verifier/-/license-verifier-0.5.0.tgz)

官方 runtime 当前 `packages/runtime/tsdown.config.ts` 明确 `unbundle:true` 并提供 v2 入口；但许可包 exports 仅 `.`，切换 runtime 子入口不会自动移除许可依赖中的 bundle。未找到公开的 license-verifier 原始 TS/构建配置：尝试 `packages/license-verifier/{package.json,tsdown.config.ts,src/index.ts}` 均返回 404，registry 的 `gitHead=c96cd64977498a56b6b1cbb8cbde54d17ce8156a` 在所声明公开仓库 tree API 返回 422。此证据只能支持“本次未定位”，不能断言源代码永不公开。[runtime 官方构建配置](https://github.com/CopilotKit/CopilotKit/blob/main/packages/runtime/tsdown.config.ts)、[许可包 registry 源数据](https://registry.npmjs.org/@copilotkit/license-verifier/0.5.0)

最小候选方案是**可重复执行、严格匹配版本的发布物去内嵌补丁**：保留 crypto 导入、全部 catalog/keystore/verify/license-check/entitlement 业务 region 和全部导出；将旧 Zod region 整体替换为固定安全 `zod/v4` 的外部导入。对 ESM 的业务 region 静态核对，目前所需构造器为 `record`、`string`、`union`、`boolean`、`number`、`object`、`literal`、`enum as _enum`；CJS 需独立核对后对应 `require`。这是依据官方发布物得到的工程建议，**本次未执行转换，未验证其运行等价性**。[官方发布物](https://registry.npmjs.org/@copilotkit/license-verifier/-/license-verifier-0.5.0.tgz)

官方 registry 的 `0.5.0` 发布包摘要为 `dist.integrity=sha512-vrwKtIpYwF0FT9ZoYASH8owa2cGV0dhDvJGaCRaRMStwDxpc6DRdydKkhx8cWZXyBRxEYcq/Vygv4JvevhQQdQ==`，`dist.shasum=2a4aa0f6b5de927374209e7609e9eaffbace1e6e`。[官方版本元数据](https://registry.npmjs.org/@copilotkit/license-verifier/0.5.0)

补充对两个格式独立核对的精确本地证据（SHA256 是本次已安装文件实测，不冒充 npm 官方摘要）：

| 文件 | SHA256 | 删除 region 的零基字符范围（右端不含） | 业务 region 起始行 |
| --- | --- | --- | --- |
| `dist/index.mjs` | `1493AAD710C85598540C34ABC02A5E383A7B1CA491DA83B387498DF32FD92DFE` | `[71,129451)` | 3998 |
| `dist/index.js` | `17DC355F16EF7F76F3FD01399932280B1B5ACCC3DC83E9E8823E7F985C190BE1` | `[1035,130415)` | 4021 |

两个格式的待替换范围均以 `//#region ../../node_modules/.pnpm/zod@4.3.6/` 开始，止于 `//#region ../../libs/license-catalog/src/index.ts` 之前；字符位置由 PowerShell 字符串索引得到，不作跨编码字节偏移使用。CJS 前置的 rolldown runtime、crypto require/interop 必须保留。两个格式的后续业务区域对八个标识符调用次数一致：`record:2`、`string:17`、`union:2`、`boolean:4`、`number:3`、`object:4`、`literal:3`、`_enum:3`。因此候选 ESM 导入为 `import {record,string,union,boolean,number,object,literal,enum as _enum} from "zod/v4"`；CJS 为 `const {record,string,union,boolean,number,object,literal,enum: _enum} = require("zod/v4")`。计数是静态辅助证据，仍须通过语法检查、导出比对与离线许可回归确认没有遗漏依赖。

实施时须固定原版本/文件摘要，断言 region 起止和替换次数；输入不匹配立即失败，禁止对未知新版模糊正则删除。不能删除许可功能、放宽 `.strict()`、feature 值校验、签名或有效期检查，也不能只替换声明文件制造“已修复”表象。优先等待上游发布外置依赖或安全重构建版本；在本次功能不变要求下，局部、可审阅的发布物补丁比迁移整个 runtime 主版本更小，但仍增加维护责任。

## 最小推荐与验收边界

1. 保留当前根 v4 安全固定；分别盘点实际加载的 v3 包根、`zod/v3` 子路径、ESM/CJS 与 bundle。不要为了 audit 数字为零直接把 v3 API 全部重定向到 v4。
2. 对确实需要 v3 的调用，选择既有补丁机制，对数组/Set 原有上界做提前失败回移；tuple 可独立说明额外加固。不要把 map/record 无界输入误记为该回移已覆盖。对四个独立副本及根包自带 `/v3` 都要验到实际加载文件。
3. 对许可包实施经版本/摘要保护的去内嵌方案，保留完整运行时行为；若无法可靠确认边界与导出依赖，则保留为明确残留风险，不用替身模块或删除功能收口。
4. 最小测试：数组 max/exact、Set max/size、tuple 无 rest 的超限输入不能调用子解析器；覆盖同步、异步、嵌套 path、自定义 message、稀疏数组、边界内错误。`.catch()`、refinement、transform、union/intersection 与错误聚合差异需回归，禁止把失败变成功。许可包对 ESM/CJS 分别验证导出相同、合法签名可用、篡改签名/未知 key/过期许可仍拒绝、entitlement 的 strict/数值约束保留。全部可使用本地合成数据，无需真实密钥、模型或网络许可服务。
5. 最后同时给出依赖树/audit 与发布物扫描、真实加载路径、行为测试证据。自定义回移不会改变 npm 公告的版本范围，audit 仍可能报警；反之 audit 为零也不证明 bundle 与 `/v3` 已安全。

本研究未执行补丁、npm install/audit、服务/容器验证、WSL、模型或 SMS 调用；未读取 `.env` 或业务数据。许可包候选方案尚待离线回归，外部无界输入的资源预算也不能由本报告宣称已经落实。
