# 小鹤真人会员隔离补验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute this approved isolated validation inline. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 复用现有 V2/SSO/文件能力，在新隔离资源中补真人会员恢复、历史分页和实际文件归并证据，保留旧受控验收与所有现有服务/数据。

**Architecture:** Windows 原生独立 API/worker/Runtime/TLS gateway；本轮独占 PG 新数据库和新的私有 MinIO。逻辑原登记 Origin/callback 不变，只在独占 Chrome context 和新 Runtime 的既有 fetch 注入点定向 AI 请求。真实 Bridge 读取既有私有配置，模型仍是 JourneyModel，不改产品接口/SDK。

**Tech Stack:** 当前锁定的 Python/FastAPI/SQLAlchemy/LangGraph、Node/CopilotKit、PostgreSQL、MinIO、严格 TLS；不新增依赖或更换上游版本。

**Spec:** [真人会员隔离补验设计](../specs/2026-11-07-xiaohe-real-member-isolation.md)。旧受控 checkpoint `03556c050380f01406596dc0ded6e64a95e9e4f0` 不回写。

## Global Constraints

- `dev-fengfeng`、保留 `.codegraph/`、其他工作树、原 443/18090/18110/18111/18593、所有既有数据库/卷、匿名 profile/冻结包/旧 Runtime 源树。
- 新拟端口 18093/18095/18595/35549，冲突时另选空闲端口并记录，不替换已有 listener。资源门重新核验，不重启 WSL/共享服务。
- 新数据库 `test_xiaohe_reception_real_member_20261107`；任何 pytest TRUNCATE fixture 均不能指向该预览库或旧真人库。
- 新真实 Bridge 不能用 MockTransport/seed 身份；不改注册、不轮换 secret。程序私有注入只选 Bridge 必需字段，绝不打印秘密。
- 不自动短信/登录/注册，不付费模型，不向真实业务后端写申请；用户登录/短信交门户给具体操作步骤。
- 同源/TLS 校验、cookie/CSRF、expectedIdentity、绝对 8h、归属和额度仍走现有产品边界，不为测试跳过。
- 本轮只新建隔离 helper/资源和证据；如实际发现产品 bug，再按最小 RED→GREEN 修复，不能以 seed 或 mock 掩盖。

## Review Focus

1. 浏览器路由并不能拦 Runtime 后台请求：新 transport 必须没有原 443 AI API fallback。
2. 私有注入误带旧 DB/storage/business URL：白名单字段及精确新资源断言先于任何启动。
3. 真人身份、假模型和实际对象层次混淆：以真实 Bridge 默认传输/可信完成与下载字节分别取证。
4. 材料没有数字 version：按真实 DTO 不变 file ID/SHA 核验，不能伪造文件版本或把 revision 当版本。
5. 原登记回调和 IP/UA 上下文：仅精确 context 路由，不能劫持中心/BFF、改全局代理或输出来路 Cookie。

### Task 1: 非敏感参数核对与隔离 helper

**Files:** 新计划专属忽略目录中的私有配置/launcher、最小 transport 模块及 self-check；现有产品源码和旧配置只读。

- [x] 从 SSO Owner 核对现有 issuer/client/callback、System/tenant、peer/IP/UA 和私有注入路径/字段名；不接收或打印 secret。
- [x] 为 logical443→新 TLS gateway 的精确四端点映射写允许/拒绝测试，观察未实现时失败；实现最小注入，确认 Cookie/Origin/CSRF/取消信号和 TLS 保留，拒绝异源/越路径/重定向，无旧 API fallback。
- [x] 私有 loader 只选 Bridge 必需字段，以 dummy 私有数据检查不会继承旧 DB/bucket/business URL、不输出秘密；真实值仅运行时加载。
- [x] 把缺项、技术裁定和安全边界写本计划 ledger；参数未确认前不建立中心认证事务或真实身份。

### Task 2: 新资源与服务准备

**Files:** 同一忽略目录的 provisioning/config/进程记录/健康证据；使用既有迁移、harness、图和 worker。

- [x] 核当前内存/端口/镜像/所有权；创建新 DB、System 元数据和新限资源 MinIO/私有 bucket，不 seed 身份/聊天/文件。
- [x] 新 API 注入真实 SsoBridgeClient 默认传输、受控 JourneyModel；新 worker 同一私有配置，真实模型网络被拒。
- [x] 新 Runtime 固定 V2/逻辑443配置，注入 Task 1 transport；新 TLS gateway 只代理新内部进程。
- [x] 分层探测 TLS、V2 echo/cache、PG、私有 bucket、worker 受控回合与外呼门；Bridge 配置/TLS 与实际凭据/真人验证分开。不额外 Create/Cancel 预认证事务；SSO Owner 已确认其写事务及预算副作用，凭据认证留正常真人流程。
- [x] 回传新端口和真实 readiness 层级，旧匿名资源/数据不变；服务准备完成后再交门户启新 context。

### Task 3: 真人与文件/历史闭环

**Files:** 本计划新公开脱敏证据、私有诊断；门户 own member-dist/profile/驱动由门户 Owner 管理。

- [x] 门户提供固定 true 测试包/源身份和严格 context 路由；普通 false 包/共享反代不动。
- [x] 用户按门户说明认证既有会员；Root 只配合新服务，不自动短信/建号，不读取验证码或公开真实身份。
- [x] 原生 UI/API 产生访客历史与实际测试文件，登录前下载 200+SHA；真实 claim 后保持消息 ID/createdAt 和文件身份/摘要/字节。
- [x] 按已核实人类原始回复完成小鹤弹窗关开、1～20完整有序无重复及普通刷新保留会员/历史/原文件的人工验收；原文件会员浏览器 GET 已通过。自动首屏/分页、真 browser context 同 profile 关闭重开、刷新后再次 HTTP 下载保留未验，不补造结果。
- [x] Root 独立只读校验新域绑定/所有权/字节相关元数据，与门户用户界面证据区分；若需用户操作只交具体已准备步骤。

### Task 4: 新阶段收口

**Files:** 新增 `docs/xiaohe/operations/2026-11-07-real-member-v2-verification.md` 和对应新 JSON；旧受控文档不回写真人成功。

- [x] 记录真实身份/对象/API/浏览器/模型/部署分别通过或未验；保留 secret/profile 私有，公开只留必要身份摘要。
- [x] 核精确 source/build/contract/日志指纹、原资源保留、新资源去留及后续人工项；不重跑旧通过 suite。
- [ ] 普通提交/推送 `origin/dev-fengfeng`、核 `git ls-remote`，向门户/SSO/协调 Owner 回固定新 checkpoint。不合并、不替换正式部署。
