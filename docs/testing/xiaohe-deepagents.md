# 小鹤标准 Skill 与 DeepAgents 验证记录

状态：后端实现及隔离验收通过，独立审阅问题已修复；门户隔离验证回执已接收；真实新后端组合待验；未部署。最终测试结果见本文末尾。

## 实现与入口

依赖固定为 DeepAgents 0.7.32、LangChain 1.4.7、langchain-core 1.7.0、LangGraph 1.2.16；保留 14 天冷却和 uv.lock。默认 `XIAOHE_DEEPAGENTS_ENABLED=false`。本次没有修改 VERSION、部署脚本或任何现有环境数据库。

外层客服图与 await_input 保留。业务图固定 `prepare_context → business_agent → finalize`，内部主 Agent 使用原生 DeepAgents。主 Agent 的七个受控工具复用正式业务校验与提交服务。child 显式绑定、只读，首版只允许 read_lead，只返回结果，不能继承文件写入、Shell、业务写工具、父业务状态或再次委派。

二十一份标准包位于 `resources/xiaohe/skills/<role-prefixed-name>/SKILL.md`，绑定在 `resources/xiaohe/deep-runtime.yaml`；三份留资包与十八份答疑包已纳入。旧嵌套 skill、旧 lead-skills、legacy／dsl-v1 编译器保留用于旧数据恢复。字段与正式后端接口没有扩大。

## 后续导入与更新

1. 将标准包放入资源目录，保留 name／description frontmatter，正文引用包内 UTF-8 资源。加载器检查名称、引用、原字节 hash、路径／符号链接越界及必要工具依赖。
2. 在 manifest 登记包及方向、认证限制、required tools，并在指定业务节点绑定 skills；一个节点可绑定多份 Skill。活动留资流程每个角色只能有一份，确定性加载；其他正文由原生技能发现按需读取。
3. 不从 metadata 导入任意 Python 模块或执行 scripts。外部依赖先审核、显式绑定可用工具；无法支持的必要能力拒绝加载。
4. 更新分组／说明时可复用现有字段。字段规则、API 或界面变更仍需对应后端／门户修改。不要把 Markdown 当成授权、字段门槛或业务回执。
5. 启用新选版时在启动阶段发布不可变快照，新 Command 选新版本；已执行 Command 和出站请求不换版。首版通过正常服务启动发布，没有热重载 API。旧草稿固定字段规则，下一轮引导读取已有资料与可核对的完成记录。

配置开启前必须安装锁定依赖、迁移到 0014，并验证已登记旧快照完整。不要在缺快照时替换为最新包。

## 隔离验证边界

使用本计划专有 PostgreSQL 17 临时容器与 `test_xiaohe_` 前缀库，不连接原有业务库、MinIO bucket、真实模型、短信或正式业务后台。pg fixture 会清空测试表，不能传入任何现有环境 DSN。凭据只保存在 git 忽略的临时工作区，不写入文档或提交。

确定性测试涵盖：

- 原生 Skill 文件读取与工具循环、二十一包一致性、未授权包无法列出／读取。
- 主／child 工具及状态边界，真实 task 委派后严格结果投影。
- 三角色完整采集与答疑、同轮修改提交、口语语境确认；登录控制拒绝作为提交同意。
- 同一副作用换 tool_call_id 复用原回执，同轮不同保存分别递增；未知结果沿原键核对。
- 真实进度、唯一／多草稿、原申请修改；旧按钮确定性处理和实际汇总校验。
- 独立 checkpoint 根 namespace、恢复不重复输入、旧 attempt／取消 fence、竞争 driver 排他。
- 真实子进程在后台已记录后被强杀；恢复只有一份正式申请、一个原提交键。
- 新包发布／关闭后在途快照不变；旧引擎等待恢复、迁移兼容与拒绝不安全 downgrade。
- 自由四组 form、修订／formId 冲突、保留组外字段、说明必须关联已落库消息。
- 两个 GET 的 v1／v2／未知值／重复头，独立表单读取无聊天副作用、旧 DTO／正式载荷不变。
- 真实内部执行流进入 worker 后，内部文本／Skill 内容不成为外部助手消息。

## 可复现命令

先由测试负责人显式设置专用 `XIAOHE_TEST_DATABASE_URL`，工作目录为 toolkit：

```powershell
uv lock --check
.venv/Scripts/python.exe -m pytest tests/xiaohe --ignore=tests/xiaohe/integration -m 'not minio' -q
.venv/Scripts/python.exe -m pytest tests/xiaohe/integration/test_deep_runtime_crashes.py -q
.venv/Scripts/python.exe -m pytest tests/agents tests/service/test_service_streaming.py tests/service/test_service_message_generator.py tests/service/test_agui.py -q
```

这不是整个仓库测试；MinIO 及其他 integration 不包含在首个命令中。迁移测试仅在同一可清空专用库运行，覆盖 upgrade、旧类型合法、native 快照和拒绝含新状态时 downgrade。

