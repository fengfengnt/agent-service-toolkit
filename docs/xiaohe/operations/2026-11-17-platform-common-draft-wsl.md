# 平台共享知识草稿整理与 WSL 0.1.14 发布

2026-11-17，按用户提供草稿整理共享知识，并继续提交、推送、建库和更新 WSL。入口为 <https://portal.lvh.me/portal/> 页面右下角“小鹤”。

## 文案与范围

- 正式文件：`resources/xiaohe/knowledge/platform-common.md`。补齐标准 YAML 元数据、四个业务 Agent 授权和 `general` 主题；正文 UTF-8 为 8031 字节，符合 8192 字节上限。
- 原始草稿约 38 KB，内容留档于 `docs/xiaohe/knowledge/platform-common-draft-2026-11-17.txt`，不进入运行资源包和向量索引。
- 合并重复问答及相近说明，保留公司、六大服务、品牌关系、七天上线、1v1、资料类别、合作流程及主要边界。整理方法见[文案说明](../knowledge/README.md)。
- 草稿中的 Word/方案描述不作为已查验来源；七天计时、固定清单、交付、费用、权益和指标仍待确认。没有将草稿建议转为正式政策或合同承诺，也未逐一核验草稿所列外部网站。
- 应用运行代码、依赖、标准 Skill、表单策略及数据库结构不变。测试中写死旧占位句的断言改为校验当前 `source.conditions` 完整注入。

## 发布身份

| 项目 | 结果 |
| --- | --- |
| WSL AI | `0.1.13` → `0.1.14`，API 与 worker 已生效 |
| 镜像 ID | `sha256:38b9be3e0a9ebbffefb9b78f9517eb1f93aa438e4e0aee3536ee3aeb7d4d2f83` |
| 冻结源码 | `ca4b3abda5f21681777ca1086981987c9bc3a089` |
| RuntimeBundle | `e3c980de50a0a6354107904a4a6856c4011b5b0ff0d9c33b3e2c8db173d37141` |
| 知识版本 | `ddc10fe32d0ceb7be151cd5457c93616092525161f45de1d69556b950cd0295a` |
| 索引 | 1 篇共享文档、4 个分块、1024 维 |
| Embedding | 沿用现有私有 URL、Key、`text-embedding-3-small` 和 1024 维；配置未修改 |
| Portal / SSO | 保持 `0.1.46` / `0.1.41` |
| 数据库 | 保持 `0015`，结构与系统、用户、项目记录未变 |

镜像在已验证的 0.1.13 依赖层上仅覆盖 `VERSION`、共享 Markdown 和 `manifest.json`；186 个实际镜像文件逐项匹配冻结清单。

归档：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/local/0.1.14/`。

- Docker 归档：787940352 字节，SHA-256 `455e53a73a2c0dadef8a8fe77e790d1078643141709591f09d14f7e9b66d7918`。
- 索引归档：60915 字节，SHA-256 `9a54a29d140809cd5c6ac12976910e47c95c8d3df6185c078d53291a08037610`。
- Windows 已验证归档摘要、镜像配置和层身份、索引清单与完成标记。归档不含私有 `.env` 或 Key。

## 验证与保护

- 实际共享源解析、Markdown 格式与相关回归通过：62 passed、2 skipped；跳过原因为 Windows 符号链接权限。主 Agent 的四角色访客及登录绑定均包含完整共享正文和当前适用条件。
- 真实 Embedding 建库成功；Linux 在隔离副本验证 4 条记录、授权元数据、正文摘要、1024 维向量和 Chroma 检索。检查未打开线上索引，没有追加在线 Embedding 请求。
- 容器内 21 个标准 Skill 的读取工具循环、完整共享正文注入和同源索引同步检查通过。
- API 健康、worker 心跳、严格 TLS 门户、匿名 API 401 边界、SSO Discovery/issuer 均通过；实际聊天模型可装配，API 已加载新正文、持有知识根锁且索引清单匹配。装配检查未调用真实聊天模型。
- 相对切换基线，54 个其他持久容器及 31007 个受保护文件保持，包含私有配置、证书与其他站点；保留原数据卷和旧知识版本，未重启整个 WSL。
- 发布备份及私有基线位于 `/home/fengfeng/yunhezhiyuan/backups/aibackend-knowledge-draft-0.1.14-20261117T081302Z/`。本次没有表结构或破坏性数据修改，没有覆盖数据库。

当前正文由主 Agent 和只读 subagent 默认加载；同源向量按设计排除重复检索。新命令使用新 Bundle，已绑定的历史命令保持原快照。

未进行真实用户登录、短信、业务聊天或留资提交验收。服务条款与展示指标仍需业务负责人确认；共用 Embedding Key 的额度、限流和轮换影响保持原有约定。
