> 当前文档修订 **v1.0.11**（2026-11-06）：新增 `system_notice` 卡片及门户↔AI结构化自动控制接口，避免将界面自动动作写成用户发言；消息容器角色与既有事件外壳保持，Collaborator/reception.v1 正式业务 schema 不变。v1.0.10及更早修订记录保留。契约检查通过不代表真实手机号登录或正式验收。

# 小鹤跨工程接口与交付契约 v1


> 当前文档修订 **v1.0.4**（2026-11-05）：新增§10 AG-UI业务适配，原wire v1.0保持；下方v1.0.3记录保留为历史。G0已通过，新增接口实施/真实验证以AI报告为准，合同冻结不代表已部署。


日期：2026-11-03，修订：2026-11-04。文档修订版 **v1.0.3**：2026-11-04用户追加确认complete响应及用户Cookie全部丢失时的原绑定上下文恢复，见§9补充；其余沿用v1.0.2。用户本轮确认P8补充建议的认证上下文、Cookie/header、1200秒基础认证操作窗口和公开重试错误，精确增量见§9；ChatSnapshot/ChatPart等既有wire仍为v1.0。§9替代下文历史v1.0.1中“认证上下文/AUTH_NOT_READY待补”表述。AI P0–P7历史实现基线为8a96c05，P8/P9当前实现与验证状态以AI工程验收记录为准，契约冻结不等于部署或真实组合验收；SSO owner文档记录本地部署及登记已完成，本轮未重复运行真实SSO/短信/AI联调或部署。



## 1. 所有权与前置门



| 工程 | 职责 | 明确不承担 |

| --- | --- | --- |

| yunhezhiyuan-portal | assistant-ui Vue 客服、卡片、OIDC 回调衔接、流恢复、Artifact UI、门户整体状态 | 不存模型/MinIO/受信应用秘密，不决定用户/租户归属，不实现 OTP 验证或归并事务 |

| yunhezhiyuan-agents | AI API、访客/用户会话、系统映射、归并、持久任务、LangGraph、MinIO 及认证中心适配 | 不签发中心 Cookie，不复制认证源用户库，不把旧 Dify 任务复活 |

| xiaocai-admin-server | 受信登录事务、captcha/短信/开户复用、单次浏览器完成、中心会话、Portal OIDC 关联、可信完成结果 | 不存聊天、文档或 LangGraph 状态，不执行 AI 业务租户下的归并 |



总体设计：[已确认设计](../superpowers/specs/2026-11-03-xiaohe-ai-platform-design.md)。后台执行与 SSO 建议依据见[任务研究](../../.scratch/xiaohe-ai-platform/issues/06-durable-worker.md)、[认证衔接研究](../../.scratch/xiaohe-ai-platform/issues/07-sso-bridge-proposal.md)。



执行顺序：P0 冻结本契约与中心桥接契约 → 各工程独立单元/契约测试 → SSO+AI 服务端闭环 → 门户真实浏览器闭环。隔离测试可以先做，最终联调不能绕开未完成的上游。



本文件是三方共享的单一基线，当前聊天维护。其他聊天在计划中列出具体修订建议，不能分别维护互相冲突的接口副本；正式冻结时在本文件记录变更与确认结果。



## 2. 必须保持的规则



- `systemId ↔ tenantId` 一对一，取自服务端受信登记。来源身份的 `sourceTenantId` 与业务 `tenantId` 分开，不能把手机号来源租户1当作全部系统的业务租户。

- 门户只展示用户默认项目中的一条长期对话；数据模型支持私人项目下多个对话，不扩展项目管理 UI。

- AI 回复不占访客额度。默认 `VISITOR_MESSAGE_LIMIT=100`，第100条可受理，第101条拒绝；重复同一提交不重复扣减。

- 默认 `VISITOR_RETENTION_SECONDS=14400`；每次成功受理访客消息滚动更新有效期，累计额度不重置。AI 回复、刷新、重连、被拒绝的消息和相同请求重试不续期。

- 到期仍未登录则停止排队/运行/等待任务并使访问失效，迟到输出不能继续提交；安全收尾后清理。已经认证归并的数据不能被访客清理误删。

- 有效期内关闭页面或断网不取消任务；恢复读取持久状态及结果，不能重发原消息来模拟重连。

- 三种服务方向是业务上下文，不是用户认证或会员权限；三个专业 Agent 的完整流程以后分别设计。

- 认证卡片凭据不进入普通消息、LLM、checkpoint、URL、日志或浏览器持久化。中心 Cookie 仅由 issuer 签发。



## 3. Portal ↔ AI HTTP 基线



公开前缀：`/ai-api/v1`。门户通过同源代理访问 AI 后端，现有 `/sso/**`、`/oauth2/**`、`/userinfo` 等认证源路径保持原归属。该前缀为本计划约定，不表示当前反代已存在。



### 已采纳的 Toolkit 边界与身份



用户已确定以 `agent-service-toolkit` 基线 `8366496` 在 `dev-fengfeng` 分支二开，尽量不改上游。新增业务层承担本节API；toolkit原生接口、Agent registry、模型工厂、checkpointer/Store复用。内部继续使用AUTH_SECRET，浏览器只向同源业务前缀携用户Cookie和当前快照的CSRF，不发送toolkit共享密钥、SSO受信应用凭据或SSO access token。



全局唯一 `user_id → 唯一 tenant_id → 唯一 systemId` 是已确认归属规则。用户随后确认：AI首次成功验证中心可信结果时自行生成全局user_id，原子保存 `(issuer,sub,sourceTenantId) → user_id`，以后登录/重试/并发首次登录复用该绑定；业务tenantId/systemId来自AI受信登记，同一身份改指其他归属时拒绝，不按system复制账号。公开Viewer.userId表示这个AI全局标识，与中心会员userId不是同一ID域。业务层仍逐项检查对象权限，不解析sub或要求中心返回AI user_id，原六字段claim保持。



公开 `conversationId` 是长期业务对话ID；服务端另存它到当前执行 `thread_id`、运行代次和允许Agent的映射。归并/恢复可换物理thread而保留canonical conversation及稳定messageId；浏览器不能传graph thread/checkpoint定位或完整graph state。公共runId是业务runId，不能用LangGraph调用ID或示例TaskData.run_id替代。



AI 会话采用后端管理的 Secure/HttpOnly Cookie；写请求带会话返回的 CSRF 证明。Cookie 名称、存储策略与会话到期规则由 AI 计划固定，不能通过前端自报用户/租户或 `authenticated=1` 授权。



AI API 成功响应使用下列 DTO 与 HTTP 状态，不套用现有中心 `code=0` 响应包装。中心原接口继续保留其原格式，AI 的认证适配负责转换。



### 共享类型



```ts

type RunStatus = 'queued'|'running'|'waiting_input'|'completed'|'failed'|'cancelled'|'expired'

type Viewer =

  | {kind:'guest';guestId:string;systemId:string;tenantId:number;messageCount:number;messageLimit:number;retentionSeconds:number;expiresAt:string}

  | {kind:'user';userId:string;systemId:string;tenantId:number;displayName:string}

type ChatPart =
  | {type:'system_notice';text:string}

  | {type:'text';text:string}

  | {type:'choice';requestId:string;prompt:string;options:{id:string;label:string}[]}

  | {type:'suggestions';items:{id:string;text:string}[]}

  | {type:'input_request';requestId:string;prompt:string}

  | {type:'auth_request';intent:'lead'|'register'|'login'}

  | {type:'artifact_ref';artifactId:string;version:number;title:string;mediaType:string;status:'pending'|'ready'|'failed'}

type ChatMessage = {id:string;role:'user'|'assistant';createdAt:string;parts:ChatPart[]}

type RunRef = {id:string;status:RunStatus}

type MergeState = {operationId:string;status:'MERGE_PENDING'|'MERGED'|'SKIPPED_EXPIRED'}

type ChatSnapshot = {viewer:Viewer;csrf:{headerName:string;token:string};conversationId:string;messages:ChatMessage[];activeRun:RunRef|null;cursor:string|null;merge:MergeState|null}

type ChatAction = {clientActionId:string;requestId:string;optionId?:string;text?:string}

type MessageAccepted = {message:ChatMessage;run:RunRef;viewer:Viewer}

type EventPayloads = {

  'message.delta': {messageId:string;delta:string}

  'message.upsert': {message:ChatMessage}

  'run.updated': {run:RunRef}

  'artifact.updated': {artifactId:string;version:number;status:'pending'|'ready'|'failed'}

  'viewer.updated': {viewer:Viewer}

  'viewer.expired': {code:'VISITOR_EXPIRED'}

  'error': ApiError

}

type ChatEvent = {[K in keyof EventPayloads]: {eventId:string;conversationId:string;runId:string|null;type:K;payload:EventPayloads[K]}}[keyof EventPayloads]

type ApiError = {error:{code:string;message:string;retryable:boolean};requestId:string}

```



时间为服务端 UTC ISO 字符串；前端倒计时仅为提示，不能据本机时钟授权。标识符均视作不透明字符串；客户端不能解析 `sub` 或自行拼接数据库/MinIO 路径。



v1.0 响应按上述精确字段校验：不接收额外身份/凭据字段、未知卡片或未知事件；格式错误只显示安全错误，不回显原值。服务方向ID是选项数据，允许新增方向。message/option/suggestion ID在对应列表内唯一；业务tenantId为非负安全整数，次数非负，额度和保留秒数为正整数，产出物版本从1开始。merge非null时viewer必须为user。响应新增字段或类型须更新契约和消费方后发布，不能悄悄绕过校验。



输入固定为：普通消息text及动作text含1–10000个Unicode码点且不能全为空白，保留原文而不自动trim；clientMessageId/clientActionId/requestId/optionId长度1–128个Unicode码点，且不能全为空白，原值保留。按码点计数，不能使用UTF-16代码单元长度或UTF-8字节数代替；例如单个补充平面emoji计1。actions中optionId与text必须且只能提供一个；未提供字段要省略，null、空白值、二者同时存在和额外认证字段均422 INPUT_INVALID。选项有效性由服务端按持久问题核验。拒收不扣额度、不续期。建议项发普通message，绑定选择/补充问题才发action；服务端生成的requestId/optionId也遵守相同输入上限。



### 基础操作



| 方法与路径 | 请求 | 响应与语义 |

| --- | --- | --- |

| `POST /session` | `{}` | `200 ChatSnapshot`；建立/恢复当前系统访客或已有 AI 用户会话，返回 CSRF 与默认对话。不是以 Portal 本地标记授权 |

| `GET /conversations/{id}` | 无 | `200 ChatSnapshot`；当前身份有权读取的持久快照，重新打开时先取快照再订阅 |

| `POST /conversations/{id}/messages` | `{clientMessageId:string,text:string}` | `202 {message:ChatMessage,run:RunRef,viewer:Viewer}`；可靠受理后计数/续期，提交重试复用 clientMessageId |

| `POST /conversations/{id}/actions` | `{clientActionId:string,requestId:string,optionId?:string,text?:string}` | 同上；绑定有效的选择或补充问题并创建一条可见用户消息，适用同一配额/续期和幂等规则。认证字段不得走此接口 |

| `GET /conversations/{id}/events?after={cursor}` | Cookie；可带 `Last-Event-ID` | SSE；重新鉴权后按游标补齐，再订阅后续内容。来源/游标冲突时明确报错，不从其他会话补事件 |

| `POST /runs/{id}/cancel` | `{}` | `200 RunRef`；明确停止请求，与断开 SSE 区分，不承诺外部调用可瞬时回滚 |

| `GET /artifacts/{id}/versions/{version}` | 无 | `200 ArtifactView`，见下；读取前校验系统与用户/访客归属和有效期 |

| `GET /artifacts/{id}/versions/{version}/download` | 无 | 受权限约束的文件响应；不返回裸 MinIO 密钥或长期公开地址 |

| `POST /auth/logout` | `{}` | `204`；撤销 AI 本地会话/访问，Portal 再协调现有中心退出。任一退出失败须如实显示，不能宣称已全局退出 |



首期同一对话同时只有一个生成写任务；忙碌时新的普通消息返回 `RUN_ACTIVE`，不扣访客额度、不续期。`waiting_input` 的对应动作恢复原任务；重复动作幂等，不能由陈旧卡片推进其他运行。归并协调期间新输入返回 `MERGE_PENDING`，不消费动作；原等待任务保留问题及 checkpoint 并让出写入位置，归并后只在空闲时由对应动作恢复。此为实施默认，避免未定义的并发生成语义。



### 受理、幂等与会话到期



- 202仅在消息/动作、业务run和可靠job事务提交后返回；MessageAccepted.message.id是服务端稳定业务ID，不等于clientMessageId。动作成功也生成可见user消息；同一waiting_input动作恢复原run。

- 幂等键按已授权业务对话及消息/动作类型区分；同键同参数复用原message.id/run.id，不重复插入、扣额、续期或入队；同键改参数409 IDEMPOTENCY_CONFLICT。先检查当前权限及有效期，再返幂等结果，不能靠缓存读取已失效数据。

- **MessageAccepted.viewer在每次响应（包括幂等重试）中重新读取当前权威次数和期限，不缓存第一次响应的viewer。** run.id保持原值、status读当前状态。重试本身不续期；如果其间另一条新消息续期，重试返回的是更新后的期限，而非第一次的旧值。

- MessageAccepted没有cursor，HTTP响应不能推进事件水位。若受理响应与SSE/身份变化交错，旧身份响应不得写入新store；无法判定显示状态先后时重取一致快照，不再次发送原消息。客户端每次写操作使用当前snapshot的CSRF，不能由迟到响应更新一个跨身份全局CSRF缓存。



| `POST /session {}` 所遇状态 | 固定行为 |

| --- | --- |

| 无AI会话Cookie的首次访问 | 校验已登记系统及精确Origin后200创建访客快照、Cookie/CSRF；不以Portal本地登录标记授予用户身份 |

| 仍有效的访客会话 | 200返回同一guestId/对话、当前累计次数和原expiresAt；刷新/重开不续期、不清零 |

| 可识别的已到期访客/tombstone | 410 VISITOR_EXPIRED；不自动发一个新guestId或替其重置额度，旧私有内容不可读 |

| 仍有效的用户会话 | 200返回本人当前系统canonical快照；同一user_id错系统访问拒绝 |

| 已到期/撤销/不可验证的用户会话 | 401 AUTH_REQUIRED；清除客户端旧身份视图，不默默降级到匿名后继续读旧数据 |



v1.0.1不新增“重开匿名会话/重置额度”端点、body字段或自动重试规则。Task3收到410时清空旧私有视图并停止自动恢复，可引导认证；仍有效的既有认证操作按自身绑定和期限处理，访客记录不复活。到期后新认证操作的安全初始化/CSRF准备与本地会话超时，在§8的认证P0中统一确认后再实施相关认证任务，不阻塞Task2/3错误与暂停状态的实现。无Cookie属于新访客入口，匿名上限按服务端登记访客执行，不声称能跨已清除Cookie的浏览器识别同一自然人。



`POST /auth/logout` 对已登录用户撤销AI会话；无用户身份时不借此清零仍有效的guest计数/期限或解除expired tombstone。注销与断开订阅不会自动取消该账号仍获授权的后台任务；旧浏览器流立即失去读取资格。真正的后台任务权限撤销由业务层单独执行。



`ArtifactView = {artifactId:string;version:number;title:string;mediaType:string;status:'pending'|'ready'|'failed';preview:{format:'markdown'|'text'|'download_only';text?:string};downloadPath:string|null}`。Markdown 使用安全渲染，不执行任意 HTML/脚本。业务版本与 MinIO Bucket Versioning 分开管理。



### 错误分类



最低覆盖：`VISITOR_LIMIT_REACHED`(429)、`VISITOR_EXPIRED`(410)、`AUTH_REQUIRED`(401)、`FORBIDDEN`(403)、`RUN_ACTIVE`(409)、`MERGE_PENDING`(409，仅新输入被阻止时)、`ACTION_STALE`(409)、`AUTH_OPERATION_EXPIRED`(410)、`AUTH_REPLAY`(409)、`IDENTITY_MISMATCH`(409)、`IDEMPOTENCY_CONFLICT`(409)、`INPUT_INVALID`(422)、`SENSITIVE_INPUT_REQUIRES_CARD`(422)、`EVENT_CURSOR_INVALID`(409)、`EVENT_CURSOR_EXPIRED`(410)、`CSRF_INVALID`(403)、`UPSTREAM_UNAVAILABLE`(503)。游标过期重新读取快照，不重发消息。精确安全文案由前后端契约测试固定，不透传异常、凭据或手机号存在性。



达到额度后仍可打开认证卡片；卡片操作不算普通聊天消息，也不借此刷新访客数据保留期。到期后的已失效访客数据不得凭重新认证复活或认领；新的认证可继续，但旧数据的处理遵守过期规则。



## 4. SSE 与 assistant-ui Vue



规范类型是§3的ChatEvent判别联合，不是任意type/payload对象。每条SSE持久事件使用 `id: <eventId>`、`event: <type>`、`data: <完整ChatEvent JSON>`；三处type/id必须对应，顺序在对话范围内明确。以下类型及payload已由AI方案采纳，后续P0写成可执行fixture：



| type | payload |

| --- | --- |

| `message.delta` | `{messageId:string;delta:string}`，更新同一 assistant 消息的持久正文 |

| `message.upsert` | `{message:ChatMessage}`，同 ID 替换/补齐，不追加重复消息 |

| `run.updated` | `{run:RunRef}` |

| `artifact.updated` | `{artifactId:string;version:number;status:'pending'|'ready'|'failed'}` |

| `viewer.updated` | `{viewer:Viewer}`，更新额度和到期提示，不代替后端授权 |

| `viewer.expired` | `{code:'VISITOR_EXPIRED'}`，立即停止显示后续内容并结束订阅 |

| `error` | 完整 `ApiError = {error:{code,message,retryable},requestId}`，不得仅发error子对象 |



选项、追问、认证请求和补充问题通过 ChatMessage.parts 表达，不额外依赖模型生成 DOM。由 Portal 的 External store / RuntimeAdapter 适配，不把 LangGraph 原始事件当作组件协议。



任务执行、最终结果与可恢复内容由后端持久化；不要求逐 token 网络分包形式原样重演，但恢复后的正文、卡片、文档及状态必须完整且不重复。允许合并文本增量写入，游标补齐与快照的水位必须一致。



v1.0水位规则：snapshot的messages/activeRun/viewer/merge与cursor取自同一一致性读视图；它包含截至cursor全部已提交的业务效果。订阅只回放严格晚于after的事件，再接实时事件，回放转实时不能留缝；cursor=null仅表示该会话尚无已提交事件（水位0）。游标不透明，不能由前端排序或递增；绑定系统/主体/会话，错误归属或未来游标409 EVENT_CURSOR_INVALID，已淘汰游标410 EVENT_CURSOR_EXPIRED，后者先重新读快照再订阅。after与Last-Event-ID同时存在须一致，否则409。客户端记录已应用eventId防重复；消息正文和对应可回放事件同事务提交，不能把不持久的token当可恢复进度。message.delta只追加到指定assistant消息最后一个text part；无text则追加text part，保留其余卡片；新消息先发message.upsert基线。message.upsert整体替换同ID消息，而非再追加一条。error payload固定为完整ApiError（含requestId），不得混用仅error子对象。



### Task2/3采用的续接与去重细则



- **snapshot.cursor、ChatEvent.eventId、SSE id和下次after属于同一不透明游标体系。** 成功应用一条事件后可直接把其eventId作为下一次after，不增加第二个cursor字段、不做编码转换或解析排序；未成功解析/应用不能推进游标。

- cursor=null且无Last-Event-ID时从水位0补齐；取过快照则严格从其cursor之后开始。eventId只在该对话的可信事件记录定位顺序，不以UUID字面大小排序。浏览器关闭、断网或SSE EOF仅表示订阅结束，不能由此将run标成completed或调用cancel。

- 传输允许断线后的重复投递，同一eventId及payload不可变；客户端先按会话/身份epoch拒绝旧流，再去重eventId后应用一次。每次安装新snapshot停止旧订阅并更换本地epoch，由新水位恢复；即使conversationId相同也不能让旧流迟到delta追加到新快照。

- 可在持久化前合并模型token，不能复用eventId改写已提交delta或用未落库token推进水位。业务消息正文/状态与事件同事务；存储回收导致无法从after连续补齐时明确410 EVENT_CURSOR_EXPIRED，由客户端重取快照，不能静默跳过遗漏事件。

- message.delta的runId非null，且绑定被更新的assistant消息；run.updated的runId非null且等于payload.run.id。归并历史的message.upsert、viewer事件或不归属单次执行的artifact/error可为null。消息ID跨快照、重放、重试和归并稳定，(createdAt,id)确定历史顺序；不得在每次协议转换时生成新消息ID。

- HTTP200且Content-Type为text/event-stream才开始按SSE解析；预流错误使用正常HTTP状态和ApiError。心跳只用 `: ping` 等注释，不含id、不持久化、不推进cursor。data多行按SSE规则以换行拼接，解码须跨UTF-8/CRLF和网络分包保留状态。流中error保持完整ApiError；运行状态以持久run.updated或重新读取snapshot为准，不能仅由error事件推断任务已终止。

- /events必须先检查当前对话访问权，失败403 FORBIDDEN；已有本对话读取权但after来自其他会话/主体或不存在/未来，返回409 EVENT_CURSOR_INVALID。已淘汰历史返回410 EVENT_CURSOR_EXPIRED。分别处理对象越权、错误游标和已淘汰游标，不用其他会话兜底补齐。



### Toolkit到业务协议的实际映射



| Toolkit/内部执行事实 | 业务层责任与门户收到的结果 |

| --- | --- |

| 原生流 `{type:'token'|'message'|'error',content:...}`、`[DONE]` 没有可靠业务水位 | 原生流不直接交门户。新增执行适配以可信run/thread运行，worker持久化后发布本节ChatEvent；`[DONE]`不映射成completed |

| 原生human/ai转换没有保留稳定业务messageId | messageId在业务消息首次创建时分配并持久化；user→HumanMessage、assistant→业务ChatMessage，图输入沿用稳定ID。原native转换器不作为业务历史的ID来源 |

| LangGraph文本增量、完整结果 | 先持久化assistant基线message.upsert，再增量message.delta；恢复重建用同ID整体upsert，不把重生成文本再次拼到旧残片 |

| interrupt和Command | 存为绑定requestId的choice/input_request及waiting_input；/actions验证有效动作后恢复同业务run。OTP等认证字段走§5，不作为普通resume输入 |

| 原生history/threads按调用方thread/user标识读取checkpoint | 业务snapshot从权限受控的业务库读取；thread只由服务端映射。历史/文件归并不拼接checkpoint、不把原生/threads当用户授权 |

| TaskData、tool消息、AG-UI完整state或RAW | 不直接作为公共payload；仅把已批准的用户可见内容转为现有六类ChatPart及事件。v1.0.1不增加progress/citation卡片或泄露工具参数、提示词、完整graph state |

| checkpointer/Store | 直接复用现有初始化和Agent注册；额度、认证、归并、持久job、事件/对象权限在新增业务层实现 |



已读门户综合研究；本次保留用户之后明确选择的“前置业务层＋内部共享密钥HTTP”边界。worker调用新增受AUTH_SECRET保护的 `/internal/xiaohe/runs/{run_id}/stream`，区分start/recover/resume，内部ToolkitEvent只在服务端使用；业务协议由worker落库后发布。与研究中可选的进程内直调图相比，这一内部调用选择不改变门户类型或Task2/3接口。



## 5. Portal ↔ AI 认证卡片操作



Portal 复用 `oauth4webapi` 生成 state/nonce/PKCE，新增“准备但不跳转”的函数供卡片使用；现有 `startPortalLogin` 继续基于相同准备函数导航。不得在两个模块各自生成不关联的授权请求。



`OidcBinding = {clientId:string;redirectUri:string;state:string;nonce:string;codeChallenge:string;codeChallengeMethod:'S256'}`。verifier 只留原浏览器的短期 OIDC 事务，不交给 AI 或模型。



```ts

type AuthStage = 'PHONE_REQUIRED'|'AGREEMENT_REQUIRED'|'BROWSER_COMPLETION_REQUIRED'|'OIDC_REQUIRED'|'COMPLETED'|'FAILED'|'EXPIRED'

type BrowserCompletion = {method:'GET'|'POST';action:string;fields:Record<string,string>}

type AuthOperation = {operationId:string;stage:AuthStage;expiresAt:string;agreement?:{version:string;url:string};completion?:BrowserCompletion}

type AuthIntent = 'lead'|'register'|'login'|'restore'

type CreateAuthOperation = {intent:AuthIntent;oidc:OidcBinding;browserCompletionChallenge:string;browserCompletionChallengeMethod:'S256'}

```



