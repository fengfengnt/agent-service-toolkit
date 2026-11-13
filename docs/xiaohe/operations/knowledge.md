# 小鹤内嵌知识库维护

四个业务 Agent 只读知识：接待 `xiaohe-reception`、采购 `xiaohe-buyer`、社区组织 `xiaohe-organization`、供应 `xiaohe-supplier`。可以在本项目的 Codex 开发对话中提供需要维护的内容，由开发助手编辑文档、构建和验证；与业务 Agent 聊天不会自动入库或发布。

知识源是 Markdown，ChromaDB 是可重建的索引。标准 `skills/<name>/SKILL.md` 继续提供技能说明和兜底，业务节点通过 DeepAgents 使用技能与授权知识；无资料时保持说明待确认的行为。

## 1. 编写知识

在 `resources/xiaohe/knowledge/` 中新增或修改 UTF-8 `.md` 文件。删除文档后也要构建并发布新版本；单独改文件不会改变线上索引。

以下仅为格式示例，**不是可发布的真实公司政策**：

```markdown
---
id: procurement-guide
title: 采购咨询说明
source: 已审定采购服务说明
conditions: 仅适用于已确认的服务范围，具体时效需另行确认。
agents:
  - xiaohe-buyer
topics:
  - buyer-rfx
---
在这里填写已经确认可对客的业务说明。
```

- `id` 是稳定文档 ID：小写字母开头，后续仅小写字母、数字、下划线、连字符，总长最多 64 字符。
- `source` 是可展示的来源名称或公开地址，不放凭据、私有文件路径、内网地址或未公开材料名称。
- `agents` 必须显式授权，不使用“全部”通配符。共享资料逐个列出 Agent。
- 专业 `topics` 使用 `deep-runtime.yaml` 中该 Agent 已绑定的标准技能名称，如 `buyer-rfx`；不能通过知识文件增加技能或业务动作。
- 平台介绍等通用资料使用 `topics: [general]`。接待仅允许此主题；如共享给四个 Agent，显式列出四个 ID。
- 正文不能为空，不接受未知字段、重复 ID、重复 YAML 键、YAML alias 或越界链接。单文件和清单各不超过 1 MiB。
- 同主题知识尽量简明。首期按审定主题标题检索，不把用户原话、联系信息、公司字段或附件发送给 Embedding；同主题的细粒度问题仍需实际评测，不保证任意 FAQ 都能命中。

模板放在本说明或测试目录，不要把虚构例子放入正式知识源。首次发布必须由维护者提供/确认真实资料；开发助手不能自行编造政策、价格或案例。

## 2. 配置 Embedding

在私有环境中配置，不提交 `.env` 或密钥：

| 配置键 | 含义 |
| --- | --- |
| `XIAOHE_KNOWLEDGE_ROOT` | 已发布索引根目录；Compose 中 API 使用 `/var/lib/xiaohe/knowledge` |
| `XIAOHE_KNOWLEDGE_EMBEDDING_BASE_URL` | 明确支持 Embedding 的 HTTP(S) endpoint，可以包含 `/v1` |
| `XIAOHE_KNOWLEDGE_EMBEDDING_MODEL` | 明确的 Embedding 模型 ID |
| `XIAOHE_KNOWLEDGE_EMBEDDING_API_KEY` | 该服务的私有凭据 |
| `XIAOHE_KNOWLEDGE_EMBEDDING_DIMENSIONS` | 正整数，必须与服务实际向量维度一致 |

Embedding 与聊天模型独立，不能根据聊天模型能工作就判定 Embedding 可用。缺少任一必要字段时运行端禁用知识检索，仍使用原有资料；构建命令会报错，不生成候选清单。endpoint 不接受 URL 内凭据、query 或 fragment，不自动切换模型、下载模型或重试收费请求。

配置齐全时，API 在启动阶段初始化本地 Embedding 客户端，不发送 Embedding 请求；查询仍保留独立的 2 秒预算，避免首次查询承担客户端初始化开销。

文档正文会在入库时发送给配置的 Embedding 服务，因此知识源仅放已确认可对客的资料；不要将用户上传附件、申请材料或会话导入。测试使用本地受控向量服务，不代表真实服务已验收。

## 3. 构建候选版本

使用 Python 3.12–3.14、项目固定的 `uv==0.13.5`。在 PowerShell 中从仓库根目录执行，预先设置上述私有 Embedding 变量：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
uvx --from uv==0.13.5 uv run --frozen --extra rag python -m xiaohe.knowledge build --resources resources/xiaohe --index-root .local/xiaohe-knowledge --manifest-out .scratch/knowledge-candidate.json
```

本地命令不自动读取项目 `.env`；需要的字段从当前进程环境提供。在容器内已有 `PYTHONPATH=/app/src`。

构建器先校验元数据、授权及向量，再在独立子进程写新 staging 目录。子进程退出并释放文件句柄后发布为：

```text
<knowledge_root>/<64 位知识版本>/
  manifest.json
  complete.json
  chroma.sqlite3
  ...Chroma 管理的索引文件
