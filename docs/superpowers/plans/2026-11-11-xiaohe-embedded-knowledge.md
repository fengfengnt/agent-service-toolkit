# 小鹤内嵌知识库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为小鹤四个业务 Agent 接入可手动维护、版本固定、按 Agent 授权的内嵌 ChromaDB 知识库，保留现有 Markdown 资料和兜底行为。

**Architecture:** Markdown 为源，离线进程构建不可变索引及清单，清单进入现有 `WorkflowBundle.resources`。实际执行 Agent 的单进程 API 持有内嵌检索器，worker 仍只调度；回答节点合并授权知识与原有技能资料。缺少配置、旧 Bundle 或检索失败时沿用原行为。

**Tech Stack:** Python `>=3.12,<3.15`、Pydantic、PyYAML、现有 `rag` extra 中的 Chroma、`langchain_openai.OpenAIEmbeddings`、LangGraph、pytest、现有 PostgreSQL 隔离测试。

**Spec:** [已批准设计](../specs/2026-11-11-xiaohe-embedded-knowledge-design.md)。执行前同时阅读设计与本计划。

## Global Constraints

- 中文名称使用“小鹤”，代码标识符保留 `xiaohe`；新增文档默认简体中文。
- 内嵌 ChromaDB，不增加独立知识库服务；只服务接待、采购、社区组织、供应四个 Agent。
- Markdown 保留技能说明和基础兜底答复；业务 Agent 只读知识，聊天不构成入库或发布。
- Agent 执行宿主是 API 进程；worker 不打开索引。不配置多个 Uvicorn workers 或 API 副本共享同一路径。
- 不修改旧 Bundle 的摘要算法，不覆盖在线索引，不自动删除旧版本，不隐式使用全局“最新版本”。
- 首期只导入 Markdown；不添加管理 UI、热更新、抓站、租户私有知识或外部检索 API。
- 不把原话、联系方式、公司字段、历史正文或附件发送给 Embedding 服务；仅使用审定主题标题查询。
- 测试使用确定性向量，不请求收费模型；真实业务知识、Embedding 联通和目标环境发布单独验收。
- 当前有其他任务的未提交修改；执行前重查 `git status`，逐块核对重叠的 `nodes.py` 等文件，不能重置、整体覆盖或提交他人修改。

## Review Focus

1. 接待 `skills: []`，仍能查询获授权的 `general` 资料，且离题/固定开场不检索：Task 4。
2. 删除最后一份资料、构建崩溃或 Windows 文件句柄未释放，不能覆盖在线目录或留下可发布的半成品：Task 2。
3. 查询超时或调用方取消后后台线程尚未结束，不能释放互斥后继续堆积查询：Task 3。
4. 旧运行、旧草稿、回退版本及相同模型名对应不同 endpoint，不能暗中换成新索引：Tasks 1、3、4。
5. 构建完成后伪造元数据、正文、来源或授权，不能将检索命中当作可信输入：Tasks 1、3、4。

## 文件与接口地图

| 文件 | 职责 |
| --- | --- |
| 新增 `src/xiaohe/knowledge/__init__.py` | 仅包标记，不急切导入 Chroma |
| 新增 `src/xiaohe/knowledge/sources.py` | 源文件、清单、分块及引用模型，确定性摘要和校验 |
| 新增 `src/xiaohe/knowledge/build.py`、`__main__.py` | 离线构建、子进程生命周期、校验及原子发布命令 |
| 新增 `src/xiaohe/knowledge/runtime.py` | 单进程目录所有权、限时检索、双重授权和降级 |
| 修改 `src/xiaohe/workflows/loader.py` | 只加载已发布的可选知识清单到 Bundle |
| 修改 `src/xiaohe/settings.py`、`main.py` | 私有配置与 API lifespan 装配 |
| 修改 `src/xiaohe/agents/runtime.py`、`nodes.py`、`semantic.py`、`src/xiaohe/graph.py` | 把同一个检索器传给新旧 Bundle 的执行器，合并材料、保留引用 |
| 修改 `pyproject.toml`、`uv.lock`、`docker/Dockerfile.xiaohe`、`deploy/xiaohe/compose.yml`、`env.example`、`.env.example`、`.gitignore`、`.dockerignore`、`.github/workflows/test.yml` | 可选依赖、专用数据卷、配置示例及自动测试 |
| 新增 `docs/xiaohe/operations/knowledge.md` | 文档更新、构建、发布、回退和备份步骤 |
| 新增 `tests/xiaohe/knowledge_support.py` 及四个 `test_knowledge_*.py` | 虚构资料、确定性 Embeddings、源/索引/检索/工作流测试 |