| 方法与路径 | 请求 | 响应 |

| --- | --- | --- |

| `POST /auth/operations` | `{intent:'lead'|'register'|'login'|'restore';oidc:OidcBinding;browserCompletionChallenge:string;browserCompletionChallengeMethod:'S256'}` | `201 AuthOperation`；绑定当前浏览器会话和已登记系统。restore 支持普通门户登录/已有中心会话续接，不额外建立账号体系 |

| `POST /auth/operations/{id}/captcha` | `{}` | `PictureChallenge = {token:string;secretKey:string;imageBase64:string;pieceImageBase64:string}`；适配现有滑块，不记录图片/挑战内容 |

| `POST /auth/operations/{id}/captcha/check` | `{token:string;pointJson:string}` | `200 {valid:true}`；失败返回安全错误，保留中心防重放 |

| `POST /auth/operations/{id}/sms` | `{mobile:string;captchaVerification:string}` | `200 {challengeId:string;retryAfterSeconds:number;expiresAt:string}` |

| `POST /auth/operations/{id}/verify` | `{mobile:string;challengeId:string;code:string}` | `200 AuthOperation`；老用户进入浏览器完成，新用户进入主动协议确认 |

| `POST /auth/operations/{id}/agreement` | `{accepted:true;version:string}` | `200 AuthOperation`；中心保留并消费对应证明，前端不伪造注册成功 |

| `POST /auth/operations/{id}/complete` | `{}` | `200 ChatSnapshot`（MERGED / SKIPPED_EXPIRED；无来源时merge=null）或 `202 ChatSnapshot`（MERGE_PENDING，Retry-After:1）；仅在原Portal OIDC成功换码及中心可信结果匹配后建立AI用户会话和持久认领意图，返回账号canonical conversationId |

| `POST /auth/operations/{id}/cancel` | `{}` | `204`；取消卡片事务并清理前端敏感字段，不等同于取消普通生成任务 |



`completion.action` 只允许已登记的固定 issuer、精确完成端点或绑定的授权端点；精确路径由 SSO 计划在 P0 冻结。Portal 的 CSP 必须只放行该受信 issuer 的必要 form-action/connect-src，不能使用通配域名。登录操作及恢复句柄由§9认证上下文Cookie/CSRF及操作绑定共同授权，不以句柄本身授权。



原卡片浏览器另外生成 `browserCompletionVerifier`，只把 S256 challenge 提交给 AI/中心，与 OIDC PKCE verifier 分开。顶层完成 POST 时由浏览器把该值补入 `browserVerifier` 字段，AI 返回的 fields 不得包含或覆盖这个字段。该 verifier 只留卡片内存，用后立即清理；卡片中途刷新丢失 verifier 时重新开始操作，不以任意服务器下发值替代。OIDC_REQUIRED 的普通授权 GET 不附带此 verifier。



手机号、OTP、captchaVerification、完成凭证和注册证明均只在对应表单/短期事务生命周期内处理，表单关闭/跳转/成功/失败时及时清理；不能串入 assistant-ui 消息 store。sessionStorage 仅保留原 OIDC state/nonce/verifier 事务和非敏感的 operationId、允许的页面/锚点、客服打开状态，不保存其他凭据。



Portal 回调先执行现有签名/state/nonce/PKCE/UserInfo 校验，再调用 AI complete；前端回调成功标记不是后端授予身份的依据。AI complete 从认证中心受信通道领取结果。普通门户登录同样须先关联 restore 操作；否则只能获得 Portal 身份，不能编造 AI 登录结果。



登录过程中访客到期、目标账号不一致、重复回调、不同标签页及第三方来源混接均需要契约测试。原访客归并后立即失去旧数据访问权限；新快照替换旧 store，关闭旧 SSE，再连接 canonical conversationId，禁止混入旧身份的异步事件。



Portal OIDC 换码已成功但 AI complete 临时失败时，重试 AI 完成操作，不重复兑换已消费授权码。202 时 viewer 已是 user，界面区分“已登录，正在合并”和 MERGED；按 Retry-After 重试同一 complete，重新打开先读 session 快照恢复，不重走 OIDC。目标正在运行时 worker 到 terminal/waiting_input 安全点自动归并；waiting_input 保留问题及 checkpoint 并让出写入位置。认领事务先于访客到期成功时，其资料受用户权属保护，匿名清理不得删除；到期先发生则返回 SKIPPED_EXPIRED，账号仍可登录但不复活旧数据。普通登录无可认领来源时 merge 为 null。snapshot 读取返回权威归并状态，不增加隐式 merge SSE 事件；同操作重试以已绑定账号授权，不因原桥接事务随后到期而要求重复登录。中心可信结果、AI 会话与归并各自完成前不能混称全部完成。



§5公开DTO及200/202语义由AI方案采纳；SSO通用参数随后由用户另行确认，范围见§8。202专用于“AI已认证且资料归并等待”，不能表示“中心身份尚未确认”；未换码/中心暂未就绪的精确公开错误映射仍待补齐，不伪造user快照。complete响应同样包含一致水位；装入canonical快照时清理旧身份/旧流，后续仅从新cursor订阅。AuthStage保留现有类型。



## 6. AI ↔ SSO 契约归属



中心新增能力由 `xiaocai-admin-server` 计划固定端点、字段、应用认证、精确时限与错误码，并在实施前与 AI 适配对齐。规划至少定义以下操作签名对应的 HTTP 契约：



- `createLoginTransaction(trustedApplication, oidcBinding, browserCompletionChallenge) -> transaction`

- `getCaptcha(transaction) / checkCaptcha(transaction, token, pointJson)`

- `sendSms(transaction, mobile, captchaVerification) -> challenge`

- `verifySms(transaction, mobile, challengeId, code) -> phoneVerified | agreementRequired`

- `acceptAgreement(transaction, version, accepted) -> browserCompletionCredential`

- `completeInBrowser(singleUseCredential, browserCompletionVerifier) -> issuerSession + resumeOriginalAuthorization`

- `claimCompletionResult(trustedApplication, transaction) -> verifiedIssuerSubjectAndBinding`

- `cancelLoginTransaction(trustedApplication, transaction)`



这些是待实现契约，不是既有可调用函数。跨站顶层 POST 的 Lax Cookie、issuer 同源续接/CSRF、不同中心账号显式确认、单次原子消费及 Portal 授权码兑换关联，依照已确认设计落实。当前 public token 只面向 UserInfo，AI 不把它用作 AI API Bearer；旧云鹤票据不能直接代替新链路。



AI 受信客户端与 Portal public client 分别登记并绑定权限，不把客户端 secret 下发浏览器，不用放宽原 Cookie API CORS/CSRF 取代受信事务。



### issuer 同源续接 UI/API 分工



- Portal 当前 Vue/Quasar 工程交付 `GET /sso/bridge/continue` 页面，由认证源 origin 的静态入口提供；不挂在 PortalLayout 下，不打开客服、不请求 AI 接口。

- 中心顶层 `POST /sso/bridge/browser-completion` 校验并暂存单次结果后303到上述页面；页面本身不要求在跨站303首跳已收到临时 Strict Cookie。页面加载后再以 issuer 同源 fetch 查询 context。

- 中心 `GET /sso/bridge/continuation` 返回既有 `CommonResult<BridgeContinuationView>`，`POST /sso/bridge/continuation` 消费带CSRF的 `{decision:'CONTINUE'|'SWITCH'|'CANCEL'}`，返回 `CommonResult<{nextUrl:string}>`，由页面导航；UI与JSON路径分开，不使用fetch自动跟随303模拟本机可读JSON。

- `BridgeContinuationView` 的规划基线为 `{stage:'READY'|'ACCOUNT_SWITCH_REQUIRED'|'EXPIRED'|'CANCELLED';expiresAt:string;csrf:{headerName:string;token:string};issuer:string;redirectUri:string;currentAccount:{label:string}|null;targetAccount:{label:string}}`。READY只自动提交一次CONTINUE，不同账号等待显式SWITCH/CANCEL；到期/取消禁止提交。精确DTO由SSO计划P0核对冻结。

- `nextUrl` 仅能是本issuer绑定的授权目标或该context已登记的精确callback目标；页面不得以自身origin构造Portal callback，不接受用户传入returnUrl。

- SSO计划中的 `portalClientId` 统一表示Portal public客户端，AI受信 `applicationId` 单独管理。AI公开 `browserCompletionChallenge` 由适配器映射中心私有 `browserVerifierChallenge`，算法固定S256；浏览器表单统一字段 `browserVerifier`，不能与PKCE verifier串用。

- 普通门户登录/已有SSO恢复同样创建restore操作并关联原OIDC请求，即使不再次短信验证，也只有授权码成功兑换后才可领取该操作的AI可信完成结果。

- 现有 portal.lvh.me 与 sso.lvh.me 属同站跨源；其通过不等于不同site的SameSite行为通过。Portal计划承担真正跨站的Lax/Strict续接浏览器用例，SSO计划承担后端状态/CSRF测试，两者都不能代替真实部署联调。



## 7. 联调证据与范围



每个工程单独记录源码完成、单元/契约测试、真实上游、浏览器、部署和业务验收。所有未执行检查保留未执行状态；真实短信、付费模型、生产部署和现有数据操作不由本次规划授权。



## 8. v1.0.1 采纳记录、Task2/3交付及SSO私有P0



2026-11-03：认证源聊天已复核公开DTO、200/202归并、browserVerifier及续接路径可作为门户Task1结构基线，并指出事件类型、水位和输入约束缺口；本文件已补齐这些公共约束。门户以 `frontend/src/chat/contracts.ts` 及契约fixture检验消费格式。固定范围为Portal↔AI公开消息/事件/认证操作的形状与语义；服务端仍未实现，不能以门户fixture证明服务端行为。两份后端计划继续保留其P0执行任务；具体私有端点认证、Cookie属性/超时/撤销传播、登记和真实SSO结果仍须在后端实施前对齐验证。本记录不替其他聊天勾选任务或解暂停状态。



### 本次采纳及版本差异



2026-11-03 后续授权：用户保留Vue＋Quasar＋assistant-ui Vue，后端在既有toolkit扩展；共享文件§3–5及§8本轮由AI方案方编辑，门户方不并发修改。§1原有单一基线要求继续成立，本次不另建公共合同副本。直接确认的全局user_id唯一租户/系统归属、内部共享密钥和dev-fengfeng分支保留。



**文档修订v1.0.1，线上的目标路径和wire DTO仍为v1.0。** §3两段TypeScript类型逐项保留，与门户现有contracts.ts对应；不新增字段、事件种类、ChatPart、progress/citation、graph state、reset接口或成功包装。没有必须返工的Task1类型变化。新增/明确的只有行为约束：



| 项目 | AI方案采纳结果 | 门户落点 |

| --- | --- | --- |

| ChatEvent各payload、输入码点长度和action互斥 | 已存在于v1.0，本轮复核并采纳；安全错误为完整ApiError，INPUT_INVALID列入错误清单 | Task2校验请求及响应/错误；Task1既有类型保留 |

| 业务conversationId与内部thread/run映射 | 长期业务ID由后端拥有，物理thread可更换；messageId稳定 | Task3只存业务ID，不解析或发送checkpoint配置 |

| 游标、水位与重复事件 | snapshot.cursor＝同体系eventId；已应用eventId可直接作为下次after；HTTP受理响应不推进游标 | Task2续接参数；Task3去重、快照替换和本地epoch隔离 |

| 幂等受理 | 原message/run ID保留，viewer和run.status读当前权威状态；无重复扣额/续期 | Task2重试同client ID；Task3不让迟到响应回退quota/TTL/身份 |

| 会话过期 | 访客410、用户401，均不自动重置匿名额度或带旧身份恢复；新认证初始化仍列私有P0依赖 | Task2错误分类；Task3停止自动恢复并清理私有视图 |

| complete200/202 | 202已认证待合并，Retry-After:1；200 merge三态/null按§5，无重复OIDC换码 | Task2/3可模拟并正确装入快照；真实认证仍等Task5/6及SSO P0 |



### 可供Task2/3使用的纵向协议样本



以下ID仅是隔离fixture标签，不是实际服务记录；无模型、短信或专业业务产物执行。示例viewer初始为guest、messageCount=0，事件水位从null开始。



| 顺序 | 请求/持久事实 | 消费结果 |

| --- | --- | --- |

| 1 | POST/session → 200，conversationId=C1、messages=[]、activeRun=null、cursor=null | 装入snapshot与CSRF，再订阅C1事件 |

| 2 | POST C1/messages，clientMessageId=K1，text=“我想了解供应商服务” → 202 | user消息M1、业务run R1 queued、viewer.messageCount=1；不移动cursor |

| 3 | E1 message.upsert(M1)，E2 run.updated(R1 queued)，E3 message.upsert(M2 assistant基线)，E4 run.updated(R1 running) | 每条完整ChatEvent分别带eventId及C1；依次应用并保存已应用水位 |

| 4 | E5 message.delta(M2,“请问您希望了解哪些内容？”)；E6 message.upsert(M2完整正文及choice，requestId=Q1、options含intro)；E7 run.updated(R1 waiting_input) | M2按同ID替换；快照cursor=E7、activeRun为R1 waiting_input；关闭窗口不取消 |

| 5 | 重开POST/session → 当前snapshot.cursor=E7；GET events?after=E7 | 只收严格晚于E7的事件；若E8已发生，必须补齐，再转实时 |

| 6 | POST C1/actions，clientActionId=A1、requestId=Q1、optionId=intro，省略text → 202 | user消息M3、原R1 queued、viewer.messageCount=2；对应新事件进入同一游标体系 |

| 7 | 独立幂等测试分支：先让R1完成，再受理另一条新消息，随后同A1同参数重试 | M3/R1 ID不变，run.status为当前completed，viewer次数/期限为当前值；重试自身不新增消息/任务/续期。不能缓存第一次的viewer |

| 8 | worker完成后持久化完整assistant消息及run.updated(R1 completed)，后续snapshot无活动生成任务 | 流EOF本身不表示成功；重新打开读到相同消息ID及最终结果 |



补充分支：重发同eventId只能应用一次；外来/未来游标409 EVENT_CURSOR_INVALID；已淘汰游标410后重取snapshot；guest到期收到viewer.expired或预流410后清理私有视图；auth complete以200/null、200/MERGED、200/SKIPPED_EXPIRED及202/MERGE_PENDING四个fixture分别验证，不依赖真实中心。



### SSO私有P0：通用参数已确认，环境及补充策略待核实



依据只读 [SSO桥接计划](C:/Users/70367/.codex/worktrees/sso-backend/xiaocai-admin-server/docs/superpowers/plans/2026-11-03-xiaohe-sso-bridge.md) 的Task1与P0核对项。2026-11-03已读取门户聊天的人类明确回复“采用建议值，冻结通用参数”：批准的通用参数见下表，issuer/client/Origin/callback/applicationId实际登记、可信网段及权威用户映射仍另行核实。AI本地8小时会话、到期后新认证上下文和其他未列入该确认问题的建议不自动获批。参数确认仅授权同步文档，不授权后端实现、登记、配置变更或部署。



| 参数/状态 | 固定值或待确认建议 | 实施前边界 |

| --- | --- | --- |

| 受信应用认证：已确认 | 每应用独立Bearer密钥，与toolkit AUTH_SECRET分开；HTTPS server-to-server，应用标识头固定 `X-SSO-Bridge-Application-Id`。沿已采纳提案使用至少256-bit随机secret，只创建/轮换显示一次，中心存单向摘要 | 实际applicationId/secret未登记或注入；不把该机制施加到已有OIDC客户端。不宣称Bearer自身防重放 |

| 密钥轮换：通用规则已确认 | 旧secret立即撤销，无双密钥宽限；应用停用阻止新桥接调用 | authVersion变化对旧事务/结果读取的细则需SSO落成可测试合同；不轮换其他RP的client_secret、不清除已归并用户资料，不等于所有RP全局退出 |

| 应用绑定 | applicationId绑定一个启用Portal public client、精确Origin及已登记callback；仅super_admin登记；body不能覆盖绑定 | issuer/client/Origin/回调实际值与管理登记尚未提供，不用示例替代 |

| 通用时限：已确认 | 事务600秒绝对截止不滑动；浏览器单次完成credential120秒；原授权码在事务deadline前兑换并固化结果，结果可独立重读至exchangeAt+600秒，可晚于事务截止但不产生新code/授权/绑定 | 各自now>=expiresAt拒绝。临时Strict Cookie120秒仍为补充建议，短信challenge/proof沿用既有300秒；此次问题未逐项列出的值不扩大为新确认。AI operation阶段期限与结果读取窗口须按已确认规则落成精确合同，不以UI过期重兑code |

| Claim重试/重放：已确认 | Idempotency-Key固定为中心transactionId，结果唯一作用域applicationId+transactionId；同key只读回同一固化结果，换key/主体冲突；禁止重复兑换OIDC code | create阶段尚无中心transactionId，其稳定操作键和其他写步骤幂等键仍待补充确认；取消/轮换后的状态边界由SSO补齐，不擅自改已确认claim键及结果时限 |

| 可信客户端IP与browserInfo | 按SSO最新建议：Portal ingress剥离/覆盖外部同名头后设置单值 `X-Portal-Client-IP`；AI只信登记Portal ingress网段的该值，校验IPv4/IPv6并绑定auth operation；调用SSO时发送 `X-Bridge-Client-IP`。中心仅对已认证AI应用及允许内网来源采纳；browserInfo只作风险上下文 | 代理网段、header、IP格式和browserInfo结构仍待P0确认。浏览器头不可透明透传，SSO socket peer/worker/NAT地址不冒充访客IP；中心仍保留app级风控 |

| 新增AI全局用户映射：已确认 | 首次成功消费可信结果由AI生成全局user_id，唯一绑定issuer+sub+sourceTenantId；tenant/system取AI受信登记。重复/并发收敛同一ID，归属冲突拒绝，不复制用户 | 不新增claim字段、不解析sub、不把中心会员ID当AI ID；方案已确定，数据库约束和事务留待实施验证。不重开new-api已验通映射 |

| AI会话时限：已确认；补充认证策略仍待核实 | 用户明确采用“建立后绝对8小时、不滑动续期”。固定AI会话expires_at=created_at+28800秒，created_at为本次可信AI会话建立时刻；中心authenticatedAt保留原含义。token32字节opaque、摘要存储和Cookie属性仍沿现有建议。到期返回401 AUTH_REQUIRED，不降级匿名、不删除账号历史/产物；未发送草稿仅在同一用户可信恢复后还原，账号切换时清空/隔离；恢复后重取原对话 | 门户本地已登录不能替代AI会话。中心SSO仍有效时可走restore/OIDC恢复，是否重新输入凭据取决于中心会话/策略；不能保证永远无跳转。具体CSRF/到期认证准备和撤销传播仍待核实，不因本次8小时确认自动实现 |



本次[只读核验报告](../research/2026-11-03-xiaohe-auth-config-mapping-audit.md)已按后续人类确认更新：中心自身也有绝对8小时；sub是组合标识，但AI自行建立唯一映射即可，无需新增claim字段。此前扩展私有结果的建议已撤回，不再作为阻塞或要求再次确认。本地登记值见下表；真实登记、密钥与网络可达性仍待实施验证。



| 已确认/预留的本地参数 | 值与状态 |

| --- | --- |

| issuer / Portal public client | https://sso.lvh.me:8443 / app_localportal；沿用既有本地配置 |

| Portal Origin / callback | https://portal.lvh.me:9444 / https://portal.lvh.me:9444/portal/auth/callback；已核对本地TLS代理文件 |

| AI系统与业务租户 | systemId=portal → tenantId=1；已由用户明确确认，显式登记，不从sourceTenantId推导 |

| Bridge applicationId | 31555063-bbe3-4907-8745-c553208fff23；认证源方案已预留，尚未登记 |

| 进程/网络 | Portal开发服务127.0.0.1:9100，Java上游127.0.0.1:18080，AI API端口预留18090；未启动AI或验证其入口 |

| secret / 信任网段 | secret待登记功能生成并安全注入，不贴聊天；实际peer/最小allowlist实施时测量，不默认信任整个Docker网段 |



完整来源与配置位置见认证源维护的[本地HTTPS联调参数](C:/Users/70367/.codex/worktrees/sso-backend/xiaocai-admin-server/docs/sso/xiaohe-local-integration.md)。该文档记录其他聊天已做的环境准备，不表示本聊天执行过启动、登记、HTTP或真实认证测试。余下认证上下文/CSRF等技术合同仍须完善，AI后端实现暂停。



**响应策略已确认：仅新bridge接口使用CommonResult body＋语义HTTP状态，不再把“所有错误HTTP200”作为本次待选方案。** 下表错误类别的HTTP语义据此固定；具体Java数字码/逐阶段归因由SSO owner落实，公开AUTH_NOT_READY及认证限流code仍是待补充确认项。已有OAuth token/UserInfo等端点保留原响应格式，不套用CommonResult。



| 私有条件/建议code | 建议中心HTTP | AI到公共错误的建议 |

| --- | --- | --- |

| SSO_APP_UNAUTHENTICATED：应用标识/secret无效 | 401 | 应用配置/服务故障，503 UPSTREAM_UNAVAILABLE；不能误报为最终用户掉线 |

| SSO_APP_DISABLED：应用/client停用或绑定不允许 | 403 | 503 UPSTREAM_UNAVAILABLE并停止自动重试；不把内部secret/登记细节发给浏览器 |

| SSO_BRIDGE_BINDING_INVALID：格式/绑定参数不合法 | 400 | 已归因到用户输入时INPUT_INVALID422或IDENTITY_MISMATCH409；纯服务配置问题503，P0按具体错误区分 |

| SSO_BRIDGE_EXPIRED / SSO_BRIDGE_REPLAYED / IDEMPOTENCY_CONFLICT | 410 / 409 / 409 | AUTH_OPERATION_EXPIRED410 / AUTH_REPLAY409 / IDEMPOTENCY_CONFLICT409；其他状态冲突由P0逐阶段固定 |

| SSO_AUTHORIZATION_NOT_EXCHANGED：原OIDC码未成功兑换 | 409，暂未就绪时retryable=true | 建议公开409 AUTH_NOT_READY、retryable=true；这是待确认新增code，不得误用202 MERGE_PENDING或返回user快照 |

| SSO_BRIDGE_RATE_LIMITED / SSO_UPSTREAM_UNAVAILABLE | 429附Retry-After / 5xx | 限流建议另定公开认证限流code和Retry-After，服务不可用用UPSTREAM_UNAVAILABLE503；不映射成访客聊天100条额度错误 |



通用凭据/标识头、轮换、事务和结果时限、claim键及CommonResult＋语义HTTP已冻结，不再重复请求确认。实际环境登记、代理信任、权威身份映射、未确认的AI会话/认证准备和具体私有DTO/错误映射仍须齐备；参数已确认不等于后端执行获准。门户Task2/3继续消费公开fixture，真实登录在上述依赖与实施授权均具备后才验证。



### 对现有new-api等OIDC接入的兼容边界



**人类已确认的实际基线：**2026-11-03门户聊天原始回复为“我的new-api之前已经认证通过了，用的新增的OAuth2的认证通路。”兼容评估以这条已验通OAuth2通路为准，保留当前provider、客户端凭据、授权/token/UserInfo端点、callback、UserInfo字段和sub/用户标识映射。本轮不再询问用户属于哪个provider、不要求切换到内置OIDC、补改邮箱或重复认证。内置OIDC的email校验属于另一实现分支的静态发现，不作为已验通通路的阻塞；本次未重新执行该既有认证。



- 新的应用Bearer、`X-SSO-Bridge-Application-Id`、桥接转发头、事务/credential TTL与CommonResult＋语义HTTP仅用于新增 `/internal-api/system/sso-bridge/**` 及相应bridge流程；不得全局套到既有OAuth2/OIDC/UserInfo/Discovery/JWKS/注销端点。

- 现有new-api等RP继续使用原client_id/client_secret、授权码、token及UserInfo合同；不要求新增bridge应用登记、不改client认证方式/回调/令牌TTL、不替换其client_secret。SSO桥接的Portal public client只为其登记入口服务，不能把其他confidential client改成public client。

