# 小鹤内嵌知识库：WSL AI 0.1.12 发布

日期：2026-11-13。状态：已推送并部署 WSL，仅更新小鹤 API 和 Worker。门户 0.1.37、SSO 0.1.30 保持，未部署 UAT 或生产。

## 发布内容

内嵌 Chroma 已接入标准 Skills / DeepAgents，不恢复旧技能或 DSL 兼容层。知识清单进入 RuntimeBundle，命令及其重试固定快照，新命令使用当前发布资源。修正 Chroma cosine 负浮点误差丢弃有效匹配的问题。

## 版本与归档

- 构建提交：`34e8a11b2600b5d4bcf47af2ca8591af6278ca59`，分支 `dev-fengfeng`。
- 镜像：`yunhezhiyuan-aibackend-0.1.12:latest`，amd64，实际 ID `sha256:0b17127e7c18824122182ff7ac7d21eb9f56deea8fde0882d60ecabde80ef490`。
- 冻结输入：183 文件，摘要 `b202fa17f522dc20a809b476e8bb1bc29824499804ad037bcf9b0191bf6845a1`，镜像内逐文件校验一致。
- 可加载归档：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/local/0.1.12/yunhezhiyuan-aibackend-0.1.12.docker.tar`，787,433,472 字节。
- 归档 SHA256：`70bdf6ae2d2e0c7865c3676a050ea3cf7f3eb3f66f7f645e4216f2d5bb74a044`，Docker 配置与层身份已核对。

## WSL 配置与使用

专用运行文件：`/home/fengfeng/yunhezhiyuan/aibackend/config/knowledge.env`，权限 0600、属主 1000:1000；Windows 可通过 `\\wsl.localhost\Ubuntu\home\fengfeng\yunhezhiyuan\aibackend\config\knowledge.env` 编辑。

参数模板位于仓库 `deploy/xiaohe/knowledge.env.example`，归档中也保留无密钥副本。保留 `XIAOHE_KNOWLEDGE_ROOT=/var/lib/xiaohe/knowledge`，取消下面四行注释并填写真实参数：

```dotenv
XIAOHE_KNOWLEDGE_EMBEDDING_BASE_URL=https://your-embedding-service.example/v1
XIAOHE_KNOWLEDGE_EMBEDDING_MODEL=your-embedding-model
XIAOHE_KNOWLEDGE_EMBEDDING_API_KEY=your-private-api-key
XIAOHE_KNOWLEDGE_EMBEDDING_DIMENSIONS=1536
```

地址、模型、密钥均为占位示例，维度必须改为实际输出维度。不要把真实密钥写回仓库模板。保存后重新创建 API 容器，使 Compose 重新读取 env_file：

```powershell
wsl.exe -d Ubuntu -- sh /home/fengfeng/yunhezhiyuan/aibackend/compose.sh up -d --no-deps --no-build --pull never --force-recreate aibackend-api
```

首次检索仍需按 `docs/xiaohe/operations/knowledge.md` 准备真实 Markdown、构建索引并发布 manifest。只填模型参数不会自动生成或导入业务知识。变更模型、endpoint 或维度必须重建索引；单纯 restart 不重新读取 env_file。

## 切换与验证

配置备份：`/home/fengfeng/yunhezhiyuan/backups/aibackend-knowledge-0.1.12-20261113T075318Z`（0700）。保留旧版 0.1.10 镜像和归档。本次新增 API 专用可写卷 `xiaohe_knowledge-data`，Worker 不挂载该卷或读取知识配置。原业务、登录、模型配置、证书、端口及数据卷保持。

- API 健康、Worker 心跳新鲜；运行版本、镜像 ID、标准资源包与候选一致。
- 严格 TLS 验证通过：`https://portal.lvh.me/portal/`、`https://portal.lvh.me/ai-api/v1/reception/progress`（匿名 401），SSO issuer 保持 `https://sso.lvh.me`。
- 数据库仍为 Alembic 0015，结构及稳定身份记录摘要一致；没有执行数据清理或数据库恢复，未改变表结构，未制作数据库 dump。
- 54 个其他持久容器及 12,771 个受保护文件核对保持，包括门户和 SSO 静态文件。
- 候选镜像中 21 个标准 Skill 读取/工具循环通过；原配置模型可组装，未发起模型请求。
- Linux 候选以 UID 1000、只读根、专用可写目录验证真实 Chroma：持久化、授权命中 1 条、越权命中 0 条。测试使用确定性本地向量，没有真实 Embedding 请求。
- 参数模板解析通过；知识源及部署配置回归 30 passed、1 skipped。功能合并测试边界见 `docs/xiaohe/testing/2026-11-13-embedded-knowledge-merge.md`，扩大回归中的既有失败未在本次发布修改。

当前 Embedding 四项参数未配置，知识检索保持关闭，沿用标准 Skill 资料。未验证真实 Embedding 连通与召回质量、真人登录、浏览器交互或正式业务提交；没有把健康检查等同业务验收。