不修改模板 `src/agents/tools.py`、旧 `scripts/create_chroma_db.py`、旧接待 `KnowledgeQuery` 或业务操作流程。不新增数据库迁移。生成索引放在源目录之外，默认不提交；只有明确发布的清单随资源包提交。

## Task 1：确定知识源、清单与 Bundle 版本契约

**Files:** 新增 `src/xiaohe/knowledge/__init__.py`、`sources.py`、`tests/xiaohe/knowledge_support.py`、`test_knowledge_sources.py`；修改 `src/xiaohe/settings.py`、`workflows/loader.py`、`tests/xiaohe/test_workflow_loader.py`、`test_triad_runtime.py`。

**Interfaces:**

- `KnowledgeSource`：严格模型，字段 `id, title, source, conditions, agents, topics, text`；`id` 为 `[a-z][a-z0-9_-]{0,63}`，其余长度沿用现有资料边界。
- `KnowledgeChunk`：`id, document_id, text`，`id` 为不包含版本的 `<document_id>:<chunk_index>`；`KnowledgeEntry` 保存对应正文 SHA-256 和可信来源/授权元数据。
- `KnowledgeManifest`：`format_version=1, version, embedding_model, embedding_identity, dimensions, chunk_size, chunk_overlap, entries`。`entries` 按 chunk ID 索引 `KnowledgeEntry`；不含正文或密钥。
- `KnowledgeMaterial(KnowledgeHit)`：沿用 `text, source, version, conditions`，新增 `display_source: str`；`source` 是机器引用 ID，`display_source` 是可展示来源。
- `load_sources(root: Path, allowed_topics: Mapping[AgentId, set[str]]) -> list[KnowledgeSource]`；`prepare_manifest(sources: Sequence[KnowledgeSource], *, embedding_model: str, embedding_identity: str, dimensions: int) -> tuple[KnowledgeManifest, list[KnowledgeChunk]]`。
- `read_manifest(text: str) -> KnowledgeManifest`；`manifest_from_bundle(bundle: WorkflowBundle) -> KnowledgeManifest | None`。
- 测试辅助 `write_knowledge_source(root: Path, *, id: str = "policy", agents: tuple[str, ...] = ("xiaohe-buyer",), topics: tuple[str, ...] = ("buyer.rfx",), text: str = "仅用于测试的虚构政策。") -> Path`；`DeterministicEmbeddings(Embeddings)` 支持三维同步/异步向量，不访问网络。
- Settings 新字段为 `XIAOHE_KNOWLEDGE_ROOT`、`XIAOHE_KNOWLEDGE_EMBEDDING_BASE_URL`、`XIAOHE_KNOWLEDGE_EMBEDDING_MODEL`、`XIAOHE_KNOWLEDGE_EMBEDDING_API_KEY`、`XIAOHE_KNOWLEDGE_EMBEDDING_DIMENSIONS`；Python 属性为小写去掉 `XIAOHE_` 前缀，默认 None，key 使用 `SecretStr`。endpoint 允许 `/v1` 等路径，拒绝 URL 内凭据、query、fragment；配置的 dimensions 必须为正整数。所需字段不齐时不构建客户端，知识保持禁用，不隐式继承聊天模型配置。

- [x] **Step 1：写失败测试。** `test_knowledge_sources.py` 覆盖 front matter/重复 YAML 键/未知字段/非法 Agent/主题错配/重复 ID/空正文/路径与链接越界；单文件及序列化清单不超过现有 `MAX_RESOURCE_BYTES=1 MiB`。专业主题必须归属至少一个获授权 Agent，接待仅支持 `general`；共享文档的专业命中仍按当前 Agent 自身主题过滤。

