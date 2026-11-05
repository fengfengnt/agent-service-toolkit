# Runtime 精确 Zod v4 解析实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已通过的隔离探针纳入正式 Runtime 启动入口，阻止明确 `zod/v4` 消费加载嵌套旧 v4。

**Architecture:** Node 同步 `registerHooks` 仅处理 `zod/v4` 及其官方 exports，ESM/CJS 分别使用根包4.6.7的 import/require 导出；在动态导入 SDK 前安装。TS paths 对齐同一官方类型，不改根/v3 或 SDK。

**Tech Stack:** Node24.21.0、TypeScript7.0.2、CopilotKit1.84.1、AG-UI1.0.2、Zod4.6.7。

**Spec:** `docs/xiaohe/operations/2026-11-05-runtime-zod-v4-probe.md`，以及用户在门户恢复后继续依赖风险处理的授权。

## Global Constraints

- 仅 `dev-fengfeng`，保留已有并行改动、lockfile及数据。
- 不改 SDK JS、不全局 override、不使用 skipLibCheck；根/v3残留不能宣称修复。
- 不构建/替换镜像，不改部署、凭据、SSO、正式开关，不自动短信/付费模型，不重跑 G2。
- 本轮只做 Windows 代码门禁；运行镜像、Linux 与真实网络另列验证边界。

## Review Focus

- 正式入口必须先注册再导入 SDK；子进程测试不得继承父进程 resolver 造成假通过。
- ESM/CJS 都必须命中4.6.7，根/v3消费者仍使用各自原包。
- 私有子路径、路径穿越与不存在的导出不能借 hook 绕过官方 exports。
- 源码和编译入口都需要真实 loopback HTTP 验证，TS路径不能只修类型不修运行。
- 无共享秘密或真实业务/模型请求进入测试，历史 G2 不等于本轮在线健康。

### Task 1: 正式入口与解析回归

**Files:** 新增 `copilot-runtime/src/zod-v4-resolution.ts`、`copilot-runtime/tests/zod-v4-resolution.test.ts`、`copilot-runtime/tests/zod-observer.mjs`；修改 `src/index.ts`、`tsconfig.json`、`tests/run.mjs`；补充上述操作记录。

**Interfaces:** 消费已安装根包官方 exports 与 Node24 `registerHooks`；产出无配置的启动 side effect，不新增业务接口。

- [x] Step 1: 写实际 client CJS + core ESM 版本/有界集合测试，断言4.6.7、超限一个 too_big、元素访问0；保留原3.25.76根/v3，并断言非法导出拒绝。写独立进程入口HTTP health/info及load观测测试，观察实际SDK解析。
- [x] Step 2: `npm test -- zod-v4-resolution`。Expected: RED，实际嵌套v4.0.0与预期4.6.7不符；不可只以不存在文件为失败证据。
- [x] Step 3: 实现小型同步 resolver，仅官方v4导出，校验版本和实际文件仍在包目录；index在动态SDK导入前注册，测试预加载同一源码，TS paths对齐官方声明。
- [x] Step 4: `npm test`、`npm run typecheck`、`npm run build`；设置 `XIAOHE_TEST_COMPILED=1` 后运行定向测试。Expected: 原38项和新增安全门全部PASS、strict/build exit0；编译入口health/info200、受控factory run200、事件完成、未支持route404，不调用外网。
- [ ] Step 5: 记录RED/GREEN、版本/lock摘要、未部署及残留，精确提交本任务文件；`task-done`以完整npm test记录完成，再做一次整批fresh review，保留验收工作目录。
