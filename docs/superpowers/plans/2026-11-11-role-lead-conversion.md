# 三方向转化后答疑与留资修复实施计划

> **For agentic workers:** 使用 superpowers:executing-plans，按任务逐项执行并验证。

**Goal:** 三个业务角色识别转化后先答疑，再为会员创建或复用本角色草稿；访客进入认证，会员不再被要求登录。

**Architecture:** 复用 SemanticDecision.accept_invitation 和现有 request_auth，不增加 API 字段。三个 YAML 将自然语言转化路径改为 answer → request_auth；显式按钮控制保留无需模型答疑的行为。采购已有三个建议入口按精确输入处理，其他自然语言继续由角色提示词识别。旧流程同步处理同一业务意图；冻结的旧 DSL auth 路径也在写草稿前复用答疑回执。门户首次收到会员 activeDraft 会自动打开填写面板，但关闭后复用同一草稿不会重开；后端以既有 system_notice 类型通知“开始填写需求资料”，门户补充观察逻辑另行交接，不直接修改门户工程。

**Tech Stack:** Python、Pydantic、LangGraph、PyYAML、PostgreSQL、pytest。

## 约束

- 在 dev-fengfeng 完成，不覆盖已有 VERSION、提示词和其他未提交文件；不自行提交、推送或部署。
- 只创建草稿，不正式提交、预约、重放认证或合并身份。否定、暂缓、条件和纯概念咨询不自动开始采集。
- 已有草稿复用，保留资料；使用本角色 YAML 的资料清单，不改变最低提交门槛。
- PostgreSQL 回归使用独立临时库和虚构身份；不连接运行库、不调用真实模型、不发短信。

## 任务与验收

- [x] 增加失败回归：三个角色会员、采购三个入口、访客、既有草稿、否定与纯咨询、旧流程组织入口。
- [x] 修复共享执行逻辑和三个角色 YAML／提示词，保持先答疑后创建草稿的顺序；答疑失败不能创建草稿。
- [x] 运行定向 PostgreSQL 与离线回归，检查真实返回的 activeDraft、fieldLayout、邀请状态和认证 part；复核门户已有自动打开条件。
- [x] 记录验证结果和部署边界，提供门户交接文档并暂停跨工程实施。

## 验证与交接记录

- 在独立临时 PostgreSQL 库 `test_xiaohe_conversion_20261111` 执行定向回归，不连接运行库；业务模型均为结构化模拟，无真实短信、模型或业务提交。
- 7 个相关测试文件的 163 项回归通过；另对旧 review 草稿重开通知和访客已有草稿保护补充的 6 项定向验证通过。临时测试容器已移除，未使用持久卷。自然语言提示词本身未进行真实模型质量验收。
- Ruff check、测试文件 format check、git diff --check 通过。仅在 dev-fengfeng 修改本项目文件，保留原 VERSION=0.1.6 等已有工作，不提交、推送、构建或部署。
- 一次独立只读审阅发现同方向未激活草稿重复创建、复用草稿不能保证前端重开；后端已补唯一复用／多份选择，并仅为会员 collecting/review 草稿发出采集通知。访客不会收到该通知。
- 门户交接：`C:/Users/70367/AppData/Local/Temp/xiaohe-role-lead-artifact-portal-handoff-20261111.md`。门户须按 owner／会话／assistant message ID 幂等观察提示，并在最新角色草稿就绪后重开原面板，不得重放 start_collection、认证或归并。
- 端到端自动弹出及真人浏览器验收尚未完成；等待门户 Agent 实现交接中的补充逻辑后，在同一隔离环境组合验证。当前运行服务和正式开关保持原状。