```python
def test_content_acl_and_embedding_identity_change_version(tmp_path):
    # 构造内容、agents 或 embedding_identity 各自改变的四份输入。
    assert len({original.version, changed_text.version, changed_acl.version, changed_endpoint.version}) == 4
    assert repeated.version == original.version
```

另在 `test_workflow_loader.py` 固定无知识清单的 Bundle 摘要与旧值一致；添加/替换 `knowledge/manifest.json` 才改变 Bundle，单改 `.md` 不改变已发布 Bundle。伪造版本、路径和超大清单必须拒绝。

- [x] **Step 2：运行失败测试。** `uv run --frozen pytest tests/xiaohe/test_knowledge_sources.py tests/xiaohe/test_workflow_loader.py tests/xiaohe/test_triad_runtime.py -q`；预期因缺少知识模块/清单加载逻辑失败，不能接受环境导入失败替代行为失败。配置测试覆盖缺字段不启用、非法 endpoint/维度和凭据不泄露。
- [x] **Step 3：实现上述模型、配置及纯函数。** 复用已有 YAML 安全加载限制和资源路径校验，不重构公共加载器。按相对路径排序，正文只规范化 BOM/换行；使用 `langchain_text_splitters.RecursiveCharacterTextSplitter`，`chunk_size=1200, chunk_overlap=200`。知识版本为排除自身 `version` 字段后的规范 JSON SHA-256，包含每块正文摘要及全部授权/来源。`embedding_identity` 为无凭据的规范 endpoint 与 model 的摘要；同名模型换 endpoint 产生新版本。清单和 Chroma 都使用不含版本的 chunk ID，因此无摘要循环依赖；运行时引用 ID 再组合为 `kb:<version>:<document_id>:<chunk_index>`。空知识集允许生成合法的空版本，用于撤下全部资料。
- [x] **Step 4：复跑 Step 2，预期全部 PASS。** 验证旧 Bundle 序列化格式无需迁移，清单进入 `resources["knowledge/manifest.json"]`，未发布源文件不被自动加载。
- [x] **Step 5：仅暂存本任务自己的改动并提交。** 消息：`feat: add versioned knowledge source contracts`。

## Task 2：离线构建并原子发布内嵌索引

**Files:** 新增 `src/xiaohe/knowledge/build.py`、`__main__.py`、`tests/xiaohe/test_knowledge_index.py`；修改 `pyproject.toml`、`uv.lock`，补充 `knowledge_support.py`。

**Interfaces:** 消费 Task 1 的清单与分块；`build_index(manifest: KnowledgeManifest, chunks: Sequence[KnowledgeChunk], directory: Path, embeddings: Embeddings) -> None` 仅在专用构建子进程调用；`publish_index(staging: Path, root: Path, manifest: KnowledgeManifest) -> Path` 只处理已退出子进程的完整目录。

维护命令确定为 `python -m xiaohe.knowledge build --resources resources/xiaohe --index-root <目录> --manifest-out <候选清单路径>`。源目录固定为 `--resources` 下的 `knowledge/`，输出候选清单不隐式覆盖活动 `manifest.json`。内部子命令 `_build` 只承载子进程构建，不作为业务接口。

- [x] **Step 1：写失败测试。** 用真实 Chroma 和确定性三维向量，验证持久化、相同版本复用校验、内容/授权变更、源文件删除、空资料版本、构建异常和无完成标记时不可发布、同名目录损坏时拒绝覆盖。子进程结束后再重命名目录；Windows、Linux 都验证构建句柄不会阻止发布。

```python
def test_failed_build_does_not_replace_published_version(tmp_path):
    # child 使用会失败的测试 Embeddings 构建另一个版本。
    assert old_index_digest_after == old_index_digest_before
    assert not candidate_manifest.exists()
    assert not (failed_version_dir / "complete.json").exists()
```

