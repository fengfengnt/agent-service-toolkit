# Copilot Runtime A0 兼容性记录

日期：2026-11-05。状态：本方A0安装、严格类型编译及真实本机Express探针通过；已互核门户P0提交`319b9155b6e13ab08f76a12d6b45caf34f101359`、补丁类型证明及7组浏览器受控断言，G0通过，可继续A1–A4。通过前提包含下述可复现声明修复，不称原发行包未经修复即全绿。用户已在门户聊天明确授权各工程原生持续执行，先A0/P0、过门后继续；无需逐阶段重复审批。

实施依据为门户已提交的`2026-11-05-xiaohe-copilotkit-ai-runtime.md`（Task 1/A0）、总门禁和已确认设计。没有创建正式`copilot-runtime/`产品包，没有操作现有部署、私有配置、业务库或用户窗口；正式开关不变，未发送短信或调用付费模型。

## 版本候选及开源元数据

| 包或工具 | 实际版本 | 许可元数据 |
| --- | --- | --- |
| Node / npm | 24.21.0 / 11.19.0 | 本机工具；SDK要求Node >=20 |
| `@copilotkit/runtime`（使用`/v2`及`/v2/express`导出） | 1.84.1 | MIT；已读取npm发行包LICENSE |
| `@copilotkit/vue` | 1.84.1 | MIT；门户P0严格编译/构建/浏览器通过，含限定声明修复 |
| `@ag-ui/client` / `@ag-ui/core` | 1.0.2 / 1.0.2 | MIT；与两端CopilotKit精确依赖对齐，不取latest1.0.3 |
| Express / RxJS | 5.3.0 / 7.8.1 | MIT / Apache-2.0 |
| TypeScript / Node types / Express types | 7.0.2 / 24.19.2 / 5.0.6 | Apache-2.0 / MIT / MIT |
| 根依赖Zod / `license-verifier` | 4.6.7 / 0.5.0 | MIT / MIT |

`compatibility.json`记录实际manifest的peerDependencies及engines，不省略可选peer范围。npm正常解析依赖、保留完整性校验、`--ignore-scripts`安装，不使用legacy-peer-deps或改SDK源文件。安装中npm对损坏下载自行重试后成功，日志保留；不清其他工程缓存。

lockfile的许可字段覆盖MIT、Apache-2.0、BSD、ISC、0BSD以及AFL/BSD可选表达式，没有因此启用Enterprise、Intelligence、BuiltInAgent或模型插件；这只是已安装制品的元数据核对，不替代未来生产依赖治理。探针在仅保留操作系统必需环境变量的子进程运行，显式禁用遥测，并拒绝非loopback fetch。