```

版本包含正文分块摘要、来源、授权、分块参数、模型、endpoint 身份摘要和维度。模型或 endpoint 改变也要构建新版本。相同版本只复用通过完成标记与清单校验的产物，不覆盖损坏目录；每次查询还会复核命中文本和授权。

同版本复用必须先退出持有知识目录的 API 进程。构建器取得独占锁后，将现有版本复制到临时目录，在独立子进程中检查实际记录、正文摘要、授权、向量和检索结果；验证进程退出后才删除副本，不打开或改写原版本。索引损坏或目录正在使用时返回失败，不导出成功候选；不同的新版本仍可在 API 查询旧版本时构建。

成功输出版本、文档数、分块数和 `published: false`，表示**候选构建成功，尚未切换运行资源包**。失败不覆盖在线目录，也不替换候选清单；不要把遗留 `.staging-*` 目录当作已发布版本。

发布到 Linux 时在目标平台或同版本兼容构建容器中构建并检查，不将 Windows 索引未经验证直接复制上线。可使用候选镜像覆盖 ENTRYPOINT 为 `python -m xiaohe.knowledge build`，绑定新的文档资源和候选清单输出目录，索引放对应环境的知识卷；该构建进程只写新目录，不打开正在查询的旧目录。候选镜像及环境更新沿用项目现有发布流程。

删除全部 Markdown 会产生合法空版本；发布该清单后，新运行回到基础资料，旧版本继续保留。缺失的知识目录同样视为空知识集，所以构建后务必检查文档数，避免误用错误路径。

## 4. 验证并发布

1. 审阅候选清单对应的文档、来源、适用条件和授权变化。用每个 Agent 的正反例验证召回、错误主题不命中及引用来源；不要只看“构建成功”。
2. 将已确认的候选清单复制为 `resources/xiaohe/knowledge/manifest.json`，使它进入新的标准 `RuntimeBundle`。Markdown 和清单一起版本管理，索引目录不进 Git/镜像。
3. 明确发布目标环境，按现有部署流程准备匹配的资源包、镜像及知识索引。处理在途执行并暂停 worker 的新调度，避免更新时仍有运行写入。
4. 更新并重启真正执行 Agent 的 API；它挂载可写知识卷并保持单进程。确认 API 就绪，再恢复 worker 调度。**只重启 worker 不会更新 API 缓存的 Bundle。**
5. 用实际新命令检查运行快照中的知识版本及回答来源，记录目标环境已生效；不能将健康检查等同业务知识验收。

标准运行时以命令为边界绑定 `RuntimeBundle`：同一命令的重试和交接继续读取原快照，已检索资料按 Agent 缓存在命令执行状态中；切换 Agent 时重新按目标 Agent 授权检索。新命令使用当前发布的资源包，即使继续处理已有草稿，也可能使用新版知识；草稿字段策略仍按既有标准流程处理。旧索引缺失或检索超时时使用该命令绑定的标准技能，不偷偷换到新知识。

运行端最多取 3 条资料，查询预算为 2 秒。超时或取消后，实际后台查询未结束时新查询立即降级，不积压任务。初始 cosine distance 门槛为 0.5，真实模型和资料需要评测校准；未命中时明确待确认。

回答的“资料来源”仅列本次实际引用的公开来源和适用条件。完整知识版本保留在运行快照与命令执行状态中，不把内部版本摘要展示给用户；未提供的来源 ID 无法通过输出校验。

## 5. 内嵌存储约束与回退

单个 API 进程持有根目录的 `.owner.lock`，直到该进程退出。不要配置多个 API 副本/Uvicorn workers 共享同一路径；第二个宿主会禁用自己的知识检索并记录原因。worker 不挂载、不打开索引，检索不新增 HTTP/MCP 服务。

Chroma 即使只查询也可能维护本地文件，因此 API 的知识卷必须可写；容器其他根文件系统仍只读。镜像以 `10001:10001` 运行，首次挂载应检查卷权限，不使用宽泛的 `chmod 777`。

回退恢复原资源包/镜像与对应清单，协调 API 重启和 worker 调度；新运行重新绑定回退版本，不修改已有运行/草稿记录。**保留所有仍被引用的旧索引**，本期没有自动清理命令。

备份保存知识源、清单及无密钥的模型标识/维度。索引可由相同配置重建；复制索引文件前停止 API 查询进程，避免不一致快照。不要在在线目录运行旧的 `scripts/create_chroma_db.py`，它默认删除旧库。

## 6. 检查与维护回执

```powershell
uvx --from uv==0.13.5 uv run --frozen --extra rag pytest tests/xiaohe/test_knowledge_sources.py tests/xiaohe/test_knowledge_index.py tests/xiaohe/test_knowledge_runtime.py tests/xiaohe/test_knowledge_workflow.py tests/xiaohe/test_knowledge_deployment.py -m "not pg and not minio" -q
```

PG 版本恢复测试仅使用独占、库名以 `test_xiaohe_` 开头的测试库。`pg_factory` 会迁移并清空测试表，禁止指向业务库或他人共享测试库。

维护回执分别记录：修改了哪些资料和授权、候选版本及测试结果、是否已发布到哪个环境。区分源码测试、真实 Chroma、隔离 PG、容器权限、真实 Embedding 和真实业务知识验收；没有执行的检查必须明确标注。