测试 CLI 不打印源正文/凭据，不创建在线 API 所持有目录的客户端；维度错误、NaN/Inf、向量数不符在写入/发布前拒绝。

- [x] **Step 2：安装已选依赖并运行失败测试。** 仅到实施阶段运行 `uv sync --frozen --extra rag`，随后 `uv run --frozen --extra rag pytest tests/xiaohe/test_knowledge_index.py -q`；预期缺少构建实现导致 FAIL。缺 Chroma 时本文件可 `importorskip` 适配基础依赖测试，但本命令必须实际运行且无 SKIP。
- [x] **Step 3：实现构建及 CLI。** 用现有 `rag` extra 安装的 `chromadb.PersistentClient`，collection 名固定 `xiaohe_knowledge`、距离 `cosine`；显式提供向量，禁止默认模型下载。因直接导入 Chroma，向 `rag` extra 补充已锁定的直接依赖 `chromadb==1.5.9`，只更新锁文件对应元数据，不升级其他包。元数据使用 `version`、引用 ID，以及布尔键 `agent.<agent_id>` / `topic.<topic_id>`，避免依赖数组元数据能力。
- [x] **Step 4：实现独立 staging 与发布，再复跑 Step 2，预期 PASS。** CLI 父进程不打开 Chroma；子进程在 staging 中建库、检查 count/ID/正文摘要并写 `manifest.json`、`complete.json`，退出成功后父进程同文件系统原子改名到 `<root>/<version>`，最后原子写候选清单。两个构建者竞争同版本时只有一个发布成功，另一个验证已完成产物或报冲突，不能合并目录；禁止递归覆盖/删除在线版本。不要假设 Chroma 有可用的公开 `close()`；构建句柄通过子进程退出释放。
- [x] **Step 5：仅提交本任务改动。** 消息：`feat: build immutable embedded knowledge indexes`。

## Task 3：实现受控内嵌检索与失败降级

**Files:** 新增 `src/xiaohe/knowledge/runtime.py`、`tests/xiaohe/test_knowledge_runtime.py`。

**Interfaces:** `EmbeddedKnowledge(settings: Settings)`，提供 `async start() -> None`、`async query(bundle: WorkflowBundle, agent_id: AgentId, topics: Sequence[str]) -> list[KnowledgeMaterial]`、`async aclose() -> None`。query 不接收用户文本、collection、路径或任意 where。消费 Task 1 配置；未配置时 query 返回空列表，且不导入 Chroma/初始化 Embedding/创建线程或目录。

- [x] **Step 1：写失败测试。** 参数化四个 Agent，检索前验证过滤器，返回后注入错误 Agent/主题/版本/ID/正文摘要及缺失来源的伪造命中，均不得进入结果。覆盖缺配置、空清单、目录不存在、错误完成标记、损坏索引、不同模型/endpoint/维度、无命中，均降级为空且不查另一个版本。

```python
async def test_timed_out_thread_keeps_admission_until_it_finishes():
    # 用可手动放行的阻塞调用控制后台线程，而非依赖长 sleep。
    assert first_result == []
    assert second_result == []
    assert blocking_calls == 1
    assert maximum_concurrent_calls == 1
```

真实子进程测试验证第二个 API 宿主不能打开相同 root；旧进程退出后锁可重新获得。另验证取消、关闭与慢调用竞态，不提前释放所有权或无界排队。

