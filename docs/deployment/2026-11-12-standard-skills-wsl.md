# 标准 Skill / DeepAgents：WSL 0.1.7 发布记录

日期：2026-11-12，环境：本地 WSL Ubuntu。用户授权提交并部署 WSL；未推送 Git，未部署 UAT 或生产。

## 发布内容与来源

AI API／Worker 从 0.1.5 升至 **0.1.7**，在两个服务中设置 `XIAOHE_DEEPAGENTS_ENABLED=true`。源代码默认值仍为 false，其他环境不随本次发布启用。接入二十一份标准 Skill、业务节点内 DeepAgents、只读 child、固定版本恢复和 v2 动态留资分组；主业务 Agent 统一保存、提交及核对回执。

| 项目 | 已部署内容 |
| --- | --- |
| 后端源码 | `1c1a1ddd3d654b037baaa16d68eb42733038e75f`，`dev-fengfeng`；版本提交 `build(xiaohe): prepare 0.1.7 WSL release` |
| 最后功能修复 | `3a399ef12f07327f5e14ef094cc0f15507e9d3fb` |
| 冻结输入 | Git 归档的 226 个文件；未带入工作区无关未跟踪文件 |
| 输入指纹 SHA256 | `f982b7984ff031d5ba7725099abf0c7db5fde0a1d3d8bf25e5c1342370f8c6a1` |
| 镜像 | `yunhezhiyuan-aibackend-0.1.7:latest`，amd64 |
| 运行镜像 ID | `sha256:ffc024bbda0b749903ee50e8c17b86d4f31c463b60b1687768dc00fd4008bcde` |
| Native bundle | `14f6e0279a9a919a2d63e63aee73ebe5eb78693d26ce740343f47ec80a7b7864`，数据库已发布，与镜像探针一致 |
| Portal | **0.1.27**，固定源码 `485a0e3a4ae23fddd2bede4d68220aaa4038014b` |

门户维护者在本次预检期间已完成 Portal 发布。本次复用其可核对的同版本归档，并在 AI 切换后独立复核 HTTPS 上的全部 233 个文件，没有再次覆盖门户。之后门户工作区出现的其他修改不在该归档中。独立 Copilot Runtime 0.1.3、Java 0.1.9、SSO 静态 0.1.18 保持不变。

## 归档与运行位置

- AI 归档：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/local/0.1.7/`。
- 可加载镜像包：`yunhezhiyuan-aibackend-0.1.7.docker.tar`，728,870,912 字节；SHA256 为 `3e6ea9efa0daf9f00e880dce2d0910821ab309c45220aa826619e26808c36a1a`。
- Portal 归档：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-portal/local/0.1.27/portal-0.1.27.tar.gz`，3,929,759 字节；SHA256 为 `f359f5127b66de263aa020bd63033f430e297f08cec5fdea2f18040f64e50da7`。
- AI 生效目录：`/home/fengfeng/yunhezhiyuan/aibackend`；容器 `yunhezhiyuan-aibackend-0.1.7`、`yunhezhiyuan-aibackend-0.1.7-worker`。
- Portal 生效目录：`/home/fengfeng/yunhezhiyuan/www/portal`，保留稳定目录。
- 构建与本次部署脚本：`D:/Code/deploy/local-deploy/build/aibackend-local-0.1.7-20261112/`。脚本含本轮状态门槛，仅作执行证据，不应当作通用重复部署入口。

归档内保留构建日志、源文件清单、校验和及各项非敏感验证 JSON。私有配置、证书私钥和数据库备份留在 WSL 私有目录，不进入发布包。

镜像包的配置 blob 摘要是 `sha256:292851b2eae64695c194bc39bfc2ca7bf824801ffa5797554d1bdc511ac54e43`，与上述本地 legacy builder 镜像 ID 分别记录。已核对 Docker save 清单、配置语义、层身份、架构及版本标签一致，不能将两种摘要直接视为同一个值。

## 数据迁移与保留

停止的服务仅为旧 AI API／Worker。实际业务库 `xiaohe_triad_local_20261104` 从 Alembic **0012 升至 0014**，使用原 PostgreSQL 容器、卷、用户和连接配置。

备份目录：`/home/fengfeng/yunhezhiyuan/backups/aibackend-0.1.7-20261112T021038Z`，目录权限 0700；包含数据库 dump、角色信息、切换前配置及核对证据。dump SHA256：`48adcfb470e929c1081aa6b71301139e64ffb6b3a7223cd7471aa25b8dd86f66`。

备份包含 38 张表、74,920 行。在无网络、无宿主端口的专有 PostgreSQL 容器中完整恢复，逐表行数与数据摘要一致；再演练 0012→0014，核对已有列内容。演练通过后才迁移实际业务库，启动新服务前再次确认原表原列数据保持（版本行除外）。这不表示运行后的正常 Worker 维护不会更新技术状态。

切换后核对：57 个其他容器的 ID、镜像、启动时间、重启计数和挂载保持；10 个受保护配置／证书／代理相关文件保持；登记附件 bucket 的 3 个底层文件路径及内容摘要保持。未清卷、未删除其他服务、未修改密钥、SSO issuer/client、端口或代理。原运行环境变量值保持，新增原生运行时开关及固定的构建工具变量，完整清单在 `environment-verification.json` 和 `verification.json`。

## 验证结果与边界

- 发布前已记录后端非 MinIO 回归 1018 passed、15 skipped、10 deselected；独立强杀恢复 1 passed；上游 Agent／流式接口 38 passed。门户固定提交的隔离测试回执见原交接文档。本次部署没有重复运行这些完整集合。
- 镜像内 226 个冻结文件逐一匹配；实际 DeepAgents Skill 文件读取、工具循环与严格结果返回探针通过。使用现有配置及证书组装 ChatOpenAI／业务图成功，网络禁用，未调用模型。
- 新 API 健康，Worker 正常且数据库心跳新鲜；两个服务实际镜像、开关、数据库 0014 和已发布 bundle 核对通过。
- [Portal 入口](https://portal.lvh.me/portal/) 与 `version.json` 返回 200；233 个静态文件通过 HTTPS 逐一匹配归档 SHA256。证书链及主机名验证开启。
- 匿名 `https://portal.lvh.me/ai-api/v1/reception/progress` 返回预期 401，SSO Discovery 返回 200。这不代表已完成认证后的表单 API 组合验收。
- 原始证据：归档内 `backup-verification.json`、`migration-verification.json`、`native-probe.json`、`model-assembly-verification.json`、`verification.json`、`final-artifact-https-verification.json`。

真实模型语义、真人登录／短信、认证后留资全流程及正式业务后台写入仍未验证。没有发送短信或制造业务申请；本次不将健康检查、静态资源校验或隔离测试等同于真人业务验收。

## 回退边界

保留旧镜像、归档和私有备份。需要停止新 native 选版时，优先在仍支持 native 的 0.1.7 服务上关闭开关，并按固定快照排空在途 Command；关闭开关不会把已有 native Command 改成旧 DSL。保留 0014 及包快照，不能直接切回无法识别 native 状态的 0.1.5，也不能为回退覆盖恢复旧 dump 或盲目降库。数据库恢复会丢失备份后的写入，需另行评估并授权。
