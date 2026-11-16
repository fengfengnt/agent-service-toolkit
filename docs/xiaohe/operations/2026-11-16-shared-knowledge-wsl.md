# 小鹤共享知识 WSL 0.1.13 发布回执

2026-11-16：已提交、推送并发布 WSL 小鹤 API 和 worker。门户入口为 <https://portal.lvh.me/portal/>，页面右下角“小鹤”。本次未作真实浏览器登录、短信、业务对话或留资提交验收。

## 版本与制品

| 项目 | 实际结果 |
| --- | --- |
| AI | `0.1.12` → `0.1.13` |
| 镜像 | `yunhezhiyuan-aibackend-0.1.13:latest` |
| 镜像 ID | `sha256:a048185fb7c54c4734f17fe32c76edd814715f3128301dd455ef0e693c978311` |
| 冻结源码 | `0560e5b0b2322774ec3b825c3a40639bd152ef93`；后续 `fa64b68` 只修正两项测试的初始资料准备 |
| 源文件验证 | 186 个文件逐项匹配 SHA-256 |
| RuntimeBundle | `3f35ddd6e79be5bd43150f23e2c5a277446648da6b67ee1ff69a38c4d7cae554` |
| 知识版本 | `8615077b33259735867edc786843ebe5b983564f44fc9c5291497f88805b3c3d` |
| 知识内容 | 1 篇共享文档、1 个分块、1024 维；两项服务的说明继续保留“待补充” |
| Portal / SSO | 保持 `0.1.44` / `0.1.41` |
| Java | 另一会话已发布 `0.1.15`，本次保留该结果 |
| 数据库 | `0015`，结构及系统、用户、项目记录保持 |

归档目录：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/local/0.1.13/`。

- Docker 归档 `yunhezhiyuan-aibackend-0.1.13.docker.tar`：787929600 字节，SHA-256 `c2320631d4784ef5833fafaf12d582a135ba10bd66366dab9323af6e72c2e8e6`。
- 知识索引归档 `xiaohe-knowledge-8615077b33259735867edc786843ebe5b983564f44fc9c5291497f88805b3c3d.tar.gz`：18890 字节，SHA-256 `289a5817da7efe9edbfcc69111fcad78087d11bd42957f639c85e36f7fced4f9`。
- Windows 已检查镜像配置与层身份、两份实际归档摘要、知识清单及完成标记；归档不含私有 `.env` 或 Key。

## Embedding 与存储

复用已批准的小采 UAT/生产共用实例 `39.106.42.10` 活动 API 的配置：`https://api.wenwen-ai.com/v1`、`text-embedding-3-small`、1024 维，以及其实际有效 Key。源端仅只读核对，不修改小采配置或服务。凭据通过 SSH 与子进程 stdin 直接进入目标私有写入器，不进入 Git、命令行参数或公开日志；最终运行中的 API 已与源端逐项比较一致，只记录布尔结果。

WSL 私有文件为 `/home/fengfeng/yunhezhiyuan/aibackend/config/knowledge.env`，权限 `0600`、属主 `1000:1000`。只更新四项 Embedding 参数，保留其他字段。回退副本与配置基线位于受限目录 `/home/fengfeng/yunhezhiyuan/backups/aibackend-shared-knowledge-0.1.13-20261116T081731Z/`，不放入发布包。

API 独占可写卷 `xiaohe_knowledge-data`，挂载到 `/var/lib/xiaohe/knowledge`；worker 不挂载、不接收知识库专用配置。首轮构建因空卷属主为镜像默认 `10001:10001` 而失败。核对空卷和 WSL 实际用户后，仅将该卷根目录属主调整为 `1000:1000`；没有删除旧知识或业务数据。离线预检通过后，真实 Embedding 建库成功，索引和正式 manifest 一起发布。

共享正文默认进入主 Agent 与只读 subagent 的命令快照。当前索引仅含这份常驻共享资料，在线不会重复请求其 Embedding；新增专业资料后按授权和主题检索。共用 Key 会共用额度、限流和轮换影响。

## 恢复与保护检查

WSL 新命令会话曾连续超时。用户明确批准重启 Ubuntu 后，`wsl --shutdown` 最终完成恢复；尝试申请管理员重启 `WSLService` 的权限提示被取消，该管理员脚本没有执行。恢复期间另一会话也重启 Ubuntu 并发布 Java `0.1.15`。本次保护检查在停止旧 API 前捕获差异，核对另一会话回执及实际身份、挂载后保留其发布结果，保存原基线，再采集切换基线。

后续观察到缺少长期用户会话时容器重复启动。依据[微软关于 systemd 不保持 WSL 实例运行的说明](https://learn.microsoft.com/en-us/windows/wsl/systemd)，启动了隐藏的 `wsl.exe -d Ubuntu -u fengfeng --exec /usr/bin/sleep infinity` 保活进程，Windows PID 记录在构建目录 `ubuntu-keepalive.json`，本次为 `11988`。其后 API 恢复就绪并完成切换；该现象支持空闲退出的判断，不将此前所有超时都归因于此。未修改 `.wslconfig`、计划任务或开机启动项。关闭 WSL 或 Windows 会结束保活进程；下次启动本地服务需要重新保持 Ubuntu 会话运行。

最终校验：相对重新核对的切换基线，54 个其他持久容器的身份、镜像、启动状态记录及挂载保持，其中包含历史停止容器；29888 个受保护文件匹配。原配置、证书、SSO 登记、数据卷及数据库记录保留。仅切换小鹤 API 和 worker，没有迁移表结构或执行业务数据清理。

## 验证结果与边界

- API 健康、worker 心跳新鲜；严格 TLS 的门户、匿名 API 的 401 边界与 SSO Discovery/issuer 通过。
- 容器内标准 Skill 读取工具循环通过，共 21 个 Skill；完整共享正文已注入，清单与共享来源同步。
- 运行私有配置有效，实际聊天模型可装配；API 已加载共享正文、持有知识根锁，索引清单及完成标记匹配。模型装配检查没有调用真实聊天模型。
- 首次共享知识实现的相关集合为 153 passed、3 skipped。加入正式清单后，非 PG 集合为 104 passed、2 skipped、1 项资料准备失败；另一个“无清单历史版本”用例也曾在前一集合中暴露同类准备问题。两项测试现显式移除正式清单以构造其原本要求的初始状态，定向复测 2 passed。跳过项为 Windows 符号链接权限限制；未将多次集合的数量相加。
- Linux 假凭据写入检查通过：特殊字符保留、其他参数保留、`0600` 与 UID 1000 正确，非法输入不覆盖旧文件。实际 Key 接入和真实 Embedding 建库另行验证通过。
- 本次没有追加真实业务聊天、专业资料中文召回、真实登录或提交验收。健康、模型装配和真实建库分别记录，不能替代业务验收。

## 后续维护

可以在本项目的 Codex 对话中提交文案，说明是所有业务 Agent 共享，还是限定采购、社群组织或供应方向，并指定是否构建及发布到哪个环境。开发助手按[维护说明](knowledge.md)修改 Markdown、构建候选索引、验证并发布。业务页面里与“小鹤”聊天不会自动入库。

共享正文唯一文件为 `resources/xiaohe/knowledge/platform-common.md`。七天极速上线与 1v1 专属方案的文案到位后在此补充；正文可先发布，同源旧向量在未同步期间被排除。需要同时更新向量索引时，应明确要求构建索引及发布目标。