- [x] **Step 2：运行失败测试。** `uv run --frozen --extra rag pytest tests/xiaohe/test_knowledge_runtime.py tests/xiaohe/test_triad_runtime.py -q`；预期新检索测试 FAIL，既有配置行为保持。
- [x] **Step 3：实现单宿主检索。** 使用标准库 Linux `fcntl` / Windows `msvcrt` 非阻塞文件锁，进程级排他占有 `<root>/.owner.lock`；同进程只建一个实例，旧 Bundle 复用它。锁冲突禁用本实例并记录原因，不再打开 Chroma。使用单线程 executor，不为每次请求建线程；2 秒调用期限、最多一项实际在途操作、繁忙立即返回空列表。超时通过 shield 保留后台操作的占用，实际完成后才允许下一次提交；取消不得排队或释放正在使用的索引所有权。进程锁在进程退出时释放，aclose 停止接收请求并等待实际在途操作完成，不调用私有 Chroma shutdown 接口。
- [x] **Step 4：实现范围、版本与元数据复核，复跑 Step 2，预期 PASS。** 各版本只打开清单指定的已完成目录，不自动创建缺失 collection；先取得/核对完成标记、清单和配置身份。专业查询取当前 Bundle 技能标题，接待固定查询词“平台介绍与服务方向”，不发送原话。query 的 topics 必须在当前 Agent 已授权技能中；无技能时仅查询 `general`。过滤器同时约束版本、Agent 与允许主题；query 后验证 Task 1 清单及正文 SHA-256。最多 3 条、每条不超过 `KnowledgeHit.text` 的 4000 字符；使用 cosine distance 最低要求 `<=0.5`，以正反例验证该初始门槛，不宣称真实效果已校准。错误只记录原因码，不记录源文/私密参数。Embedding 使用显式模型、`max_retries=0` 与有限 HTTP timeout；拒绝形状、维度和数值异常。
- [x] **Step 5：仅提交本任务改动。** 消息：`feat: enforce knowledge scope version and query limits`。

## Task 4：接入实际 API 执行链及可追溯答复

**Files:** 修改 `src/xiaohe/main.py`、`graph.py`、`agents/runtime.py`、`agents/nodes.py`、`agents/semantic.py`；新增 `tests/xiaohe/test_knowledge_workflow.py`；扩展 `test_agent_semantics.py`、`test_workflow_versions.py`、`test_workflow_recovery.py`。

**Interfaces:** 为现有 `AgentRegistry`、`load_agent_registry`、`run_agent_turn`、`make_node_executors`、`build_graph` 增加可选关键字参数 `knowledge=None`，其余入参和默认行为不变；每个旧 Bundle 重建 registry 的分支都传同一个 `EmbeddedKnowledge`。保持 `compose_answer(model, frame, decision, materials, prompt)` 签名，接受 `KnowledgeHit` 及其 `KnowledgeMaterial` 子类。测试替身实现 Task 3 的 query 签名，保存调用范围。

- [x] **Step 1：写失败测试。** 新增纯回答/执行器测试覆盖三专业 Agent 的已授权主题、接待 `skills=[]` 的 general 知识、缺库的 Markdown 原答复；固定开场、离题、流程控制和 operation 节点断言 query 未调用。复用 `test_agent_semantics.py` 的 ScriptedModel 和当前 fixture，不改语义判定规则。

```python
async def test_knowledge_citations_are_verified_and_rendered():
    assert payload_material["version"] == bound_knowledge_version
    assert payload_material["display_source"] == "测试审定资料"
    assert len(citation_text_parts) == 1
    assert "测试审定资料" in citation_text_parts[0]
    assert bound_knowledge_version in visible_reply
    assert "仅适用测试场景" in visible_reply
```

对模型伪造 source ID 保持拒绝；未引用的知识不自动附会成回答依据，旧技能来源不被改写。新增 PG 测试验证同一运行重试/交接和旧草稿加载旧 Bundle，丢失旧索引时只用旧 Markdown，不能用新知识；API lifespan 关闭/启动失败时不会泄漏检索线程或第二实例。