- 普通OIDC授权码兑换没有匹配bridge事务时，应继续原成功路径，桥接关联逻辑不执行；不能把“没有bridge transaction”当旧RP登录错误。现有Cookie登录CORS/CSRF和中心会话Cookie策略保持原合同；临时Strict Cookie仅属于新bridge续接，不全局改中心Cookie。

- 共享中心会话有实际联动：在小鹤流程显式切换中心账号或调用既有中心注销时，其他RP下次SSO登录、中心令牌校验/续期可能受原有中心策略影响；这属于共享身份行为。不能因此声称其他系统永不受影响，也不能把new-api既有本地会话自动切成小鹤的新账号或承诺实时全局退出。

- 先前只读核对的new-api本地HEAD为 `177a0b8cf`：[通用OAuth](D:/Code/new-api/oauth/generic.go:89)支持参数或Basic方式提交client凭据；[内置OIDC](D:/Code/new-api/oauth/oidc.go:51)仅作旁支源码参考。当前实际已验通入口以人类确认的新增OAuth2通路为准，不能以另一分支的字段要求覆盖它。未读取真实密钥、未改该工作区；用户此前验通与本次未运行新桥接兼容回归分别记录。

- 回归留待获授权的SSO实施阶段，围绕已验通OAuth2通路检查provider/凭据/端点/callback/UserInfo/sub映射不变，以及普通非bridge授权、错误secret/失效code、并发、bridge密钥轮换、中心账号切换/取消/注销、原CORS/CSRF和响应格式。使用既有验通作为基线，不要求用户为本轮文档工作再次认证；未来新增实现的回归结果单独记录，不能直接沿用旧通过数宣称新桥接兼容通过。



