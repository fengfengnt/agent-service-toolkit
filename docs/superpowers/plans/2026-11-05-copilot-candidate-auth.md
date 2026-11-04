# Copilot 候选会员认证准备实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 用户已选择各工程原生持续执行，不逐阶段审批。

**Goal:** 只给新候选环境准备可验证的既有 SSO 可信适配，供门户 P3 后续隔离会员验收，不执行真实登录或短信。

**Architecture:** 保留原产品认证机制。候选 JSON 显式 opt-in 后，启动器从固定相邻 `sso-bridge.json` 只读秘密挂载读取既有应用配置；精确校验现行 issuer/application/callback，使用容器内公共 CA。候选库仅更新 portal System 的公开 client 映射。Runtime 和原部署不变。

**Tech Stack:** Python/FastAPI、既有 SsoBridgeClient、PostgreSQL、WSL Docker、HTTPS443；受控模型保持。

**Spec:** 既有门户迁移 P3 计划及 SSO Owner 的 `C:/Users/70367/.codex/worktrees/sso-backend/xiaocai-admin-server/docs/sso/copilot-candidate-sso-handoff-2026-11-05.md`。主合同仍为 `D:/Code/yunhezhiyuan-agents/docs/contracts/2026-11-03-xiaohe-integration-v1.md`，本次不新增业务协议。

## Global Constraints

- 在 `dev-fengfeng` 原生实施；不修改上游认证、安全策略或共享 Nginx。
- 只操作 `xiaohe_copilot_candidate_20261105` / `xiaohe-copilot-candidate-20261105` 和候选 API/worker；不修改旧服务/库/身份键/SSO登记。
- 既有私有来源由 Owner 同机只读挂载，Agent 不读取/打印密钥值、不跨聊天传递，不生成/轮换应用凭据，不复制到仓库/归档。
- 原部署正式开关保持原样；仅候选可 opt-in。保持受控模型与短信拦截；真实登录、验证码和会员归并未验收。
- issuer=`https://sso.lvh.me`，client=`app_localportal`，applicationId=`31555063-bbe3-4907-8745-c553208fff23`，callback=`https://portal.lvh.me/portal/auth/callback`。
- portal tenant=1、会员来源=1、OAuth管理owner=166分层；只改候选 client 映射，不把owner166写入业务租户。

## Review Focus

- 未 opt-in 时秘密文件存在也不能自动启用；拒绝 inline 覆盖、缺失来源及不完整配置。
- 错 issuer/app/callback、不可见/无效 CA 必须启动失败，不关闭 TLS 或放宽可信 peer。
- 私有来源必须模式0600；只读挂载原文件，不在 Agent 输出或公共测试结果出现密钥。
- 短期候选配置与登记更新不能波及旧数据库/容器，保留旧候选镜像与配置可回退。
- 无短信探针只能创建/读取/取消自己的隔离认证流程，不能调用 sms/verify/claim 完成或真实会员登录。

### Task 1: A5 — 候选 SSO opt-in 与严格启动校验

**Files:** `tests/xiaohe/integration/copilot_candidate.py`、`tests/xiaohe/test_copilot_candidate.py`。

**Interfaces:** `load_settings(path)` 保持原签名及所有资源限制。基础 JSON 仅控制 `sso_bridge_enabled`，不接收其余 Bridge 字段；启用时读取相邻固定 `sso-bridge.json`，仅接受现行五字段，对相邻 `public-ca.pem` 建立严格 SSL context 后覆盖旧来源 CA 路径。禁用时不读取秘密来源。

- [x] 写允许合法 opt-in 的失败测试，并补 metadata 错误、缺配置/CA、inline 覆盖、默认关闭不读取来源的边界。
- [x] 运行并观察 RED，最小修改启动器，保留 DB/桶/代理限制及短信拦截。
- [x] 运行 `uv run --frozen --python 3.14.7 pytest tests/xiaohe/test_copilot_candidate.py tests/xiaohe/test_triad_runtime.py -q --tb=short` 和 Ruff，通过后精确提交。

### Task 2: A6 — 私有只读挂载、登记与无短信适配验证

**Files:** 新公共结果文档 `docs/xiaohe/operations/2026-11-05-copilot-candidate-auth.md`；探针只放本计划忽略 workspace。

**Interfaces:** Owner 内部确认现有挂载源的文件模式及公开字段白名单（不输出密钥），给候选 API/worker增加原 `sso-bridge.json` 和公共 CA 只读挂载；只修改候选私有 JSON 的 opt-in 与候选 portal client 行。构建不可变 `spike-<commit>` 候选 Python 镜像，仅 recreate 这两个服务。

- [x] 先静态核对来源元数据/路径，不读取密钥到 Agent；来源缺失则准确记录，不生成替代凭据。
- [x] 按 A5 的准确配置准备新候选镜像/只读挂载、保存当前候选配置与旧镜像身份；以候选 DB guards 更新 portal 的 client/issuer/origin，tenant 不变。
- [x] 从候选进程用严格 TLS、合法现有应用认证做无短信受控流程探针，实测套接字出口 peer，证明不是猜测 host 网络。验证旧 Origin 拒绝、sms 路由仍503；取消只属于本探针的 operation。
- [x] 核对旧容器 ID/正式部署配置未变，候选受控消息仍工作；写入公开版本/路径/peer证据和回调、真实会员未验范围，交门户继续。
- [x] 通过后提交公开文档，做一次本批只读最终审阅；保留证据及分支，不合并/推送。