- [x] **Step 2：先运行失败测试。** `uv run --frozen --extra rag pytest tests/xiaohe/test_knowledge_workflow.py tests/xiaohe/test_agent_semantics.py -m "not pg and not minio" -q`；新测试因缺装配/材料和引用信息 FAIL。PG 部分用最后的独占测试库命令单独运行。
- [x] **Step 3：实现装配与调用。** API lifespan 创建唯一检索器并 start，把它传入 registry 及 graph，确保关闭/异常路径调用 aclose。graph 对旧 Bundle/重试路径传递同一实例。回答节点先读取原技能资料，按受信 frame/decision 调 query，再调用 compose_answer；不能变更已有控制分支、路由、表单写入或确认。检索器故障不影响原回答；不能吞掉调用方取消和写入 fence 错误。
- [x] **Step 4：实现来源输出并复跑 Step 2，预期 PASS。** compose_answer 对知识材料增加 version/display_source 输入，继续校验 `source_ids`。answer 节点将实际引用的 `KnowledgeMaterial` 元数据存入当前 command/Agent 的阶段缓存 `knowledge_citations`，与已校验 answer 一起保存。result 节点从同一受信缓存读取并追加一个去重后的“资料依据” text part，包含展示来源、完整知识版本和适用条件；无引用不追加。保留现有 AnswerDraft/source_ids 格式和 4000 字符正文限制，不增加 LangGraph 状态字段或前端事件。outcome 阶段缓存保证重试不重复追加，测试包含最大长度正文。接待没有专业技能 Markdown 的情况沿用无依据说明，不捏造兜底资料。
- [x] **Step 5：运行 PG 版本/恢复测试并仅提交本任务改动。** 在独占测试库中执行最后的 PG 命令，预期 PASS；不具备隔离库时报告此项未验证，不能改用现有业务数据库。消息：`feat: integrate scoped knowledge into xiaohe answer workflow`。

## Task 5：提供持久化配置、维护说明与发布前检查

**Files:** 修改 `docker/Dockerfile.xiaohe`、`deploy/xiaohe/compose.yml`、`deploy/xiaohe/env.example`、`.env.example`、`.gitignore`、`.dockerignore`、`.github/workflows/test.yml`；新增 `docs/xiaohe/operations/knowledge.md`；扩展 `test_knowledge_index.py`、`test_knowledge_runtime.py`。

**Interfaces:** 消费 Task 1 已定义的五个知识配置字段和 Task 2 的构建 CLI，在私有 runtime.env 中配置；这里仅增加示例与部署接线，不再定义第二套配置或独立开关。

- [x] **Step 1：写失败测试。** 验证 CLI 与运行环境消费相同配置、秘密不进入异常和回执，不读取或覆盖项目现有 `.env`。验证 Compose 只有 API 挂载可写知识卷、worker 无索引挂载；镜像以现有 UID/GID `10001:10001` 使用专用目录，根文件系统保持只读。
- [x] **Step 2：运行失败测试。** `uv run --frozen --extra rag pytest tests/xiaohe/test_knowledge_index.py tests/xiaohe/test_knowledge_runtime.py -q`；新增配置/容器边界测试应 FAIL。
- [x] **Step 3：实现部署文件和示例。** 小鹤 Dockerfile 安装 `--extra rag` 并预建有正确 owner 的 `/var/lib/xiaohe/knowledge`，为 API 单独增加持久化卷与对应 root 环境配置；worker 不打开卷。忽略 `.local/xiaohe-knowledge/`、staging 和本地锁文件，不把测试索引 COPY 进镜像。`.env.example` 仅放字段说明与占位符，不填真实凭据或未验证模型。构建 CLI 复用 Task 3 配置。基础测试保持不用 rag 可运行；CI 新增使用 `--extra rag` 的知识测试步骤，先 `python -c "import chromadb"`，不允许以全部 SKIP 通过。
- [x] **Step 4：编写运维说明并复跑 Step 2，预期 PASS。** 提供一份明确标为测试用的 front matter 示例放在文档中，不向正式知识目录加入虚构事实。说明新增/修改/删除 MD、候选清单和活动清单的区别、按 Agent 显式共享、审阅样例、源文件不自动生效、空版本撤下全部资料、相同 embedding 配置可重建。发布顺序明确：处理在途执行、暂停 worker 新调度、将新索引与清单配套发布、重启 API、确认就绪、恢复 worker；回退保留老目录与 Bundle，停 API 才能做索引文件备份。Compose 发布方式保留现有部署技能/流程，不在实施时实际重启任何环境。
- [x] **Step 5：执行下方最终检查并仅提交本任务改动。** 消息：`docs: add embedded knowledge maintenance and release checks`。每项如失败，区分本次回归和执行前已有问题；不修改无关代码来清理基线。

