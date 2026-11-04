# 上游差异清单

上游基线：`836649674568320c0cc994553a8a930ed82398d0`，开发分支 `dev-fengfeng`。P0 提交 `e82a8ae`；P0–P10核心与P11配置已实施，P12受控组合通过；各阶段增量见下文，真实三方联调尚未完成。

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

P7新增artifacts及0003迁移、固定镜像清单、隔离MinIO fixture；扩展自身settings/API/worker/main完成配置、读取下载、生命周期和ready引用校验。复用锁内boto3，不新增依赖；pyproject只加minio测试marker。原Compose不改，仅业务测试Compose增加独立profile/私有服务/专用volume。真实MinIO权限、摘要、到期和重启门见 [P7产出物记录](artifacts.md)。

P2–P7整批独立评审后，在自身worker/admission/runtime/streaming补齐活锁接管、凭据受理/历史checkpoint过滤和断流关闭。12项先RED后GREEN，完整231项及正式参数恢复132.00秒通过，见 [复核记录](continuous-review.md)。P8在跨工程契约交接点暂停，具体补充见 [认证契约建议](integrations/p8-auth-contract-proposal.md)。

P11新增业务Docker/Compose/代理/启动校验及内部健康检查，真实临时Nginx隔离验证通过；原Docker/Compose不变。P10独立部分接原维护任务停止过期匿名run，未开始物理删除；P12加入真实PG/MinIO已有能力组合旅程。全仓426通过/4跳过/1原Streamlit测试失败，详见[验收记录](testing/xiaohe-backend-acceptance.md)。共享v1.0.2由门户owner获用户授权更新，契约阻塞解除；本聊天接续P8/P9，正式开关保持关闭。

P8/P9核心提交`afe08f4`新增authentication/auth_bridge/claim、0004/0005增量迁移和隔离测试；复用HTTPX、业务身份/原任务框架及checkpoint，不增加依赖。认证上下文与聊天分离，服务端可信结果建立固定8小时会话；真实认领保护、202自动协调、原消息/文件归属迁移及新用户namespace接续。0001–0003不改，不执行已有业务库升级。

整批独立审查后，同一次修复补齐首次complete当前账号校验、退出当前未绑定上下文撤销、API ready前等待及Compose健康依赖；fixture与实际HTTP探针同步。最终业务/真实PG与MinIO/原client与threads288项通过；原全仓Streamlit失败仍独立保留。详见[本批审查](testing/2026-11-04-auth-deployment-review.md)。P10物理清理和P12真实联调尚未完成，不把新增迁移文件或fixture通过当作正式上线。

P10/P12接续扩展自身expiry/artifacts/worker/models，新增0006清理时间和0007最小对象key记录，复用原维护/PG事务/官方saver删除API，未增加依赖。补齐未认领过期正文、checkpoint和私有对象删除，pending/merged保护、上传锁/取消、真实清理硬杀及迟到对象巡检。P12新增跨方向到已有用户归并的真实内部API/worker硬杀组合，公共适配仍为ASGI，中心仍fixture。独立审查后补清理/巡检各5秒预算和已完成对象进度独立提交，最终314项完整所选回归通过，见[接续验收](testing/2026-11-04-cleanup-journey.md)；正式环境未变。
