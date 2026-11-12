# 小鹤标准 Skill 唯一执行链 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 在本聊天逐项实施；最后独立审阅，不向用户重复询问是否继续。

**Goal:** 删除旧 Skill／DSL 执行路径，在清空指定 WSL 旧数据后部署 0.1.8 并验证原生业务。

**Architecture:** 保留 LangGraph 外壳和 DeepAgents 业务图，标准包及固定字段策略作为唯一资源来源。旧数据清理为单独受控操作，0015 仅验证清理前提并移除旧结构，不自动清空用户资料。

**Tech Stack:** 现有 Python、SQLAlchemy、Alembic、LangGraph、DeepAgents、PostgreSQL、S3／MinIO，不新增依赖。

**Spec:** [清理设计](../specs/2026-11-12-xiaohe-standard-skills-only-design.md)。

## Global Constraints

- 发布版本 0.1.8；仅 WSL `xiaohe_triad_local_20261104`，不部署 UAT／生产或推送 Git。
- 保留二十一份标准包、受控工具和原生快照／checkpoint／幂等恢复。
- 用户授权删除旧会话、草稿、执行及归属明确的对象，不保留旧数据迁移档案。
- 保护身份／认证／额度、既有备份、业务后端和其他容器／数据卷。
- 门户 DTO 与现有业务 action 不作破坏性变更；definitionBundleId 返回原生包指纹。
- 专用 test_xiaohe_ 数据库运行可清空的测试，不把 fixture 指向实际业务库。

## Review Focus

- 原生链仍暗中导入旧资源，删除后启动或新建草稿失败：Task 1 无旧资源测试。
- 独立表单和草稿字段校验发生漂移：Task 2 三方向表单与保存／提交测试。
- 删除旧运行时后丢失确认／写栅栏／幂等：Task 2 保留原生 effects、确认和恢复集合。
- 清理误删共享 checkpoint、对象或身份：Task 3 隔离数据中的非目标记录及失败／重复执行测试。
- 清理与 DDL 后旧镜像误启动、版本身份不明：Task 4 版本／镜像／数据库结构与 HTTPS 验证。

## Task 1: 标准资源和原生启动成为唯一入口

**Files:** `src/xiaohe/{main,settings,admission,graph,models}.py`、`deep_runtime/`、`agents/`、`workflows/`、`lead_skills/`、`resources/xiaohe/`，相关 `tests/xiaohe/`。

**Interfaces:** 保留 `load_runtime_bundle(root)`、`persist_runtime_bundle(db,bundle)`、`run_deep_turn(...)` 和既有主业务工具接口；共享类型与状态帮助函数从旧执行器移出，不引入第二套实现。

- [x] 先新增 `test_standard_only.py`：断言二十一标准包、无 legacy_id，旧目录不存在时无需旧 registry／DSL 即可运行；运行确认 RED。
- [x] 原生 startup／admission／outer dispatch 唯一路径，移除旧开关，移出共用校验和状态帮助函数；只删除无调用者的旧执行器与资源。
- [x] 改写原生测试中的旧 DSL fixture；移除仅验证旧执行器的测试，保留其可观察业务保护并迁入原生用例。
- [x] 运行无数据库标准包／工厂测试及相关静态检查，确认 GREEN；记录改动。

## Task 2: 草稿与独立表单完全依赖字段策略

**Files:** `src/xiaohe/reception/{store,forms,submission,api,models}.py`、`lead_skills/{store,views}.py`、`deep_runtime/{forms,tools}.py`；表单／提交／恢复测试和 fixtures。

**Interfaces:** `resolve_draft_policy(db,row)` 只读取固定 FieldPolicy；新草稿必绑字段策略；`form_definition(direction)`／`form_field_policy(direction,expected_bundle_id=...)` 使用标准原生包，wire DTO 不变。

- [x] 增加三方向草稿和独立表单的新建／读取／保存测试，断言无 workflow 定义绑定仍可工作、陈旧指纹冲突；确认 RED。
- [x] 切换策略读取／创建及投影，移除旧草稿回填和 definition 依赖；保留原生确认、业务回执和当前 UI action。
- [x] 运行原生工具、三方向 journey、form、effects、versions、children、recovery 和真实强杀恢复；确认 GREEN。

## Task 3: 受控旧数据清理与 0015

**Files:** 新增 `migrations/versions/0015_standard_skills_only.py`、本次清理入口及 `tests/xiaohe/test_standard_cleanup.py`；更新 ORM 与迁移测试。

**Interfaces:** 清理入口只针对显式数据库和固定目标清单；0015 在旧运行／草稿引用未清空时拒绝，在清空或新安装库移除旧表／绑定列。清理不得由应用启动调用。

- [x] 增加迁移拒绝旧数据、新安装升级、目标线程／对象删除、非目标数据保留、失败重试测试；确认 RED。
- [x] 按 spec 实现精确清理、对象验证和依赖顺序，保留身份与额度；补齐旧模型／列删除。
- [x] 隔离 PostgreSQL／对象存储替身验证，完整非 MinIO 回归及静态检查；审阅全改动，一轮修复并回归。

## Task 4: 0.1.8 WSL 发布测试

**Files:** `VERSION`、发布归档／部署控制脚本、`docs/deployment/2026-11-12-standard-skills-only-wsl.md` 和相关操作文档。

**Interfaces:** 复用现有 WSL 私有配置和 Compose，仅替换小鹤 API／Worker；镜像输入来自已验证源码，保存可加载镜像归档和校验和。

- [x] 按用户指定更新 VERSION=0.1.8，复核实际运行版本与同版本归档冲突，构建／校验／导出镜像。
- [x] 停止小鹤入口和 API／Worker，重新核对 native 新记录及外部附件引用为零；只清理 spec 授权集合，不新建旧业务数据备份。
- [x] 执行精确清理及 0015，启动 0.1.8，复核身份／配置／其他容器／非目标存储保持。
- [x] 验证标准包读取、原生新会话与表单、API 健康、Worker 心跳、HTTPS 和门户资源；受控模型／真实业务写入未执行则明确记录。
- [x] 完成归档、发布记录与源代码提交；不推送。最终记录已删范围、版本、地址和未验项。

## 执行记录

- 2026-11-12：用户批准实施并指定 0.1.8。使用现有 dev-fengfeng checkout；不创建新 worktree，保护已有六项无关未跟踪内容。这是按当前共享工作区连续实施的选择，不执行全局清理。
- Pre-flight：Task 1 提供原生唯一入口及共享类型，Task 2 消费；Task 2 的固定字段策略约束与 Task 3 的新 ORM／0015 必须同时进入发布包；Task 4 仅在前序验证通过后清理实际数据。