## 最终验证与证据

以下是实施阶段的命令，本计划编写阶段不安装依赖、不运行收费服务、不部署。命令从仓库根目录执行，使用本仓库 `uv==0.13.5`。

```powershell
uv run --frozen --extra rag pytest tests/xiaohe/test_knowledge_sources.py tests/xiaohe/test_knowledge_index.py tests/xiaohe/test_knowledge_runtime.py tests/xiaohe/test_knowledge_workflow.py tests/xiaohe/test_agent_semantics.py tests/xiaohe/test_workflow_loader.py tests/xiaohe/test_triad_runtime.py -m "not pg and not minio" -q
uv run --frozen --extra rag ruff check src/xiaohe/knowledge src/xiaohe/main.py src/xiaohe/settings.py src/xiaohe/graph.py src/xiaohe/agents/runtime.py src/xiaohe/agents/nodes.py src/xiaohe/agents/semantic.py src/xiaohe/workflows/loader.py tests/xiaohe/knowledge_support.py tests/xiaohe/test_knowledge_sources.py tests/xiaohe/test_knowledge_index.py tests/xiaohe/test_knowledge_runtime.py tests/xiaohe/test_knowledge_workflow.py tests/xiaohe/test_agent_semantics.py tests/xiaohe/test_workflow_loader.py tests/xiaohe/test_workflow_versions.py tests/xiaohe/test_workflow_recovery.py tests/xiaohe/test_triad_runtime.py
uv run --frozen --extra rag pyrefly check
uv run --frozen pymarkdown scan docs/xiaohe/operations/knowledge.md
git diff --check
```

格式检查对本次修改文件运行 `ruff format --check`，使用上一条 ruff check 的相同路径。类型检查覆盖仓库既有配置，发现既有问题时记录基线，不据此扩大修改范围。

PG 测试仅在维护人员已提供独占 `XIAOHE_TEST_DATABASE_URL` 时执行，库名必须以 `test_xiaohe_` 开头。现有 `pg_factory` 会迁移并 TRUNCATE 测试表，因此不能指向共享或业务数据库：

```powershell
uv run --frozen --extra rag pytest tests/xiaohe/test_knowledge_workflow.py tests/xiaohe/test_workflow_versions.py tests/xiaohe/test_workflow_recovery.py -m pg -q
```

容器检查：`docker build -f docker/Dockerfile.xiaohe -t xiaohe-knowledge-check .`；只使用临时卷和虚构环境验证 UID 10001 可以打开/持久化 Chroma、第二个进程被拒绝，worker 不打开索引。不能为跑检查替换正式 Compose 或修改真实 runtime.env。Windows 和 Linux 至少各运行索引发布/排他子进程测试；某一平台不可用时明确记录。

交付证据区分：源码/单元检查、真实 Chroma 确定性向量、隔离 PG 恢复、容器检查、真实 Embedding、真实业务知识和目标环境生效。后面三项没有发生就明确写未验证；不能用测试资料代替业务验收。

## 自审与执行交接

- 设计第 1–4 节由 Tasks 1–2 覆盖；第 5 节由 Tasks 3–4 覆盖；第 6 节由 Tasks 1–4 覆盖；第 7–8 节由 Tasks 2–5 覆盖；第 9 节映射到各任务测试和最终检查；第 10 节作为全局非目标。
- 查询 API、清单资源键、配置字段和引用类型在任务间使用相同名字；未定义的 fixture/帮助函数由所属任务明确新增，测试代码片段为该任务需实现的断言，不是现有可运行代码。
- 本计划未承诺真实检索效果已校准，初始阈值需在首次业务资料验收时检查；没有额外引入管理后台或服务层。
- 推荐本会话由主 Agent 按顺序执行：五项任务共享清单、检索器和引用契约，顺序实施方便维护接口一致性，也降低上下文开销。实施前请用户审阅本计划并选择执行方式；如选择主 Agent 实施，下一步使用 `superpowers:executing-plans`。
