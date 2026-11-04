# 本地三方隔离联调运行记录

用户已在门户聊天明确批准按隔离方案启动API、worker、专用数据库和bucket，继续三方协调；正式开关关闭、不替换现有部署、不发短信或调用付费模型。AI在执行前通过read_thread核验真实用户原话，未再次索取同范围确认。基线dev-fengfeng/9b79b51，本轮只新增测试专用启动器，不改上游产品源码、Windows .venv、依赖锁、共享合同或其他工程。

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

真实身份绑定仍要求中心受信claim；无既有中心会员会话时由用户正常登录，不能用fixture或前端成功标记冒充。正式Portal包保持false；只有门户Owner的独立浏览器context覆盖静态GET/原callback使用测试包，API/SSO走真实网络。当前已获私有配置，加载/真实事务及浏览器闭环结果后续续记，不提前标完整联调通过。

## 自有进程操作

从Windows运行以下命令，脚本校验PID、内核启动时刻和专用runner路径，只操作自有API/worker；不改Nginx/SSO/存储服务：

```powershell
wsl.exe -d Ubuntu -- /home/fengfeng/.local/share/xiaohe-triad/20261104/venv/bin/python /mnt/d/Code/yunhezhiyuan-agents/agent-service-toolkit/.superpowers/sdd/2026-11-03-xiaohe-ai-backend/triad_processes.py status
```

将最后的status改为stop可停止两个自有进程，start可再次启动；restart-api仅在协商安全点加载新的专用配置。私有目录内api.log/worker.log及processes.json用于本地排查，不向聊天导出请求体或认证材料。停止API后现有/ai-api会再次不可达，但不会删除数据或改变正式开关。再次建环境必须按已批准的新资源范围操作，不对原库/bucket执行重置。