## 回退与未验项

关闭配置只影响后续选版。已绑定 native 的 Command 恢复原快照；native Run 没有旧 DSL 快照，继续使用其固定 native 内容完成排空；旧 dsl-v1 Run 后续 Command 可以恢复旧选版。保留 0014、包快照、旧执行器与现有数据，不能直接降库或运行不能识别 native Command 的旧服务程序。

门户交接见 [交接文档](../handoffs/2026-11-11-standard-skills-portal.md)，协议见 [v2 契约](../contracts/xiaohe-lead-policy-v2.md)。已获得并核对[门户固定提交回执](../handoffs/2026-11-11-standard-skills-portal-receipt.md)：前端隔离协议桩及浏览器覆盖脏输入保护、焦点、角色／认证恢复等场景；真实新后端组合仍未执行，不能据此标记组合通过。

隔离测试阶段未验证真实模型语义选择、真实登录／短信、正式业务后台、MinIO 和环境发布。后续 WSL 发布见文末；隔离模型脚本结果不代替真实业务验收。

## 本轮执行结果

- 首轮完整非 MinIO 集合：995 passed、15 skipped、10 deselected、5 failed。一个失败是旧测试写死 0013（已更新为 0014）；四个来自一次性部署迁移脚本的精确测试库名白名单。未放松或修改这些生产脚本。
- 同一专有容器中新建 test_xiaohe_agent 隔离库后，历史迁移专项 6 passed，portal origin 迁移随最终专项通过。
- 最终业务／v2／强杀／origin 专项 26 passed；依赖升级相关 Agent／流式接口 38 passed；uv lock --check 和改动范围 ruff 通过。
- 新库完整非 MinIO 回归：1005 passed、15 skipped、10 deselected，耗时 621.68 秒；这是审阅前结果，修复后结果见下一节。


## 最终独立审阅与修复

审阅基点 67c16a8，独立 reviewer 为 gpt-6-astra。无 Critical／Minor，5 项 Important 均进入同一轮 TDD 修复：近期可见对话及待答问题、写入后坏候选的真实回执收尾、关闭新选版时不加载损坏新包、child 移除可补发提交的 query_progress、接待事实与保守边界纳入不可变快照。接待显式登录意图继续产生服务端认证卡片。

11 个新回归先全部 RED，修复后相关集合 41 passed／1 skipped；等待中 Run 的坏快照校验也经 RED→GREEN；另补意外内层 interrupt 不得被写入回执掩盖的边界检查，专用回归文件共 13 passed。接待真实模型的语义效果仍待单独授权验证。第一版 child 不能查询业务进度，主 Agent 保留查询和核对；以后如需委派，先提供不会补发或更新正式业务的读取适配。

修复后完整非 MinIO 回归：**1018 passed、15 skipped、10 deselected**，耗时 596.21 秒；真实子进程强杀恢复再次验证 **1 passed**；上游 Agent／流式接口 **38 passed**。uv lock、ruff、格式及契约／交接／测试文档链接检查通过。

本轮按一次独立审阅＋TDD 修复流程，以失败复现及最终完整绿灯验证修复，未另发第二次审阅。无延后 Minor。门户和后端已分别通过隔离验证，仍不表示真实新后端组合／真实模型／正式业务／MinIO 或部署验收完成。


## 门户回执接收

已只读核对门户固定 SHA `485a0e3a4ae23fddd2bede4d68220aaa4038014b`、干净工作区、两端 fixture 字节一致及维护者归档日志。回执记录 637 项单测、36 项安全检查、38 项留资浏览器和 20 项 SSO 浏览器通过，typecheck／build 通过；本聊天读取了对应日志和报告文件存在性，未重新执行门户测试。

两端 fixture 的 SHA256 均为 `9bc7c5f113d888b9015c04e3e4bafd04423a61cc92d3f75c59cef10e3988c69c`。Task 8 的实现与隔离验证回执闭环；后续缺口为新门户连接实际运行的新后端、沿真实 API 完成受控组合验收。默认开关、现有环境数据库及部署状态未因此改变。

## 2026-11-12 WSL 发布

用户随后授权提交并部署 WSL：AI API／Worker 0.1.7 已启用 `XIAOHE_DEEPAGENTS_ENABLED=true`，实际数据库迁至 0014；源代码默认值仍为 false。门户维护者已发布固定 SHA 的 Portal 0.1.27，本聊天复核 233 个 HTTPS 文件与归档一致。实际镜像探针、配置模型组装、API 健康、Worker 心跳、native bundle 及数据／配置保留检查通过，见[发布记录](../deployment/2026-11-12-standard-skills-wsl.md)。

这补齐本地环境发布，不表示真实模型、真人登录／短信、认证后表单全流程或正式业务写入已验收；未部署 UAT／生产。
