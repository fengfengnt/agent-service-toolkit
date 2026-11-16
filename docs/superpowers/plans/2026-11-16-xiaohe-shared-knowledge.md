# 小鹤共享官方知识 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一份共享官方知识默认供所有小鹤业务 Agent 和 subagent 使用，并保持同源索引、命令快照和 Embedding 配置一致。

**Architecture:** 共享 Markdown 进入现有 `RuntimeBundle.resources`，统一 Agent 工厂注入完整正文；向量检索在可信 manifest 上排除未同步的同源条目，再沿用现有 Chroma 授权与完整性校验。继续使用标准 Skills，不新增工作流协议、服务、数据库字段或前端消息协议。

**Tech Stack:** 现有 Python、Pydantic、PyYAML、DeepAgents、LangGraph、ChromaDB 1.5.9、langchain-openai、pytest；不新增依赖。

**Spec:** [已定稿设计](../specs/2026-11-16-xiaohe-shared-knowledge-design.md)。执行者必须同时阅读设计与计划。

## Global Constraints

- 当前授权仅限设计与实施计划，暂时不要写产品代码、测试文件或修改运行环境。下列复选框均为后续工作，不代表已执行。
- 共享文件固定为 `resources/xiaohe/knowledge/platform-common.md`，文档 ID 为 `platform-common`，主题为 `general`，显式授权四个业务 Agent；正文 UTF-8 上限 8 KiB，超限拒绝发布，不截断。
- 主/子 Agent 使用同一命令绑定的 Bundle；新命令才使用新发布版本。历史缺少共享文件的旧 Bundle 不补读全局最新版。
- 允许正文先发布；未同步时排除该文档所有旧向量条目，不阻塞其他授权知识，不删除旧索引。默认正文与向量命中不重复注入。
- Embedding 为 APIPro、`https://api.wenwen-ai.com/v1`、`text-embedding-3-small`、1024 维，并复用 `39.106.42.10` 小采 UAT/生产复用实例实际有效的 APIPro Key；真实 Key 不写文档、Git、发布包或工具输出。
- 在线 Embedding 请求 3 秒、整次检索 4 秒、离线建库请求 30 秒，均不自动重试；错误维度不截取、不静默切换模型。
- 两项服务说明保持待补充，不编造承诺；不自动抓取链接页面、不新增检索意图分类、热更新或管理界面。
- 保留当前工作区已有未提交/未跟踪文件。尤其是 `VERSION`、`factory.py`、`runtime.py`、`test_deep_factory.py` 和 `test_deep_collection.py` 的既有工作，先确认实施基线，禁止覆盖或混入自己的提交。本轮不升版本、不推送、不部署。

## Review Focus

1. 新资源文件丢失、越界、非法 YAML 或多字节正文超限时，拒绝新发布并保留旧版本；Task 1 测试精确验证。
2. 历史 Bundle、检查点恢复或命令内交接不得偷偷读取新正文，也不得扩大子 Agent 权限；Task 2、Task 3、Task 5 验证。
3. 共享文档增加/删除分块或仅修改授权、来源、条件时，所有同源旧条目都应排除，其他知识不能被旧条目挤出前三条；Task 3 验证。
4. 服务忽略 `dimensions`、返回错误维度或请求超时后线程仍运行时，必须按既有准入回退，不能堆积查询或悄悄改模型；Task 4 验证。
5. 复用 Key 时不得误读另一客户、复制整份配置、改变小采或泄漏日志；Task 5 用脱敏替身验证操作流程，真实接线仅在后续明确目标后执行。

## 文件职责与依赖

| 文件 | 最小职责 |
| --- | --- |
| `resources/xiaohe/knowledge/platform-common.md` | 唯一共享事实正文及现有知识源元数据 |
| `src/xiaohe/knowledge/sources.py` | 抽取并复用现有单份源文档解析，不复制 YAML 协议 |
| `src/xiaohe/knowledge/shared.py`（新增） | 共享文档常量、校验、从 Bundle 读取、同源条目比较和材料去重 |
| `src/xiaohe/deep_runtime/bundles.py` | 新发布必须包含共享资源，内容进入现有摘要 |
| `src/xiaohe/deep_runtime/factory.py` | 主/子 Agent 统一注入同一共享正文 |
| `src/xiaohe/deep_runtime/runtime.py` | 共享来源白名单、恢复/持久化材料去重 |
| `src/xiaohe/knowledge/runtime.py` | 查询前过滤同源旧条目与空候选，保留检索安全边界 |
| `src/xiaohe/knowledge/build.py`、`__main__.py` | 构建/查询显式维度和分用途超时 |
| 对应 `tests/xiaohe/` 文件 | 复用现有确定性模型、受控 Embedding、Chroma 与隔离 PG fixture |
| `docs/xiaohe/operations/knowledge.md`、`deploy/xiaohe/knowledge.env.example` | 公开维护与接线说明，仅占位密钥 |

