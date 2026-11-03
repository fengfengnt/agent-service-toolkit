# P1 业务身份、会话与私人对话

日期：2026-11-03。用户授权在 `dev-fengfeng` 执行 P1，基线为 P0 提交 `e82a8ae`；P2–P12 尚未执行。本文描述业务函数及隔离验证，不表示公开服务、真实 SSO 或代理配置已经上线。

## 已实现边界

新增 `database.py`、`models.py`、`identity.py`、`conversations.py` 及 Alembic 初始迁移。上游 `service/schema/core/memory/client/agents` 和顶层测试配置不改。

- `resolve_system(request, db)`：只从登记的 socket peer 接受单个 `X-Xiaohe-System-Id`，查询启用的系统；写请求必须携单个、精确匹配的 Origin，读取如携 Origin 也必须匹配。要求可信 HTTPS scheme；不从浏览器 body、tenant/user header 或 X-Forwarded-For 推导系统。
- `resolve_actor(request, system, db)`：解析唯一 Cookie、查摘要、锁会话，核对系统/租户/主体与有效期；普通写操作验证独立 CSRF。禁用系统/用户及撤销令牌失效。
- `bootstrap_session(request, response, db)`：首次无 Cookie 建访客；已有有效 Cookie 只恢复原会话与默认对话，返回 CSRF，不续期。该初始化以精确 Origin 防护，不能要求浏览器在刷新后已有尚未取得的 CSRF。
- `require_conversation`、`load_snapshot`：每次检查当前会话和数据库归属，不以 UUID 难猜授权；跨主体/系统访问在任何 toolkit 调用前拒绝。消息从业务表按 `(created_at,id)` 读取，稳定 ID 不重建。
- `create_user_session`：**内部受信函数**，只供后续已成功验证的中心结果调用。核对 issuer/client 后，用来源三元组唯一绑定一次 AI UUID user_id；并发、重试复用。P1 不提供接收浏览器 VerifiedCompletion 的入口，也没有验证真实中心 claim。
- `logout_session`：有效用户的本地会话撤销并清 Cookie，保留账号/历史；访客注销不修改次数或期限、不清 tombstone。它不实现中心全局退出，也不取消后台任务。

调用方负责事务提交/回滚；这些函数不自行提交。P6 装配时必须在事务成功后才发送响应/Cookie，错误使用安全 ApiError；不能把原始数据库异常、请求 body 或 Cookie 写入日志。业务 AsyncSession 不与上游 checkpoint 连接池混用。

P1 的快照可读取私人消息，`activeRun/cursor/merge` 仍为 null，因为本阶段没有运行/事件/认领表。这不是已完成事件水位或归并协议，P3/P6/P9 需增量接入；公开 DTO 保持 P0 类型。

## 会话时限与凭据

认证 Cookie 名称 `__Host-XIAOHESESSION`，Secure、HttpOnly、SameSite=Lax、Path=/，无 Domain。原值为 32 随机字节生成的 43 字符 URL-safe token；数据库仅存 SHA256 摘要。CSRF 是独立随机值，存会话行并经快照返回，不复用 AUTH_SECRET。数据库引擎关闭参数回显；认证材料不得进入消息、模型、事件或 checkpoint。

Cookie 不设置 Max-Age/Expires，业务期限由服务端判定，以便到期后仍能识别旧会话并返回 401/410，避免 Cookie 自动消失后被误当作新访客。清除 Cookie/整个浏览器会话后无法识别同一自然人，这仍受共享合同的匿名识别边界限制。

访客创建时保存配置默认值 100 条和 14400 秒；刷新/重开/注销不修改累计次数与期限。滚动续期和额度受理在 P3 实现。到期访客返回 `410 VISITOR_EXPIRED`，保留行作为 tombstone；未知、重复、格式错误 Cookie 返回 `401 AUTH_REQUIRED`，不创建新访客。跨系统 Cookie 返回 403。

用户会话 `expires_at = created_at + 28800秒`。创建时取 PostgreSQL `clock_timestamp()`，中心 `authenticatedAt` 单独保存原值，不作为 AI 计时起点。期限到达即 401；读取与验证写请求不滑动续期。数据库约束/触发器同时阻止修改身份、移动用户创建/到期时间和复活已撤销会话；访客过期时间保留给后续合法受理续期使用。锁等待结束后及返回快照前重新检查有效期，不使用事务开始时冻结的 now()。

用户过期/撤销后不降级匿名读取旧资料；新认证上下文/CSRF 及真实 restore/OIDC 仍归 P8，不添加匿名 reset 接口。

## schema、键与迁移

初始业务 schema 固定 `xiaohe`，Alembic version 表也在此 schema。迁移不管理 public 或上游表；downgrade 只删除本迁移的六张业务表及保护触发器/函数，保留 schema 和版本表基础设施。**回退会删除这些业务表的数据，不能对已有真实数据直接运行。**本次进退测试只在专用临时测试库执行。

