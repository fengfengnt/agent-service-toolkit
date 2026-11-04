# 本地三方隔离联调运行记录

用户已在门户聊天明确批准按隔离方案启动API、worker、专用数据库和bucket，继续三方协调；正式开关关闭、不替换现有部署、不发短信或调用付费模型。AI在执行前通过read_thread核验真实用户原话，未再次索取同范围确认。基线dev-fengfeng/9b79b51，本轮新增测试专用启动器，并补齐原总设计已有的首屏欢迎功能；不改上游产品源码、Windows .venv、依赖锁、共享合同或其他工程。

## 专用资源

| 资源 | 当前边界 |
| --- | --- |
| Linux运行环境 | Python3.14.7，`/home/fengfeng/.local/share/xiaohe-triad/20261104/venv`，原uv.lock冻结安装 |
| API | WSL宿主`127.0.0.1:18090`，Uvicorn通用proxy headers关闭 |
| worker | 独立Linux进程，无监听端口，复用原Procrastinate/执行器/维护 |
| PG | 原自有xiaohe-agent-test-pg17/35417；新db `xiaohe_triad_local_20261104`，新owner role `xiaohe_triad_local`，只在新库初始化业务0007及任务/原saver/store |
| MinIO | 原自有xiaohe-agent-test-minio/35418；新私有bucket `xiaohe-triad-local-20261104`，专用应用key仅可Get/Put/Delete该桶；实际head目标200、原测试桶403 |
| 本地私有配置 | Linux专用目录0700，runtime.json为0600；包含新测试应用凭据，不复制到Git或聊天 |
| 中心应用配置 | SSO Owner将既有应用私有注入同目录sso-bridge.json，精确5键、0600；AI Agent仅检查路径/权限，专用进程正常加载，未打印值、未重新登记或轮换 |

本专用库名不符合自动测试test_xiaohe_前缀，不会被既有pg_factory接受并TRUNCATE。自动化测试仍在原测试库/bucket，浏览器会话使用本新库/bucket。现有测试PG为tmpfs，本次不承诺其重启后保留联调数据；MinIO原卷保留，未删除其他库、桶、容器或卷。

## 入口、peer与模型

继续沿用issuer `https://sso.lvh.me:8443`、public client `app_localportal`、Origin `https://portal.lvh.me:9444`和精确callback `/portal/auth/callback`。原Nginx已有/ai-api upstream指向18090，本轮未改入口配置；启动后该本地路径从502变为可达，属于已批准的隔离运行效果。GET /ai-api/v1/session返回405是方法不支持，初始化必须POST。

先保持trusted_proxy_cidrs为空，从Portal做无凭据GET，Uvicorn实际socket日志测得127.0.0.1，再仅配置127.0.0.1/32并重启自有API。没有按XFF推断peer或信任整个网段。实际HTTPS匿名初始化还带过伪造system/XFF/Proto，入口覆盖后仍正确建立portal访客。

受控DecisionModel和文件handler仅由`tests/xiaohe/integration/triad_runtime.py`装配；公开router、原lifespan、PostgreSQL saver/store、执行器与对象服务仍为真实实现。隔离进程禁用真实provider及追踪、不自动发现加载其他部署.env，不新增产品fake开关或认证成功后门。fixture发送短信路径提前返回不可重试503，即使加载真实SSO应用配置也不能经该路径外发短信。

## 已完成的真实匿名API证据

北京时间15:09起，使用严格公共CA，经Portal9444实际Nginx→API→独立worker→专用PG/MinIO：

- 依次输入“我代表供应商，想了解服务”“切换到协会组织方向”“切换到采购方方向”，每条202，最终各生成一个独立Artifact、version1、标题“隔离联调说明.md”、text/markdown。正文含对应supplier/organization/buyer及隔离说明。
- 每次精确版本下载200、private/no-store，摘要与方向正文一致；同clientMessageId重试复用run且只计一次；三条计数为1/2/3。
- 实际SSE接收到artifact.updated、message.upsert、run.updated、viewer.updated；本fixture不是付费模型逐token流。
- Cookie仅在验证进程内存中复用，重开客户端仍同conversation/原消息ID，文件可再下载；匿名下载401、另一guest403，公开internal404，隔离SMS503且未出站。

摘要：supplier `71b29beb7739dce78d3bf34dccd774ad93e0a73ac6d2178c53eca37dfa32af11`；organization `71f5130c1995566a100630f600a520369bbbcbb96a8f4a84d0c1d8bc233daa66`；buyer `d0e0043886f1755726c0236dc0de8faac1466d6ab5552d195a954cac79871ef2`。脱敏证据在本计划忽略目录`triad-anonymous-evidence.json`，不含Cookie/CSRF/secret。门户Owner收到实际输入与结果后并行做自己的隔离浏览器验证，本记录不替代其结果。

## 中心网络与后续认证