依次完成 Task 1 → Task 2 → Task 3 → Task 4 → Task 5。依赖紧密，不并行修改同一运行时文件；实施方式仍待用户选择。下列测试名称是待新增的验证契约，辅助数据使用现有 `write_test_bundle`、`write_knowledge_source`、`make_script_model` 和隔离 PG fixture，不另造测试框架。

---

### Task 1：共享文档解析与新发布快照

**Files:** Create `resources/xiaohe/knowledge/platform-common.md`、`src/xiaohe/knowledge/shared.py`、`tests/xiaohe/test_shared_knowledge.py`；Modify `src/xiaohe/knowledge/sources.py`、`src/xiaohe/deep_runtime/bundles.py`；Test `tests/xiaohe/test_knowledge_sources.py`、`tests/xiaohe/test_deep_bundles.py`。

**Interfaces:**

- 在 `sources.py` 提供 `parse_source(text: str, allowed_topics: Mapping[AgentId, set[str]]) -> KnowledgeSource`，`load_sources` 复用它；保留原有文件路径、字节数、异常隐藏和重复 ID 校验。
- `shared.py` 提供 `SHARED_PATH = "knowledge/platform-common.md"`、`SHARED_DOCUMENT_ID = "platform-common"`、`SHARED_SOURCE_ID = "shared:platform-common"`、`MAX_SHARED_BODY_BYTES = 8192`。
- 提供 `parse_shared_source(text: str) -> KnowledgeSource` 与 `shared_source(bundle: RuntimeBundle) -> KnowledgeSource | None`；后者只读 Bundle，缺少资源返回 None，存在但非法则报错，不读磁盘。使用 TYPE_CHECKING 避免 Bundle/知识模块循环导入。

- [ ] **Step 1：新增失败测试。** `test_shared_source_contract` 断言文档 ID、四 Agent 授权、`topics == ["general"]`；`test_new_bundle_requires_valid_shared_source` 参数化缺失、空白、重复 YAML 键、alias、错误 ID、少一个 Agent、非 general、8 KiB+1 的多字节正文及资源路径越界/符号链接，断言拒绝新发布；8192 字节正文可通过，禁止截断。
- [ ] **Step 2：运行 RED。** `python -m pytest tests/xiaohe/test_shared_knowledge.py tests/xiaohe/test_deep_bundles.py -q`，预期新接口/共享发布约束尚不存在而失败；区分已有失败。
- [ ] **Step 3：实现上述最小接口和载入。** loader 对共享文件沿用 `_read` 的路径与资源限制，补齐符号链接检查，解析后把原文放进 `RuntimeBundle.resources[SHARED_PATH]`。正文、来源、条件或 ACL 变化均应改变 bundle_id；不要把所有未发布知识源扫描进快照。更新现有复制资源的 fixture 假设，保持 no-manifest 的 Bundle 合法。
- [ ] **Step 4：编写共享初稿并运行 GREEN。** 平台名为“云鹤智元”，公司全称为“云鹤智元（上海）科技有限公司”，简介采用门户已有“专注于商用人工智能解决方案的专业服务商”口径；官方链接待确认。两项服务各写“具体说明待补充，适用条件与交付范围尚未确认”，不补写承诺。运行 Step 2 与 `python -m pytest tests/xiaohe/test_knowledge_sources.py -q`，预期新增测试通过且原安全测试保持。
- [ ] **Step 5：提交本任务文件。** 使用明确路径 staging，提交 `feat(xiaohe): add versioned shared platform knowledge`，不包含其他任务已有变更。

### Task 2：主/子 Agent 默认装配与可信来源

**Files:** Modify `src/xiaohe/deep_runtime/factory.py`、`src/xiaohe/deep_runtime/runtime.py`、`resources/xiaohe/deep-runtime/reception-policy.md`；Test `tests/xiaohe/test_deep_factory.py`、`tests/xiaohe/test_deep_children.py`、`tests/xiaohe/test_knowledge_workflow.py`。

**Interfaces:**

