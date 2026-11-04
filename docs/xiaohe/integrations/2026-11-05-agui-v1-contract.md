# AG-UI业务出口与合同增量：A1

日期：2026-11-05。G0已由AI A0及门户P0提交319b915互核通过。当前交付源码与隔离验证，不部署、不启用正式开关，不调用短信或付费模型；G1真实Runtime链路仍待A2–A4。

## 唯一合同与制品

- 唯一主文档：`D:/Code/yunhezhiyuan-agents/docs/contracts/2026-11-03-xiaohe-integration-v1.md`，修订v1.0.4的§10；原REST/wire v1.0保持。
- 主文档SHA256：`752a6365665a6eab3a1cfd7ccf0d064005b4625ad8e2113f6d7a5a4e07ba5a0c`。它在Git外，本提交没有包含该文档，不能把Git内派生物说成主合同已提交。
- Git内派生物：[xiaohe-agui-v1.schema.json](../../../copilot-runtime/contracts/xiaohe-agui-v1.schema.json)，SHA256 `2c51b56490026013b10aec9b6d92917dad63aaaf1381e2a80e67d6bf95009b83`；直接提取§10的唯一规范JSON块，仅附来源版本/hash的`$comment`，测试逐项比对，不另维护第二套定义。

## 接线与语义

| 业务路径 | 结果与责任 |
| --- | --- |
| POST `/ai-api/v1/agui/run` | 标准RunAgentInput，只消费当前user文本或forwardedProps.xiaohe绑定动作；原事务提交后返回202 message/run/viewer+attemptId/businessRunId |
| POST `/ai-api/v1/agui/connect` | `{threadId,attemptId?}`，SSE权威一致快照及水位后事件，唯一流出口，不再受理输入或job |
| POST `/ai-api/v1/agui/status` | 同一固定DTO，返回activeRun/选定run/attempt及isRunning/cursor，不续期 |
| POST `/ai-api/v1/agui/stop` | 同一固定DTO，仅显式取消；有attempt时须为目标run最新Command，旧attempt不会取消后来动作 |

`RunAgentInput.runId`绑定已有Command.client_key，businessRunId仍是服务端Run.id；waiting_input后的新action复用业务run并用新attempt。同键内容冲突、跨种类复用attempt拒绝；不新增表或迁移。已完成旧attempt重连只结束旧交互，仍保留数据库当前businessRunStatus及activeRun，不附着或结束新action。

HTTP Actor、Cookie/CSRF、require_conversation行锁、原accept_message/action、cancel_run及持久stream_events直接复用。各流事件重新核对会话及ownership_epoch；撤销后不再发私有帧，源订阅在异常或取消时关闭。流结束不是业务成功，不从EOF伪造RUN_FINISHED；已完成运行从授权行状态生成生命周期，不伪造持久CUSTOM eventId。

公开投影只含原六种parts、稳定ID/createdAt/文件版本、viewer/activeRun/cursor/merge及version/attemptId/businessRunId/businessRunStatus；去掉csrf。CUSTOM `xiaohe.v1.event`仅承载已验证原EventEnvelope，不输出RAW、内部graph/tool或未知事件。JS标准文本/消息与公开元数据是同一业务源的呈现；门户只保留一个消息应用路径。

Runtime将在202后附着connect，而不是复制第二套Python流。首次会话、认证卡和文件保持REST，完整SDK历史/state都不能写入业务事实。包体上限64KiB保留；门户通过公开fetch规范化出站历史/state，不依赖该优化做服务端授权。关窗/解绑采用detach及transport abort，不调用会发POST stop的SDK abortRun。

## 验证与边界

- 新模块缺失先观察RED；随后观察非法action错误、终态伪event、旧attempt重连和流初期失效未关闭source的失败，逐项最小修复后通过。
- 23项新增投影/API/JSONschema用例、相邻原HTTP/持久事件/归并/到期24项共47通过（19 warnings，20.85s）；之后补充提交后丢失202回执的ASGI传输故障用例，单独13项API通过。最终整组命令以原生账本task-done记录为准。
- 真实独立`test_xiaohe_agent` PG：同键1 message/Command/job、不重复额度及截止；action同业务run新attempt；旧attempt停止拒绝；快照到订阅之间新提交事件不漏；流中真实撤销会话后无私有快照/文本，订阅关闭。只使用test_xiaohe_库，没有接运行/会员库。
- 回执丢失用例在原ASGI transport实际完成事务后主动丢弃首个202并抛ReadError；同键恢复无第二job/计次/续期。这是框架传输故障验证，尚不称真实HTTPS丢包。
- Python真实投影的10个事件又经固定AG-UI JS1.0.2 EventSchemas及HttpAgent reducer消费：稳定messageId、部分文本续写恰一次、六类parts、终态业务状态及无CSRF通过；这是受控fetch的跨语言协议验证，不是Python/Runtime真实网络闭环。
- Ruff通过。原Streamlit已知问题未重跑，不称全仓全绿。A2–A4仍需验证真实Runtime、隔离PG/worker/MinIO、TLS/SSE、重启/断网/跨系统与配额链路；原会员443人工验收未被补足。

原始编译/测试/SDK结果保存在忽略目录`.superpowers/sdd/2026-11-05-xiaohe-copilotkit-ai-runtime/`。现有Compose、VERSION等并行修改保持，未纳入本批提交。
