# 三角色留资 Skill 验证与交接记录

日期：2026-11-11。依据[定稿设计](../superpowers/specs/2026-11-11-role-lead-skills-design.md)及[实施计划](../superpowers/plans/2026-11-11-role-lead-skills.md)。本次仅开发与隔离验证，未更新任何部署、正式开关或真实业务资料。

## 实现与版本

- `14b5618`：三角色资源、严格加载、原字段校验对照及旧 bundle hash 兼容。
- `9463855`：策略快照、草稿／Command 引用、可区分确认证据及 `0013` 扩展迁移。
- `f058bf0`：草稿字段固定、新 Command 交互固定、模型上下文与恢复绑定。
- `02c0b18`：单独归档本次依赖的既有三角色转化修复；未带入 VERSION 或域名／部署改动。
- `db8a9f8`：口语直接提交、同轮修改、真实确认证据与原键重试。
- `851b2ec`：统一关键提示、会员准备资料、常见伪造业务状态答复拦截。
- `5edcb4c`：显式 opt-in 的 `leadPolicy` 投影、独立表单一致性及固定旧格式校验快照。
- `0fa7a1659e335b8d45cc2f19908e2ddff78731b4`：最终实现与验证基准；补齐新建／恢复面板标记、状态声明拦截、可信模型上下文、匿名认证分流及组合验证。

上述提交位于 `dev-fengfeng`，未推送。后续集成修补和最终测试状态见本文件后续更新，不把阶段 SHA 当作已部署版本。

## 自动化证据

| 检查 | 已观察结果／边界 |
| --- | --- |
| 原顶层业务基线 | 412 passed；不包含需要单独运行环境的 integration 目录 |
| Task 1 加载与编译 | 47 passed；后续增加存储归属错误配置拒绝用例 |
| Task 2 存储与迁移 | 5 passed；旧提交／草稿资料及键保留，新绑定／证据存在时拒绝破坏性降级 |
| Task 3 版本与恢复 | 19 passed；另加未适配旧草稿原格式限制 RED→GREEN |
| Task 4 语义提交与原提交回归 | 42 passed，含三角色、短口语、同轮修改、缺项、过期修订、伪造证据和响应丢失 |
| Task 5 转化与通知旧流程 | 71 passed；新增登录不提交后通知测试文件 14 passed |
| Task 6 投影、字段版本、契约、独立表单 | 95 passed；Task 6 完成命令 85 passed |
| Task 8 实际 admission→worker→graph 组合 | 三角色直接提交、新建／恢复面板标记、同一等待 Run 的下一轮交互换版、原键受理核对通过；附件增删冲突及中断同一 Command 后跨规范升级恢复也通过 |
| 最终完整顶层业务回归 | **918 passed、5 skipped、10 deselected**，19 项已有依赖警告，耗时 736.98 秒；本功能没有剩余失败 |
| 独立审阅 | 0 Critical、2 Important、0 Minor；两项重要问题已按 RED→GREEN 修复，另补审阅建议覆盖，最终完整回归通过 |
| 门户回执全量验证 | 核对门户归档日志：624 单测、36 安全检查、typecheck、生产构建通过；30 项留资浏览器回归与 18 项协议桩 SSO 组合通过，审阅修复后相关 5 项 SSO 复跑通过（不作为额外独立用例累加） |
| 本对话核收时复验 | 固定前端 SHA 下复跑 5 个相关单测文件，61 passed；前端工作区仍干净，前后端规则 fixture SHA256 一致 |

首轮完整回归为 13 failed、887 passed、5 skipped：旧字段配置入口、旧话术、schema head、旧 Run 新轮次预期按已批准设计适配；另四项历史 443 SQL 迁移因专用库名称不在脚本白名单而拒绝。未放宽 SQL 保护，改在本轮同一临时容器的白名单测试库复验。

测试模型与业务客户端均为替身；调用链、数据库、事务和恢复使用实际实现。上述结果不证明真实 LLM 对自然语言的准确率。状态声明拦截覆盖已测试的常见中英文、被动句和倒装句，同时保留条件、引用和否定答疑；有限句式防护不能替代真实模型评测。

## 隔离测试执行

本轮创建专用、无业务卷的临时 PostgreSQL 容器 `codex-xiaohe-lead-test-20261111`，仅回环端口。阶段验证使用 `test_xiaohe_lead_skills`，最终复验在同一容器创建 `test_xiaohe_agent` 以满足历史脚本的精确白名单；没有连接以前的同名库，没有复用业务 PG、知识库 Agent 的测试容器、MySQL 或 MinIO。结束时只清理本轮容器。

后续运行需自行提供专用可清空库的 `XIAOHE_TEST_DATABASE_URL`；现有 `pg_factory` 会迁移并清空测试表，禁止使用业务库／原真人验收库。命令从仓库根目录执行：

```powershell
.venv/Scripts/python.exe -m pytest tests/xiaohe --ignore=tests/xiaohe/integration --ignore=tests/xiaohe/test_conversion_signals.py -m 'not minio' -q
```

原 `tests/xiaohe` 全目录基线已有 `integration/test_deploy_proxy.py` 对同名 `test_deploy_contract` 的导入冲突；integration 中还有需专用 HTTP／MySQL／MinIO 的三方验收。本轮不使用现有三方配置绕过这些边界，亦不启用真实模型评测开关。

5 项跳过均来自原有 `test_uat_domain_sql.py`：它要求另行显式提供带指定标签和名称的域名迁移专用容器，本轮未提供。10 项为按 `not minio` 排除的既有用例。真实模型评测文件和 integration 目录没有执行，不能据此宣称三方环境或整个上游仓库全部验收通过。

## 迁移与回退限制