- 消费 Task 1 的 `shared_source(bundle)` 与 `SHARED_SOURCE_ID`。
- 工厂内部接口调整为 `_native(model, *, bundle: RuntimeBundle, tools, prompt, response_format, checkpointer=None, children=())`；两个现有调用点均传同一 bundle。公开 `build_business_agent`、`build_readonly_child` 签名不变。
- runtime 仅在该命令 Bundle 确有合法共享来源时，将 `SHARED_SOURCE_ID` 加入回答来源白名单；不依赖向量开关。

- [ ] **Step 1：新增失败测试。** `test_all_bindings_receive_full_shared_source` 参数化四 Agent × 访客/登录，断言 system message 包含完整正文及稳定来源且无需 read_file；`test_child_receives_shared_source_without_parent_context` 断言子任务获得同份正文，但父对话、身份哨兵和私有文件不在请求中，写工具仍不可用。
- [ ] **Step 2：运行 RED。** `python -m pytest tests/xiaohe/test_deep_factory.py tests/xiaohe/test_deep_children.py -q`，预期共享装配断言失败。
- [ ] **Step 3：实现统一注入与来源白名单。** `_native` 从 bundle 读取正文，以 JSON 数据块标注来源和适用条件；保持业务范围、安全和工具约束。旧 Bundle 缺少共享资源时不补读新版。接待资料去掉重复的平台事实，改为依据共享资料的已确认定义解释，未列条件仍待确认；不改留资流程或静态前端品牌文案。
- [ ] **Step 4：验证来源。** 新增 PG 测试 `test_shared_answer_without_retrieval_has_valid_source`：关闭知识检索时，引用 `shared:platform-common` 可通过；引用 `shared:forged` 被拒绝，普通回复不新增强制来源尾句。新增 `test_pending_services_and_injected_instructions_do_not_grant_authority`，用确定性模型/工具约束核对待补充文本仍存在且资料不能授予写权限；真实模型是否遵循语义留待业务验收，不能用脚本模型冒充。运行 Step 2 与该文件 PG 用例，预期通过。
- [ ] **Step 5：提交本任务差异。** 提交 `feat(xiaohe): share platform facts across business agents and children`；提交前核对不混入原有业务范围或留资工作的补丁。

### Task 3：同源旧索引过滤、去重与命令恢复

**Files:** Modify `src/xiaohe/knowledge/shared.py`、`src/xiaohe/knowledge/runtime.py`、`src/xiaohe/deep_runtime/factory.py`、`src/xiaohe/deep_runtime/runtime.py`；Test `tests/xiaohe/test_shared_knowledge.py`、`tests/xiaohe/test_knowledge_runtime.py`、`tests/xiaohe/test_knowledge_workflow.py`。

**Interfaces:**

- 新增 `stale_shared_entry_ids(bundle: RuntimeBundle, manifest: KnowledgeManifest) -> frozenset[str]`。仅当 bundle 有共享正文且不匹配时，返回 manifest 中 `entry.id == SHARED_DOCUMENT_ID` 的所有 key；无共享资源的历史 bundle 返回空集，保持原检索行为。
- 新增 `filter_shared_materials(bundle: RuntimeBundle, materials: Sequence[KnowledgeMaterial]) -> list[KnowledgeMaterial]`。bundle 有共享正文时，用 manifest 可信 entry key 和版本构造精确 kb 来源集合，移除该文档匹配或陈旧的向量材料；其他材料不变。禁止靠任意字符串前缀猜测来源。
- 两处消费者复用过滤函数：factory 防止直接装配时重复注入；runtime 在新结果持久化及检查点恢复后过滤，并用同一结果校验来源。既有 `_command` 写栅栏与缓存结构保持。