| 表 | 关键约束 |
| --- | --- |
| systems | system_id 主键、tenant_id 唯一；显式保存 client/Origin/issuer、启用标记 |
| users | 全局不透明字符串 user_id 主键；来源 issuer/sub/source_tenant_id 唯一；系统/租户复合 FK；身份归属不可重写；guest: 前缀保留 |
| sessions | token_digest 唯一；user/guest 主体互斥；user 与系统/租户复合 FK；用户固定 8 小时检查及不可滑动触发器 |
| projects | 用户与系统/租户复合 FK；每 system/user 一个默认项目的部分唯一索引，仍可保存其他私人项目 |
| conversations | 用户/访客互斥；项目与用户/系统/租户复合 FK；用户门户长期对话、访客对话各自部分唯一；thread_id 独立唯一 |
| messages | 对话/系统/租户复合 FK；稳定 UUID；角色检查；对话/client_message_id 唯一；历史排序索引 |

访客临时 toolkit 主体持久保存为 `guest:<guest_uuid>`，与 AI 新建用户 UUID 区分；已有非 UUID 的不透明用户 ID 可读取复用，但不能跨租户重绑。默认项目/对话创建以用户行锁和唯一索引保证并发唯一，不向 toolkit 获取随机用户/线程默认值。

## 配置与运行准备

沿用 P0 Settings；本阶段新增：

| 配置 | 内容 |
| --- | --- |
| `XIAOHE_DATABASE_URL` | 业务迁移/引擎使用 `postgresql+psycopg` URL；不读取或复制上游 .env，不把连接写进 alembic.ini |
| `XIAOHE_TRUSTED_PROXY_CIDRS` | JSON CIDR 列表；默认空即拒绝全部入口，实际 peer 需测量后登记，不能猜测整段 Docker 网段 |

P11 必须在可信代理剥离外部同名系统声明并注入受控值，限制直接访问，并正确配置可信 HTTPS scheme 转发。P1 的 IP/Origin 校验不代表该代理规则已部署；只有 app.state.xiaohe_settings 配置完成后业务函数才能使用。P6 才会装配真实路由。

依赖只增量声明 `sqlalchemy[asyncio]>=2.1.5,<2.2` 和 `alembic>=1.17,<2`，锁定 SQLAlchemy 2.1.5（原已间接存在）、Alembic 1.20.0、Greenlet 3.5.6、Mako 1.4.3；其余已锁定版本未变。Alembic 迁移使用同步 psycopg，业务使用 AsyncSession。参考 [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) 与 [Psycopg 并发限制](https://www.psycopg.org/psycopg3/docs/advanced/async.html)；Windows pg 测试仅对 pg 标记使用 SelectorEventLoop，不改变 P0/上游测试事件循环。

```powershell
# 仅在明确选择并备份过的目标环境提供 XIAOHE_DATABASE_URL 后执行，不是默认启动动作。
uv run --frozen --python 3.14.7 alembic upgrade head
uv run --frozen --python 3.14.7 alembic check

# 测试必须显式提供隔离 URL，库名以 test_xiaohe_ 开头；缺少环境会失败，不以 SQLite/skip 替代。
uv run --frozen --python 3.14.7 pytest tests/xiaohe/test_identity_sessions.py -q -m pg
```

## 隔离验证记录

本次专用 WSL 容器 `xiaohe-p1-test-pg17`，库 `test_xiaohe_p1`，发布 `127.0.0.1:35417`，数据为临时 tmpfs；未访问任何现有业务库。测试认证仅用于该回环临时容器，不作为运行配置。实测 PostgreSQL **17.11**，镜像固定为 `postgres@sha256:3b0cda93be7c8dba2ed484d19393d93845dc3dafb88973e248ba8e6d99d784e1`。测试夹具只清空该明确选择的测试库中列出的业务表，不使用 CASCADE。

- 初始 RED：identity 模块不存在。随后中心认证时间未保存由断言复现，再补独立 authenticated_at 字段。
- 27 项 P1 pg 测试通过；包括并发身份/默认对象唯一、跨用户先拒绝、复合 FK、数据库归属不可改写、8 小时不可滑动、访客 tombstone、CSRF/注销及禁用/重复 Cookie、仅 POST 初始化。
- 28799 秒仍有效、28800 秒失效使用受控时钟断言；真实数据库锁等待测试通过 pg_stat_activity 确认已等待，再在提交前使访客期限到达，最终返回 410，无 sleep 猜时序。
- 在真实上游 AsyncPostgresSaver/AsyncPostgresStore 中保存哨兵，业务迁移 base→head 进退后 checkpoint ID、Store 内容及 public 表清单保持不变。
- `alembic check`：No new upgrade operations detected。

合并回归 **116 项通过（P1 pg 27、P0 67、上游 22），19 条既有依赖警告**；Ruff check/format check 通过。独立只读审阅复跑 P1 27 项通过，无 Critical/Important。

已知小项：初始迁移可正常执行，但 `migrations/script.py.mako` 尚未添加，标准 `alembic revision --autogenerate` 的生成阶段会失败；在 P3 新增业务迁移前补齐标准模板。本次不声称已验证迁移生成命令。

门户 Task6 另外提出到期后“仅认证上下文”的精确获取入口及 `AUTH_NOT_READY` 公开重试语义需求；现仍作为 P8 前置契约依赖。P1 保持 401/410、不恢复旧消息、不新建 guest、不重置额度；本次不添加认证恢复端点或修改公共合同，不能让门户绕过 CSRF。

未运行真实短信/模型/SSO、浏览器或生产部署；不存在对外可用的 P1 登录端点。测试完成后停止本次临时容器，不触碰其他容器或卷。
