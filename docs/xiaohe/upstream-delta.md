# 上游差异清单

上游基线：`836649674568320c0cc994553a8a930ed82398d0`，开发分支 `dev-fengfeng`。P0 提交 `e82a8ae`；当前增量执行 P1。

P1 新增 `src/xiaohe/{database,models,identity,conversations}.py`、业务 Alembic 配置/初始迁移、pg 会话测试及[说明](identity-sessions.md)。仅改既有业务 settings/conftest、增量 pyproject 依赖和 pg marker，以及相应 uv.lock；已有锁定版本未升级，新增 Alembic/Greenlet/Mako。上游源码及顶层测试配置继续保持原样。以下为 P0 历史差异与复用边界：

| 位置 | 本次新增 | 用途 |
| --- | --- | --- |
| `src/xiaohe/__init__.py` | 包入口 | 业务代码独立于上游模块 |
| `src/xiaohe/contracts.py` | Pydantic DTO/内部类型/安全错误 | 实现共享 wire v1.0 及服务端内部命令，不改上游 Schema |
| `src/xiaohe/settings.py` | 业务配置 | 访客额度/保留期、内部 URL/共享密钥、业务连接引用 |
| `src/xiaohe/toolkit_client.py` | HTTPX 内部流客户端 | 调用待 P4 实现的可信执行端点，不复制原 AgentClient |
| `tests/xiaohe/` | 受控测试与八份模拟 fixture | 契约、配置、敏感字段及内部传输边界 |
| `docs/xiaohe/` | 冻结和差异记录 | 说明已实现层次与后续未实现门 |

保护路径 `src/service`、`src/schema`、`src/core`、`src/memory`、`src/client`、`src/agents` 均不改。P0 时 `pyproject.toml/uv.lock` 原样保留，P1 按上述清单增量修改；`.gitignore`、顶层 `tests/conftest.py`、README、Docker/Compose 继续原样保留。

不追加依赖，直接用锁内 HTTPX、Pydantic、pydantic-settings、pytest、Ruff。目标 Python 3.14.7 已安装并核验锁；不新增 `.python-version`，执行命令显式选版本。`.venv` 是被原规则忽略的本地环境。

未跟踪的 `.codegraph/` 是执行前已有资料，保留且不加入提交；`.superpowers/sdd/` 仅放本计划自忽略的临时证据，不提交。父目录设计、计划和共享合同不属于本 Git 仓库；不声称它们随本代码提交。

后续复用而非重复实现：`core.get_model`/已有模型枚举、Agent registry、上游 app/verify_bearer/lifespan、PostgreSQL saver/store。P0 没有改模型调用、启动方式或上游认证规则。新增内部端点、注册小鹤图和公开业务入口仍待后续任务。

P2新增jobs.py、独立PG17测试Compose与真实进程探针；依赖增量锁定Procrastinate3.10.0/asgiref3.12.1。复用既有ORM事务连接，不改上游服务/Agent/模型/存储源码。框架验证与实际恢复测量见 [P2验证门](testing/durable-worker-gate.md)。

P3新增admission.py、最小events.py及0002迁移，扩展业务models/conversations和测试。job_app测试fixture提升到共用业务conftest，清理仅限已明确选择的测试库及业务metadata列出的表，不用CASCADE；上游测试配置不改。标准迁移模板补齐，无新增依赖。见 [P3记录](admission.md)。

P4新增runs/worker/graph/toolkit_runtime、0002b增量迁移和真实HTTP进程探针；仅扩展业务models/admission/jobs及业务fixture。框架最终丢失回调用于释放业务占用，无新增依赖。0001/0002既有迁移不改；原上游代码及Compose继续保持。内部执行端点尚待P6产品装配，见 [P4恢复规则](execution-recovery.md)。

P5扩展业务graph/contracts，新增professional、接待策略与测试；内部流仅转发显式公开文本，首版结构化接待只输出校验后的最终回答。直接复用core.get_model及原模型配置，未改上游模型/Agent源码，未增加依赖或真实模型调用。详见 [P5边界](reception.md)。

P6新增api/main、扩展events/models/worker及0002c迁移，业务REST/SSE与原app装配并存。仍不改上游service/memory/registry源码；跨副本setup由业务入口的短期autocommit会话锁保护，原saver/store工厂和池完整复用。详见 [P6接口与证据](http-events.md)。
