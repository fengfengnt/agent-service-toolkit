# UAT portal System 域名修正准备包

状态：仅准备，尚未在 UAT 执行。只用于 `admin@39.106.42.10` 的 AI PostgreSQL `xiaohe` 库，不用于 WSL，也不改变模型服务、密钥、开关或业务版本。

## 变更与执行条件

仅更新 `xiaohe.systems` 的 `system_id=portal`、`tenant_id=1`、`portal_client_id=app_localportal`、`enabled=true` 这一行：

| 字段 | 原值 | 新值 |
| --- | --- | --- |
| allowed_origin | https://portal-uat.yuhezhiyuan.com | https://portal-uat.yunhezhiyuan.com |
| issuer | https://sso-uat.yuhezhiyuan.com | https://sso-uat.yunhezhiyuan.com |

执行前由协调方确认维护窗口及精确旧值。保留 UAT bridge 关闭，不进行登录、认证重放或新会员绑定；发生任何预检失败即停止，不放宽条件。`apply.sql` 和 `rollback.sql` 在事务中锁定用户表以防并发绑定，要求 portal 无会员，并要求精确命中一行；只更新两个字段，其余行与列均保留。锁等待上限 3 秒、语句上限 15 秒。已有会员时，本包不适用，不能绕过保护或自行改用户 issuer。

UAT 当前两项配置及 Runtime Origin 已正确，不需要改容器环境或重启。WSL 的外部 New API 地址 `https://api.wenwen-ai.com/v1` 保留；本包不将其切换到 `llm.lvh.me`。

## 备份与执行步骤（等待协调授权，以下命令本次未执行）

在 **UAT 宿主 Bash** 中操作，先将本目录三个 SQL 文件置于独立审核目录，并将 `SQL_DIR` 改为该目录。重新核对 API 的实际数据库目标以及 PG 容器的 `POSTGRES_USER` 与下述记录一致；不要输出完整环境变量或连接串。本次只读确认目标库为 `xiaohe`，公开部署定义用户为 `xiaohe`。

```bash
set -euo pipefail
umask 077
PG=yunhezhiyuan-aibackend-postgres-17.11
SQL_DIR=/opt/yunhezhiyuan/maintenance/20261109_uat_domains
AI_DOMAIN_BACKUP=$(mktemp -d /opt/yunhezhiyuan/backups/ai-uat-domains-20261109.XXXXXX)

docker exec "$PG" psql -X -U xiaohe -d xiaohe -v ON_ERROR_STOP=1 -c \
  "BEGIN READ ONLY; SELECT system_id,tenant_id,portal_client_id,allowed_origin,issuer,enabled FROM xiaohe.systems WHERE system_id='portal'; SELECT count(*) AS portal_members FROM xiaohe.users WHERE system_id='portal'; COMMIT;"

# 只备份 System 配置，不导出用户、会话、文件或密钥。
docker exec "$PG" pg_dump -U xiaohe -d xiaohe --data-only \
  --table=xiaohe.systems --column-inserts > "$AI_DOMAIN_BACKUP/systems.before.sql"
docker exec "$PG" psql -X -U xiaohe -d xiaohe -v ON_ERROR_STOP=1 -At -c \
  "SELECT row_to_json(s) FROM xiaohe.systems s WHERE system_id='portal';" \
  > "$AI_DOMAIN_BACKUP/portal.before.json"
test -s "$AI_DOMAIN_BACKUP/systems.before.sql"
test -s "$AI_DOMAIN_BACKUP/portal.before.json"
(cd "$AI_DOMAIN_BACKUP" && sha256sum systems.before.sql portal.before.json > SHA256SUMS && sha256sum -c SHA256SUMS)
```

检查备份 JSON 恰好一条，且六列与上表及定位条件一致，然后才执行：

```bash
docker exec -i "$PG" psql -X -U xiaohe -d xiaohe -v ON_ERROR_STOP=1 < "$SQL_DIR/apply.sql"
docker exec -i "$PG" psql -X -U xiaohe -d xiaohe -v ON_ERROR_STOP=1 < "$SQL_DIR/verify.sql"
```

备份由 `umask 077` 限制权限，保留目录及校验和。SQL 再次原子校验旧值与用户数，防止备份后漂移。此备份不是完整数据库备份；不要将 `systems.before.sql` 直接导回在线表覆盖其他配置。

## 回退与确认

仅在两个新值仍匹配、portal 仍无会员绑定时回退：

```bash
docker exec -i "$PG" psql -X -U xiaohe -d xiaohe -v ON_ERROR_STOP=1 < "$SQL_DIR/rollback.sql"
docker exec "$PG" psql -X -U xiaohe -d xiaohe -v ON_ERROR_STOP=1 -At -c \
  "SELECT row_to_json(s) FROM xiaohe.systems s WHERE system_id='portal';" \
  > "$AI_DOMAIN_BACKUP/portal.rollback.json"
cmp "$AI_DOMAIN_BACKUP/portal.before.json" "$AI_DOMAIN_BACKUP/portal.rollback.json"
```

若新增了会员或登记漂移，回退自动拒绝；不要删除会员、禁用身份保护触发器或恢复整库。回退到原错误值会重新导致正确门户 Origin 被拒绝，它只是撤销变更，不表示业务恢复。`verify.sql` 校验的是修正后状态，不能用作回退成功判断。

## WSL 无模型调用检查

在 **WSL Bash** 中运行下述三条，只做无 Cookie 的 GET；不创建访客、不发消息、不发短信。保留 TLS 校验并禁用环境代理。第三条使用不存在的 conversation，预期 401 且无 Set-Cookie；不是发起登录或会话归并。

```bash
curl --noproxy '*' -fsS http://127.0.0.1:18090/internal/xiaohe/health/ready
curl --noproxy '*' --cacert /home/fengfeng/yunhezhiyuan/certs/local-ca.pem -fsS -D - -o /dev/null https://portal.lvh.me/copilot-api/info
curl --noproxy '*' --cacert /home/fengfeng/yunhezhiyuan/certs/local-ca.pem -sS -D - -o /dev/null -H 'Origin: https://portal.lvh.me' -H 'X-Xiaohe-View-Version: 2' https://portal.lvh.me/ai-api/v1/conversations/00000000-0000-0000-0000-000000000000
```

预期依次为 ready、200（view version=2）、401。这里只证明基础健康、路由和未登录拒绝边界，不证明模型、SSO、附件或真实 SSE 业务验收。

## SQL 隔离测试

测试文件为 `tests/xiaohe/test_uat_domain_sql.py`。只接受显式指定、名称以 `xiaohe-domain-sql-test-` 开头且带 `purpose=xiaohe-uat-domain-sql-test` 标签的临时容器。数据库固定为容器内的 `xiaohe`，测试会重建该临时库的 `xiaohe` schema。不得给运行中的任何业务容器添加该测试标签。

测试使用现有 PostgreSQL 镜像、`--pull never --network none --tmpfs /var/lib/postgresql/data`，不挂载现有卷、不发布端口。覆盖精确修正／验证／回退、其他列和行保留、重复执行拒绝、旧值或租户漂移拒绝、已有会员及回退后新增会员保护。结束后仅停止该临时容器，由 `--rm` 清理，不清理其他资源。