官方依据：[AgentRunner接口与持久化](https://docs.copilotkit.ai/angular/strands/backend/agent-runner)、[Runtime HTTP routes及handler模式](https://docs.copilotkit.ai/angular/strands/backend/runtime-endpoints)、[npm Runtime 1.84.1](https://www.npmjs.com/package/@copilotkit/runtime/v/1.84.1)。以下结论以实际发行包及实测为准，不能仅从文档推断接入成功。

## 实际签名与必要接线

| 接口 | 实测约束与接入方向 |
| --- | --- |
| `run(AgentRunnerRunRequest)` | 返回`Observable<BaseEvent>`；接收`threadId/agent/input`及可选owner/auth等字段，Cookie在本请求克隆的`agent.headers`上 |
| `connect(AgentRunnerConnectRequest)` | 返回同类Observable，接收threadId及可选agentId/headers/joinCode/ownerId；只附着，不再run |
| `isRunning({threadId,ownerId?})` | 返回`Promise<boolean>`，无headers字段 |
| `stop({threadId,runId?,ownerId?})` | 返回`Promise<boolean或undefined>`，无headers字段；显式停止与unsubscribe区分 |
| 请求上下文 | Express的`AsyncLocalStorage.run`包围整个SDK handler，`onBeforeHandler`校验/拒绝或克隆读取正文；四个Runner方法都从同一请求作用域读取，不用全局可变凭据 |
| Cookie转发 | 默认仅转部分Authorization/x-*，Cookie默认不转；显式`forwardHeaders.allow=[cookie,x-xiaohe-csrf,origin]`才符合本方需要。A2还须只提取AI会话Cookie，不能把整串浏览器Cookie照搬上游 |
| connect正文/游标 | HTTP connect同样要求完整RunAgentInput，只有threadId会400。SDK解析lastSeenEventId后未交给Runner；Hook通过`request.clone().json()`提取到本请求上下文，并发实测保留。A1/A2据此冻结游标接线，不依赖默认内存历史 |

默认以multi-route挂载`/copilot-api`，采用`createCopilotExpressHandler`、`cors:false`、`activateChannels:false`。仅`GET /info`、`POST /agent/default/run`、`POST /agent/default/connect`、`POST /agent/default/stop/:threadId`是后续实现允许的SDK入口。不能用单路由envelope替代而不同时调整门户provider。

实际声明有23种Hook route method。本轮观察到19种：4个memories种类因默认关闭而直接404；其余通过Hook明确允许或拒绝。21个拒绝的HTTP方法/路径组合逐个实测404，覆盖thread list/messages/events/state/clear/update/delete/archive/subscribe、transcribe、inspector metadata/learning、suggest、trajectory connect、memories及annotate/debug。完整清单及返回值见JSON；历史、state均由后续业务connect输出权威快照，不放行SDK默认存储接口。

## RED、修正与GREEN

1. `tsc --noEmit`首次真实失败：`license-verifier@0.5.0`声明使用`z.core`却未声明Zod依赖，解析到Zod3后TS2694。根显式声明Zod4.6.7，SDK自己的Zod3保留嵌套；不使用skipLibCheck、any或手工覆盖声明，严格编译通过。
2. 首次HTTP probe在默认转发策略下Cookie断言失败；显式allow后run通过。connect仅threadId实际400；按真实RunAgentInput补全后通过。原始错误日志均保留。
3. 两组完全虚构Cookie/CSRF并发执行run/connect/isRunning/stop，异步延迟期间上下文不串；两次run各有独立agent clone，注册原agent headers保持空。
4. SDK实际SSE包含RUN_STARTED/RUN_FINISHED，connect含MESSAGES_SNAPSHOT；unsubscribe不增加stop，只有两次显式stop被记录；缺CSRF/错Origin403、未知agent404。21个非支持路由组合404。

最终命令：探针目录执行`npm run typecheck`，随后`node run-probe.mjs`，均exit0。探针是SDK本地接线验证，不连接真实Python/PG/worker/SSO，不代表V1–V9业务验证。

## 制品与G0状态

忽略目录绝对路径：`D:/Code/yunhezhiyuan-agents/agent-service-toolkit/.superpowers/sdd/2026-11-05-copilotkit-v0/`，内有`package.json`、`package-lock.json`、`probe.ts`、`compatibility.json`、编译/失败/成功日志。只提交本脱敏报告，临时依赖和源码不合入产品。原生执行账本/brief位于相邻的`2026-11-05-xiaohe-copilotkit-ai-runtime/`，没有覆盖门户账本或并行部署文件。

- 当前`result=PASS`、`g0=PASS`、`vueProbeCommit=319b9155b6e13ab08f76a12d6b45caf34f101359`；原始待P0状态是前一阶段历史。
- package-lock SHA256：`c983e07e9ce28d7409d0558a262b620545499e1307c406efe7da0a36857e59fa`；其他制品摘要见JSON。
- 唯一主合同未改，仍在Git外；本次读取时SHA256为`0f7592698bb9b45e3d81bd5364ebff59fabb384bf74d420c9940a270c32583f8`。未添加协议字段或生成第二份权威合同。
- 门户P0修复两种声明：Vue SDK 16处GlobalComponents/GlobalDirectives物化为逐键等价有限mapped type，编译断言证明键和值不变；verifier单处类型import指向精确Zod4.3.6 alias，门户根仍为Zod3.25.76。未改SDK JS、未加全局index signature/any/skipLibCheck。后续门户产品安装须保留版本/计数校验的可复现脚本，升级重新验证；Runtime侧根Zod4.6.7方案属于独立依赖树，不强求前端照搬。
- 门户中文label英文literal类型限制通过官方send-button slot及本地文案处理，未扩大SDK类型。真实浏览器仅HTTP fixture，不能当作真实Python/SSO/文件链路。完整公开P0报告位于门户`docs/sso/2026-11-05-copilotkit-validation.md`，制品与补丁摘要写入`portal-p0-receipt.json`及compatibility JSON。
- 首期connect固定从Python授权一致快照和水位恢复，再读其后持久事件；不要求Vue发送其接口未提供的客户端游标，不新造header/JS补丁。A0的可选lastSeenEventId捕获只证明Hook能力。isRunning在本探针由stop内部调用以验证无headers方法的请求作用域，SDK没有额外独立isRunning HTTP入口。

后续按已授权计划继续A1；G1仍需真实权限/持久/重连验证后才能放行门户产品替换。真实会员443、付费模型、短信、专业业务、UAT/生产发布及原完整验收未在本轮执行；现有受控容器及并行未提交修改保持。
