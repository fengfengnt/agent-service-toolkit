# 小鹤仅保留标准 Skill：WSL 0.1.8 发布记录

日期：2026-11-12。环境：本地 WSL Ubuntu。按用户指定版本 0.1.8 发布；用户明确授权丢弃旧业务数据。未推送 Git，未部署 UAT／生产。

## 交付内容

小鹤仅保留标准 Skill／DeepAgents 执行链：21 个标准包（18 个问答技能、3 个留资技能）、业务节点绑定、固定快照恢复和字段策略。旧嵌套目录、catalog、DSL、旧执行器、别名及兼容开关已删除。主业务 Agent 统一保存／提交，child 保持只读。

| 项目 | 本轮实测 |
| --- | --- |
| 源码提交 | `72f420ffe120cf0e73cd985112ff45d7977691ad`，`dev-fengfeng` |
| 冻结输入 | 178 文件；SHA256 `e3381e62d852994b58ad1ab030aa0964c04eebea7c486f53d129afaed8a18eff` |
| 镜像 | `yunhezhiyuan-aibackend-0.1.8:latest`，amd64 |
| 运行镜像 ID | `sha256:11a297f464df6da3451ebce8caa35285efa456ee6ec376edb34331e55bb6d5fe` |
| 原生包指纹 | `14673b643242a8971bfb25d5330eb8be01b990764a505544867493ffd4f21ed0` |
| 数据库 | `xiaohe_triad_local_20261104`，0014→0015 |
| API／Worker | `yunhezhiyuan-aibackend-0.1.8`／`yunhezhiyuan-aibackend-0.1.8-worker` |
| 生效目录 | `/home/fengfeng/yunhezhiyuan/aibackend` |
| 门户 | 保留维护者已发布的 0.1.30，本次未覆盖静态目录 |

归档：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/local/0.1.8/`。镜像包 `yunhezhiyuan-aibackend-0.1.8.docker.tar`，729,354,752 字节，SHA256 `ee07ed30955c63389116daecc919b217e11398e7f983f1e2b60e150cfbc55a6b`。已核对归档清单、配置语义、层身份及运行镜像，保留旧版归档。

## 清理范围与保护

停止小鹤 API／Worker 后再次检查无新增 native Run／Command、无受理或待核实提交的附件引用、无独立表单提交，再显式清理。实际清理清单包含 **55 个会话、11 个 Run、6 个 claim、61 个归属线程标识、3 个对象和 51 个队列任务**；关联旧消息、事件、草稿、附件元数据和 checkpoint 同步删除。线程标识清单包含无 checkpoint 的会话，不将其计数解释为实际 checkpoint 行数。

身份、认证、额度逐表摘要保持；归属外对象保持，目标对象删除后逐一验证不存在。0015 移除旧 workflow 表和旧绑定列，并要求 native bundle／字段策略绑定。正常应用启动不执行清理。

首次切换的对象版本核验遭 MinIO `AccessDenied`，删除尚未开始即恢复旧 API／Worker。诊断后使用仅允许小鹤桶 `GetBucketVersioning`、`ListBucket`、`GetObject`、`DeleteObject` 的临时维护账号完成清理，随后撤销账号与策略并删除私有凭据文件；原应用账号权限未改变。

未新建旧业务数据 dump 或旧数据迁移档案。切换前配置及受限核对清单保留在 `/home/fengfeng/yunhezhiyuan/backups/aibackend-standard-only-0.1.8-20261112T051821Z`（0700）；不进入发布包。既有历史备份、其他业务系统和原持久卷保留。应用环境仅移除已删除的 `XIAOHE_DEEPAGENTS_ENABLED`，57 个其他容器及 10 个受保护文件核对保持；未重启 WSL／共享服务。

## 验证与访问

- 后端非 MinIO 回归 **701 passed、1 skipped、10 deselected**；welcome／实际强杀恢复／部署契约 **17 passed**；上游 Agent／流式接口 **38 passed**。测试范围见[验证记录](../testing/xiaohe-standard-only.md)。
- 镜像内标准包加载、Skill 读取、DeepAgents 工具循环、严格返回及真实配置模型组装通过；隔离空库升级 0015 和 native 资源发布通过。
- 实际 API 健康、Worker 新鲜心跳、镜像身份、0015 和发布的 native bundle 一致。
- [门户入口](https://portal.lvh.me/portal/) 及版本文件 200；Portal 0.1.30 的 235 个文件严格 HTTPS 摘要匹配，证书链和主机名验证开启。
- [进展 API](https://portal.lvh.me/ai-api/v1/reception/progress) 匿名返回预期 401；SSO Discovery 200。
- HTTPS v2 匿名新会话与重复打开保持同一会话／欢迎消息，消息额度为 0；仅为此新增一个测试访客会话。运行容器三方向独立表单策略读取通过，采购／组织／供应当前分组数为 4／4／5。

归档内 `verification.json`、`cleanup-verification.json`、`smoke-verification.json`、`archive-https-verification.json` 记录实测结果，不含凭据或业务资料。

未验证真实模型推理、会员登录、短信或正式业务提交；未运行完整 MinIO 集成集合。健康、匿名会话和字段策略读取不等同于真实留资验收。旧业务数据已按授权删除，不能直接切回只支持旧结构的 0.1.7；恢复历史备份需另行评估新写入及授权。