本次兼容检查参考 [OWASP OAuth2](https://cheatsheetseries.owasp.org/cheatsheets/OAuth2_Cheat_Sheet.html) 与 [ASVS 5.0.0](https://asvs.owasp.org/) 的协议/认证会话边界；未进行完整ASVS审计或声称满足全部要求。



### 当前交付状态



AI计划已采用本次公共约束；P0/P3/P4/P6/P8/P9覆盖相应具名验证任务，13任务65步骤保持未执行。本轮只静态核对文档/源码及已有门户类型；没有写业务实现、运行产品测试、安装、启动服务、调用模型/短信或部署。门户Task1的历史通过记录保持其原证据层，不冒充本次真实后端联调通过。



本轮静态文档检查：19个共享类型与当前门户contracts.ts一致，原两段TypeScript类型定义未变；未编辑的§1/§2/§6/§7与本轮开始快照一致。SSO和门户源文件在各自聊天有并发更新，本聊天只读并据最新SSO建议同步本节，没有写入它们。toolkit仍为dev-fengfeng，仅原有未跟踪.codegraph，无本轮产品代码差异。



共享必测场景：老账号并入单一长期对话；新用户主动协议确认；卡片凭据不进入消息/日志；Portal+中心+AI 身份一致；来源租户1与不同业务租户隔离；100/101边界及重试；滚动4小时与到期停止；归并/清理/迟到结果竞态；页面断开后任务继续与恢复；MinIO 私有对象与版本；不同账号/标签页/系统的越权拒绝。



三个专业 Agent 的流程、业务文档生成规则和来源知识仍由用户后续另行设计。测试替身不等同于这些 Agent 已上线，不恢复已撤销 Dify 方案。





## 9. v1.0.2：P8认证专用上下文与公开重试（2026-11-04冻结）



用户已在门户聊天确认采纳AI的[p8-auth-contract-proposal](../../agent-service-toolkit/docs/xiaohe/integrations/p8-auth-contract-proposal.md)，并授权补齐门户消费端。以下是新增公共合同，不表示AI P8端点已运行，也不改变SSO既有600/120秒、claim结果读取及New API OAuth2通路。



### 独立认证能力



| 项目 | 冻结值与约束 |

| --- | --- |

| 获取 | `POST /ai-api/v1/auth/context`，body精确`{}`；当前受信系统、HTTPS入口、精确Origin校验后返回`200 AuthContext`，`Cache-Control:no-store` |

| DTO | `AuthContext={csrf:{headerName:'X-Xiaohe-Auth-CSRF';token:string};expiresAt:string}`；精确两字段及精确csrf两字段；非空opaque token，UTC ISO expiresAt；不能含viewer、额度、对话、历史、手机号或其他凭据 |

| Cookie | `__Host-XIAOHEAUTH`：opaque能力，服务端摘要存储，`Secure;HttpOnly;SameSite=Lax;Path=/`、无Domain；不能作为聊天/Artifact/身份授权 |

| CSRF使用 | `/auth/operations`及其captcha/check/sms/verify/agreement/complete/cancel均用认证专用Cookie+`X-Xiaohe-Auth-CSRF`；普通聊天写请求和`POST /auth/logout`仍用ChatSnapshot的CSRF，两个证明不能互换 |

| 复用 | 同一有效认证上下文返回稳定CSRF，可由同浏览器标签页复用；不因读取/新标签页无故轮换。无/失效上下文可新建认证能力；不得新建guest、删除tombstone、重置计数、续聊天期限或返回旧内容 |

| 基础窗口 | 新认证操作基础绝对窗口1200秒，不因客户端重复操作滑动。中心事务、credential和短信仍各自按较短的有效期校验；AuthOperation.expiresAt由服务端返回，不能用1200秒放宽中心期限 |

| 上下文期限 | 至少覆盖仍有效的绑定操作、绑定中心事务截止及合法claim重读窗口；服务端可仅为这些既有绑定延长认证能力，有效截止以AuthContext.expiresAt为准，不给聊天身份续期。AI用户固定8小时不滑动规则不变 |

| 绑定 | 服务端绑定来源会话提示、system/Origin、认证上下文、operationId、独立browser challenge及原OIDC tuple；前端generation/取消阻断旧响应，后台逐操作校验。任意句柄不单独授予访问 |



过期guest的/session仍410、失效user仍401；新auth/context允许认证，不能复活旧消息或扩大配额。认证成功才按既有canonical规则读取用户历史，过期来源记录返回SKIPPED_EXPIRED。认证上下文不是AI用户，未认证不能伪造user快照或202。



首次可信完成前必须使用原绑定认证上下文。可信完成已持久化后的同operation归并重读，继续遵循§5“以已绑定账号授权、不因原桥接事务到期重复登录”：当前AI用户Cookie与认证CSRF均有效，且user/tenant/system与operation已绑定账号精确一致，才允许返回200/202；新认证上下文本身不能认领别人的操作。关闭后重开先读session的merge状态，并取得当前认证CSRF后重试同operation。



### complete响应全部丢失的恢复补充（v1.0.3，2026-11-04用户确认）



用户明确允许：中心可信结果及AI建立会话已提交，但浏览器连用户Cookie都没有收到时，原浏览器可以凭**原绑定且仍有效的认证上下文Cookie＋对应认证CSRF**重试同一operation的complete。此项是上段“已完成必须有当前用户Cookie”的受限例外，不允许新上下文本身认领他人的操作。



- 系统、Origin、原context、operation和已绑定账号均须精确匹配，操作未取消，原1200秒基础窗口及首次AI会话绝对截止均未到达。不得仅凭operationId、Portal登录标记或前端success恢复。

- 重试可重新签发同一绑定账号的opaque用户会话，服务端只存摘要；created_at与expires_at沿用首次可信AI会话建立时刻及其固定+28800秒，不能重新起算8小时，亦不重复中心换码、OTP或短信。

- 浏览器仍携原访客Cookie或没有AI用户Cookie均可适用；若已有另一有效AI账号Cookie，拒绝该旧操作的恢复，不能借原上下文覆盖当前账号。上下文失效、换系统/Origin/CSRF、取消、越过操作恢复窗口或首次8小时截止均拒绝此例外。

- 其他已可信完成的归并重读仍按当前有效、精确匹配的用户Cookie＋当前认证CSRF授权，不因原中心事务期限结束重复OIDC。该路径不给会话续期。

- 独立认证上下文本身仍不授予聊天/文件访问；只有受限complete成功签发的绑定用户会话可读取该账号canonical内容。AI须覆盖成功恢复及上述拒绝重放测试，门户wire DTO和已有重试动作不变。



### 安全公开错误与重试



| 情形 | 固定HTTP/ApiError与处理 |

| --- | --- |

| 中心未成功换码/结果暂未就绪 | `409 AUTH_NOT_READY`，`error.retryable=true`；可给正整数秒`Retry-After`，缺省1秒。Portal保留operationId、原页面/打开提示和本页已成功验证OIDC状态，只重试原AI complete；不再消费原code、OTP或重发短信 |

| 认证限流 | `429 AUTH_RATE_LIMITED`，`error.retryable=true`，必须给正整数秒`Retry-After`；区别于访客聊天次数用完。前端冷却后只允许用户重试原动作，不自动重放SMS/写请求 |

| 错上下文/CSRF/Origin/操作归属 | 拒绝并返回既有安全错误，不能回退为ChatSnapshot CSRF或创建新guest；客户端不透出内部中心数字码/诊断 |

| 已可信认证、归并等待 | 仍为既有`202 ChatSnapshot`、user viewer、MERGE_PENDING与正整数Retry-After；200不得携MERGE_PENDING。与AUTH_NOT_READY的未认证状态严格区分 |



Retry-After只消费正整数秒，拒绝畸形/非安全整数值，不把不可信HTTP-date或后台诊断写入提示。不会因429/409/网络重试改变访客次数或截止。操作创建及SMS的中心稳定幂等键、主动重发与响应丢失后的重读，按SSO既有合同由AI adapter处理，不能让前端随意换键造成重复外发。



### 入口与实施责任



Portal HTTPS入口剥离/覆盖外部`X-Xiaohe-System-Id`、`X-Portal-Client-IP`、scheme及转发链，生成登记的单值system和原socket客户端IP。AI只从实测的最小受信peer采纳，必须保留真实socket peer再校验HTTPS scheme，不能先被通用XFF中间件替换client地址。开发HTTP反代不能伪造可信HTTPS身份。AI→SSO的Bearer、应用ID、原IP/UA及出站peer由AI/SSO独立落实，秘密不进入门户。



门户负责严格AuthContext parser、独立CSRF、双标签/迟到响应、过期后认证、409/429冷却与仅complete重试；AI负责实现auth/context、认证能力存储/绑定/期限、中心适配及可信身份；SSO继续提供已实现的桥接，不新增中心端点。源码/fixture、真实HTTPS入口、真实短信/AI/MinIO和部署分别记录；功能开关在真实组合验收前保持关闭。



## 10. v1.0.4：AG-UI业务适配增量（2026-11-05，G0通过后冻结）

本节只新增接口，原wire v1.0的REST、DTO、认证、额度和归并语义保持。Copilot Runtime/Vue锁定1.84.1、AG-UI JS 1.0.2；声明兼容修复与G0证据见AI和门户各自兼容报告。Python仍为唯一业务权威，Runtime不持有Toolkit密钥、第二份历史或业务库。

### 路径、受理与关联

下列路径均在受信业务网关的`/ai-api/v1/agui`下，逐请求沿用原Cookie、精确Origin、聊天CSRF、对象权限。Runtime只过滤转发AI会话Cookie/聊天CSRF及已核实Origin，不接受调用者的系统/代理头或任意上游。所有请求仍有64KiB包体上限和既有文本/ID码点校验；历史不是事实来源，调用方可减少历史以控制包体，不减弱服务端验证。

| 路径 | 正文 | 结果 |
| --- | --- | --- |
| POST /run | 标准RunAgentInput | 202 Accepted：原message/run/viewer，加attemptId及businessRunId；不在此处再开第二个流 |
| POST /connect | `{threadId,attemptId?}` | 200 SSE：授权一致快照及水位，再订阅其后持久事件；不创建输入、消息或job |
| POST /status | 同上 | 200 Status：当前activeRun、选定run/attempt、isRunning及cursor，不续期 |
| POST /stop | 同上 | 200 `{stopped,run}`；仅显式取消，不创建任务 |

普通run只抽取最后一条user文本，忽略先前assistant/tool历史与state，不允许这些内容写业务库。tools、context不作为执行指令；branch/resume/parentRunId不支持。forwardedProps.xiaohe省略或`{kind:'message'}`表示普通输入；`{kind:'action',requestId,optionId}`或`{kind:'action',requestId,text}`表示原绑定动作，optionId/text恰一，字段按§3校验，未知xiaohe控制字段拒绝。所有字段都不能改变身份、system、上游URL或既有历史。

`RunAgentInput.runId`是稳定交互attemptId（1–128码点），与该对话的持久Command.client_key绑定；message/action在新出口不可复用相同attemptId跨种类，冲突返回409。业务run.id仍由Python生成；waiting_input后新action使用新的attemptId但复用原businessRunId。同键同内容仅复用原受理且读取当前viewer/run，不增job/计次/截止；同键异内容409。运行受理结果丢失可同键重试，不能凭SDKrunId访问物理thread或越权数据。不新增schema，只复用已有持久Command/Run关联。

Runtime自定义Runner先取得已提交202回执，再附着connect（指定本次attemptId）；刷新SDKconnect只给业务threadId，由Python选择当前业务run/最后Command，空闲时仅装载历史。connect总是从同一事务里的授权快照及cursor恢复，再读其后事件，不要求Vue发送其API未提供的客户端游标，不新造header，不重发旧输入。状态isRunning仅queued/running为true，waiting_input是业务等待且本次AG-UI交互已结束。

stop携带attemptId时必须匹配目标业务run的最新Command，旧attempt不能停止后来恢复的任务；省略时表示用户显式停止该对话当前activeRun。所有情况下仍逐对象授权；没有可停任务返回stopped=false。关窗、断网、身份解绑和unsubscribe只断订阅；门户使用SDK的detachActiveRun，不调用会转为POST stop的abortRun。Runtime上游AbortSignal只取消订阅请求，不代发取消业务动作。

### 快照与事件

初始MESSAGES_SNAPSHOT保持稳定messageId/role/文本；STATE_SNAPSHOT的snapshot.xiaohe为PublicSnapshot：含原六种parts、createdAt、文件版本、viewer/activeRun/cursor/merge及attemptId/businessRunId/businessRunStatus/version，明确去掉csrf，不能序列化整个ChatSnapshot。当前输入的客户端message.id不是服务端messageId。

增量CUSTOM的name固定`xiaohe.v1.event`，value是原过滤后的EventEnvelope，保留eventId/conversationId/业务runId；不透传RAW、graph state、内部工具参数或未知事件。message.upsert可重建消息快照及完整parts，message.delta投影TEXT_MESSAGE_START/CONTENT/END，保持原messageId；元数据/状态可由STATE_SNAPSHOT更新。同一eventId重放不能再次追加；重连以新一致快照重建，不重发用户输入。

RUN_STARTED只在受理已提交或已授权附着一个业务attempt后发送。waiting_input发送RUN_FINISHED只结束本次交互，业务状态仍waiting_input；completed结束成功，failed/cancelled/expired为过滤的RUN_ERROR并保留公开业务状态。EOF、断网、服务进程退出不是成功；流中失效或ownership变化安全停止，不再输出私有历史，必要时给过滤的RUN_ERROR要求清视图或重新取快照，不能假装RUN_FINISHED。

HTTP开始前错误继续使用原ApiError及HTTP语义，不输出验证输入；SSE已开始后的错误仅公开code及固定安全message，不带内部异常、Cookie或CSRF。认证卡、Cookie建立/恢复、验证码、绑定归并及文件仍走原REST，不放进AG-UI state/messages。Runtime info及run/connect/stop之外的SDKthread/history/state/clear、branch、suggest、memories、inspector、transcribe等路径默认拒绝，不回退内存历史。

### 公开示例（全部虚构ID，不含凭据）

受理：`{"attemptId":"attempt-A","businessRunId":"00000000-0000-4000-8000-000000000002","message":{"id":"00000000-0000-4000-8000-000000000003","role":"user","createdAt":"2026-11-05T00:00:00Z","parts":[{"type":"text","text":"了解采购服务"}]},"run":{"id":"00000000-0000-4000-8000-000000000002","status":"queued"},"viewer":{"kind":"guest","guestId":"guest-A","systemId":"portal","tenantId":1,"messageCount":1,"messageLimit":100,"retentionSeconds":14400,"expiresAt":"2026-11-05T04:00:00Z"}}`

connect控制状态：`{"type":"STATE_SNAPSHOT","snapshot":{"xiaohe":{"version":1,"conversationId":"00000000-0000-4000-8000-000000000001","messages":[],"activeRun":null,"cursor":null,"merge":null,"attemptId":null,"businessRunId":null,"businessRunStatus":null,"viewer":{"kind":"guest","guestId":"guest-A","systemId":"portal","tenantId":1,"messageCount":0,"messageLimit":100,"retentionSeconds":14400,"expiresAt":"2026-11-05T04:00:00Z"}}}}`

### 机器schema（本节唯一规范源）

以下块为新增协议的权威schema；Git内制品由本块提取并附加主文档版本/hash元数据，不能另外手工维护不同定义。原§3的字段语义、白字符与动作绑定校验仍适用；RunInput引用标准AG-UI输入模型，但仅上述当前输入/控制字段被业务消费。


旧attempt已完成而同一businessRun已由新action恢复时，旧attempt的重试/订阅仅结束旧交互，不附着或取消新attempt；businessRunStatus仍保留数据库的当前状态，不能用旧RUN_FINISHED把当前activeRun改为完成。初始终态从已授权数据库快照生成生命周期事件，不伪造持久CUSTOM eventId。

<!-- xiaohe-agui-v1-schema:start -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:xiaohe:agui:v1",
  "title": "Xiaohe AG-UI wire v1 extension",
  "$defs": {
    "AguiActivityMessage": {
      "additionalProperties": true,
      "description": "Structured progress that is not conversation content, materialised as a\nmessage so it keeps its place in the sequence. Stands alone rather than\ncomposing BaseMessage, because its content is an object rather than a\nstring.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "activity",
          "default": "activity",
          "title": "Role",
          "type": "string"
        },
        "activityType": {
          "title": "Activitytype",
          "type": "string"
        },
        "content": {
          "additionalProperties": true,
          "title": "Content",
          "type": "object"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "id",
        "activityType",
        "content"
      ],
      "title": "ActivityMessage",
      "type": "object"
    },
    "AguiAssistantMessage": {
      "additionalProperties": true,
      "description": "A message from the agent. Content is optional because a turn may consist\nonly of tool calls.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "assistant",
          "default": "assistant",
          "title": "Role",
          "type": "string"
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Name"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        },
        "content": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Content"
        },
        "toolCalls": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/AguiToolCall"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Toolcalls"
        }
      },
      "required": [
        "id"
      ],
      "title": "AssistantMessage",
      "type": "object"
    },
    "AguiAudioPart": {
      "additionalProperties": true,
      "description": "An audio part.",
      "properties": {
        "type": {
          "const": "audio",
          "default": "audio",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "source": {
          "discriminator": {
            "mapping": {
              "data": "#/$defs/AguiDataSource",
              "file": "#/$defs/AguiFileSource",
              "url": "#/$defs/AguiUrlSource"
            },
            "propertyName": "type"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/AguiDataSource"
            },
            {
              "$ref": "#/$defs/AguiUrlSource"
            },
            {
              "$ref": "#/$defs/AguiFileSource"
            }
          ],
          "title": "Source"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "source"
      ],
      "title": "AudioPart",
      "type": "object"
    },
    "AguiContext": {
      "additionalProperties": true,
      "description": "A named piece of ambient information given to the agent for the run,\ndistinct from the conversation.",
      "properties": {
        "description": {
          "title": "Description",
          "type": "string"
        },
        "value": {
          "title": "Value",
          "type": "string"
        }
      },
      "required": [
        "description",
        "value"
      ],
      "title": "Context",
      "type": "object"
    },
    "AguiDataSource": {
      "additionalProperties": true,
      "description": "Bytes carried inline.",
      "properties": {
        "type": {
          "const": "data",
          "default": "data",
          "title": "Type",
          "type": "string"
        },
        "value": {
          "title": "Value",
          "type": "string"
        },
        "mimeType": {
          "title": "Mimetype",
          "type": "string"
        }
      },
      "required": [
        "value",
        "mimeType"
      ],
      "title": "DataSource",
      "type": "object"
    },
    "AguiDeveloperMessage": {
      "additionalProperties": true,
      "description": "Instructions from the application developer.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "developer",
          "default": "developer",
          "title": "Role",
          "type": "string"
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Name"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        },
        "content": {
          "title": "Content",
          "type": "string"
        }
      },
      "required": [
        "id",
        "content"
      ],
      "title": "DeveloperMessage",
      "type": "object"
    },
    "AguiDocumentPart": {
      "additionalProperties": true,
      "description": "A document part.",
      "properties": {
        "type": {
          "const": "document",
          "default": "document",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "source": {
          "discriminator": {
            "mapping": {
              "data": "#/$defs/AguiDataSource",
              "file": "#/$defs/AguiFileSource",
              "url": "#/$defs/AguiUrlSource"
            },
            "propertyName": "type"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/AguiDataSource"
            },
            {
              "$ref": "#/$defs/AguiUrlSource"
            },
            {
              "$ref": "#/$defs/AguiFileSource"
            }
          ],
          "title": "Source"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "source"
      ],
      "title": "DocumentPart",
      "type": "object"
    },
    "AguiFileSource": {
      "additionalProperties": true,
      "description": "Bytes already at the provider, named by a handle the provider issued: an\nOpenAI or Anthropic file id, a Gemini file URI, a storage URL only that\nprovider can read. No bytes travel and nothing is fetched. Only the\nprovider that minted the handle can resolve it; a peer that cannot drops\nthe part as it drops any part it cannot use.",
      "properties": {
        "type": {
          "const": "file",
          "default": "file",
          "title": "Type",
          "type": "string"
        },
        "value": {
          "title": "Value",
          "type": "string"
        },
        "provider": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Provider"
        },
        "mimeType": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Mimetype"
        }
      },
      "required": [
        "value"
      ],
      "title": "FileSource",
      "type": "object"
    },
    "AguiFunctionCall": {
      "additionalProperties": true,
      "description": "The name and arguments of a tool call.",
      "properties": {
        "name": {
          "title": "Name",
          "type": "string"
        },
        "arguments": {
          "title": "Arguments",
          "type": "string"
        }
      },
      "required": [
        "name",
        "arguments"
      ],
      "title": "FunctionCall",
      "type": "object"
    },
    "AguiImagePart": {
      "additionalProperties": true,
      "description": "An image part.",
      "properties": {
        "type": {
          "const": "image",
          "default": "image",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "source": {
          "discriminator": {
            "mapping": {
              "data": "#/$defs/AguiDataSource",
              "file": "#/$defs/AguiFileSource",
              "url": "#/$defs/AguiUrlSource"
            },
            "propertyName": "type"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/AguiDataSource"
            },
            {
              "$ref": "#/$defs/AguiUrlSource"
            },
            {
              "$ref": "#/$defs/AguiFileSource"
            }
          ],
          "title": "Source"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "source"
      ],
      "title": "ImagePart",
      "type": "object"
    },
    "AguiReasoningMessage": {
      "additionalProperties": true,
      "description": "A span of the agent's reasoning, materialised as a message. Stands alone\nrather than composing BaseMessage, because it carries no name.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "reasoning",
          "default": "reasoning",
          "title": "Role",
          "type": "string"
        },
        "content": {
          "title": "Content",
          "type": "string"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "id",
        "content"
      ],
      "title": "ReasoningMessage",
      "type": "object"
    },
    "AguiResumeEntry": {
      "additionalProperties": true,
      "description": "An answer to one interrupt, sent on the run that continues from it.",
      "properties": {
        "interruptId": {
          "title": "Interruptid",
          "type": "string"
        },
        "status": {
          "enum": [
            "resolved",
            "cancelled"
          ],
          "title": "Status",
          "type": "string"
        },
        "payload": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Payload"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "interruptId",
        "status"
      ],
      "title": "ResumeEntry",
      "type": "object"
    },
    "AguiSystemMessage": {
      "additionalProperties": true,
      "description": "Instructions from the system.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "system",
          "default": "system",
          "title": "Role",
          "type": "string"
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Name"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        },
        "content": {
          "title": "Content",
          "type": "string"
        }
      },
      "required": [
        "id",
        "content"
      ],
      "title": "SystemMessage",
      "type": "object"
    },
    "AguiTextPart": {
      "additionalProperties": true,
      "description": "A text part.",
      "properties": {
        "type": {
          "const": "text",
          "default": "text",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "text": {
          "title": "Text",
          "type": "string"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "text"
      ],
      "title": "TextPart",
      "type": "object"
    },
    "AguiTool": {
      "additionalProperties": true,
      "description": "A tool the agent may call.",
      "properties": {
        "name": {
          "title": "Name",
          "type": "string"
        },
        "description": {
          "title": "Description",
          "type": "string"
        },
        "parameters": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Parameters"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "name",
        "description"
      ],
      "title": "Tool",
      "type": "object"
    },
    "AguiToolCall": {
      "additionalProperties": true,
      "description": "A call an assistant message made. Carries no subagent attribution of its\nown and inherits its containing message's, since several calls can share\none parent.",
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "type": {
          "const": "function",
          "default": "function",
          "title": "Type",
          "type": "string"
        },
        "function": {
          "$ref": "#/$defs/AguiFunctionCall"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "id",
        "function"
      ],
      "title": "ToolCall",
      "type": "object"
    },
    "AguiToolMessage": {
      "additionalProperties": true,
      "description": "What a tool returned, as a message in the conversation. Stands alone\nrather than composing BaseMessage, because it carries no name.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "tool",
          "default": "tool",
          "title": "Role",
          "type": "string"
        },
        "content": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "items": {
                "discriminator": {
                  "mapping": {
                    "audio": "#/$defs/AguiAudioPart",
                    "document": "#/$defs/AguiDocumentPart",
                    "image": "#/$defs/AguiImagePart",
                    "text": "#/$defs/AguiTextPart",
                    "video": "#/$defs/AguiVideoPart"
                  },
                  "propertyName": "type"
                },
                "oneOf": [
                  {
                    "$ref": "#/$defs/AguiTextPart"
                  },
                  {
                    "$ref": "#/$defs/AguiImagePart"
                  },
                  {
                    "$ref": "#/$defs/AguiAudioPart"
                  },
                  {
                    "$ref": "#/$defs/AguiVideoPart"
                  },
                  {
                    "$ref": "#/$defs/AguiDocumentPart"
                  }
                ]
              },
              "type": "array"
            }
          ],
          "title": "Content"
        },
        "toolCallId": {
          "title": "Toolcallid",
          "type": "string"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "id",
        "content",
        "toolCallId"
      ],
      "title": "ToolMessage",
      "type": "object"
    },
    "AguiUrlSource": {
      "additionalProperties": true,
      "description": "Bytes referenced by URL, fetched by whoever needs them.",
      "properties": {
        "type": {
          "const": "url",
          "default": "url",
          "title": "Type",
          "type": "string"
        },
        "value": {
          "title": "Value",
          "type": "string"
        },
        "mimeType": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Mimetype"
        }
      },
      "required": [
        "value"
      ],
      "title": "UrlSource",
      "type": "object"
    },
    "AguiUserMessage": {
      "additionalProperties": true,
      "description": "A message from the person using the application.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "user",
          "default": "user",
          "title": "Role",
          "type": "string"
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Name"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        },
        "content": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "items": {
                "discriminator": {
                  "mapping": {
                    "audio": "#/$defs/AguiAudioPart",
                    "document": "#/$defs/AguiDocumentPart",
                    "image": "#/$defs/AguiImagePart",
                    "text": "#/$defs/AguiTextPart",
                    "video": "#/$defs/AguiVideoPart"
                  },
                  "propertyName": "type"
                },
                "oneOf": [
                  {
                    "$ref": "#/$defs/AguiTextPart"
                  },
                  {
                    "$ref": "#/$defs/AguiImagePart"
                  },
                  {
                    "$ref": "#/$defs/AguiAudioPart"
                  },
                  {
                    "$ref": "#/$defs/AguiVideoPart"
                  },
                  {
                    "$ref": "#/$defs/AguiDocumentPart"
                  }
                ]
              },
              "type": "array"
            }
          ],
          "title": "Content"
        }
      },
      "required": [
        "id",
        "content"
      ],
      "title": "UserMessage",
      "type": "object"
    },
    "AguiVideoPart": {
      "additionalProperties": true,
      "description": "A video part.",
      "properties": {
        "type": {
          "const": "video",
          "default": "video",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "source": {
          "discriminator": {
            "mapping": {
              "data": "#/$defs/AguiDataSource",
              "file": "#/$defs/AguiFileSource",
              "url": "#/$defs/AguiUrlSource"
            },
            "propertyName": "type"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/AguiDataSource"
            },
            {
              "$ref": "#/$defs/AguiUrlSource"
            },
            {
              "$ref": "#/$defs/AguiFileSource"
            }
          ],
          "title": "Source"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "source"
      ],
      "title": "VideoPart",
      "type": "object"
    },
    "RunInput": {
      "additionalProperties": true,
      "description": "A request to run an agent. Also echoed back as RUN_STARTED.input. Only\nthreadId, runId and messages are required: those are the three the SDKs\nalready agree on, and for tools and context an absent key and an empty\narray mean the same thing, so requiring them would catch nothing a\nproducer could get wrong.",
      "properties": {
        "threadId": {
          "type": "string",
          "format": "uuid"
        },
        "runId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        },
        "protocolVersion": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Protocolversion"
        },
        "parentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Parentrunid"
        },
        "state": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "messages": {
          "items": {
            "discriminator": {
              "mapping": {
                "activity": "#/$defs/AguiActivityMessage",
                "assistant": "#/$defs/AguiAssistantMessage",
                "developer": "#/$defs/AguiDeveloperMessage",
                "reasoning": "#/$defs/AguiReasoningMessage",
                "system": "#/$defs/AguiSystemMessage",
                "tool": "#/$defs/AguiToolMessage",
                "user": "#/$defs/AguiUserMessage"
              },
              "propertyName": "role"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/AguiDeveloperMessage"
              },
              {
                "$ref": "#/$defs/AguiSystemMessage"
              },
              {
                "$ref": "#/$defs/AguiAssistantMessage"
              },
              {
                "$ref": "#/$defs/AguiUserMessage"
              },
              {
                "$ref": "#/$defs/AguiToolMessage"
              },
              {
                "$ref": "#/$defs/AguiActivityMessage"
              },
              {
                "$ref": "#/$defs/AguiReasoningMessage"
              }
            ]
          },
          "title": "Messages",
          "type": "array"
        },
        "tools": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/AguiTool"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Tools"
        },
        "context": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/AguiContext"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Context"
        },
        "forwardedProps": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Forwardedprops"
        },
        "resume": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/AguiResumeEntry"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Resume"
        }
      },
      "required": [
        "threadId",
        "runId",
        "messages"
      ],
      "title": "RunAgentInput",
      "type": "object"
    },
    "ArtifactRef": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "artifact_ref",
          "title": "Type",
          "type": "string"
        },
        "artifactId": {
          "minLength": 1,
          "title": "Artifactid",
          "type": "string"
        },
        "version": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Version",
          "type": "integer"
        },
        "title": {
          "title": "Title",
          "type": "string"
        },
        "mediaType": {
          "minLength": 1,
          "title": "Mediatype",
          "type": "string"
        },
        "status": {
          "enum": [
            "pending",
            "ready",
            "failed"
          ],
          "title": "Status",
          "type": "string"
        }
      },
      "required": [
        "type",
        "artifactId",
        "version",
        "title",
        "mediaType",
        "status"
      ],
      "title": "ArtifactRef",
      "type": "object"
    },
    "AuthRequestPart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "auth_request",
          "title": "Type",
          "type": "string"
        },
        "intent": {
          "enum": [
            "lead",
            "register",
            "login"
          ],
          "title": "Intent",
          "type": "string"
        }
      },
      "required": [
        "type",
        "intent"
      ],
      "title": "AuthRequestPart",
      "type": "object"
    },
    "ChatMessage": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "minLength": 1,
          "title": "Id",
          "type": "string"
        },
        "role": {
          "enum": [
            "user",
            "assistant"
          ],
          "title": "Role",
          "type": "string"
        },
        "createdAt": {
          "format": "date-time",
          "title": "Createdat",
          "type": "string"
        },
        "parts": {
          "items": {
            "discriminator": {
              "mapping": {
                "artifact_ref": "#/$defs/ArtifactRef",
                "auth_request": "#/$defs/AuthRequestPart",
                "choice": "#/$defs/ChoicePart",
                "input_request": "#/$defs/InputRequestPart",
                "suggestions": "#/$defs/SuggestionsPart",
                "text": "#/$defs/TextPart",
                "system_notice": "#/$defs/SystemNoticePart"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/TextPart"
              },
              {
                "$ref": "#/$defs/ChoicePart"
              },
              {
                "$ref": "#/$defs/SuggestionsPart"
              },
              {
                "$ref": "#/$defs/InputRequestPart"
              },
              {
                "$ref": "#/$defs/AuthRequestPart"
              },
              {
                "$ref": "#/$defs/ArtifactRef"
              },
              {
                "$ref": "#/$defs/SystemNoticePart"
              }
            ]
          },
          "title": "Parts",
          "type": "array"
        }
      },
      "required": [
        "id",
        "role",
        "createdAt",
        "parts"
      ],
      "title": "ChatMessage",
      "type": "object"
    },
    "ChoicePart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "choice",
          "title": "Type",
          "type": "string"
        },
        "requestId": {
          "maxLength": 128,
          "minLength": 1,
          "title": "Requestid",
          "type": "string"
        },
        "prompt": {
          "title": "Prompt",
          "type": "string"
        },
        "options": {
          "items": {
            "$ref": "#/$defs/Option"
          },
          "title": "Options",
          "type": "array"
        }
      },
      "required": [
        "type",
        "requestId",
        "prompt",
        "options"
      ],
      "title": "ChoicePart",
      "type": "object"
    },
    "GuestViewer": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "guest",
          "title": "Kind",
          "type": "string"
        },
        "guestId": {
          "minLength": 1,
          "title": "Guestid",
          "type": "string"
        },
        "systemId": {
          "minLength": 1,
          "title": "Systemid",
          "type": "string"
        },
        "tenantId": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Tenantid",
          "type": "integer"
        },
        "messageCount": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Messagecount",
          "type": "integer"
        },
        "messageLimit": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Messagelimit",
          "type": "integer"
        },
        "retentionSeconds": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Retentionseconds",
          "type": "integer"
        },
        "expiresAt": {
          "format": "date-time",
          "title": "Expiresat",
          "type": "string"
        }
      },
      "required": [
        "kind",
        "guestId",
        "systemId",
        "tenantId",
        "messageCount",
        "messageLimit",
        "retentionSeconds",
        "expiresAt"
      ],
      "title": "GuestViewer",
      "type": "object"
    },
    "InputRequestPart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "input_request",
          "title": "Type",
          "type": "string"
        },
        "requestId": {
          "maxLength": 128,
          "minLength": 1,
          "title": "Requestid",
          "type": "string"
        },
        "prompt": {
          "title": "Prompt",
          "type": "string"
        }
      },
      "required": [
        "type",
        "requestId",
        "prompt"
      ],
      "title": "InputRequestPart",
      "type": "object"
    },
    "Option": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "maxLength": 128,
          "minLength": 1,
          "title": "Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "id",
        "label"
      ],
      "title": "Option",
      "type": "object"
    },
    "RunRef": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "minLength": 1,
          "title": "Id",
          "type": "string"
        },
        "status": {
          "enum": [
            "queued",
            "running",
            "waiting_input",
            "completed",
            "failed",
            "cancelled",
            "expired"
          ],
          "title": "Status",
          "type": "string"
        }
      },
      "required": [
        "id",
        "status"
      ],
      "title": "RunRef",
      "type": "object"
    },
    "Suggestion": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "minLength": 1,
          "title": "Id",
          "type": "string"
        },
        "text": {
          "title": "Text",
          "type": "string"
        }
      },
      "required": [
        "id",
        "text"
      ],
      "title": "Suggestion",
      "type": "object"
    },
    "SuggestionsPart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "suggestions",
          "title": "Type",
          "type": "string"
        },
        "items": {
          "items": {
            "$ref": "#/$defs/Suggestion"
          },
          "title": "Items",
          "type": "array"
        }
      },
      "required": [
        "type",
        "items"
      ],
      "title": "SuggestionsPart",
      "type": "object"
    },
    "TextPart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "text",
          "title": "Type",
          "type": "string"
        },
        "text": {
          "title": "Text",
          "type": "string"
        }
      },
      "required": [
        "type",
        "text"
      ],
      "title": "TextPart",
      "type": "object"
    },
    "UserViewer": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "user",
          "title": "Kind",
          "type": "string"
        },
        "userId": {
          "minLength": 1,
          "title": "Userid",
          "type": "string"
        },
        "systemId": {
          "minLength": 1,
          "title": "Systemid",
          "type": "string"
        },
        "tenantId": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Tenantid",
          "type": "integer"
        },
        "displayName": {
          "title": "Displayname",
          "type": "string"
        }
      },
      "required": [
        "kind",
        "userId",
        "systemId",
        "tenantId",
        "displayName"
      ],
      "title": "UserViewer",
      "type": "object"
    },
    "WireAccepted": {
      "additionalProperties": false,
      "properties": {
        "message": {
          "$ref": "#/$defs/ChatMessage"
        },
        "run": {
          "$ref": "#/$defs/RunRef"
        },
        "viewer": {
          "discriminator": {
            "mapping": {
              "guest": "#/$defs/GuestViewer",
              "user": "#/$defs/UserViewer"
            },
            "propertyName": "kind"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/GuestViewer"
            },
            {
              "$ref": "#/$defs/UserViewer"
            }
          ],
          "title": "Viewer"
        }
      },
      "required": [
        "message",
        "run",
        "viewer"
      ],
      "title": "MessageAccepted",
      "type": "object"
    },
    "ApiError": {
      "additionalProperties": false,
      "properties": {
        "error": {
          "$ref": "#/$defs/ErrorDetail"
        },
        "requestId": {
          "minLength": 1,
          "title": "Requestid",
          "type": "string"
        }
      },
      "required": [
        "error",
        "requestId"
      ],
      "title": "ApiError",
      "type": "object"
    },
    "ArtifactEvent": {
      "additionalProperties": false,
      "properties": {
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "artifact.updated",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ArtifactUpdated"
        }
      },
      "required": [
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "ArtifactEvent",
      "type": "object"
    },
    "ArtifactUpdated": {
      "additionalProperties": false,
      "properties": {
        "artifactId": {
          "minLength": 1,
          "title": "Artifactid",
          "type": "string"
        },
        "version": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Version",
          "type": "integer"
        },
        "status": {
          "enum": [
            "pending",
            "ready",
            "failed"
          ],
          "title": "Status",
          "type": "string"
        }
      },
      "required": [
        "artifactId",
        "version",
        "status"
      ],
      "title": "ArtifactUpdated",
      "type": "object"
    },
    "DeltaEvent": {
      "additionalProperties": false,
      "properties": {
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string"
        },
        "runId": {
          "minLength": 1,
          "title": "Runid",
          "type": "string"
        },
        "type": {
          "const": "message.delta",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/MessageDelta"
        }
      },
      "required": [
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "DeltaEvent",
      "type": "object"
    },
    "ErrorDetail": {
      "additionalProperties": false,
      "properties": {
        "code": {
          "minLength": 1,
          "title": "Code",
          "type": "string"
        },
        "message": {
          "title": "Message",
          "type": "string"
        },
        "retryable": {
          "title": "Retryable",
          "type": "boolean"
        }
      },
      "required": [
        "code",
        "message",
        "retryable"
      ],
      "title": "ErrorDetail",
      "type": "object"
    },
    "ErrorEvent": {
      "additionalProperties": false,
      "properties": {
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "error",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ApiError"
        }
      },
      "required": [
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "ErrorEvent",
      "type": "object"
    },
    "ExpiredEvent": {
      "additionalProperties": false,
      "properties": {
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "viewer.expired",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ViewerExpired"
        }
      },
      "required": [
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "ExpiredEvent",
      "type": "object"
    },
    "MessageDelta": {
      "additionalProperties": false,
      "properties": {
        "messageId": {
          "minLength": 1,
          "title": "Messageid",
          "type": "string"
        },
        "delta": {
          "title": "Delta",
          "type": "string"
        }
      },
      "required": [
        "messageId",
        "delta"
      ],
      "title": "MessageDelta",
      "type": "object"
    },
    "MessageEvent": {
      "additionalProperties": false,
      "properties": {
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "message.upsert",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/MessageUpsert"
        }
      },
      "required": [
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "MessageEvent",
      "type": "object"
    },
    "MessageUpsert": {
      "additionalProperties": false,
      "properties": {
        "message": {
          "$ref": "#/$defs/ChatMessage"
        }
      },
      "required": [
        "message"
      ],
      "title": "MessageUpsert",
      "type": "object"
    },
    "RunEvent": {
      "additionalProperties": false,
      "properties": {
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string"
        },
        "runId": {
          "minLength": 1,
          "title": "Runid",
          "type": "string"
        },
        "type": {
          "const": "run.updated",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/RunUpdated"
        }
      },
      "required": [
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "RunEvent",
      "type": "object"
    },
    "RunUpdated": {
      "additionalProperties": false,
      "properties": {
        "run": {
          "$ref": "#/$defs/RunRef"
        }
      },
      "required": [
        "run"
      ],
      "title": "RunUpdated",
      "type": "object"
    },
    "ViewerEvent": {
      "additionalProperties": false,
      "properties": {
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "viewer.updated",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ViewerUpdated"
        }
      },
      "required": [
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "ViewerEvent",
      "type": "object"
    },
    "ViewerExpired": {
      "additionalProperties": false,
      "properties": {
        "code": {
          "const": "VISITOR_EXPIRED",
          "title": "Code",
          "type": "string"
        }
      },
      "required": [
        "code"
      ],
      "title": "ViewerExpired",
      "type": "object"
    },
    "ViewerUpdated": {
      "additionalProperties": false,
      "properties": {
        "viewer": {
          "discriminator": {
            "mapping": {
              "guest": "#/$defs/GuestViewer",
              "user": "#/$defs/UserViewer"
            },
            "propertyName": "kind"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/GuestViewer"
            },
            {
              "$ref": "#/$defs/UserViewer"
            }
          ],
          "title": "Viewer"
        }
      },
      "required": [
        "viewer"
      ],
      "title": "ViewerUpdated",
      "type": "object"
    },
    "WireEvent": {
      "discriminator": {
        "mapping": {
          "artifact.updated": "#/$defs/ArtifactEvent",
          "error": "#/$defs/ErrorEvent",
          "message.delta": "#/$defs/DeltaEvent",
          "message.upsert": "#/$defs/MessageEvent",
          "run.updated": "#/$defs/RunEvent",
          "viewer.expired": "#/$defs/ExpiredEvent",
          "viewer.updated": "#/$defs/ViewerEvent"
        },
        "propertyName": "type"
      },
      "oneOf": [
        {
          "$ref": "#/$defs/DeltaEvent"
        },
        {
          "$ref": "#/$defs/MessageEvent"
        },
        {
          "$ref": "#/$defs/RunEvent"
        },
        {
          "$ref": "#/$defs/ArtifactEvent"
        },
        {
          "$ref": "#/$defs/ViewerEvent"
        },
        {
          "$ref": "#/$defs/ExpiredEvent"
        },
        {
          "$ref": "#/$defs/ErrorEvent"
        }
      ]
    },
    "Csrf": {
      "additionalProperties": false,
      "properties": {
        "headerName": {
          "minLength": 1,
          "title": "Headername",
          "type": "string"
        },
        "token": {
          "minLength": 1,
          "title": "Token",
          "type": "string"
        }
      },
      "required": [
        "headerName",
        "token"
      ],
      "title": "Csrf",
      "type": "object"
    },
    "MergeState": {
      "additionalProperties": false,
      "properties": {
        "operationId": {
          "minLength": 1,
          "title": "Operationid",
          "type": "string"
        },
        "status": {
          "enum": [
            "MERGE_PENDING",
            "MERGED",
            "SKIPPED_EXPIRED"
          ],
          "title": "Status",
          "type": "string"
        }
      },
      "required": [
        "operationId",
        "status"
      ],
      "title": "MergeState",
      "type": "object"
    },
    "PublicSnapshot": {
      "additionalProperties": false,
      "properties": {
        "viewer": {
          "discriminator": {
            "mapping": {
              "guest": "#/$defs/GuestViewer",
              "user": "#/$defs/UserViewer"
            },
            "propertyName": "kind"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/GuestViewer"
            },
            {
              "$ref": "#/$defs/UserViewer"
            }
          ],
          "title": "Viewer"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string"
        },
        "messages": {
          "items": {
            "$ref": "#/$defs/ChatMessage"
          },
          "title": "Messages",
          "type": "array"
        },
        "activeRun": {
          "anyOf": [
            {
              "$ref": "#/$defs/RunRef"
            },
            {
              "type": "null"
            }
          ]
        },
        "cursor": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Cursor"
        },
        "merge": {
          "anyOf": [
            {
              "$ref": "#/$defs/MergeState"
            },
            {
              "type": "null"
            }
          ]
        },
        "version": {
          "const": 1
        },
        "attemptId": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1,
              "maxLength": 128
            },
            {
              "type": "null"
            }
          ]
        },
        "businessRunId": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1,
              "maxLength": 128
            },
            {
              "type": "null"
            }
          ]
        },
        "businessRunStatus": {
          "anyOf": [
            {
              "type": "string",
              "enum": [
                "queued",
                "running",
                "waiting_input",
                "completed",
                "failed",
                "cancelled",
                "expired"
              ]
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "viewer",
        "conversationId",
        "messages",
        "activeRun",
        "cursor",
        "merge",
        "version",
        "attemptId",
        "businessRunId",
        "businessRunStatus"
      ],
      "title": "XiaohePublicAguiState",
      "type": "object"
    },
    "ConversationInput": {
      "type": "object",
      "properties": {
        "threadId": {
          "type": "string",
          "format": "uuid"
        },
        "attemptId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        }
      },
      "required": [
        "threadId"
      ],
      "additionalProperties": false
    },
    "Accepted": {
      "additionalProperties": false,
      "properties": {
        "message": {
          "$ref": "#/$defs/ChatMessage"
        },
        "run": {
          "$ref": "#/$defs/RunRef"
        },
        "viewer": {
          "discriminator": {
            "mapping": {
              "guest": "#/$defs/GuestViewer",
              "user": "#/$defs/UserViewer"
            },
            "propertyName": "kind"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/GuestViewer"
            },
            {
              "$ref": "#/$defs/UserViewer"
            }
          ],
          "title": "Viewer"
        },
        "attemptId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        },
        "businessRunId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        }
      },
      "required": [
        "message",
        "run",
        "viewer",
        "attemptId",
        "businessRunId"
      ],
      "title": "MessageAccepted",
      "type": "object"
    },
    "Status": {
      "type": "object",
      "properties": {
        "conversationId": {
          "type": "string",
          "format": "uuid"
        },
        "activeRun": {
          "anyOf": [
            {
              "$ref": "#/$defs/RunRef"
            },
            {
              "type": "null"
            }
          ]
        },
        "run": {
          "anyOf": [
            {
              "$ref": "#/$defs/RunRef"
            },
            {
              "type": "null"
            }
          ]
        },
        "attemptId": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1,
              "maxLength": 128
            },
            {
              "type": "null"
            }
          ]
        },
        "businessRunId": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1,
              "maxLength": 128
            },
            {
              "type": "null"
            }
          ]
        },
        "isRunning": {
          "type": "boolean"
        },
        "cursor": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1,
              "maxLength": 128
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "conversationId",
        "activeRun",
        "run",
        "attemptId",
        "businessRunId",
        "isRunning",
        "cursor"
      ],
      "additionalProperties": false
    },
    "Stopped": {
      "type": "object",
      "properties": {
        "stopped": {
          "type": "boolean"
        },
        "run": {
          "anyOf": [
            {
              "$ref": "#/$defs/RunRef"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "stopped",
        "run"
      ],
      "additionalProperties": false
    },
    "RUN_STARTED": {
      "type": "object",
      "properties": {
        "type": {
          "const": "RUN_STARTED"
        },
        "threadId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        },
        "runId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        }
      },
      "required": [
        "type",
        "threadId",
        "runId"
      ],
      "additionalProperties": false
    },
    "RUN_FINISHED": {
      "type": "object",
      "properties": {
        "type": {
          "const": "RUN_FINISHED"
        },
        "threadId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        },
        "runId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        }
      },
      "required": [
        "type",
        "threadId",
        "runId"
      ],
      "additionalProperties": false
    },
    "RUN_ERROR": {
      "type": "object",
      "properties": {
        "type": {
          "const": "RUN_ERROR"
        },
        "code": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        },
        "message": {
          "type": "string"
        }
      },
      "required": [
        "type",
        "code",
        "message"
      ],
      "additionalProperties": false
    },
    "MESSAGES_SNAPSHOT": {
      "type": "object",
      "properties": {
        "type": {
          "const": "MESSAGES_SNAPSHOT"
        },
        "messages": {
          "items": {
            "discriminator": {
              "mapping": {
                "activity": "#/$defs/AguiActivityMessage",
                "assistant": "#/$defs/AguiAssistantMessage",
                "developer": "#/$defs/AguiDeveloperMessage",
                "reasoning": "#/$defs/AguiReasoningMessage",
                "system": "#/$defs/AguiSystemMessage",
                "tool": "#/$defs/AguiToolMessage",
                "user": "#/$defs/AguiUserMessage"
              },
              "propertyName": "role"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/AguiDeveloperMessage"
              },
              {
                "$ref": "#/$defs/AguiSystemMessage"
              },
              {
                "$ref": "#/$defs/AguiAssistantMessage"
              },
              {
                "$ref": "#/$defs/AguiUserMessage"
              },
              {
                "$ref": "#/$defs/AguiToolMessage"
              },
              {
                "$ref": "#/$defs/AguiActivityMessage"
              },
              {
                "$ref": "#/$defs/AguiReasoningMessage"
              }
            ]
          },
          "title": "Messages",
          "type": "array"
        }
      },
      "required": [
        "type",
        "messages"
      ],
      "additionalProperties": false
    },
    "STATE_SNAPSHOT": {
      "type": "object",
      "properties": {
        "type": {
          "const": "STATE_SNAPSHOT"
        },
        "snapshot": {
          "type": "object",
          "properties": {
            "xiaohe": {
              "$ref": "#/$defs/PublicSnapshot"
            }
          },
          "required": [
            "xiaohe"
          ],
          "additionalProperties": false
        }
      },
      "required": [
        "type",
        "snapshot"
      ],
      "additionalProperties": false
    },
    "TEXT_MESSAGE_START": {
      "type": "object",
      "properties": {
        "type": {
          "const": "TEXT_MESSAGE_START"
        },
        "messageId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        },
        "role": {
          "const": "assistant"
        }
      },
      "required": [
        "type",
        "messageId",
        "role"
      ],
      "additionalProperties": false
    },
    "TEXT_MESSAGE_CONTENT": {
      "type": "object",
      "properties": {
        "type": {
          "const": "TEXT_MESSAGE_CONTENT"
        },
        "messageId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        },
        "delta": {
          "type": "string"
        }
      },
      "required": [
        "type",
        "messageId",
        "delta"
      ],
      "additionalProperties": false
    },
    "TEXT_MESSAGE_END": {
      "type": "object",
      "properties": {
        "type": {
          "const": "TEXT_MESSAGE_END"
        },
        "messageId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        }
      },
      "required": [
        "type",
        "messageId"
      ],
      "additionalProperties": false
    },
    "CUSTOM": {
      "type": "object",
      "properties": {
        "type": {
          "const": "CUSTOM"
        },
        "name": {
          "const": "xiaohe.v1.event"
        },
        "value": {
          "$ref": "#/$defs/WireEvent"
        }
      },
      "required": [
        "type",
        "name",
        "value"
      ],
      "additionalProperties": false
    },
    "StreamEvent": {
      "oneOf": [
        {
          "$ref": "#/$defs/RUN_STARTED"
        },
        {
          "$ref": "#/$defs/RUN_FINISHED"
        },
        {
          "$ref": "#/$defs/RUN_ERROR"
        },
        {
          "$ref": "#/$defs/MESSAGES_SNAPSHOT"
        },
        {
          "$ref": "#/$defs/STATE_SNAPSHOT"
        },
        {
          "$ref": "#/$defs/TEXT_MESSAGE_START"
        },
        {
          "$ref": "#/$defs/TEXT_MESSAGE_CONTENT"
        },
        {
          "$ref": "#/$defs/TEXT_MESSAGE_END"
        },
        {
          "$ref": "#/$defs/CUSTOM"
        }
      ]
    },
    "SystemNoticePart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "system_notice",
          "title": "Type",
          "type": "string"
        },
        "text": {
          "title": "Text",
          "type": "string"
        }
      },
      "required": [
        "type",
        "text"
      ],
      "title": "SystemNoticePart",
      "type": "object"
    }
  },
  "oneOf": [
    {
      "$ref": "#/$defs/RunInput"
    },
    {
      "$ref": "#/$defs/ConversationInput"
    },
    {
      "$ref": "#/$defs/Accepted"
    },
    {
      "$ref": "#/$defs/Status"
    },
    {
      "$ref": "#/$defs/Stopped"
    },
    {
      "$ref": "#/$defs/PublicSnapshot"
    },
    {
      "$ref": "#/$defs/StreamEvent"
    }
  ]
}
```
<!-- xiaohe-agui-v1-schema:end -->


## 11. 接待 REST 与 Collaborator 正式业务

用户已批准完整流程与 Native 实施，最新要求独立 `yudao-module-collaborator`，新表全部为 `co_`，不沿用 member 接待业务。新业务表为 co_collaborator、co_subject、co_application、co_revision、co_followup、co_onboarding、co_submission；归属使用独立 collaborator_id。原 member、supplier、CRM 表和业务流程不改。中心新增只读认证适配，不新增登录体系。

### 新增结构化资料

- `Direction = buyer | organization | supplier`；`DraftKind = service | onboarding`。
- `FieldValue = string | string[] | null`，金额／产能保留用户原述与单位；字段名白名单由方向 schema 决定。省略表示不改，null 表示用户清空，必填不得以 null 提交。
- `DraftView = {id, kind, direction, sourceApplicationId?, subjectId?, revision, step, state, fields, missingRequired, files, summaryVersion?, fieldLayout?}`。`step = round1|round2|round3|files|summary`，`state = collecting|review|submitting|accepted`；所有业务 ID 为不透明字符串，revision 是正整数。
- `ReceptionView = {version:1, conversationId, direction, invitationState, pendingRequestId, draftRefs, activeDraft}`。`invitationState = none|offered|deferred|accepted`；草稿引用可多份，activeDraft 可空。
- `DraftRef = {id,kind,direction,subjectName,summary,step,revision,state}`；`SubjectPage = {items,nextCursor}`，items 仅含当前用户可用的 `id,kind,name,publicFields`，不返回其他账号或内部管理信息。
- `FileRef = {id, category, filename, mediaType, bytes, sha256, status}`，`status = pending|ready|failed`；不公开对象 key 或存储密钥。
- `SubmitReceipt = {submissionKey,kind,applicationId,onboardingId?,revision,status}`，`status = accepted|pending|unknown|failed`，pending/unknown 不称已成功；入驻回执保留原 applicationId，另给 onboardingId。
- `SubmissionResult = {status:'not_found'|'pending'|'accepted',receipt:SubmitReceipt|null}`，用于查询原键，不能把 not_found 当作 accepted。`ApplicationView = {id,subjectId,direction,revision,publicFields,requestFields,files}`，用于从后台当前版本初始化修改草稿；不从聊天缓存反推正式资料。
- `ProgressGroup = {subjectId, subjectName, applications}`；申请包含 `id,direction,summary,submittedAt,stage,manager,grade,expectedContactAt,publicRecords,onboarding`；未分派／未定级为 null，严禁 `internalNotes`。`stage = recorded|communicating|awaiting_user|completed|closed`。

组织字段名固定：`organizationName, organizationType, memberSize, coverage, city, industries, services, onlineChannels, aiNeeds, contactName, contactRole, email, activeRatio, revenueStructure, desiredLaunchTime, decisionProcess, resources`。五个必填为 `organizationName, memberSize, coverage, onlineChannels, contactName`。枚举使用设计中的展示原值，避免另造数值端点规则；100/500/2000 的自然语言端点先澄清。

采购商字段：`companyName, industry, companyScale, city, category, requirement, annualPurchase, painPoints, contactName, contactRole, existingSystems, projectStatus, budget, desiredTime, decisionProcess`。供应商字段：`companyName, companyType, category, city, productDescription, capabilityTags, qualifications, serviceAreas, orderTypes, contactName, capacity, deliveryCycle, revenue, teamSize, targetCustomers, targetAreas`。只有 companyName 最终必填。

公共字段划分固定为：company 的 `companyName,industry,companyScale,city,companyType`；organization 的 `organizationName,organizationType,memberSize,coverage,city,industries,services,onlineChannels`。其余为本次申请字段，联系人不自动传播到其他申请；供应商 category 不与采购商 category 互相预填。由服务端按方向 schema 分区，客户端不得自行指定越界 publicFields。组织枚举、名称 2—40 字及文本上限按 Unicode 码点校验，Java 不可用 UTF-16 length 充当码点数。

新接待错误沿原 ApiError 形状，固定 `DRAFT_CONFLICT`（409，需读取最新版本）、`SUBJECT_CONFLICT`（409，需选择主体）、`FILE_INVALID`（422）、`FILE_TOO_LARGE`（413）、`SUBMISSION_UNKNOWN`（503，可按原键核对）；原 AUTH_REQUIRED/FORBIDDEN/ACTION_STALE/INPUT_INVALID/IDEMPOTENCY_CONFLICT 保持。前端错误词典新增固定安全提示，不能透传后台原异常。

### Portal → AI：`/ai-api/v1/reception`

所有接口沿原 Cookie、精确 Origin、CSRF 和对象授权；不接受客户端 userId/tenantId/systemId。

| 操作 | 请求与响应 |
| --- | --- |
| GET `/conversations/{id}` | 返回 ReceptionView；与授权会话同范围 |
| GET `/subjects` | `kind` 与 `cursor` 查询参数 → SubjectPage；后端真实查询，不从聊天推测公司目录 |
| POST `/conversations/{id}/drafts` | `{clientDraftId,direction,kind,subjectId?,sourceApplicationId?}` → 201 DraftView；同键同内容复用，所选主体／原申请逐项授权 |
| PATCH `/drafts/{id}` | `{expectedRevision,fields}` → 200 DraftView；保存不推进、不提交；冲突 409 |
| POST `/drafts/{id}/actions` | `{clientActionId,requestId,expectedRevision,action,summaryVersion?}` → 202 原 MessageAccepted；action 为 save_round/skip_optional/show_summary/confirm/resume；resume 绑定当前草稿选择问题及目标草稿；同一次动作重试复用原受理 |
| POST `/drafts/{id}/files` | `{clientUploadId,category,filename,mediaType,bytes,expectedRevision}` → 201 FileRef＋相对 uploadPath；只是待上传记录 |
| PUT `/drafts/{id}/files/{fileId}` | 原始二进制流，32 MiB 硬上限及文件签名校验 → ready FileRef；不引入 multipart 依赖 |
| DELETE `/drafts/{id}/files/{fileId}` | JSON `{expectedRevision}`；解除未提交草稿关联，受版本／历史引用保护 → 最新 DraftView |
| GET `/files/{fileId}/download` | 当前用户／归属／文件就绪检查后安全下载 |
| GET `/progress` | 分页返回 `{groups,nextCursor}`，由 AI 服务代理后台真实查询 |

表单上下文不塞进 AG-UI state 或新 ChatPart：当前 `input_request.requestId` 与 ReceptionView.pendingRequestId 匹配时，升级前端渲染专用表单；否则原文本补充组件保持。右侧编辑读取 DraftView，不执行任意 HTML，也不更改只读 ArtifactView；普通历史产出物继续按旧协议查看。

动作入口先锁草稿并保存版本／确认记录，再在同一 AI 数据库事务中调用原 `accept_action(...)` 排队固定文本动作；worker 只读取服务端记录的草稿和确认版本。普通聊天确认调用同一个草稿确认服务，不复制提交逻辑。PATCH 本身不创建业务 Run；客户端不能用任意字符串制造已确认版本。

### AI → Java：`/internal-api/reception/v1`

采用独立接待服务凭据与显式应用→system/业务租户/issuer 登记，和 Toolkit/短信桥接权限分开。受信 AI 从本地已验证身份映射构造 `ReceptionActor = {aiUserId,systemId,issuer,sub,sourceTenantId}`，模型与浏览器不可填此结构。后台通过中心只读身份适配器校验来源账号状态，返回不透明身份，再在独立 co_collaborator 建立映射；AI 和 Collaborator 不能将 sub/UUID 转为会员编号，不保存 memberId 或复用会员业务逻辑；绑定冲突拒绝。同一 system 内来源身份与 AI 用户一对一绑定，不同 system 独立且不得互访。

`POST /subjects/query`、`POST /applications/get`、`POST /applications/submit`、`POST /applications/result`、`POST /progress/query`、`POST /intents`、`POST /onboarding/submit` 共用认证及 actor 校验。applications/get 请求包含 applicationId，返回当前用户有权查看的 ApplicationView。

`Submission = {submissionKey,draftId,draftRevision,operation:'create'|'update',applicationId?,expectedApplicationRevision?,subjectId?,subjectKind:'company'|'organization',direction,publicFields,requestFields,files}`。后台按内容摘要校验幂等；同键异内容 409；身份来自受信 actor，files 仅接受 ready 的受控引用。新建回 201，重复成功回 200；结果查询未知回明确的 not_found/pending/accepted，不能以 HTTP 200 一概表示受理成功。后台保留 CommonResult 包装，AI 客户端适配成公共安全错误。

`OnboardingSubmission = {submissionKey,draftId,draftRevision,applicationId,onboardingId?,expectedOnboardingRevision?,publicFields,requestFields,files}`。原服务 applicationId 始终必填；有 onboardingId 时更新该入驻记录，否则新建关联入驻。业务内容以 C0 五必填校验，服务申请的确认不能复用为入驻确认。后台所有接待操作在 B2 的可信身份绑定校验后执行。

### 文件与运营

后台保存受控 FileRef 关联；原文件仍在 AI 私有存储。运营下载经 `/admin-api/collaborator/reception/applications/{id}/files/{fileId}` 校验后台权限，再 POST AI 的 `/internal-api/reception-files/v1/files/{fileId}/download`，正文为后台派生的 `{applicationId,ownerAiUserId}`，使用独立 scope `reception.file.read` 服务凭据；AI 核对已受理 application/file/owner 关联。不能返回公开对象 URL、把用户令牌当服务凭据或公开内部下载入口。后台已受理但 AI 尚未关联回执时先返回可重试状态，恢复核对后再允许读取，不放宽权限兜底。


### 校验与实施界限

- 可机读定义：[xiaohe-reception-v1.schema.json](xiaohe-reception-v1.schema.json)，版本 reception.v1；UTF-8/LF 文件 SHA-256：`cfc323b58e95e7aebb336243d538f83154914a0f3a59c2c4ce112d6d2c7c27cc`。正反例：[reception-fixtures.json](reception-fixtures.json)。
- 运行 `node docs/contracts/check-reception.mjs`；复用 Runtime 已声明的 @cfworker/json-schema 4.1.6，无新增依赖。当前 44 个 schema 用例、10 个状态向量。动态版本、权限、幂等与单独确认向量仅用于复用预期，不证明实际服务已实现这些规则；B/A/F 测试必须调用各自真实实现再断言。
- 所有对象拒绝白名单以外字段；字段缺省不改，null 清空。普通文本最多 4000 个 Unicode 码点，列表最多 32 项、单项 256 码点；企业名最多 200，组织名 2—40，组织联系人最多 100，邮箱最多 254。空白名称不能正式提交。列表值不得重复，枚举按 schema 原值。金额及产能不数值换算。浏览器不得按 UTF-16 code unit 代替码点计数。
- 来源 sourceTenantId 与所有其他 ID 均用非空字符串传输；不将 AI UUID 转成后台 Long。后台 collaboratorId 仅由可信绑定派生，不允许客户端自报身份或覆盖归属。不同 system 独立，同一 system 内来源身份与 AI 用户一对一绑定。
- create 禁止指定 applicationId/expectedApplicationRevision；update 必须带 applicationId、subjectId 和 expectedApplicationRevision。入驻 update 必须同时带 onboardingId 和 expectedOnboardingRevision。独立确认与对象归属仍由服务端校验。
- FileRef 就绪时必须有 SHA-256；正式提交只接收 ready 文件。文件扩展名、内容签名、真实字节数及历史关联必须在实现层核验，JSON Schema 的 mediaType 不是内容安全证明。预留上传回包为 `{file:FileRef,uploadPath:string}`，路径必须是同源接待相对路径。
- 日期统一 UTC ISO-8601，以 Z 结尾；revision 不超过 JS 安全整数。未知受理结果不返回 accepted 回执。内部备注及存储 key 不进入对外 DTO。
- 协作方管理路由为 `/admin-api/collaborator/reception/**`；权限固定 `collaborator:reception:query|assign|update|audit|file`。原会员菜单、权限和业务对象不接收这些操作。
- 本次只追加新契约及当前修订说明。追加前文档 UTF-8 正文摘要 `2471733433ba09f7f3a8e4cb576bea9aa273105239d83af10f7cb7453fa30fb6`，历史正文原样保留；图件冻结包也不改写。

### B1/B3 实施消歧（v1.0.6）

- 内部请求固定为 `{actor:ReceptionActor,payload:对应请求 DTO}`；四个资料入口分别使用 SubjectQuery、ApplicationQuery、Submission、ResultQuery，其余为 PageCursor、IntentRequest、OnboardingSubmission，均已给出 Envelope schema。未知字段拒绝，客户端不得传入 collaboratorId。
- 专用请求头为 `X-Reception-Application: <登记 ID>` 与 `Authorization: Bearer <独立服务秘密>`。后台配置前缀 `yudao.collaborator.reception`，默认 enabled=false；applications 登记 id/systemId/businessTenantId/issuer/secretSha256。配置只保存秘密的 SHA-256，不复用 SSO_BRIDGE 或 Toolkit 凭据，不读取浏览器会话。
- 公司档案的公共字段可以同时包含 companyName/industry/companyScale/city/companyType；具体申请仍按 buyer/supplier 各自白名单分区。新增跨方向档案 fixture 锁定这一差异，避免把复用档案错误限定为单一申请字段集。
- 后台 CommonResult.code：1050000001=INPUT_INVALID、1050000002=DRAFT_CONFLICT、1050000003=SUBJECT_CONFLICT、1050000004=IDEMPOTENCY_CONFLICT、1050000005=SUBMISSION_UNKNOWN；HTTP 状态同时表达 422/409/503。服务鉴权为 HTTP401+code401，归属拒绝为 HTTP403+code403。AI 仅转换登记的安全错误，不透传异常。
- 新建 201／重放 200 必须由同一提交事务的真实受理结果决定，不能先查询存在性再猜测并发请求是否重放。
- 此补充不改变旧 wire，不表示正式库已升级；C0 当前为 41 个 schema 用例与 10 个状态向量。

### B4 进展与材料读取约定

- `/progress/query` 返回按申请分页（每页最多 50 条），随后按 subjectId 分组的 ProgressPage；同一主体可以跨页。nextCursor 不透明，门户将各页的同 subjectId 分组合并，并按 applicationId 去重，不将单页当成完整公司记录。
- 后台读取 AI 材料使用独立 `X-Reception-Application` 与 Bearer 凭据，scope 固定 reception.file.read。后台配置 `yudao.collaborator.reception.file-access.{base-url,application-id,secret}`；AI 对应独立 FILE_APPLICATION_ID/FILE_APPLICATION_SECRET 登记，不复用正向提交或 SSO 凭据。secret 使用 32—256 位 URL-safe 字符；base-url 是固定 AI 服务 origin，不由请求或模型提供。
- 材料下载必须先验证管理员权限、数据范围和 application/file 历史关联，再由 co_collaborator 派生 ownerAiUserId；允许读取已确认历史快照中的文件。请求仅包含 applicationId、ownerAiUserId。服务禁止跟随重定向，限制 32 MiB，并核对实际字节数及确认时 SHA-256。临时不可读返回 HTTP503 / code1050000006 FILE_UNAVAILABLE，不输出存储地址或凭据。

### B5 入驻修改当前版本读取（v1.0.7）

- 新增 POST /internal-api/reception/v1/onboarding/get，正文 OnboardingQueryEnvelope；payload={applicationId,onboardingId}，返回 OnboardingApplicationView={id,applicationId,revision,stage,publicFields,requestFields,files}。每次验证原申请及入驻的归属，返回当前正式资料，不从聊天或原服务申请覆盖已补充内容。
- CreateDraft/DraftView 增加可选 sourceOnboardingId；出现时 kind 必须为 onboarding、direction 必须为 organization，且 sourceApplicationId 必填。AI 新建修改草稿时必须经该新接口读取当前资料及 expectedOnboardingRevision；提交后仍更新原 onboardingId。
- 该缺口属于已批准的“修改更新原申请”语义补足，未增加业务阶段；旧 ChatPart、ArtifactView 和 AG-UI wire 均不改。reception.v1 schema 当前为 44 个 schema 用例及 10 个状态向量。

- v1.0.8 实施消歧：DELETE 文件关联请求的版本位于 JSON 正文 {expectedRevision}。接待 PATCH 使用 512 KiB 有界正文以容纳 C0 合法的多字段 Unicode 内容，旧聊天／认证正文限制不变。未新增 ChatPart、未修改 reception.v1 schema 或既有 AG-UI wire。

- v1.0.9 实施对齐：ReceptionAction.clientActionId 与 requestId 沿原 ChatAction 的 128 字符上限；其余接待 ID 仍最多 200 字符。新增四个 128/129 边界用例，当前 48 个 schema 用例、10 个状态向量。旧聊天协议未改变。

- v1.0.10 会话前置条件：GET `/ai-api/v1/reception/subjects` 与 `/progress` 必须携带当前 ChatSnapshot.csrf 指定的会话校验头。目录查询在 Cookie 所属会话内比较该随机令牌，防止共享 AI Cookie 已换号而页面仍持有旧快照时读取另一账号目录；缺失或不匹配返回既有 `403 CSRF_INVALID`。身份及归属仍由服务端 Cookie 校验。其它既有 GET 语义保持；reception.v1 JSON Schema、48个用例/10个状态向量及原 AG-UI wire 不变。


### 自动控制与系统提示（v1.0.11）

- `POST /ai-api/v1/reception/conversations/{id}/controls`：请求 `{clientActionId:string;action:'accept_invitation'|'start_collection'}`，202返回原 `AcceptedTurn` 外壳。仅接收结构化动作，不接收用户发言、作者、登录身份或任意通知正文。沿用当前会话Cookie、精确Origin、CSRF、会话归属与持久Run/Command管线。
- `accept_invitation` 只允许已知接待方向的有效邀请或用户主动重新选择，服务端记录“开始注册或登录”；`start_collection` 要求真实AI登录身份及已接受邀请或本人现有草稿，记录“采集信息”。
- 控制回执的 `message` 使用既有 `role:'assistant'` 服务端消息容器，`parts` 必须仅包含 `system_notice`；卡片类型明确表示系统事件，不代表助手自然语言回答或用户发言。普通消息回执仍必须为 `role:'user'`，不放宽其作者校验。
- 系统动作按会话和clientActionId幂等，重新请求返回同一消息/Run，不重复创建草稿；不得增加访客消息次数或续访客期限。用户亲自输入的文字仍按真实用户消息记录。
- 客户端以灰色系统提示呈现通知，认证源校验完成并绑定AI身份后显示“您已经进行了登录”。该提示来自可信用户快照，不使用页面本地标记授予权限。
- 本接口与系统提示属于门户↔AI会话交互，不进入Java Collaborator正式申请DTO/schema，也不改变最低资料、版本确认或正式提交规则。

### 四个独立 Agent 的 YAML 布局兼容增量（2026-11-06）

新服务的 `DraftView.fieldLayout = {rounds:[{id,name,fields}],requiredFields}` 来自该草稿绑定的持久流程版本，门户据此展示轮次与必填提示。该字段为兼容旧服务而可选；缺失时仅保留旧固定布局作为兼容显示。字段类型白名单不变，权限、正式提交最低要求与版本确认仍由服务端强制校验。 `fieldLayout`描述当前采集范围，不等于提交资料全集；从正式申请复用的`fields`可包含当前布局外的既有值。汇总必须同时展示这些保留值，并明确其将随本次提交保留；当前流程不支持编辑的保留值只读显示，不得静默隐藏或丢弃。本增量不导出提示词或完整流程，不增加编辑／导出界面，不修改第12节生命周期设计。

## 12. 生命周期与历史视图 V2 规划（2026-11-06，设计已批准，待实施）

本节为新增功能的唯一跨工程规划基线，不表示 API、schema、迁移或部署已存在。用户已在门户聊天批准[生命周期与历史设计 v1.0](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/superpowers/specs/2026-11-06-xiaohe-lifecycle-history-design.md)。实施计划须再次审阅后才能执行；正式开关保持关闭，不部署、不自动短信、不调用模型。本节不修改前述 V1 wire/schema；文档修订号仍保留既有 v1.0.11 记录，本节 V2 是表示版本，二者不是一回事。

AI/Runtime [实施计划](../../agent-service-toolkit/docs/superpowers/plans/2026-11-06-xiaohe-lifecycle-history-ai-runtime.md)。门户和 SSO 只引用本节，不分别复制规范。本文外于嵌套 Git 仓库，本次仅本地维护，未作为已提交文件发布。

### 12.1 表示版本与授权

- 路径仍使用 `/ai-api/v1`；`X-Xiaohe-View-Version: 2` 显式选择 V2。省略为 V1，显式 `1` 等同省略。重复、空值、其他值返回 `406 VIEW_VERSION_UNSUPPORTED`，不能静默回退。响应回显同名头 `1` 或 `2`，含流前响应；沿用 `Cache-Control: private, no-store`。
- 回显覆盖所有成功解析版本的适用 AI 路由成功及过滤错误（含认证非快照响应、流前错误）；头本身非法时返回406且不反射非法值，可省略回显。Runtime 的 SSE、JSON/错误及 `/copilot-api/info` 均回显其固定配置版本；info body 保留 SDK 原形状，回显只证明 Runtime 配置，不代表 Python 已就绪。`/healthz` 不参与视图协议。
- V2 门户必须校验回显头及 JSON `version:2`；缺失、V1 或格式错误按协议不兼容处理。新建历史页及定位路由仅支持显式 `2`。V1 全量读取及投影语义保留；本节匿名预算规则由公共业务层实施，不能让 V1 继续独立发放另一份额度。
- Runtime 通过服务端 `XIAOHE_VIEW_VERSION=1|2` 固定选择，默认 `1`；对 `agui/run|status|stop|connect` 都发送并校验同一版本。浏览器不能覆盖该配置或指定任意上游。V2 Runtime 遇不支持的旧 Python 必须失败，不请求 V1 兜底。保留现有 SDK 和 TLS/Origin/Cookie 转发白名单，只增本节受控版本头。
- 为保留既有组合，Runtime的V1模式可接受旧Python缺少回显，但必须仍通过原V1严格schema，收到显式2则拒绝；只有显式V2模式必须收到2且严格V2，不能将上述V1兼容用作V2回退。
- 版本只决定表示，不是身份、授权码、业务幂等键或认证事务键。同一 operation 的 V1/V2 complete 重读都必须指向同一可信用户、原会话及原绝对截止；不再次换码、建立绑定或归并。不得先构建全量快照再截取为 V2。
- 既有精确 Origin、作用域、Cookie、CSRF、对象权限全部保留。V2 的快照、历史页、定位 GET 额外携带当前聊天 `X-Xiaohe-CSRF` 并校验会话，防止浏览器 Cookie 换号而页面仍持有旧视图；bootstrap 仍按原创建语义校验 Origin，complete 仍用认证专用 Cookie/CSRF，两种证明不能互换。读取不扣额、不续期。

### 12.2 DTO 与读取入口

下列为严格对象，未声明字段拒绝；ID、UTC 时间、整数、Viewer、Csrf、ChatMessage、RunRef、MergeState 和所有现有 ChatPart 的定义复用前文。`historyEpoch`、`watermarkSequence` 为 `0..9007199254740991` 的 JSON integer，事件 `sequence` 为 `1..9007199254740991` 的 JSON integer，复用既有 NonNegative/Positive；不是十进制字符串。Python 在递增和序列化边界都检查范围，超限失败关闭，不能取模/截断或以浮点丢精度；客户端用 Number.isSafeInteger 校验。`pageSize` 固定 `10`。消息计数按单条消息，不按轮数。

```typescript
type HistoryV2 = {
  pageSize: 10;
  hasMore: boolean;
  olderCursor: string | null;
  historyEpoch: number;
};
type ActiveInputV2 = {
  runId: string;
  messageId: string;
  requestId: string;
  kind: 'choice' | 'input_request';
} | null;
type ChatSnapshotV2 = {
  version: 2;
  viewer: Viewer;
  csrf: Csrf;
  sessionExpiresAt: string;
  conversationId: string;
  messages: ChatMessage[];
  activeRun: RunRef | null;
  activeInput: ActiveInputV2;
  merge: MergeState | null;
  history: HistoryV2;
  sync: {eventCursor: string | null; watermarkSequence: number};
};
type HistoryPageV2 = {
  version: 2;
  conversationId: string;
  messages: ChatMessage[];
  history: HistoryV2;
  watermarkSequence: number;
};
type MessageBaselineV2 = {
  version: 2;
  conversationId: string;
  message: ChatMessage;
  historyEpoch: number;
  watermarkSequence: number;
};
```

| 入口（均带版本头 `2`） | 响应与限制 |
| --- | --- |
| `POST /session`，V2 body为 `{}` 或 `{expectedIdentity}` | `200 ChatSnapshotV2`；空body为匿名接待/无门户预期身份的原语义；带预期身份时先核对已授权AI用户，不先建匿名窗口或输出另一账号历史。有效窗口复用，匿名已过期且预算有余才原子建立新周期 |
| `GET /conversations/{id}` | `200 ChatSnapshotV2`，当前 canonical 最近至多 10 条；GET 不建立或续期窗口 |
| `POST /auth/operations/{id}/complete`，原 body/认证 CSRF | `200` 或 `202 ChatSnapshotV2`；原 merge 三态、Retry-After 及响应丢失重试规则不变 |
| `GET /conversations/{id}/messages?before=<olderCursor>` | `200 HistoryPageV2`；before 必填，固定至多 10 条，拒绝 limit/offset；没有更早消息时 messages=[]、hasMore=false、olderCursor=null |
| `GET /conversations/{id}/messages/{messageId}` | `200 MessageBaselineV2`；授权定位单条完整消息，不推进分页或全局事件水位 |

`auth/context`、`auth/operations` create/advance/cancel 等非快照响应继续原 DTO，不新增 JSON version 字段；只按请求回显版本。V2 restore 的 create body 有本节下述明确增量，不能由门户私造字段。

门户已有经校验的登录身份时，V2 bootstrap 必须带下面同型 `expectedIdentity` 拒绝约束；服务端将现有有效用户Cookie对应的可信User绑定与其比较，相符才输出快照。AI Cookie缺失/过期或仍是guest时401 AUTH_REQUIRED，账号不同409 IDENTITY_MISMATCH，不先返回私有历史、不新建可输入guest。门户随后使用相同预期身份走restore。空body不能作为已登录门户的自动恢复兜底；V1空body仍不改。后续快照/页/定位GET用已成功恢复的canonical和当前CSRF，防止Cookie换号后的迟到请求。此扩展避免为确认一个仍有效AI绑定而每次强制跳SSO，expectedIdentity自身仍不授予读取权。

**V2 restore 的原身份拒绝约束：** `CreateAuthOperationV2` 复用原 intent/oidc/browserCompletionChallenge/browserCompletionChallengeMethod，另加 `expectedIdentity:{issuer:string;sub:string;sourceTenantId:number}`。此字段仅 intent=restore 时允许且必填；其他 intent 必须省略，不允许 null。issuer须等于受信登记，sub沿现有Id校验，sourceTenantId沿既有安全整数规则，不能填AI user_id或业务tenantId。V1 Create仍保留严格原字段，不将此字段无版本加入V1。

门户在顶层往返**之前**从原已校验 Portal 会话取得此约束，不在B账号回调后改成B；它只是一项拒绝条件，不授予身份，也不能代替受信中心claim。AI在原AuthOperationRecord中持久冻结 expected_identity，纳入原request_digest和认证上下文/OIDC tuple/browserChallenge关联；回调和complete不允许更新它。已有可识别旧AI用户会话时，服务端从原可信User绑定派生身份，客户端约束与之不同即 `409 IDENTITY_MISMATCH`。AI Cookie全失时，客户端约束只能使不符claim被拒绝，不能成为可信身份源；中心经原安全流程取得的claim仍是唯一授予身份的依据。识别旧会话仅作拒绝约束，不恢复其已撤销访问权。

服务端在verify_completion后、`bind_user`、新用户/会话/默认对话创建和任何 guest claim/merge **之前**，逐项比较 issuer+sub+sourceTenantId。原A与claim B不符时409，不能先把guest归并到B再让门户隐藏；即使直接调用complete绕过门户也必须拒绝，无用户/会话/对话/归并副作用。已经完成的重读仍校验同一冻结约束及原绑定/截止；无须修改中心Create/六字段claim合同。V2自动restore不能降级V1来绕过约束。

`hasMore=false` 当且仅当 `olderCursor=null`。消息按 `(createdAt,id)` 正序返回；较早页用反向 keyset 查询再正序输出。最近窗口和每一页均只包含完整消息，保留卡片、系统提示和精确 artifact 版本，不拆 parts。`sessionExpiresAt` 为服务器当前内容/身份授权截止，用户为原建立时刻 +28800 秒，guest 与 viewer.expiresAt 一致；前端时钟不授予权限。

`activeInput` 只指服务端判定仍有效、属于当前 activeRun 的 waiting_input 消息，请求 ID 与原卡片相同；没有则 null。若消息不在最近 10 条，门户显示定位入口，通过单条路由读取，不把第 11 条消息暗加进首屏。定位成功不保证之后动作仍有效，提交时仍执行 ACTION_STALE/版本校验。文件详情/下载复用原授权接口，每次重新校验。

### 12.3 历史成员边界、归并与同步水位

- 历史 cursor 不透明且防篡改，绑定授权主体、system/tenant、canonical conversation、historyEpoch、初始成员上界和 before 二元键。页大小固定，不接收调用者自行构造的 ID/时间排序边界。
- 初始视图冻结“哪些消息已加入时间线”，不承诺重建过去正文版本。需持久消息加入序号：新消息即使 createdAt 与旧消息相同、ID 排在旧上界之前，也不能混入已经冻结的旧页。页正文按本页事务时刻读取；同一 Conversation 锁下取页和该页事件水位，不能先读水位再在另一视图读正文。
- `historyEpoch` 独立于 `ownership_epoch`；归并向目标插入旧消息时增加，保留原消息 ID/createdAt。目标旧历史游标返回 HISTORY_RESET_REQUIRED，普通 append 不使其失效。源 guest 已撤销时优先拒绝其访问，不能在错误中泄漏会员 canonical ID。
- `sync.eventCursor` 沿用现有 Event UUID 续传体系；`watermarkSequence` 复用服务端单调事件序列，空事件流为 0/null。V2 事件信封在原字段外增加 `sequence` 和 `historyEpoch`；V1 不增字段。V2 原生 `/events` 续传另带 `historyEpoch` 查询参数，与 after/Last-Event-ID 一并校验；历史翻页 cursor 不能传给事件入口。
- 页和定位水位仅作为所含消息的完整正文基线，不能推进全局已消费事件位置。前端按身份/canonical/epoch/messageId 去重，迟到页不能覆盖已应用更高 sequence 的同一消息；基线已涵盖的旧 delta 不再拼接。其他消息仍正常应用这些事件。跨身份、跨 canonical 或旧 epoch 的迟到响应丢弃。
- 重连取得新的最近窗口/成员上界时，缓存中的旧消息不等于与新窗口之间全部连续。门户从新快照的 olderCursor 维护本轮页链/已覆盖区间，补到旧已覆盖区间后才可拼成连续历史，不能拿缓存中“最老ID”替代服务端cursor而跳过离线期间的消息。保留消息索引不代表伪装缺口已加载，也不要求自动读完全部历史。
- canonical/epoch 变化后，已授权客户端丢弃旧分页游标并读取新 canonical 最近页；事件订阅中发生变化必须停止旧流并要求重建，不能继续混用新旧成员。安全归属 fence 仍独立生效。

### 12.4 V2 AG-UI 投影与通道结束

`PublicSnapshotV2 = Omit<ChatSnapshotV2,'csrf'> & {attemptId:string|null;businessRunId:string|null;businessRunStatus:RunStatus|null}`。`agui/connect` 的初始/重连 STATE_SNAPSHOT 使用此结构，最近窗口不超过 10 条；MESSAGES_SNAPSHOT 只更新 SDK 当前窗口，门户另存的已加载历史不归 SDK 替换所有权。后续投影也保持有界，不把全部数据库历史装进内存，不把已加载旧页作为模型可信上下文回灌。

门户历史索引以受权REST基线与V2 CUSTOM业务事件为唯一增量来源；同一delta投影出的标准TEXT_MESSAGE_CONTENT只服务SDK窗口，不能再重复写入同一历史索引。

- `CUSTOM name='xiaohe.v2.event'`：value 是本节 V2 业务事件信封，包含原 eventId/conversationId/runId/type/payload 及 sequence/historyEpoch。V1 继续 `xiaohe.v1.event`，不能在同一流混发两种版本。
- `CUSTOM name='xiaohe.v2.message-baseline'`：value 为 MessageBaselineV2。活跃 assistant 不在窗口、随后会收到 delta 时，服务端先给它的受权完整基线，门户只初始化消息流缓存，不额外改变首屏条数。基线读取须与后续流建立一致界线；对该消息忽略 sequence 不高于其基线的 delta，但不跳过别的消息。无基线不得对未知消息从空文本拼接。
- `CUSTOM name='xiaohe.v2.ready'`：value 为 `{conversationId,historyEpoch,watermarkSequence}`，表示初始受权快照和必要基线已交付，可作为空闲连接的正常结束标记。它不推进实时游标、不意味着业务 run 完成。空闲连接收到它后可正常 EOF；活跃 run 收到后仍须等待原 RUN_FINISHED/RUN_ERROR，意外 EOF 仍为中断。
- run/status/stop 的业务 DTO、attemptId、受理幂等及运行状态语义沿用 V1；V2 schema 在同一主契约新增独立机器块，生成副本后各工程消费，不改现有 V1 机器块。新 Runtime 对各响应版本严格检查；SDK/上游 toolkit 不新增身份或历史事实源。

### 12.5 持久匿名预算与内容周期

匿名 100 条额度从 Session 分离为 system/tenant 内稳定预算；当前 Session/guestId/conversation 是单个内容周期。V1/V2、REST/AG-UI 和真实创建用户消息的有效动作共用同一原子 admission，不能双算或各发 100。界面自动 controls/system_notice、欢迎语、AI 回复、读取、失败及同键重试不扣额、不滑动 4h。

本节实施后，§3中“已识别过期guest的POST/session一律410且不能新建”的旧生命周期规则由“证明同一预算且仍有剩余时建立新内容周期”取代，V1/V2共用；不是给过期guest另一份100。各自快照表示仍保持V1 full/V2 bounded，GET/旧流/文件的过期拒绝不变。

- `__Host-XIAOHEBUDGET` 为新 opaque Secure/HttpOnly/SameSite=Lax/Path=/ Cookie，无 Domain；32 随机字节，只存摘要。Max-Age=31536000 秒，每次受权 bootstrap 可续签相同预算凭据；浏览器实际保留可短于该值。预算记录不定期清零或随正文删除，凭据续签/轮换仍属于同一预算。已知无效/撤销凭据不能被当作首次访问。
- guest 内容 Cookie 可同样持久保存最多 31536000 秒，只为关闭浏览器后仍识别原会话；服务器仍严格执行滚动 14400 秒内容截止，Cookie 留存不是访问延期。用户 Cookie 保留原策略，用户会话绝对 8 小时不变。预算 Cookie 单独不能授权旧正文、会员数据或复活已撤销的源会话。
- 旧内容期满后立即拒绝原流、写请求、文件和运行授权，再按既有安全点物理清理；若剩余预算大于 0，POST/session 原子复用或创建一个新周期及免费欢迎语。并发标签只得到同一周期，不创建重复欢迎。旧 conversation 的迟到请求不能改投新周期。
- 当前有效窗口已用 100 时仍可读；第 100 条已受理回复可完成，不因次数恰好用尽取消。第 101 条拒绝。窗口已过期且用尽时 POST/session 返回 `429 VISITOR_LIMIT_REACHED`，无私有旧内容、无新可发送窗口，认证仍可用独立 auth/context。缺内容 Cookie 但仍识别一个有效周期时，不得仅凭预算读取旧内容或再送新周期，保守返回 AUTH_REQUIRED；正常关闭/重开由持久内容 Cookie 恢复。
- 登录认领、注销、撤销短期内容会话不清零预算，之后匿名仍使用剩余额度。注销不泄漏会员内容，也不能用旧被认领 guest 的读取权限恢复内容；独立预算身份只允许后续合规匿名周期。
- 存量按唯一、可证明且无认领/撤销冲突的旧 token/Session 及其保留 count/limit 事务绑定预算一次。清理正文后仍保留的 Session 计数可作为来源，禁止按剩余消息行数推算。已知旧身份但 count/归属无法证明时返回 `401 AUTH_REQUIRED` 引导登录，不能按 0 补额度。真实数据库承接另需批准；本节只规定未来实现，不执行承接。
- 4h 正文清理会删除原 Command；跨周期只保存预算所需的最小幂等键摘要、请求摘要和旧周期标记，不保留旧正文/文件。旧请求只能按旧授权拒绝，不能重新投递。保留最小账本不授予旧数据访问权。
- 全部凭据被清除、设备/隐私窗口改变后无法证明是同一匿名自然人；真正首次访客可以建立预算。不引入 IP、指纹或跨系统身份追踪，不宣称清站点数据也绝不重置。

### 12.6 错误及恢复边界

沿用 ApiError 外壳及过滤提示，新增以下 V2 代码（待实施）：

| 状态/代码 | 行为 |
| --- | --- |
| `406 VIEW_VERSION_UNSUPPORTED` | 表示不支持/版本头非法，不回退 V1、不发业务请求 |
| `409 HISTORY_CURSOR_INVALID` | malformed、伪造、历史/事件 cursor 混用；不返回私有游标内部字段 |
| `409 HISTORY_RESET_REQUIRED` | 已授权目标的历史 epoch 已变；重取 canonical 最近页，不重发业务输入 |
| `404 MESSAGE_NOT_FOUND` | 当前已授权 conversation 内消息不存在；跨主体/作用域先按 FORBIDDEN 拒绝 |

原 `401 AUTH_REQUIRED`、`403 FORBIDDEN/CSRF_INVALID`、`410 VISITOR_EXPIRED`、`429 VISITOR_LIMIT_REACHED`、事件 cursor 错误、AUTH_NOT_READY/AUTH_RATE_LIMITED/Retry-After、ACTION_STALE 等保留。V1 错误集不被无版本扩展；新错误只给 V2 调用者。不可识别的新错误按协议失败处理，不能降级匿名或重新扣额。

认证继续顶层 form/OIDC 往返，校验受信 issuer+sub+sourceTenantId、原绑定与作用域，不新增 iframe/popup/BFF、不放开 frame 策略、不伪造 prompt=none 能力；错误账号停止恢复/归并。新的真实认证与已完成 operation 重试严格区分，后者仍保持原绝对截止。网络故障不能转成匿名会话；空闲 ready EOF 与活跃流异常 EOF 分别处理。返回页面/锚点和窗口打开意图由门户按允许列表恢复，URL 不放聊天正文或凭据。

### 12.7 实施前置与验收状态

依赖：共享契约核对 → 用户审阅各工程计划 → AI/Runtime 同一基线实现 → 门户消费适配 → 隔离组合验收。本节最初为规划基线，后续各工程实际实现、固定提交及逐层验证以对应实施计划和验收记录为准；机器块存在不代表真实迁移、部署或三方验收。原 G2 或文件证据仅可复用其已验证范围，不代表分页、持久预算、V2 已通过。

至少覆盖：20→4h→剩80；99/100/101/幂等；关闭/隔天及凭据轮换不清零；已知坏旧凭据不补0；并发重开与旧流/清理/claim 竞争；35条10/10/10/5及同时间戳新消息；归并后旧cursor；迟到页与delta水位；旧waiting_input定位及精确文件版本；跨身份/系统游标；V1/V2兼容与拒绝混用；原8h及complete丢失；空闲ready/活跃EOF。测试只能在批准后使用隔离库、离线/受控模型和伪造身份 fixture，不触碰现有体验窗口/服务。


### 12.8 V2 机器契约（2026-11-07，Task 1 离线消费基线）

下块是 §12 DTO 与 AG-UI 的唯一机器来源，Runtime 副本仅提取。导出名称为 `$defs` 中的 `ChatSnapshotV2`、`HistoryPageV2`、`MessageBaselineV2`、`PublicSnapshotV2`、`BootstrapInputV2`、`CreateAuthOperationV2`、`ExpectedIdentity`、`WireEventV2` 和 `StreamEventV2`。固定虚构样例位于 `tests/xiaohe/contracts/view_v2.json`。 Task 6 补齐机器约束：V2 标准 `MESSAGES_SNAPSHOT.messages` 同样最多 10 条，与原 §12.4 有界窗口要求一致；V1 机器块和所有 DTO 字段不变。此交付只解锁离线消费；预算、分页 API、恢复约束的实际持久化和 Runtime V2 尚待后续任务，不代表已经部署或通过三方验收。V1 机器块保持原字节。

<!-- xiaohe-agui-v2-schema:start -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:xiaohe:agui:v2",
  "title": "小鹤显式 V2 有界视图与 AG-UI 扩展",
  "$defs": {
    "AguiActivityMessage": {
      "additionalProperties": true,
      "description": "Structured progress that is not conversation content, materialised as a\nmessage so it keeps its place in the sequence. Stands alone rather than\ncomposing BaseMessage, because its content is an object rather than a\nstring.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "activity",
          "default": "activity",
          "title": "Role",
          "type": "string"
        },
        "activityType": {
          "title": "Activitytype",
          "type": "string"
        },
        "content": {
          "additionalProperties": true,
          "title": "Content",
          "type": "object"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "id",
        "activityType",
        "content"
      ],
      "title": "ActivityMessage",
      "type": "object"
    },
    "AguiAssistantMessage": {
      "additionalProperties": true,
      "description": "A message from the agent. Content is optional because a turn may consist\nonly of tool calls.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "assistant",
          "default": "assistant",
          "title": "Role",
          "type": "string"
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Name"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        },
        "content": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Content"
        },
        "toolCalls": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/AguiToolCall"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Toolcalls"
        }
      },
      "required": [
        "id"
      ],
      "title": "AssistantMessage",
      "type": "object"
    },
    "AguiAudioPart": {
      "additionalProperties": true,
      "description": "An audio part.",
      "properties": {
        "type": {
          "const": "audio",
          "default": "audio",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "source": {
          "discriminator": {
            "mapping": {
              "data": "#/$defs/AguiDataSource",
              "file": "#/$defs/AguiFileSource",
              "url": "#/$defs/AguiUrlSource"
            },
            "propertyName": "type"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/AguiDataSource"
            },
            {
              "$ref": "#/$defs/AguiUrlSource"
            },
            {
              "$ref": "#/$defs/AguiFileSource"
            }
          ],
          "title": "Source"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "source"
      ],
      "title": "AudioPart",
      "type": "object"
    },
    "AguiContext": {
      "additionalProperties": true,
      "description": "A named piece of ambient information given to the agent for the run,\ndistinct from the conversation.",
      "properties": {
        "description": {
          "title": "Description",
          "type": "string"
        },
        "value": {
          "title": "Value",
          "type": "string"
        }
      },
      "required": [
        "description",
        "value"
      ],
      "title": "Context",
      "type": "object"
    },
    "AguiDataSource": {
      "additionalProperties": true,
      "description": "Bytes carried inline.",
      "properties": {
        "type": {
          "const": "data",
          "default": "data",
          "title": "Type",
          "type": "string"
        },
        "value": {
          "title": "Value",
          "type": "string"
        },
        "mimeType": {
          "title": "Mimetype",
          "type": "string"
        }
      },
      "required": [
        "value",
        "mimeType"
      ],
      "title": "DataSource",
      "type": "object"
    },
    "AguiDeveloperMessage": {
      "additionalProperties": true,
      "description": "Instructions from the application developer.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "developer",
          "default": "developer",
          "title": "Role",
          "type": "string"
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Name"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        },
        "content": {
          "title": "Content",
          "type": "string"
        }
      },
      "required": [
        "id",
        "content"
      ],
      "title": "DeveloperMessage",
      "type": "object"
    },
    "AguiDocumentPart": {
      "additionalProperties": true,
      "description": "A document part.",
      "properties": {
        "type": {
          "const": "document",
          "default": "document",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "source": {
          "discriminator": {
            "mapping": {
              "data": "#/$defs/AguiDataSource",
              "file": "#/$defs/AguiFileSource",
              "url": "#/$defs/AguiUrlSource"
            },
            "propertyName": "type"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/AguiDataSource"
            },
            {
              "$ref": "#/$defs/AguiUrlSource"
            },
            {
              "$ref": "#/$defs/AguiFileSource"
            }
          ],
          "title": "Source"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "source"
      ],
      "title": "DocumentPart",
      "type": "object"
    },
    "AguiFileSource": {
      "additionalProperties": true,
      "description": "Bytes already at the provider, named by a handle the provider issued: an\nOpenAI or Anthropic file id, a Gemini file URI, a storage URL only that\nprovider can read. No bytes travel and nothing is fetched. Only the\nprovider that minted the handle can resolve it; a peer that cannot drops\nthe part as it drops any part it cannot use.",
      "properties": {
        "type": {
          "const": "file",
          "default": "file",
          "title": "Type",
          "type": "string"
        },
        "value": {
          "title": "Value",
          "type": "string"
        },
        "provider": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Provider"
        },
        "mimeType": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Mimetype"
        }
      },
      "required": [
        "value"
      ],
      "title": "FileSource",
      "type": "object"
    },
    "AguiFunctionCall": {
      "additionalProperties": true,
      "description": "The name and arguments of a tool call.",
      "properties": {
        "name": {
          "title": "Name",
          "type": "string"
        },
        "arguments": {
          "title": "Arguments",
          "type": "string"
        }
      },
      "required": [
        "name",
        "arguments"
      ],
      "title": "FunctionCall",
      "type": "object"
    },
    "AguiImagePart": {
      "additionalProperties": true,
      "description": "An image part.",
      "properties": {
        "type": {
          "const": "image",
          "default": "image",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "source": {
          "discriminator": {
            "mapping": {
              "data": "#/$defs/AguiDataSource",
              "file": "#/$defs/AguiFileSource",
              "url": "#/$defs/AguiUrlSource"
            },
            "propertyName": "type"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/AguiDataSource"
            },
            {
              "$ref": "#/$defs/AguiUrlSource"
            },
            {
              "$ref": "#/$defs/AguiFileSource"
            }
          ],
          "title": "Source"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "source"
      ],
      "title": "ImagePart",
      "type": "object"
    },
    "AguiReasoningMessage": {
      "additionalProperties": true,
      "description": "A span of the agent's reasoning, materialised as a message. Stands alone\nrather than composing BaseMessage, because it carries no name.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "reasoning",
          "default": "reasoning",
          "title": "Role",
          "type": "string"
        },
        "content": {
          "title": "Content",
          "type": "string"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "id",
        "content"
      ],
      "title": "ReasoningMessage",
      "type": "object"
    },
    "AguiResumeEntry": {
      "additionalProperties": true,
      "description": "An answer to one interrupt, sent on the run that continues from it.",
      "properties": {
        "interruptId": {
          "title": "Interruptid",
          "type": "string"
        },
        "status": {
          "enum": [
            "resolved",
            "cancelled"
          ],
          "title": "Status",
          "type": "string"
        },
        "payload": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Payload"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "interruptId",
        "status"
      ],
      "title": "ResumeEntry",
      "type": "object"
    },
    "AguiSystemMessage": {
      "additionalProperties": true,
      "description": "Instructions from the system.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "system",
          "default": "system",
          "title": "Role",
          "type": "string"
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Name"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        },
        "content": {
          "title": "Content",
          "type": "string"
        }
      },
      "required": [
        "id",
        "content"
      ],
      "title": "SystemMessage",
      "type": "object"
    },
    "AguiTextPart": {
      "additionalProperties": true,
      "description": "A text part.",
      "properties": {
        "type": {
          "const": "text",
          "default": "text",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "text": {
          "title": "Text",
          "type": "string"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "text"
      ],
      "title": "TextPart",
      "type": "object"
    },
    "AguiTool": {
      "additionalProperties": true,
      "description": "A tool the agent may call.",
      "properties": {
        "name": {
          "title": "Name",
          "type": "string"
        },
        "description": {
          "title": "Description",
          "type": "string"
        },
        "parameters": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Parameters"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "name",
        "description"
      ],
      "title": "Tool",
      "type": "object"
    },
    "AguiToolCall": {
      "additionalProperties": true,
      "description": "A call an assistant message made. Carries no subagent attribution of its\nown and inherits its containing message's, since several calls can share\none parent.",
      "properties": {
        "id": {
          "title": "Id",
          "type": "string"
        },
        "type": {
          "const": "function",
          "default": "function",
          "title": "Type",
          "type": "string"
        },
        "function": {
          "$ref": "#/$defs/AguiFunctionCall"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "id",
        "function"
      ],
      "title": "ToolCall",
      "type": "object"
    },
    "AguiToolMessage": {
      "additionalProperties": true,
      "description": "What a tool returned, as a message in the conversation. Stands alone\nrather than composing BaseMessage, because it carries no name.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "tool",
          "default": "tool",
          "title": "Role",
          "type": "string"
        },
        "content": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "items": {
                "discriminator": {
                  "mapping": {
                    "audio": "#/$defs/AguiAudioPart",
                    "document": "#/$defs/AguiDocumentPart",
                    "image": "#/$defs/AguiImagePart",
                    "text": "#/$defs/AguiTextPart",
                    "video": "#/$defs/AguiVideoPart"
                  },
                  "propertyName": "type"
                },
                "oneOf": [
                  {
                    "$ref": "#/$defs/AguiTextPart"
                  },
                  {
                    "$ref": "#/$defs/AguiImagePart"
                  },
                  {
                    "$ref": "#/$defs/AguiAudioPart"
                  },
                  {
                    "$ref": "#/$defs/AguiVideoPart"
                  },
                  {
                    "$ref": "#/$defs/AguiDocumentPart"
                  }
                ]
              },
              "type": "array"
            }
          ],
          "title": "Content"
        },
        "toolCallId": {
          "title": "Toolcallid",
          "type": "string"
        },
        "error": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Error"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "id",
        "content",
        "toolCallId"
      ],
      "title": "ToolMessage",
      "type": "object"
    },
    "AguiUrlSource": {
      "additionalProperties": true,
      "description": "Bytes referenced by URL, fetched by whoever needs them.",
      "properties": {
        "type": {
          "const": "url",
          "default": "url",
          "title": "Type",
          "type": "string"
        },
        "value": {
          "title": "Value",
          "type": "string"
        },
        "mimeType": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Mimetype"
        }
      },
      "required": [
        "value"
      ],
      "title": "UrlSource",
      "type": "object"
    },
    "AguiUserMessage": {
      "additionalProperties": true,
      "description": "A message from the person using the application.",
      "properties": {
        "subagentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subagentrunid"
        },
        "id": {
          "title": "Id",
          "type": "string"
        },
        "role": {
          "const": "user",
          "default": "user",
          "title": "Role",
          "type": "string"
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Name"
        },
        "encryptedValue": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Encryptedvalue"
        },
        "metadata": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        },
        "content": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "items": {
                "discriminator": {
                  "mapping": {
                    "audio": "#/$defs/AguiAudioPart",
                    "document": "#/$defs/AguiDocumentPart",
                    "image": "#/$defs/AguiImagePart",
                    "text": "#/$defs/AguiTextPart",
                    "video": "#/$defs/AguiVideoPart"
                  },
                  "propertyName": "type"
                },
                "oneOf": [
                  {
                    "$ref": "#/$defs/AguiTextPart"
                  },
                  {
                    "$ref": "#/$defs/AguiImagePart"
                  },
                  {
                    "$ref": "#/$defs/AguiAudioPart"
                  },
                  {
                    "$ref": "#/$defs/AguiVideoPart"
                  },
                  {
                    "$ref": "#/$defs/AguiDocumentPart"
                  }
                ]
              },
              "type": "array"
            }
          ],
          "title": "Content"
        }
      },
      "required": [
        "id",
        "content"
      ],
      "title": "UserMessage",
      "type": "object"
    },
    "AguiVideoPart": {
      "additionalProperties": true,
      "description": "A video part.",
      "properties": {
        "type": {
          "const": "video",
          "default": "video",
          "title": "Type",
          "type": "string"
        },
        "id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Id"
        },
        "source": {
          "discriminator": {
            "mapping": {
              "data": "#/$defs/AguiDataSource",
              "file": "#/$defs/AguiFileSource",
              "url": "#/$defs/AguiUrlSource"
            },
            "propertyName": "type"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/AguiDataSource"
            },
            {
              "$ref": "#/$defs/AguiUrlSource"
            },
            {
              "$ref": "#/$defs/AguiFileSource"
            }
          ],
          "title": "Source"
        },
        "metadata": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Metadata"
        }
      },
      "required": [
        "source"
      ],
      "title": "VideoPart",
      "type": "object"
    },
    "RunInput": {
      "additionalProperties": true,
      "description": "A request to run an agent. Also echoed back as RUN_STARTED.input. Only\nthreadId, runId and messages are required: those are the three the SDKs\nalready agree on, and for tools and context an absent key and an empty\narray mean the same thing, so requiring them would catch nothing a\nproducer could get wrong.",
      "properties": {
        "threadId": {
          "type": "string",
          "format": "uuid"
        },
        "runId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        },
        "protocolVersion": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Protocolversion"
        },
        "parentRunId": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Parentrunid"
        },
        "state": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "messages": {
          "items": {
            "discriminator": {
              "mapping": {
                "activity": "#/$defs/AguiActivityMessage",
                "assistant": "#/$defs/AguiAssistantMessage",
                "developer": "#/$defs/AguiDeveloperMessage",
                "reasoning": "#/$defs/AguiReasoningMessage",
                "system": "#/$defs/AguiSystemMessage",
                "tool": "#/$defs/AguiToolMessage",
                "user": "#/$defs/AguiUserMessage"
              },
              "propertyName": "role"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/AguiDeveloperMessage"
              },
              {
                "$ref": "#/$defs/AguiSystemMessage"
              },
              {
                "$ref": "#/$defs/AguiAssistantMessage"
              },
              {
                "$ref": "#/$defs/AguiUserMessage"
              },
              {
                "$ref": "#/$defs/AguiToolMessage"
              },
              {
                "$ref": "#/$defs/AguiActivityMessage"
              },
              {
                "$ref": "#/$defs/AguiReasoningMessage"
              }
            ]
          },
          "title": "Messages",
          "type": "array"
        },
        "tools": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/AguiTool"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Tools"
        },
        "context": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/AguiContext"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Context"
        },
        "forwardedProps": {
          "anyOf": [
            {},
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Forwardedprops"
        },
        "resume": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/AguiResumeEntry"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Resume"
        }
      },
      "required": [
        "threadId",
        "runId",
        "messages"
      ],
      "title": "RunAgentInput",
      "type": "object"
    },
    "ArtifactRef": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "artifact_ref",
          "title": "Type",
          "type": "string"
        },
        "artifactId": {
          "minLength": 1,
          "title": "Artifactid",
          "type": "string",
          "pattern": "\\S"
        },
        "version": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Version",
          "type": "integer"
        },
        "title": {
          "title": "Title",
          "type": "string"
        },
        "mediaType": {
          "minLength": 1,
          "title": "Mediatype",
          "type": "string",
          "pattern": "\\S"
        },
        "status": {
          "enum": [
            "pending",
            "ready",
            "failed"
          ],
          "title": "Status",
          "type": "string"
        }
      },
      "required": [
        "type",
        "artifactId",
        "version",
        "title",
        "mediaType",
        "status"
      ],
      "title": "ArtifactRef",
      "type": "object"
    },
    "AuthRequestPart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "auth_request",
          "title": "Type",
          "type": "string"
        },
        "intent": {
          "enum": [
            "lead",
            "register",
            "login"
          ],
          "title": "Intent",
          "type": "string"
        }
      },
      "required": [
        "type",
        "intent"
      ],
      "title": "AuthRequestPart",
      "type": "object"
    },
    "ChatMessage": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "minLength": 1,
          "title": "Id",
          "type": "string",
          "pattern": "\\S"
        },
        "role": {
          "enum": [
            "user",
            "assistant"
          ],
          "title": "Role",
          "type": "string"
        },
        "createdAt": {
          "format": "date-time",
          "title": "Createdat",
          "type": "string"
        },
        "parts": {
          "items": {
            "discriminator": {
              "mapping": {
                "artifact_ref": "#/$defs/ArtifactRef",
                "auth_request": "#/$defs/AuthRequestPart",
                "choice": "#/$defs/ChoicePart",
                "input_request": "#/$defs/InputRequestPart",
                "suggestions": "#/$defs/SuggestionsPart",
                "system_notice": "#/$defs/SystemNoticePart",
                "text": "#/$defs/TextPart"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/TextPart"
              },
              {
                "$ref": "#/$defs/ChoicePart"
              },
              {
                "$ref": "#/$defs/SuggestionsPart"
              },
              {
                "$ref": "#/$defs/InputRequestPart"
              },
              {
                "$ref": "#/$defs/AuthRequestPart"
              },
              {
                "$ref": "#/$defs/ArtifactRef"
              },
              {
                "$ref": "#/$defs/SystemNoticePart"
              }
            ]
          },
          "title": "Parts",
          "type": "array"
        }
      },
      "required": [
        "id",
        "role",
        "createdAt",
        "parts"
      ],
      "title": "ChatMessage",
      "type": "object"
    },
    "ChoicePart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "choice",
          "title": "Type",
          "type": "string"
        },
        "requestId": {
          "maxLength": 128,
          "minLength": 1,
          "title": "Requestid",
          "type": "string",
          "pattern": "\\S"
        },
        "prompt": {
          "title": "Prompt",
          "type": "string"
        },
        "options": {
          "items": {
            "$ref": "#/$defs/Option"
          },
          "title": "Options",
          "type": "array"
        }
      },
      "required": [
        "type",
        "requestId",
        "prompt",
        "options"
      ],
      "title": "ChoicePart",
      "type": "object"
    },
    "GuestViewer": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "guest",
          "title": "Kind",
          "type": "string"
        },
        "guestId": {
          "minLength": 1,
          "title": "Guestid",
          "type": "string",
          "pattern": "\\S"
        },
        "systemId": {
          "minLength": 1,
          "title": "Systemid",
          "type": "string",
          "pattern": "\\S"
        },
        "tenantId": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Tenantid",
          "type": "integer"
        },
        "messageCount": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Messagecount",
          "type": "integer"
        },
        "messageLimit": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Messagelimit",
          "type": "integer"
        },
        "retentionSeconds": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Retentionseconds",
          "type": "integer"
        },
        "expiresAt": {
          "format": "date-time",
          "title": "Expiresat",
          "type": "string"
        }
      },
      "required": [
        "kind",
        "guestId",
        "systemId",
        "tenantId",
        "messageCount",
        "messageLimit",
        "retentionSeconds",
        "expiresAt"
      ],
      "title": "GuestViewer",
      "type": "object"
    },
    "InputRequestPart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "input_request",
          "title": "Type",
          "type": "string"
        },
        "requestId": {
          "maxLength": 128,
          "minLength": 1,
          "title": "Requestid",
          "type": "string",
          "pattern": "\\S"
        },
        "prompt": {
          "title": "Prompt",
          "type": "string"
        }
      },
      "required": [
        "type",
        "requestId",
        "prompt"
      ],
      "title": "InputRequestPart",
      "type": "object"
    },
    "Option": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "maxLength": 128,
          "minLength": 1,
          "title": "Id",
          "type": "string",
          "pattern": "\\S"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "id",
        "label"
      ],
      "title": "Option",
      "type": "object"
    },
    "RunRef": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "minLength": 1,
          "title": "Id",
          "type": "string",
          "pattern": "\\S"
        },
        "status": {
          "enum": [
            "queued",
            "running",
            "waiting_input",
            "completed",
            "failed",
            "cancelled",
            "expired"
          ],
          "title": "Status",
          "type": "string"
        }
      },
      "required": [
        "id",
        "status"
      ],
      "title": "RunRef",
      "type": "object"
    },
    "Suggestion": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "minLength": 1,
          "title": "Id",
          "type": "string",
          "pattern": "\\S"
        },
        "text": {
          "title": "Text",
          "type": "string"
        }
      },
      "required": [
        "id",
        "text"
      ],
      "title": "Suggestion",
      "type": "object"
    },
    "SuggestionsPart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "suggestions",
          "title": "Type",
          "type": "string"
        },
        "items": {
          "items": {
            "$ref": "#/$defs/Suggestion"
          },
          "title": "Items",
          "type": "array"
        }
      },
      "required": [
        "type",
        "items"
      ],
      "title": "SuggestionsPart",
      "type": "object"
    },
    "TextPart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "text",
          "title": "Type",
          "type": "string"
        },
        "text": {
          "title": "Text",
          "type": "string"
        }
      },
      "required": [
        "type",
        "text"
      ],
      "title": "TextPart",
      "type": "object"
    },
    "UserViewer": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "user",
          "title": "Kind",
          "type": "string"
        },
        "userId": {
          "minLength": 1,
          "title": "Userid",
          "type": "string",
          "pattern": "\\S"
        },
        "systemId": {
          "minLength": 1,
          "title": "Systemid",
          "type": "string",
          "pattern": "\\S"
        },
        "tenantId": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Tenantid",
          "type": "integer"
        },
        "displayName": {
          "title": "Displayname",
          "type": "string"
        }
      },
      "required": [
        "kind",
        "userId",
        "systemId",
        "tenantId",
        "displayName"
      ],
      "title": "UserViewer",
      "type": "object"
    },
    "ApiError": {
      "additionalProperties": false,
      "properties": {
        "error": {
          "$ref": "#/$defs/ErrorDetail"
        },
        "requestId": {
          "minLength": 1,
          "title": "Requestid",
          "type": "string",
          "pattern": "\\S"
        }
      },
      "required": [
        "error",
        "requestId"
      ],
      "title": "ApiError",
      "type": "object"
    },
    "ArtifactUpdated": {
      "additionalProperties": false,
      "properties": {
        "artifactId": {
          "minLength": 1,
          "title": "Artifactid",
          "type": "string",
          "pattern": "\\S"
        },
        "version": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Version",
          "type": "integer"
        },
        "status": {
          "enum": [
            "pending",
            "ready",
            "failed"
          ],
          "title": "Status",
          "type": "string"
        }
      },
      "required": [
        "artifactId",
        "version",
        "status"
      ],
      "title": "ArtifactUpdated",
      "type": "object"
    },
    "ErrorDetail": {
      "additionalProperties": false,
      "properties": {
        "code": {
          "minLength": 1,
          "title": "Code",
          "type": "string",
          "pattern": "\\S"
        },
        "message": {
          "title": "Message",
          "type": "string"
        },
        "retryable": {
          "title": "Retryable",
          "type": "boolean"
        }
      },
      "required": [
        "code",
        "message",
        "retryable"
      ],
      "title": "ErrorDetail",
      "type": "object"
    },
    "MessageDelta": {
      "additionalProperties": false,
      "properties": {
        "messageId": {
          "minLength": 1,
          "title": "Messageid",
          "type": "string",
          "pattern": "\\S"
        },
        "delta": {
          "title": "Delta",
          "type": "string"
        }
      },
      "required": [
        "messageId",
        "delta"
      ],
      "title": "MessageDelta",
      "type": "object"
    },
    "MessageUpsert": {
      "additionalProperties": false,
      "properties": {
        "message": {
          "$ref": "#/$defs/ChatMessage"
        }
      },
      "required": [
        "message"
      ],
      "title": "MessageUpsert",
      "type": "object"
    },
    "RunUpdated": {
      "additionalProperties": false,
      "properties": {
        "run": {
          "$ref": "#/$defs/RunRef"
        }
      },
      "required": [
        "run"
      ],
      "title": "RunUpdated",
      "type": "object"
    },
    "ViewerExpired": {
      "additionalProperties": false,
      "properties": {
        "code": {
          "const": "VISITOR_EXPIRED",
          "title": "Code",
          "type": "string"
        }
      },
      "required": [
        "code"
      ],
      "title": "ViewerExpired",
      "type": "object"
    },
    "ViewerUpdated": {
      "additionalProperties": false,
      "properties": {
        "viewer": {
          "discriminator": {
            "mapping": {
              "guest": "#/$defs/GuestViewer",
              "user": "#/$defs/UserViewer"
            },
            "propertyName": "kind"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/GuestViewer"
            },
            {
              "$ref": "#/$defs/UserViewer"
            }
          ],
          "title": "Viewer"
        }
      },
      "required": [
        "viewer"
      ],
      "title": "ViewerUpdated",
      "type": "object"
    },
    "Csrf": {
      "additionalProperties": false,
      "properties": {
        "headerName": {
          "minLength": 1,
          "title": "Headername",
          "type": "string",
          "pattern": "\\S"
        },
        "token": {
          "minLength": 1,
          "title": "Token",
          "type": "string",
          "pattern": "\\S"
        }
      },
      "required": [
        "headerName",
        "token"
      ],
      "title": "Csrf",
      "type": "object"
    },
    "MergeState": {
      "additionalProperties": false,
      "properties": {
        "operationId": {
          "minLength": 1,
          "title": "Operationid",
          "type": "string",
          "pattern": "\\S"
        },
        "status": {
          "enum": [
            "MERGE_PENDING",
            "MERGED",
            "SKIPPED_EXPIRED"
          ],
          "title": "Status",
          "type": "string"
        }
      },
      "required": [
        "operationId",
        "status"
      ],
      "title": "MergeState",
      "type": "object"
    },
    "ConversationInput": {
      "type": "object",
      "properties": {
        "threadId": {
          "type": "string",
          "format": "uuid"
        },
        "attemptId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        }
      },
      "required": [
        "threadId"
      ],
      "additionalProperties": false
    },
    "Accepted": {
      "additionalProperties": false,
      "properties": {
        "message": {
          "$ref": "#/$defs/ChatMessage"
        },
        "run": {
          "$ref": "#/$defs/RunRef"
        },
        "viewer": {
          "discriminator": {
            "mapping": {
              "guest": "#/$defs/GuestViewer",
              "user": "#/$defs/UserViewer"
            },
            "propertyName": "kind"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/GuestViewer"
            },
            {
              "$ref": "#/$defs/UserViewer"
            }
          ],
          "title": "Viewer"
        },
        "attemptId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        },
        "businessRunId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        }
      },
      "required": [
        "message",
        "run",
        "viewer",
        "attemptId",
        "businessRunId"
      ],
      "title": "MessageAccepted",
      "type": "object"
    },
    "Status": {
      "type": "object",
      "properties": {
        "conversationId": {
          "type": "string",
          "format": "uuid"
        },
        "activeRun": {
          "anyOf": [
            {
              "$ref": "#/$defs/RunRef"
            },
            {
              "type": "null"
            }
          ]
        },
        "run": {
          "anyOf": [
            {
              "$ref": "#/$defs/RunRef"
            },
            {
              "type": "null"
            }
          ]
        },
        "attemptId": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1,
              "maxLength": 128,
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ]
        },
        "businessRunId": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1,
              "maxLength": 128,
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ]
        },
        "isRunning": {
          "type": "boolean"
        },
        "cursor": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1,
              "maxLength": 128,
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "conversationId",
        "activeRun",
        "run",
        "attemptId",
        "businessRunId",
        "isRunning",
        "cursor"
      ],
      "additionalProperties": false
    },
    "Stopped": {
      "type": "object",
      "properties": {
        "stopped": {
          "type": "boolean"
        },
        "run": {
          "anyOf": [
            {
              "$ref": "#/$defs/RunRef"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "stopped",
        "run"
      ],
      "additionalProperties": false
    },
    "RUN_STARTED": {
      "type": "object",
      "properties": {
        "type": {
          "const": "RUN_STARTED"
        },
        "threadId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        },
        "runId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        }
      },
      "required": [
        "type",
        "threadId",
        "runId"
      ],
      "additionalProperties": false
    },
    "RUN_FINISHED": {
      "type": "object",
      "properties": {
        "type": {
          "const": "RUN_FINISHED"
        },
        "threadId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        },
        "runId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        }
      },
      "required": [
        "type",
        "threadId",
        "runId"
      ],
      "additionalProperties": false
    },
    "RUN_ERROR": {
      "type": "object",
      "properties": {
        "type": {
          "const": "RUN_ERROR"
        },
        "code": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        },
        "message": {
          "type": "string"
        }
      },
      "required": [
        "type",
        "code",
        "message"
      ],
      "additionalProperties": false
    },
    "MESSAGES_SNAPSHOT": {
      "type": "object",
      "properties": {
        "type": {
          "const": "MESSAGES_SNAPSHOT"
        },
        "messages": {
          "items": {
            "discriminator": {
              "mapping": {
                "activity": "#/$defs/AguiActivityMessage",
                "assistant": "#/$defs/AguiAssistantMessage",
                "developer": "#/$defs/AguiDeveloperMessage",
                "reasoning": "#/$defs/AguiReasoningMessage",
                "system": "#/$defs/AguiSystemMessage",
                "tool": "#/$defs/AguiToolMessage",
                "user": "#/$defs/AguiUserMessage"
              },
              "propertyName": "role"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/AguiDeveloperMessage"
              },
              {
                "$ref": "#/$defs/AguiSystemMessage"
              },
              {
                "$ref": "#/$defs/AguiAssistantMessage"
              },
              {
                "$ref": "#/$defs/AguiUserMessage"
              },
              {
                "$ref": "#/$defs/AguiToolMessage"
              },
              {
                "$ref": "#/$defs/AguiActivityMessage"
              },
              {
                "$ref": "#/$defs/AguiReasoningMessage"
              }
            ]
          },
          "title": "Messages",
          "type": "array",
          "maxItems": 10
        }
      },
      "required": [
        "type",
        "messages"
      ],
      "additionalProperties": false
    },
    "TEXT_MESSAGE_START": {
      "type": "object",
      "properties": {
        "type": {
          "const": "TEXT_MESSAGE_START"
        },
        "messageId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        },
        "role": {
          "const": "assistant"
        }
      },
      "required": [
        "type",
        "messageId",
        "role"
      ],
      "additionalProperties": false
    },
    "TEXT_MESSAGE_CONTENT": {
      "type": "object",
      "properties": {
        "type": {
          "const": "TEXT_MESSAGE_CONTENT"
        },
        "messageId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        },
        "delta": {
          "type": "string"
        }
      },
      "required": [
        "type",
        "messageId",
        "delta"
      ],
      "additionalProperties": false
    },
    "TEXT_MESSAGE_END": {
      "type": "object",
      "properties": {
        "type": {
          "const": "TEXT_MESSAGE_END"
        },
        "messageId": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128,
          "pattern": "\\S"
        }
      },
      "required": [
        "type",
        "messageId"
      ],
      "additionalProperties": false
    },
    "SystemNoticePart": {
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "system_notice",
          "title": "Type",
          "type": "string"
        },
        "text": {
          "title": "Text",
          "type": "string"
        }
      },
      "required": [
        "type",
        "text"
      ],
      "title": "SystemNoticePart",
      "type": "object"
    },
    "ActiveInputV2": {
      "additionalProperties": false,
      "properties": {
        "runId": {
          "minLength": 1,
          "title": "Runid",
          "type": "string",
          "pattern": "\\S"
        },
        "messageId": {
          "minLength": 1,
          "title": "Messageid",
          "type": "string",
          "pattern": "\\S"
        },
        "requestId": {
          "minLength": 1,
          "title": "Requestid",
          "type": "string",
          "pattern": "\\S"
        },
        "kind": {
          "enum": [
            "choice",
            "input_request"
          ],
          "title": "Kind",
          "type": "string"
        }
      },
      "required": [
        "runId",
        "messageId",
        "requestId",
        "kind"
      ],
      "title": "ActiveInputV2",
      "type": "object"
    },
    "HistoryV2": {
      "additionalProperties": false,
      "properties": {
        "pageSize": {
          "const": 10,
          "title": "Pagesize",
          "type": "integer"
        },
        "hasMore": {
          "title": "Hasmore",
          "type": "boolean"
        },
        "olderCursor": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string",
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ],
          "title": "Oldercursor"
        },
        "historyEpoch": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Historyepoch",
          "type": "integer"
        }
      },
      "required": [
        "pageSize",
        "hasMore",
        "olderCursor",
        "historyEpoch"
      ],
      "title": "HistoryV2",
      "type": "object",
      "allOf": [
        {
          "if": {
            "properties": {
              "hasMore": {
                "const": false
              }
            }
          },
          "then": {
            "properties": {
              "olderCursor": {
                "type": "null"
              }
            }
          },
          "else": {
            "properties": {
              "olderCursor": {
                "type": "string",
                "minLength": 1,
                "pattern": "\\S"
              }
            }
          }
        }
      ]
    },
    "SyncV2": {
      "additionalProperties": false,
      "properties": {
        "eventCursor": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string",
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ],
          "title": "Eventcursor"
        },
        "watermarkSequence": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Watermarksequence",
          "type": "integer"
        }
      },
      "required": [
        "eventCursor",
        "watermarkSequence"
      ],
      "title": "SyncV2",
      "type": "object",
      "allOf": [
        {
          "if": {
            "properties": {
              "watermarkSequence": {
                "const": 0
              }
            }
          },
          "then": {
            "properties": {
              "eventCursor": {
                "type": "null"
              }
            }
          },
          "else": {
            "properties": {
              "eventCursor": {
                "type": "string",
                "minLength": 1,
                "pattern": "\\S"
              }
            }
          }
        }
      ]
    },
    "ChatSnapshotV2": {
      "additionalProperties": false,
      "properties": {
        "version": {
          "const": 2,
          "title": "Version",
          "type": "integer"
        },
        "viewer": {
          "discriminator": {
            "mapping": {
              "guest": "#/$defs/GuestViewer",
              "user": "#/$defs/UserViewer"
            },
            "propertyName": "kind"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/GuestViewer"
            },
            {
              "$ref": "#/$defs/UserViewer"
            }
          ],
          "title": "Viewer"
        },
        "sessionExpiresAt": {
          "format": "date-time",
          "title": "Sessionexpiresat",
          "type": "string"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "messages": {
          "items": {
            "$ref": "#/$defs/ChatMessage"
          },
          "maxItems": 10,
          "title": "Messages",
          "type": "array"
        },
        "activeRun": {
          "anyOf": [
            {
              "$ref": "#/$defs/RunRef"
            },
            {
              "type": "null"
            }
          ]
        },
        "activeInput": {
          "anyOf": [
            {
              "$ref": "#/$defs/ActiveInputV2"
            },
            {
              "type": "null"
            }
          ]
        },
        "merge": {
          "anyOf": [
            {
              "$ref": "#/$defs/MergeState"
            },
            {
              "type": "null"
            }
          ]
        },
        "history": {
          "$ref": "#/$defs/HistoryV2"
        },
        "sync": {
          "$ref": "#/$defs/SyncV2"
        },
        "csrf": {
          "$ref": "#/$defs/Csrf"
        }
      },
      "required": [
        "version",
        "viewer",
        "sessionExpiresAt",
        "conversationId",
        "messages",
        "activeRun",
        "activeInput",
        "merge",
        "history",
        "sync",
        "csrf"
      ],
      "title": "ChatSnapshotV2",
      "type": "object"
    },
    "HistoryPageV2": {
      "additionalProperties": false,
      "properties": {
        "version": {
          "const": 2,
          "title": "Version",
          "type": "integer"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "messages": {
          "items": {
            "$ref": "#/$defs/ChatMessage"
          },
          "maxItems": 10,
          "title": "Messages",
          "type": "array"
        },
        "history": {
          "$ref": "#/$defs/HistoryV2"
        },
        "watermarkSequence": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Watermarksequence",
          "type": "integer"
        }
      },
      "required": [
        "version",
        "conversationId",
        "messages",
        "history",
        "watermarkSequence"
      ],
      "title": "HistoryPageV2",
      "type": "object"
    },
    "MessageBaselineV2": {
      "additionalProperties": false,
      "properties": {
        "version": {
          "const": 2,
          "title": "Version",
          "type": "integer"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "message": {
          "$ref": "#/$defs/ChatMessage"
        },
        "historyEpoch": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Historyepoch",
          "type": "integer"
        },
        "watermarkSequence": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Watermarksequence",
          "type": "integer"
        }
      },
      "required": [
        "version",
        "conversationId",
        "message",
        "historyEpoch",
        "watermarkSequence"
      ],
      "title": "MessageBaselineV2",
      "type": "object"
    },
    "PublicSnapshotV2": {
      "additionalProperties": false,
      "properties": {
        "version": {
          "const": 2,
          "title": "Version",
          "type": "integer"
        },
        "viewer": {
          "discriminator": {
            "mapping": {
              "guest": "#/$defs/GuestViewer",
              "user": "#/$defs/UserViewer"
            },
            "propertyName": "kind"
          },
          "oneOf": [
            {
              "$ref": "#/$defs/GuestViewer"
            },
            {
              "$ref": "#/$defs/UserViewer"
            }
          ],
          "title": "Viewer"
        },
        "sessionExpiresAt": {
          "format": "date-time",
          "title": "Sessionexpiresat",
          "type": "string"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "messages": {
          "items": {
            "$ref": "#/$defs/ChatMessage"
          },
          "maxItems": 10,
          "title": "Messages",
          "type": "array"
        },
        "activeRun": {
          "anyOf": [
            {
              "$ref": "#/$defs/RunRef"
            },
            {
              "type": "null"
            }
          ]
        },
        "activeInput": {
          "anyOf": [
            {
              "$ref": "#/$defs/ActiveInputV2"
            },
            {
              "type": "null"
            }
          ]
        },
        "merge": {
          "anyOf": [
            {
              "$ref": "#/$defs/MergeState"
            },
            {
              "type": "null"
            }
          ]
        },
        "history": {
          "$ref": "#/$defs/HistoryV2"
        },
        "sync": {
          "$ref": "#/$defs/SyncV2"
        },
        "attemptId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string",
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ],
          "title": "Attemptid"
        },
        "businessRunId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string",
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ],
          "title": "Businessrunid"
        },
        "businessRunStatus": {
          "anyOf": [
            {
              "enum": [
                "queued",
                "running",
                "waiting_input",
                "completed",
                "failed",
                "cancelled",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Businessrunstatus"
        }
      },
      "required": [
        "version",
        "viewer",
        "sessionExpiresAt",
        "conversationId",
        "messages",
        "activeRun",
        "activeInput",
        "merge",
        "history",
        "sync",
        "attemptId",
        "businessRunId",
        "businessRunStatus"
      ],
      "title": "PublicSnapshotV2",
      "type": "object"
    },
    "ExpectedIdentity": {
      "additionalProperties": false,
      "properties": {
        "issuer": {
          "minLength": 1,
          "title": "Issuer",
          "type": "string",
          "pattern": "\\S"
        },
        "sub": {
          "minLength": 1,
          "title": "Sub",
          "type": "string",
          "pattern": "\\S"
        },
        "sourceTenantId": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Sourcetenantid",
          "type": "integer"
        }
      },
      "required": [
        "issuer",
        "sub",
        "sourceTenantId"
      ],
      "title": "ExpectedIdentity",
      "type": "object"
    },
    "BootstrapInputV2": {
      "additionalProperties": false,
      "properties": {
        "expectedIdentity": {
          "$ref": "#/$defs/ExpectedIdentity"
        }
      },
      "title": "BootstrapInputV2",
      "type": "object"
    },
    "OidcBinding": {
      "additionalProperties": false,
      "properties": {
        "clientId": {
          "minLength": 1,
          "title": "Clientid",
          "type": "string",
          "pattern": "\\S"
        },
        "redirectUri": {
          "minLength": 1,
          "title": "Redirecturi",
          "type": "string",
          "pattern": "\\S"
        },
        "state": {
          "minLength": 1,
          "title": "State",
          "type": "string",
          "pattern": "\\S"
        },
        "nonce": {
          "minLength": 1,
          "title": "Nonce",
          "type": "string",
          "pattern": "\\S"
        },
        "codeChallenge": {
          "minLength": 1,
          "title": "Codechallenge",
          "type": "string",
          "pattern": "\\S"
        },
        "codeChallengeMethod": {
          "const": "S256",
          "title": "Codechallengemethod",
          "type": "string"
        }
      },
      "required": [
        "clientId",
        "redirectUri",
        "state",
        "nonce",
        "codeChallenge",
        "codeChallengeMethod"
      ],
      "title": "OidcBinding",
      "type": "object"
    },
    "CreateAuthOperationV2": {
      "additionalProperties": false,
      "properties": {
        "intent": {
          "enum": [
            "lead",
            "register",
            "login",
            "restore"
          ],
          "title": "Intent",
          "type": "string"
        },
        "oidc": {
          "$ref": "#/$defs/OidcBinding"
        },
        "browserCompletionChallenge": {
          "minLength": 1,
          "title": "Browsercompletionchallenge",
          "type": "string",
          "pattern": "\\S"
        },
        "browserCompletionChallengeMethod": {
          "const": "S256",
          "title": "Browsercompletionchallengemethod",
          "type": "string"
        },
        "expectedIdentity": {
          "$ref": "#/$defs/ExpectedIdentity"
        }
      },
      "required": [
        "intent",
        "oidc",
        "browserCompletionChallenge",
        "browserCompletionChallengeMethod"
      ],
      "title": "CreateAuthOperationV2",
      "type": "object",
      "allOf": [
        {
          "if": {
            "properties": {
              "intent": {
                "const": "restore"
              }
            }
          },
          "then": {
            "required": [
              "expectedIdentity"
            ]
          },
          "else": {
            "not": {
              "required": [
                "expectedIdentity"
              ]
            }
          }
        }
      ]
    },
    "ArtifactEventV2": {
      "additionalProperties": false,
      "properties": {
        "sequence": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Sequence",
          "type": "integer"
        },
        "historyEpoch": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Historyepoch",
          "type": "integer"
        },
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string",
          "pattern": "\\S"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string",
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "artifact.updated",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ArtifactUpdated"
        }
      },
      "required": [
        "sequence",
        "historyEpoch",
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "ArtifactEventV2",
      "type": "object"
    },
    "DeltaEventV2": {
      "additionalProperties": false,
      "properties": {
        "sequence": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Sequence",
          "type": "integer"
        },
        "historyEpoch": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Historyepoch",
          "type": "integer"
        },
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string",
          "pattern": "\\S"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "runId": {
          "minLength": 1,
          "title": "Runid",
          "type": "string",
          "pattern": "\\S"
        },
        "type": {
          "const": "message.delta",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/MessageDelta"
        }
      },
      "required": [
        "sequence",
        "historyEpoch",
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "DeltaEventV2",
      "type": "object"
    },
    "ErrorEventV2": {
      "additionalProperties": false,
      "properties": {
        "sequence": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Sequence",
          "type": "integer"
        },
        "historyEpoch": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Historyepoch",
          "type": "integer"
        },
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string",
          "pattern": "\\S"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string",
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "error",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ApiError"
        }
      },
      "required": [
        "sequence",
        "historyEpoch",
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "ErrorEventV2",
      "type": "object"
    },
    "ExpiredEventV2": {
      "additionalProperties": false,
      "properties": {
        "sequence": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Sequence",
          "type": "integer"
        },
        "historyEpoch": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Historyepoch",
          "type": "integer"
        },
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string",
          "pattern": "\\S"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string",
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "viewer.expired",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ViewerExpired"
        }
      },
      "required": [
        "sequence",
        "historyEpoch",
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "ExpiredEventV2",
      "type": "object"
    },
    "MessageEventV2": {
      "additionalProperties": false,
      "properties": {
        "sequence": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Sequence",
          "type": "integer"
        },
        "historyEpoch": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Historyepoch",
          "type": "integer"
        },
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string",
          "pattern": "\\S"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string",
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "message.upsert",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/MessageUpsert"
        }
      },
      "required": [
        "sequence",
        "historyEpoch",
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "MessageEventV2",
      "type": "object"
    },
    "RunEventV2": {
      "additionalProperties": false,
      "properties": {
        "sequence": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Sequence",
          "type": "integer"
        },
        "historyEpoch": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Historyepoch",
          "type": "integer"
        },
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string",
          "pattern": "\\S"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "runId": {
          "minLength": 1,
          "title": "Runid",
          "type": "string",
          "pattern": "\\S"
        },
        "type": {
          "const": "run.updated",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/RunUpdated"
        }
      },
      "required": [
        "sequence",
        "historyEpoch",
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "RunEventV2",
      "type": "object"
    },
    "ViewerEventV2": {
      "additionalProperties": false,
      "properties": {
        "sequence": {
          "maximum": 9007199254740991,
          "minimum": 1,
          "title": "Sequence",
          "type": "integer"
        },
        "historyEpoch": {
          "maximum": 9007199254740991,
          "minimum": 0,
          "title": "Historyepoch",
          "type": "integer"
        },
        "eventId": {
          "minLength": 1,
          "title": "Eventid",
          "type": "string",
          "pattern": "\\S"
        },
        "conversationId": {
          "minLength": 1,
          "title": "Conversationid",
          "type": "string",
          "pattern": "\\S"
        },
        "runId": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string",
              "pattern": "\\S"
            },
            {
              "type": "null"
            }
          ],
          "title": "Runid"
        },
        "type": {
          "const": "viewer.updated",
          "title": "Type",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ViewerUpdated"
        }
      },
      "required": [
        "sequence",
        "historyEpoch",
        "eventId",
        "conversationId",
        "runId",
        "type",
        "payload"
      ],
      "title": "ViewerEventV2",
      "type": "object"
    },
    "WireEventV2": {
      "discriminator": {
        "mapping": {
          "artifact.updated": "#/$defs/ArtifactEventV2",
          "error": "#/$defs/ErrorEventV2",
          "message.delta": "#/$defs/DeltaEventV2",
          "message.upsert": "#/$defs/MessageEventV2",
          "run.updated": "#/$defs/RunEventV2",
          "viewer.expired": "#/$defs/ExpiredEventV2",
          "viewer.updated": "#/$defs/ViewerEventV2"
        },
        "propertyName": "type"
      },
      "oneOf": [
        {
          "$ref": "#/$defs/DeltaEventV2"
        },
        {
          "$ref": "#/$defs/MessageEventV2"
        },
        {
          "$ref": "#/$defs/RunEventV2"
        },
        {
          "$ref": "#/$defs/ArtifactEventV2"
        },
        {
          "$ref": "#/$defs/ViewerEventV2"
        },
        {
          "$ref": "#/$defs/ExpiredEventV2"
        },
        {
          "$ref": "#/$defs/ErrorEventV2"
        }
      ]
    },
    "ReadyV2": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "conversationId": {
          "type": "string",
          "minLength": 1,
          "pattern": "\\S"
        },
        "historyEpoch": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "watermarkSequence": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        }
      },
      "required": [
        "conversationId",
        "historyEpoch",
        "watermarkSequence"
      ]
    },
    "CUSTOMV2": {
      "oneOf": [
        {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "type": {
              "const": "CUSTOM"
            },
            "name": {
              "const": "xiaohe.v2.event"
            },
            "value": {
              "$ref": "#/$defs/WireEventV2"
            }
          },
          "required": [
            "type",
            "name",
            "value"
          ]
        },
        {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "type": {
              "const": "CUSTOM"
            },
            "name": {
              "const": "xiaohe.v2.message-baseline"
            },
            "value": {
              "$ref": "#/$defs/MessageBaselineV2"
            }
          },
          "required": [
            "type",
            "name",
            "value"
          ]
        },
        {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "type": {
              "const": "CUSTOM"
            },
            "name": {
              "const": "xiaohe.v2.ready"
            },
            "value": {
              "$ref": "#/$defs/ReadyV2"
            }
          },
          "required": [
            "type",
            "name",
            "value"
          ]
        }
      ]
    },
    "STATE_SNAPSHOTV2": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "type": {
          "const": "STATE_SNAPSHOT"
        },
        "snapshot": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "xiaohe": {
              "$ref": "#/$defs/PublicSnapshotV2"
            }
          },
          "required": [
            "xiaohe"
          ]
        }
      },
      "required": [
        "type",
        "snapshot"
      ]
    },
    "StreamEventV2": {
      "oneOf": [
        {
          "$ref": "#/$defs/RUN_STARTED"
        },
        {
          "$ref": "#/$defs/RUN_FINISHED"
        },
        {
          "$ref": "#/$defs/RUN_ERROR"
        },
        {
          "$ref": "#/$defs/MESSAGES_SNAPSHOT"
        },
        {
          "$ref": "#/$defs/STATE_SNAPSHOTV2"
        },
        {
          "$ref": "#/$defs/TEXT_MESSAGE_START"
        },
        {
          "$ref": "#/$defs/TEXT_MESSAGE_CONTENT"
        },
        {
          "$ref": "#/$defs/TEXT_MESSAGE_END"
        },
        {
          "$ref": "#/$defs/CUSTOMV2"
        }
      ]
    }
  },
  "anyOf": [
    {
      "$ref": "#/$defs/ChatSnapshotV2"
    },
    {
      "$ref": "#/$defs/HistoryPageV2"
    },
    {
      "$ref": "#/$defs/MessageBaselineV2"
    },
    {
      "$ref": "#/$defs/PublicSnapshotV2"
    },
    {
      "$ref": "#/$defs/BootstrapInputV2"
    },
    {
      "$ref": "#/$defs/CreateAuthOperationV2"
    },
    {
      "$ref": "#/$defs/WireEventV2"
    },
    {
      "$ref": "#/$defs/RunInput"
    },
    {
      "$ref": "#/$defs/ConversationInput"
    },
    {
      "$ref": "#/$defs/Accepted"
    },
    {
      "$ref": "#/$defs/Status"
    },
    {
      "$ref": "#/$defs/Stopped"
    },
    {
      "$ref": "#/$defs/StreamEventV2"
    }
  ]
}
```
<!-- xiaohe-agui-v2-schema:end -->

## 13. 失效会员退出清理补充（2026-11-07，修复实施中，未部署）

修复用户报告的“门户退出后，小鹤被旧会员Cookie锁在AUTH_REQUIRED而不能匿名打开”。本节是原退出接口的受限清理例外，不是V2上线，也不改变§12的分页/预算机器协议。源码/fixture通过不等于用户隔离窗口已验收。

- `POST /ai-api/v1/auth/logout` 仍使用空JSON `{}`，成功204。有效会员/访客仍执行原聊天Cookie和聊天CSRF校验；独立认证CSRF不能替代有效会话的聊天CSRF。
- 仅当SESSION Cookie可严格识别为当前system/tenant内的**会员**Session，且数据库时间已过其绝对expires_at或该Session已经revoked时，允许以有效的独立认证Cookie、`X-Xiaohe-Auth-CSRF` 和精确Origin执行退出清理；不要求无法重新取得的旧聊天CSRF。认证上下文仍受原有效期/作用域和桥接开关约束。
- 清理只撤销该已失效会员授权及对应完成操作、使本次认证上下文失效，并删除SESSION/AUTH Cookie；保留原revoked_at和原会话截止，不删用户/会员消息/文件，不动预算Cookie或访客计数，不新建guest，也不恢复旧任务。
- 未知或格式非法token、跨system/tenant、过期guest、有效member缺聊天CSRF、错误/重复/失效认证证明仍拒绝，不在错误响应中清Cookie或发送新额度。不得将任何401统一当guest。
- 门户明确退出时：有聊天CSRF先走原注销；若原快照是member且原注销精确返回401/AUTH_REQUIRED，可用新认证上下文重试清理一次。没有快照且bootstrap精确返回401/AUTH_REQUIRED时，也可尝试此受限清理，由后端最终判定。清理失败必须保留退出未完成状态，不继续中心退出或假装匿名成功；403、410、网络故障不使用该回退。
- 退出成功后再打开走正常匿名bootstrap；登录为可选入口。认证错误与网络断线在前端分开表示，不把AUTH_REQUIRED直接标成“连接已中断”。真实隔离窗口需验证退出后匿名欢迎和状态，不能用新无Cookie请求200代替该链路验收。


### 2026-11-09 联系资料与全量表单增量

三类 Agent 分别展示各自全量字段，不合并方向。新增本次申请字段 `contactPhone`，字符串最多 4000 个 Unicode 码点，不验证号码格式。正式提交时采购商与供应商必填 `companyName,contactName,contactPhone`；组织保留既有五项必填并增加 `contactPhone`。草稿允许缺失与显式清空；历史读取不新增必填限制。`contactName` 统一显示“联系人”，保留组织 100 字符、采购商与供应商普通文本 4000 字符的既有上限。保存资料只保存草稿，不推进轮次、不正式提交。`DraftView.acceptedApplicationId` 仅由已核验的持久化受理回执产生，供用户编辑原申请；不接受模型提供关联 ID。新增字段须由三端同步发布，旧严格客户端不可混用。
