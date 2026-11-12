# 小鹤交接与留资入口修复：WSL 发布记录

日期：2026-11-12。AI **0.1.9**、门户 **0.1.31** 已发布本地 WSL Ubuntu；版本由用户指定。SSO 静态页面保持 **0.1.30**。未部署 UAT／生产，未推送 Git。

## 内容与来源

取消新回复的强制“一般建议：”前缀，规范 br 换行；欢迎身份按钮通过受控动作落实方向，右上显示对应专家，页脚统一提供“注册/登录”。咨询结尾改为“您可以进行咨询或者留下您的信息”，留资入口先完成认证，再按对应 Agent 的标准 Skill 引导。取消认证后可恢复，访客额度耗尽仍能进入认证；入口点击不是正式提交同意。

| 项目 | 本次构建与部署 |
| --- | --- |
| AI 构建 HEAD | `3054b71321f984461c24413ba0101c573e1e62fb`；VERSION 按授权改为 0.1.9，运行输入冻结为 178 文件 |
| AI 输入指纹 | `7684c7633cba393d49633f6380e2faa88b1d67609370594a18d52d53b1ec5e8f` |
| AI 镜像 | `yunhezhiyuan-aibackend-0.1.9:latest`，amd64 |
| 运行镜像 ID | `sha256:a8062870ed0192ae971ff294c90191e3f9c9912df4f7301ad7f85b425c5a206d` |
| 标准 Skill 包 | 21；native bundle `d1f4dc46548cc60a768082f97d0bc384ac529cf62589fa40701ab49ccaeaa2a4` |
| 门户构建 HEAD | `ae8d0933f5614a272355460c98046c7e70b50382`，包含已部署但尚未提交的认证源码及版本文件修改 |
| 门户输入指纹 | `51f1530289edffd4281a8e64cab4d25b559b5dd8769181dafe303f6a27e8ebf3` |
| 门户归档 | `portal-0.1.31.tar.gz`，234 文件 |
| SSO／业务后端／独立 Runtime | 本次不更新；SSO 静态保持 0.1.30 |

门户 13 份认证相关源码与已部署 0.1.30 的输入清单逐一匹配，保留协议、手机号提示和回调诊断。源码工作区的其他修改保持原样，构建取自冻结副本；没有把门户包覆盖到 SSO 站点。

## 归档与校验

- AI：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/local/0.1.9`；镜像包 729,840,128 字节，SHA256 `8d88158ae8c1b6906784be4fcf840641d06b59911def68ab6e29fc2d27efde80`。
- 门户：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-portal/local/0.1.31`；静态包 SHA256 `b7e1474bd0a43d58b8cf4373db19a7262e164d8c5f08e137e310867acacf9147`。
- AI 镜像输入、Skill 文件读取与工具循环探针通过；真实配置的 ChatOpenAI 组装通过，探针未调用真实模型。
- WSL 读取大镜像归档摘要时曾返回 ENOMEM，切换尚未开始；改在 Windows 完整校验 SHA256、Docker 归档配置及层身份，校验结果绑定实际运行镜像。没有覆盖镜像包或跳过校验，没有重启 WSL。

## 切换与保留

运行目录 `/home/fengfeng/yunhezhiyuan/aibackend`，API／Worker 容器 `yunhezhiyuan-aibackend-0.1.9`／`yunhezhiyuan-aibackend-0.1.9-worker`。门户仍在 `/home/fengfeng/yunhezhiyuan/www/portal`，先写资源、最后原子替换版本文件和入口，保留旧哈希资源。

备份：`/home/fengfeng/yunhezhiyuan/backups/chat-entry-0.1.9-0.1.31-20261112T070339Z`（0700），包含原门户、VERSION、Compose 与来源记录及核对基线。旧镜像与归档保留。数据库迁移文件与原运行版逐一相同，实际结构及 Alembic 仍为 **0015**；本次没有执行数据清理或 DDL，也没有制作数据库 dump。系统／用户／项目摘要保持，运行环境变量和受保护文件保持。

切换前门禁捕捉到并行任务的临时 Go 构建退出，以及临时测试容器增减；旧服务当时尚未停止。原始基线保留，复核实际服务、配置与站点未变后，将自动删除容器和无重启策略、非 Compose 服务的 `codex-*` 测试容器单独排除。发布后 **54 个其他持久容器**及 **10,248 个受保护文件**核对保持，未停止或删除这些测试容器，也未操作共享服务、持久卷或既有备份。

## 实测结果与访问

- API 健康、Worker 新鲜心跳、实际镜像与 0.1.9 文件身份一致，native bundle 已发布。
- [门户](https://portal.lvh.me/portal/) 全部 **234 个文件**经 HTTPS 状态及 SHA256 核对；证书链和主机名验证开启。SSO 版本 0.1.30、Discovery issuer、匿名进展 API 401 符合预期。
- Chrome 在真实 HTTPS 页面验证三个欢迎身份选择依次显示采购专家、供应商专家、组织社群专家；页脚显示“注册/登录”，新回复没有固定前缀／br 字面标签，显示可点击留资入口。
- 点击“留下您的信息”进入真实手机号认证卡片。动作序列为三次 `switch_direction` 和一次 `accept_invitation`；页面错误 0，受保护业务／短信请求尝试 0。本轮只建立合成访客会话及认证准备状态，没有填写或发送手机号／验证码。
- 浏览器前两轮分别在认证卡片、方向更新等待时超时，未定位为确定的服务错误；随后两轮完整通过，末轮明确等待每次转接状态回到“在线”再继续。保留这一验证边界，不将两轮成功称为长期稳定性验收。
- 界面截图及实测 JSON 见门户归档的 `live-chat-expert.png`、`live-lead-login.png`、`browser-verification.json`；服务与保护证据见两份归档中的 `verification.json`。

实现阶段回归与独立审阅见[验证记录](D:/Code/yunhezhiyuan-agents/agent-service-toolkit/docs/testing/2026-11-12-chat-entry-ux.md)。本次没有重复运行全部单测集合，构建和部署验证使用同一份冻结代码。

真实模型自然语言答疑／交接、真实会员登录、短信和正式资料提交仍待验收；此前真实模型合成探针曾在 TLS 读取阶段超时，本次没有把按钮交接或模型组装的通过当作真实模型验收。