`0013` 添加不可变策略记录、nullable 绑定及新确认证据，旧数据不伪造来源，不重建 payload 或提交键。旧流程格式校验由固定 `legacy-lead-fields-v1.json` 保留，缺失或摘要不符则明确失败，不能改用当前默认值。

未产生新绑定／证据前可在隔离环境降级／再升级；产生后迁移明确拒绝删除新语义，旧二进制也可能无法理解 nullable summary。应保留兼容读取能力的应用版本处理已有数据，不能为回退伪造“已展示汇总”。正式备份、排空、迁移和发布另行安排，本轮未执行。

## 门户交接与未验项

门户交接：`C:/Users/70367/AppData/Local/Temp/xiaohe-role-lead-skills-portal-handoff-20261111.md`；精确协议见 [leadPolicy v1](../contracts/xiaohe-lead-policy-v1.md)。没有直接修改门户工程，也没有新增 Java 或 Runtime 开发依赖。

2026-11-11 已收到并核收[门户原始回执](receipts/2026-11-11-role-lead-skills-portal.md)。固定组合为后端 `e19cac7ad510c8f45b2aba0e4754bdfe5943b967`（代码基准 `0fa7a1659e335b8d45cc2f19908e2ddff78731b4`）与门户 `9cdadb3dd74b97e073765938b05bd5ab7074b0f8`，均在各自 `dev-fengfeng` 本地提交，未部署。门户版本仍为 `0.1.27`。核收期间仓库另有标准 Skill／DeepAgents 的设计及依赖准备提交；本次不评估或覆盖这些工作，也不把当前 HEAD 等同于上述已验固定组合。

核对内容：只在两个 GET 声明能力，写入／确认请求保持原结构；旧后端缺投影时兼容，显式 null 不继承旧规则；状态依据可信结果；采集标记等待匹配草稿和输入请求、按 owner／会话／消息去重；保留未保存输入及焦点。门户沿用前置提交 `f543a0f` 已有的单一“提交信息”入口，没有恢复额外按钮或给语义提交增加用户点击；资料总览仍可核对，确认绑定真实修订。

前后端 `lead-policy-v1.json` 逐字节一致，文件 SHA256 为 `63363e25a49a05b9ca071801e45e8eb75ceaf10d8ab9e19b98b31276eda6788f`。回执日志位置：`C:/Users/70367/AppData/Local/Temp/xiaohe-role-lead-policy-portal-20261111-evidence/`。本对话读取全量验证日志并执行以下定向复验，未重跑门户全量浏览器或构建，也未跨工程修改代码：

```powershell
# 在 D:/Code/yunhezhiyuan-portal/frontend 执行
.\node_modules\.bin\vitest.cmd run tests/unit/reception-lead-policy.test.ts tests/unit/reception-lead-status.test.ts tests/unit/reception-collection-notice.test.ts tests/unit/application-form.test.ts tests/unit/reception-presentation.test.ts --maxWorkers=4
```

| 组合 | 当前证据 | 尚未覆盖 |
| --- | --- | --- |
| 旧后端／新门户 | 门户旧形状协议桩、解析及原操作回归 | 实际旧后端与新门户共同运行 |
| 新后端／新门户 | 同一三角色 fixture、字段／通知／状态及隔离浏览器协议桩 | 实际新 API／Runtime／数据库与新门户共同运行 |
| 新后端／旧门户 | 后端 opt-in 默认旧响应形状及原客户端契约回归 | 实际旧门户运行组合 |

据此 Task 7 开发交付及 Task 8 的证据汇总已完成，不能将协议桩兼容矩阵写为真实环境组合验收通过。后续未验项如下：

- 新旧前后端实际运行组合；真实环境中的事件／读取乱序、账号切换和窗口恢复。已有协议桩证据可复用，不冒充真人证据。
- 真实窗口中刷新／重开、附件原文件下载、真实业务受理及进展未在本轮重放。
- 真实模型短口语意图质量、多语言自然表达尚未评测；确定性桩测试不能替代。
- 未保存表单仍仅保留窗口内存；接待状态刷新／重连不覆盖当前输入，整页 F5 销毁后的未保存输入不承诺恢复。本轮没有新增个人资料持久化。
- 保持现有部署与正式开关；未发送短信、创建真实账号、重放认证／归并或调用付费模型。

门户原临时目录按回执保留；其自动审批拒绝删除属于来源记录，本对话未尝试删除或扩大清理范围。

## 实施调整与代价

1. 复用 `dev-fengfeng` checkout，未新增工作树；保留原改动并保存本地基线，需区分既有配置与本次代码。
2. 门户由指定 Agent 实现；本对话仅交接与后端独立验证，组合 UI 结果继续待回执。
3. 不修复无关的 integration 导入问题，不动其专用环境；完整顶层业务回归不能代替三方验收。
4. 在 admission 原子固定新 Command 的策略与 `leadWorkflowBundleId`，恢复不猜测是否升级；选版错误会影响旧 Run 的新轮次，已由真实调用链及中断恢复覆盖。
5. 旧分组外的正式资料保留完整校验定义，分组控制可编辑范围；否则会拒绝旧申请补充或丢资料，相关回归通过。
6. 前置修复 `02c0b18` 单独纳入历史；它是本次依赖的已部署转化修复，避免新提交引用尚未入库的逻辑。VERSION、域名、部署文件未带入。
7. 旧格式限制固化为受摘要校验的资源；缺失／篡改则失败，不能偷偷换成新版门槛。
8. 旧测试按新配置归属、统一话术、schema head 和新轮次选版要求适配；保留业务断言强度，不改变正式门槛。
9. UI、真实 LLM、生产／真实 Java／SMS 不在本轮通过项；这些待验项未被豁免。
10. 历史 SQL 复验只在本轮拥有的临时容器使用白名单库名；不修改保护条件，也不连接现有环境。
