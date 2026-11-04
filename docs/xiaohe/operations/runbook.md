# 小鹤业务后端运行说明

## 当前交付与启动门

在 dev-fengfeng 增量二开；原 Dockerfile、Compose、README 及 toolkit 保护目录未改。业务镜像使用 Python 3.14.7，API 与 worker 共用镜像、独立进程。新增 Compose 是待验收配置，不代表已部署。P8/P9核心适配与认领已有隔离集成证据，正式 SSO 开关保持关闭；实际认证配置、三方浏览器和部署验证尚未完成，不能直接用于上线。

所有命令从 agent-service-toolkit 根目录执行。不要把现有部署的 .env 复制到开发仓库，不要执行 down -v，不要对已有数据库重新套初始化脚本。

## 配置与隔离

`deploy/xiaohe/env.example` 只列 Compose 非秘密参数和私有文件路径。为目标环境准备独立受限目录，运行文件不放进源码/构建上下文：

- runtime.env：AUTH_SECRET、XIAOHE_DATABASE_URL、原 POSTGRES_USER/PASSWORD/HOST/PORT/DB，以及 XIAOHE_MINIO_BUCKET/ACCESS_KEY/SECRET_KEY。业务库与原 saver/store 必须指向同一 PostgreSQL 17 实例及数据库。数据库密码含 URL 特殊字符时要正确编码业务 URL，并核对上游原连接串的实际兼容性。
- 模型继续复用上游 COMPATIBLE_BASE_URL、COMPATIBLE_API_KEY、COMPATIBLE_MODEL、DEFAULT_MODEL=openai-compatible，或已配置的 DeepSeek；不要把真实供应商 model ID 填到上游固定枚举 DEFAULT_MODEL。没有指定/验通真实模型时只能记录未验收，不能把健康探测解释为模型可用。
- PostgreSQL 密码文件与 MinIO root 用户/密码文件仅由各存储服务挂载；API/worker 使用独立 bucket 权限的应用 key。提前建立私有 bucket 和仅覆盖该 bucket 的 IAM policy；不把 root key 给业务容器。
- 新的 SSO app Bearer 与 AUTH_SECRET、存储凭据相互独立。P8 的配置及本地桥接登记沿已有交接接入，不重新生成现有 secret；新增键与联调输入见[SSO适配](../integrations/sso-required-inputs.md)。正式开关继续false。
- TLS 证书/私钥通过只读挂载传入 gateway。公开域名、Origin、client、system/tenant 映射按受信登记配置，示例域名不可运行使用。

Compose 的默认主机绑定是 127.0.0.1:19443，仅 gateway 有映射。API、worker、PostgreSQL、MinIO 无主机端口；MinIO console 关闭。生产部署前检查 172.31.240.0/28 与宿主网络不冲突；若调整，必须一起修改 gateway 固定地址、API 地址及最小 trusted_proxy_cidrs。

代理配置 `nginx-ai.conf` 供独立 HTTPS 验证/后续门户入口合并参考，仅放行 /ai-api/v1/。它覆盖 system、原访客IP、HTTPS scheme，去掉外部 XFF/Forwarded/Authorization，保留 Origin/Cookie/CSRF；关闭 SSE 缓冲并拒绝所有其他路径。不得直接把示例部署成门户页面站点。真实门户入口由门户 Agent 管理，合并规则与实际转发链需分别验收。

业务启动器关闭 Uvicorn 通用 proxy headers，新增业务 middleware 只从登记代理采纳单一 https 标记，不改原 socket peer。原生 /internal/toolkit 和执行路由只能走私网/运维网络，仍由原 AUTH_SECRET 验证；不要添加 catch-all 反代或公开 Streamlit。

部署启动器拒绝 fake model、公开追踪、缺少共享密钥/存储/代理配置及密钥不一致。Compose 固定禁用 fake/tracing/正式 SSO 开关；不将密钥放入命令行或健康响应。Dockerfile 专用 allowlist 上下文排除 .superpowers、.env、privatecredentials 和其他工程文件。

## 镜像与配置验证

Python、PostgreSQL、MinIO、Nginx 的来源和 digest 见 `deploy/xiaohe/images.lock.json`。Python/Nginx 的远端 manifest 已核对；业务镜像尚未构建。MinIO 使用先前锁定的社区预编译镜像，仅隔离验证，不是官方供应链/生产安全审查结论，详见 [存储记录](../artifacts.md)。

准备私有配置后，用显式 Compose env 文件校验；只输出 quiet，避免展开运行密钥：

```sh
docker compose --env-file /secure/xiaohe/compose.env -f deploy/xiaohe/compose.yml config --quiet
```