SSO Owner确认Discovery/JWKS/continuation已恢复严格TLS200；此前11:57的502是历史快照。实际WSL公开探针UTC07:10:34为127.0.0.1:46050→127.0.0.1:8443/HTTP200，后续UTC07:13:09为127.0.0.1:47046→同8443/HTTP200；使用HTTPX network_stream实际地址，连接保持供Owner只读对照。现Nginx access_log off，未为探针开启日志或重载；不能声称按query命中过日志。公开peer不等于私有bridge鉴权通过。

真实身份绑定仍要求中心受信claim；无既有中心会员会话时由用户正常登录，不能用fixture或前端成功标记冒充。正式Portal包保持false；只有门户Owner的独立浏览器context覆盖静态GET/原callback使用测试包，API/SSO走真实网络。

加载Owner私有配置后，AI实测auth/context 200、真实中心create经AI 201、未换码complete 409 AUTH_NOT_READY且无用户Cookie、浏览器顶层POST 303、中心续接页面/READY JSON 200、CSRF CONTINUE 200并限定同issuer、cancel 204。未登录会员、未换码或完成claim，未发送短信。最初探针误用browserCompletion字段及LOGIN_REQUIRED枚举，按原completion/READY修正，仅脚本变化。首个公开探针46050的四元组由Owner在edge实测匹配；第二47046未捕获，不能混用。backend peer172.24.0.1是独立诊断证据。

门户Owner独立真实浏览器已验证三方向消息/文件摘要、SSE、重开和整页刷新不重复计数；fresh context还验证顶层POST、中心续接、authorize至正常手机号登录表单，pageerror和SMS均为0。其结果是门户Owner提供的证据，AI不冒充自己执行。完整会员认证、换码、身份绑定及归并仍待合法会员会话。

## 首屏欢迎漏项与修复

真实浏览器首次初始化返回messages=[]，保留为失败证据。总设计§2规则5要求方向未知时展示欢迎及采购方、社群组织、供应商选择，故补齐既有需求，不增加专业业务流程。

服务端在bootstrap及认证完成后的空会话中，持久化一条assistant欢迎消息，复用suggestions和message.upsert；行锁与固定消息ID防止并发重复。无run、command或任务、额度消耗及会话续期；点击三个入口才走正常/messages。已有非空历史不补插欢迎。归并保留源欢迎消息的ID、内容和创建时间；来源过期时不迁移，目标空会话只生成自己的新欢迎。裸“社群组织”与既有alias一致，确定性识别为organization，无需模型猜测。

欢迎4项专项先RED（空首屏），再暴露裸社群组织路由错误，修复后GREEN。9个旧测试因欢迎改变空历史或数量预期而失败，按实际消息身份/内容调整，保留原隐私、过期、并发及8小时锚点检查；相关47项回归通过。完整业务回归、独立审查和切换后的浏览器复测结果后续追加，不以旧314项通过覆盖新修复。

提交173dd40后完整所选业务/原client及threads回归326 passed、无skip，19条既有依赖警告、216.92秒；Alembic无差异。一次独立只读审查9b79b51..173dd40：0 Critical、1 Important、0 Minor；确认欢迎、归并、过期及领域夹具改动未削弱原安全断言。Important是专用启动器仅检查URL主体，psycopg的query可覆盖实际host/dbname。两个覆盖用例先RED；最小修复拒绝全部非空url.query，无此参数的实际专用配置无需更改。修复后的最终结果后续追加。

审查范围裁定：正式发布/短信/付费模型继续保持未执行；完整真实会员仍须合法会话，不由夹具替代；新欢迎在线行为须切换后另取浏览器证据；历史批次/上游问题不重审，进程脚本只证明本次本地隔离，不提供生产运维保证。这些边界的代价分别是尚不可正式上线、会员闭环未验收、浏览器未复测前不能宣布首屏通过，以及原有问题仍保留。无延期Minor，不做第二轮审查。

运行源码使用Git tracked文件的私有固定快照，未复制.env、untracked或凭据，避免工作区开发期间Python延迟import混用版本。欢迎修复经验证后在门户已确认的无活动窗口，切换自有API及worker到同一新快照；不操作其他进程。

## 自有进程操作

从Windows运行以下命令，脚本校验PID、内核启动时刻和专用runner路径，只操作自有API/worker；不改Nginx/SSO/存储服务：

```powershell
wsl.exe -d Ubuntu -- /home/fengfeng/.local/share/xiaohe-triad/20261104/venv/bin/python /mnt/d/Code/yunhezhiyuan-agents/agent-service-toolkit/.superpowers/sdd/2026-11-03-xiaohe-ai-backend/triad_processes.py status
```

将最后的status改为stop可停止两个自有进程，start可再次启动；restart-api仅在协商安全点加载新的专用配置。私有目录内api.log/worker.log及processes.json用于本地排查，不向聊天导出请求体或认证材料。停止API后现有/ai-api会再次不可达，但不会删除数据或改变正式开关。再次建环境必须按已批准的新资源范围操作，不对原库/bucket执行重置。
