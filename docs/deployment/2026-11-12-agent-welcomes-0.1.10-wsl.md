# 小鹤业务 Agent 迎接词：WSL AI 0.1.10 发布

日期：2026-11-12。状态：已提交、推送并发布本地 WSL。仅更新 AI API／Worker；门户 **0.1.34**、SSO 静态 **0.1.30** 保持。未部署 UAT／生产。

## 本次变化

选择采购商、社群组织或供应商后，由对应助手友好介绍服务能力，替换“已进入当前服务方向，您可以继续咨询或填写资料。”。采购介绍需求、成本、供应商筛选与智能寻源；组织介绍会员服务、供需撮合、AI 应用和组织合作；供应商介绍产品、供货能力、AI 能力提升及采购／商机对接。原有留资链接、方向权限与提交确认保持。

迎接词集中在 `src/xiaohe/deep_runtime/runtime.py`，按当前已验证的 Agent 绑定选择；不额外调用模型或创建留资草稿。三方向回归先复现缺少能力介绍，再修复通过；相关 **20 项测试**、Ruff 和 diff 检查通过。

## 来源、归档与身份

| 项目 | 内容 |
| --- | --- |
| 迎接词提交 | `4de3929` |
| 构建／版本提交 | `05e856449cab447633f1c88c35e0a4a86b8a9b76`，VERSION=0.1.10 |
| 分支 | `dev-fengfeng`，推送到 `origin/dev-fengfeng` |
| 冻结输入 | 178 文件，仅 VERSION 与迎接词模块不同于 0.1.9 |
| 输入 SHA256 | `cba930417f4150dbd2c1d2ad404c3ecc5975df8f319ad6b75d6607b625865977` |
| 镜像 | `yunhezhiyuan-aibackend-0.1.10:latest`，amd64 |
| 运行镜像 ID | `sha256:73464aead1bd9bfaac83e417104a48d8696fa0ef952e012a4a7737d4e2cb7a2a` |
| 镜像包 | `yunhezhiyuan-aibackend-0.1.10.docker.tar`，729,850,880 字节 |
| 镜像包 SHA256 | `3ea3157b4f6176fe5b5b29581dc19ba5ce21676d4fe774f4a5eacd9acdf33efb` |

归档：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/local/0.1.10`。已完整核对包摘要、Docker 配置及层身份；178 个镜像内文件逐一匹配冻结清单。21 个标准 Skill 的读取与工具循环、镜像内三套迎接词检查通过；真实配置模型组装通过，没有调用真实模型。

## 切换与保护

运行目录 `/home/fengfeng/yunhezhiyuan/aibackend`；容器 `yunhezhiyuan-aibackend-0.1.10` 和 `yunhezhiyuan-aibackend-0.1.10-worker`。只切换这两个服务，保留原环境变量、证书、密钥、SSO 配置、端口和数据卷。

配置备份：`/home/fengfeng/yunhezhiyuan/backups/aibackend-greetings-0.1.10-20261112T150900Z`（0700），旧版 0.1.9 镜像及归档保留。数据库结构和 Alembic **0015** 不变，没有执行数据清理或 DDL，没有制作数据库 dump；身份记录摘要保持。**54 个其他持久容器**和 **12,562 个受保护文件**核对保持，包括门户与 SSO 静态文件。并行任务的自动删除容器及无重启策略、非 Compose 的 `codex-*` 测试容器不计入持久服务核对，未操作这些容器。

## 实际验证

- API 健康、Worker 新鲜心跳、运行镜像及版本文件一致；HTTPS 证书链和主机名验证开启。
- Chrome 从[门户](https://portal.lvh.me/portal/)选择三种身份，分别核对当前专家及对应能力迎接词，确认不再返回通用方向切换提示；原留资链接仍可用。
- 三类实际截图为归档中的 `welcome-1.png`、`welcome-2.png`、`welcome-3.png`；`browser-verification.json` 记录页面错误 0、受保护写入尝试 0。
- `verification.json`、`archive-verification.json`、`image-input-verification.json` 和 `native-probe.json` 保存服务、保护和构建证据。

既有历史消息不改写。本次未验真实模型问答、真人登录／短信及正式业务提交；没有把静态迎接词验证当作这些业务验收。
