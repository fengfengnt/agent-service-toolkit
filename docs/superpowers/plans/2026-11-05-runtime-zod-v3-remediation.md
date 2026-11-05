# Runtime 残留依赖风险实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保持功能与API，修复实际Zod v3有界集合解析路径，并移除许可包内嵌的旧v4解析实现。

**Architecture:** 使用版本与原/后SHA256固定的安装补丁，先预检全部目标再修改，支持幂等和只读检查；v3只在已有上限超限时早返回原INVALID。许可包仅外置Zod region至已有4.6.7，保留全部业务、crypto、信任锚与导出；无法通过兼容门就不采用该部分。

**Tech Stack:** Node24.21.0、Zod3.25.76/4.6.7、CopilotKit1.84.1、license-verifier0.5.0、现有npm/Docker构建链。

**Spec:** `docs/xiaohe/security/2026-11-05-zod-v3-upstream-research.md`，用户门户1793859526直接指令及后端协调：剩余风险持续处理、功能不变、正式false、不替换部署/短信/付费。

## Global Constraints

- `dev-fengfeng`，基线3563f90，保护并行修改及现有配置/数据/会话。
- 无KaTeX的Runtime lock不加无关依赖；不改变Zod版本、不全局替换v3为v4、不放宽schema或skipLibCheck。
- 未设上限的map/record等不伪造阈值，不声称官方v3修复或audit全绿；网络audit有界失败单列。
- 不改现有服务/SSO/Nginx/凭据，不真实G2/短信/模型。仅新不可变测试tag与独立临时容器。
- 原42回归继续，必要修改仅更新已被新补丁修复的旧残留断言。

## Review Focus

- 12个实际JS目标（5包各2格式加许可2格式）版本/摘要、批量预检、漂移拒绝和幂等，不得部分未知替换。
- 数组max/exact、Set max/size的同步/异步超限早拒绝，正常/短输入、自定义错误、嵌套path、effects/catch仍兼容。
- 许可crypto、密钥和全部业务region/导出保持；ESM/CJS安全schema、合法签名/篡改/未知key/过期/entitlement均离线验证。
- npm正常安装与Docker ignore-scripts后的显式补丁都覆盖；只读test/build门禁不能误把未打补丁制品放行。
- Linux新镜像实际入口采用本地补丁，旧容器/镜像不变，版本告警与实际修补边界分开报告。

### Task 1: 精确安装补丁与安全回归

**Files:** 新增`copilot-runtime/scripts/apply-security-patches.mjs`、摘要清单、`tests/dependency-security.test.ts`、许可合成测试及安装守卫测试；修改package scripts、Dockerfile/.dockerignore、旧v3行为断言；新建公开修补说明。

**Interfaces:** 消费锁定原发布物；产出`npm run patch:security`、默认postinstall及`--check`只读模式，保留版本和依赖锁。每次变更前验证全批。

- [ ] 写实际5个Zod包ESM/CJS、数组/Set有界样本及许可版本4.6.7的RED；先用`node tests/run.mjs dependency-security`运行，Expected:旧3.25.76的元素仍被访问，许可仍4.3.6，不是只因缺文件失败。
- [ ] 写补丁安装/幂等/版本与内容漂移/全批预检测试及许可功能离线正负测试；不得改产品信任锚，合成签名只在临时测试副本中使用临时公钥，私钥仅内存。
- [ ] 实现最小回移和许可region外置，固定before/after SHA；接入正常npm与Docker显式链，更新旧残留断言为新提前拒绝行为。
- [ ] `npm test`、`npm run typecheck`、`npm run build`、编译入口定向验证。Expected:原42+新增全通过，严格编译exit0，正常许可/拒绝与原有HTTP行为不变。
- [ ] 精确提交本任务并task-done，不推送或部署。

### Task 2: 独立Linux门与验收

**Files:** `docs/xiaohe/operations/2026-11-05-runtime-dependency-security.md`及脱敏JSON。

**Interfaces:** 消费Task1固定提交，按原隔离约定构建新tag，验证实际安装补丁与入口；不使用旧tag或替换现有容器。

- [ ] 固定来源与新镜像身份、断网/无秘密、nonroot临时容器运行新安全门和health/info。Expected:与Windows结果一致，现场旧容器保留，临时容器仅清本方创建项。
- [ ] 保存各原/后摘要、版本树、audit网络失败与未设限范围、历史G2复用及旧运行镜像差异；一次整批fresh-context审查，按执行技能处理真实阻塞，不重复上一批review。
