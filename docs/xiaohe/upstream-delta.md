# 上游差异清单

基线：`836649674568320c0cc994553a8a930ed82398d0`，开发分支 `dev-fengfeng`。本次只执行 P0。

| 位置 | 本次新增 | 用途 |
| --- | --- | --- |
| `src/xiaohe/__init__.py` | 包入口 | 业务代码独立于上游模块 |
| `src/xiaohe/contracts.py` | Pydantic DTO/内部类型/安全错误 | 实现共享 wire v1.0 及服务端内部命令，不改上游 Schema |
| `src/xiaohe/settings.py` | 业务配置 | 访客额度/保留期、内部 URL/共享密钥、业务连接引用 |
| `src/xiaohe/toolkit_client.py` | HTTPX 内部流客户端 | 调用待 P4 实现的可信执行端点，不复制原 AgentClient |
| `tests/xiaohe/` | 受控测试与八份模拟 fixture | 契约、配置、敏感字段及内部传输边界 |
| `docs/xiaohe/` | 冻结和差异记录 | 说明已实现层次与后续未实现门 |

保护路径 `src/service`、`src/schema`、`src/core`、`src/memory`、`src/client`、`src/agents` 均不改。原 `pyproject.toml`、`uv.lock`、`.gitignore`、顶层 `tests/conftest.py`、README、Docker/Compose 原样保留。

不追加依赖，直接用锁内 HTTPX、Pydantic、pydantic-settings、pytest、Ruff。目标 Python 3.14.7 已安装并核验锁；不新增 `.python-version`，执行命令显式选版本。`.venv` 是被原规则忽略的本地环境。

未跟踪的 `.codegraph/` 是执行前已有资料，保留且不加入提交；`.superpowers/sdd/` 仅放本计划自忽略的临时证据，不提交。父目录设计、计划和共享合同不属于本 Git 仓库；不声称它们随本代码提交。

后续复用而非重复实现：`core.get_model`/已有模型枚举、Agent registry、上游 app/verify_bearer/lifespan、PostgreSQL saver/store。P0 没有改模型调用、启动方式或上游认证规则。新增内部端点、注册小鹤图和公开业务入口仍待后续任务。