- [ ] **Step 1：新增失败测试。** `test_shared_manifest_change_excludes_all_old_chunks` 参数化正文改动、分块增加/删除、来源、条件、ACL 改动，断言旧文档全部 key 排除、其他文档 key 不排除；一致文档返回空集。比较过程不得发起 Embedding。
- [ ] **Step 2：运行 RED。** `python -m pytest tests/xiaohe/test_shared_knowledge.py tests/xiaohe/test_knowledge_runtime.py -q`，预期新排除/去重行为失败。
- [ ] **Step 3：实现纯比较与查询前过滤。** 对共享源调用现有 `prepare_manifest([source], embedding_model=manifest.embedding_model, embedding_identity=manifest.embedding_identity, dimensions=manifest.dimensions)`，比较该文档完整 entries 集合，复用现有分块参数；不改 manifest 格式。查询前按 Agent、topics 与 stale keys 计算候选，Chroma `query(ids=候选列表, ...)` 保留既有 where、摘要、距离、版本复核，`n_results=min(3,候选数)`。已核对锁定 Chroma 1.5.9 支持 ids 参数。
- [ ] **Step 4：实现空候选与材料去重。** 无候选直接返回；候选只有已默认常驻的共享文档时也直接返回，不请求 Embedding。若还有其他知识，允许同步共享条目参与原有召回，但在模型装配和缓存前去重；不新增意图分类或重排流程。
- [ ] **Step 5：验证真实 Chroma 和恢复。** `test_stale_shared_chunks_cannot_crowd_out_other_hits` 放入至少四个同源旧块和一条其他授权资料，断言仍命中其他资料且实际 query ids 不含旧块；`test_only_shared_candidates_make_no_embedding_request` 同时覆盖匹配/未同步两态。PG 测试 `test_retry_child_and_handoff_keep_shared_snapshot` 在切换当前发布版本后恢复旧命令，断言主/子与交接仍用旧正文、材料不重复、缓存不再次查询；另以删除共享资源的历史 bundle 验证不读取新文件。
- [ ] **Step 6：运行 GREEN 并提交。** 运行 Step 2 与 `python -m pytest tests/xiaohe/test_knowledge_workflow.py tests/xiaohe/test_deep_recovery.py -q`，需提供本任务专用隔离 PG。预期新增与既有超时/篡改/ACL 测试通过；提交 `feat(xiaohe): isolate stale shared vectors from pinned command context`。

### Task 4：显式维度与分用途请求预算

**Files:** Modify `src/xiaohe/knowledge/build.py`、`src/xiaohe/knowledge/runtime.py`、`src/xiaohe/knowledge/__main__.py`、`deploy/xiaohe/knowledge.env.example`；Test `tests/xiaohe/knowledge_support.py`、`tests/xiaohe/test_knowledge_index.py`、`tests/xiaohe/test_knowledge_runtime.py`。

**Interfaces:**

- 调整为 `create_embeddings(settings: Settings, *, request_timeout: float = 3.0) -> OpenAIEmbeddings`；显式 `dimensions=settings.knowledge_embedding_dimensions`，`max_retries=0`。构建 CLI `_build` 传 `request_timeout=30.0`；在线启动和惰性重建使用默认值；`QUERY_TIMEOUT=4.0`。
- 保持 `validate_vectors`、endpoint/model/dimensions 版本匹配、根目录锁、线程准入及取消语义；不新增 provider 适配层或配置别名。

- [ ] **Step 1：新增失败测试。** `test_build_and_query_send_configured_dimensions` 让受控 HTTP 服务记录请求，断言两类请求均包含所选维度；`test_embedding_timeouts_are_explicit` 捕获 OpenAIEmbeddings 构造参数，断言默认 3.0、离线 30.0、整检索 4.0、重试为 0。fixture 不支持的维度应返回确定错误，不能自动改成 1536。
- [ ] **Step 2：运行 RED。** `python -m pytest tests/xiaohe/test_knowledge_index.py tests/xiaohe/test_knowledge_runtime.py -q`，预期未传维度及预算断言失败。
- [ ] **Step 3：实现参数传递并更新非秘密模板。** 模板写入批准的 URL、模型和 1024，Key 保持占位/注释，未完整配置时仍关闭检索；不得把真实 Key 作为默认值。必要时更新既有 embeddings 测试替身的签名，不让替身掩盖缺失参数。
- [ ] **Step 4：运行 GREEN。** 再跑 Step 2，保留真实 Chroma 负 cosine 浮点误差、返回错误维度、超时/取消后后台未结束则拒绝新查询等回归。预算测试用事件和短测试替代时长，不实际等待 30 秒。
- [ ] **Step 5：提交本任务差异。** 提交 `fix(xiaohe): send embedding dimensions with bounded runtime requests`。

### Task 5：集成验收与私有配置接线说明

**Files:** Modify `docs/xiaohe/operations/knowledge.md`；Create `docs/xiaohe/testing/YYYY-MM-DD-shared-knowledge.md`（执行当日）；Test 前述测试及 `tests/xiaohe/test_deep_runtime.py`、`tests/xiaohe/test_deep_recovery.py`、`tests/xiaohe/test_knowledge_deployment.py`。本任务不引入新产品接口。