后续获相应构建/部署授权时，再构建并记录业务镜像 digest、代码提交、uv.lock hash；将 XIAOHE_IMAGE 改为该不可变产物。示例 tag 只是格式示范。不能以配置解析成功代替镜像构建/服务验收。

## 初始化次序

1. 备份并确认目标实例。只对新专用数据库创建必要角色/业务配置；已有业务库升级须单独授权。首次建库角色与长期运行角色分开，当前 Compose 的首次用户是库 owner，生产最小权限尚待实际验证。
2. 用该业务镜像/私有环境运行 `python -m alembic upgrade head`，仅管理 xiaohe schema；不要把 Alembic 用于 checkpoint 或任务框架表。Compose 不自动升级数据库。
3. 首次初始化 Procrastinate schema，用 `build_job_app(...).schema_manager.apply_schema_async()`，先检查 public.procrastinate_jobs 是否存在；存在时走框架正式升级途径，不重复 apply。见 [持久任务记录](../testing/durable-worker-gate.md)。不要在网页请求里做 DDL。
4. 启动 API；原 lifespan 依次建立 saver/store、运行原 setup，再注册图使用的持久组件。P6 已以新隔离库双进程验证启动互斥；该互斥为 autocommit 会话锁，避免阻塞原 CREATE INDEX CONCURRENTLY。
5. API ready 后启动 worker，再接代理。worker 依赖任务表和原内部执行地址，重启不重发客户端用户消息。

Compose 的 worker 显式等待 API healthy；运行期间每次执行也先探测内部 ready，并持续复核会话、归属与运行截止。API不可用期间仍维持框架心跳，但不进入 acquire_run、不消耗真实执行次数；到期或撤销直接停止。探测通过后实际调用失败仍走原有限重试，不保证依赖永远可用。

## 健康与恢复

- `/internal/xiaohe/health/live` 只说明 API 进程能响应。
- `/internal/xiaohe/health/ready` 验证业务表、任务表和私有 bucket 的连通，失败只给安全503，不返回配置或依赖诊断。它不验证实际模型、SSO、短信或 worker 执行能力；gateway 不暴露这两个端点。
- worker 依赖 Procrastinate 心跳/任务状态；告警应检查任务积压和最后 heartbeat，不能用 API live 代表 worker 正常。正式参数 heartbeat10秒、失联120秒、扫描30秒；本计划隔离硬杀已验证恢复，证据见 [执行恢复](../execution-recovery.md)。
- 页面/SSE断开不取消已受理任务；恢复先读快照/游标，不重新 POST 消息。按业务 runId/jobId 定位日志，禁止输出消息正文、请求体、Cookie、认证字段或私有 URL。错误定位在受限运维环境进行。

原30秒维护同时做到期、pending认领、匿名物理清理及迟到对象巡检。worker使用自己的最小bucket权限ArtifactService；除Get/Put外需要该桶的DeleteObject权限，真实生产权限仍待验收。只支持无原生S3 versioning的桶，遇VersionId保留记录并拒绝假清理成功。清理状态和残留key巡检见[清理说明](../expiry.md)；0006/0007仅在本次隔离测试库应用，现有数据库仍须另行批准升级。

保持框架原并发设置；清理与迟到对象巡检各有5秒处理预算，超时让出执行槽并保留进度，不通过无界增加并发掩盖慢依赖。此预算限制清理处理，不替代数据库可用性或真实环境恢复验收；原正式参数恢复142.40秒是上一批证据，本轮另测了默认单槽在慢存储下的聊天/两轮恢复推进。

## 备份、回滚与待验收

先在独立恢复环境演练，不覆盖现有数据：停止接收新写、等待或暂停 worker，记录代码/镜像/迁移版本；备份完整 PostgreSQL（xiaohe、Procrastinate、checkpoint/store 相关表及角色/权限）和私有 bucket/对象版本，保持相同时间点。单独复制业务 schema 无法恢复运行记录与 checkpoint 的关系。

恢复到新库/新 bucket 后核对消息ID/顺序、run/command/事件水位、对象摘要、归属、鉴权与重放；失联 job 按框架恢复，不手写队列状态或重放已完成外部动作。镜像回滚前确认迁移向后兼容；不自动 downgrade 生产数据库。保留原卷和原镜像作为恢复依据。

本阶段已做 Compose 解析、启动拒绝测试、受信 scheme 与 socket 证据；真实 Nginx 隔离路由验证单独记录。业务镜像构建、该 Compose up、目标最小权限、真实环境重启/备份恢复、真实门户/SSO/模型/短信和生产/UAT部署均未完成。下一步按交接清单准备联合验收，正式开关继续关闭。

参考：[Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)、[Nginx proxy](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)。