**Interfaces:** 消费前四任务的既有公开入口；配置目标仍为四项 `XIAOHE_KNOWLEDGE_EMBEDDING_*`，私有 Key 来源为已确认实例内实际有效的 `FLARE_EMBEDDING_API_KEY or APIPRO_API_KEY`，与此前核对的专用 Key 为空、APIPro Key 有效一致，不从历史 `.env` 猜值。

- [ ] **Step 1：准备受控验收环境。** 按执行时实际工作树核对依赖和基线；使用已有固定 uv 0.13.5 与 rag extra，禁止升级依赖。PG 必须为本任务独占、名称以 `test_xiaohe_` 开头的测试库；fixture 会清表，禁止业务库。只在获准实施后创建测试容器，不使用旧任务容器。
- [ ] **Step 2：运行功能集合。** `python -m pytest tests/xiaohe/test_shared_knowledge.py tests/xiaohe/test_deep_bundles.py tests/xiaohe/test_deep_factory.py tests/xiaohe/test_deep_children.py tests/xiaohe/test_deep_runtime.py tests/xiaohe/test_deep_recovery.py tests/xiaohe/test_knowledge_sources.py tests/xiaohe/test_knowledge_index.py tests/xiaohe/test_knowledge_runtime.py tests/xiaohe/test_knowledge_workflow.py tests/xiaohe/test_knowledge_deployment.py -q`。预期相关用例通过；已知 `test_partner_switch_control_rejects_invalid_payload[body1]` 为历史失败，若扩大测试仍复现，先对基线确认，不顺带修改方向业务规则。
- [ ] **Step 3：完成静态及候选镜像检查。** 对本次 Python 文件运行 Ruff check/format --check，对本次文档运行 Markdown 检查和 git diff --check。若后续获准构建候选镜像，以只读根、专用可写临时目录、实际非 root 用户验证完整正文/工具循环/Chroma 持久化与 ACL；不接真实业务存储，不宣称等于环境发布。
- [ ] **Step 4：写明私有 Key 复用流程，并用固定假 Key 验证脱敏输出。** 后续只有在用户指定目标并授权环境操作时，严格主机校验连接 admin@39.106.42.10；在一个私有进程内从活动 API 容器读取有效 Key，经 SSH 加密通道直接传到目标私有环境写入器。进程只输出“已配置/一致”的布尔值，不输出密钥、其片段或请求头；不经命令行参数/临时明文文件/模型上下文中转，不打印配置差异。不读取或复制其他客户环境，也不复制整份小采 `.env`。写入器在目标私有目录以 0600、原属主原子替换，并保留该文件其他字段及受限回退副本；失败保持原配置。用假 Key 断言标准输出/错误不含值且保留非目标参数。
- [ ] **Step 5：记录后续环境动作，当前不执行。** WSL 私有候选路径为 `/home/fengfeng/yunhezhiyuan/aibackend/config/knowledge.env`，执行时须重新核对；只更新四项 Embedding 参数，ROOT 沿用专用卷。配置写入不等于生效，后续需按实际 Compose 重新创建 API，不能只 restart；该计划不授权小采重启或修改，也不授权此次小鹤发布。索引需按新配置离线构建、验证、发布，常驻正文可先单独发布，旧索引保留。真正接线时仅在内存中比较 Key，公开回执只记录 equality=true，不记录摘要或值。
- [ ] **Step 6：区分验收并提交文档。** 记录受控测试、历史一次真实 API 测试、执行时新增真实接口检查及中文资料召回各自是否完成；不能沿用 1.49 秒作为 WSL 性能结论。正式两项服务说明未补齐时仍标待补充。提交 `docs(xiaohe): document shared knowledge operation and verification`；是否推送、发布与版本号另按用户指示，不自行执行。

## 计划自检与交接

设计覆盖对应关系：单一来源/校验/长度 → Task 1；主子装配/权限/来源 → Task 2；先发正文/旧向量/快照缓存 → Task 3；模型/维度/预算 → Task 4；同 URL 与 Key 的私有接线、真实验收与操作边界 → Task 5。已逐项检查五类 Review Focus 的拥有者、接口名称和测试归属；不新增未使用的通用服务、迁移或前端功能。

建议后续采用 Native：由当前会话依次完成五个紧密依赖的任务，最后独立审查整组差异，减少工厂、来源和缓存接口在多个实现者间反复交接。用户也可选择 Subagent-driven；本计划不预先启动任何实现代理。用户当前明确要求暂不写代码，先交付计划供审阅，不因计划完成而开始实施。
