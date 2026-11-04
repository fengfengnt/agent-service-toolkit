# Task 8 真实边界补验证据

日期：2026-11-04。范围：复用已有证据，仅补本地隔离真实链路缺口；正式开关关闭、不替换部署、不自动发送短信、不调用付费模型。产品代码及原API/worker不变，新增执行代码仅为忽略目录内的测试脚本。会员前期证据见[三方运行记录](2026-11-04-isolated-triad.md)；门户负责最终验收汇总。

下列原始文件均位于 `D:/Code/yunhezhiyuan-agents/agent-service-toolkit/.superpowers/sdd/2026-11-03-xiaohe-ai-backend/`。JSON仅包含公有ID、时间、状态、计数及文件摘要，不包含Cookie、CSRF、手机号、验证码或凭据。

## 100/101 与四小时滚动

门户使用真实HTTPS新建conversation `f390aa80-d0c4-4d54-943e-69460cef57e4`，逐条完成100轮；第101条被拒绝，同幂等键不重复计数、不续期。原始网络证据在门户 `triad-browser/task8-boundaries-result.json`，本AI没有重复这些请求。

AI只读汇总：user消息100、assistant101（欢迎+100回复）、run100且全部completed、artifact100、ready version100；session计数100，仍为guest、未归并。计数证据附于 `triad-expiry-boundary-evidence.json`。

门户原脚本容许截止时间不倒退及五秒误差，单凭其断言不能证明100轮每次严格改变。AI补做READ ONLY事件聚合，仅提取该会话的viewer.updated之messageCount、expiresAt及事件时间：

- 100个事件，messageCount按顺序恰为1..100，各一次。
- 100个不同截止时间，严格递增；最小增量0.466711秒。
- 截止减事件创建时间范围14399.956962–14399.991314秒，与事件约+14400秒一致。事件时间不是SQL更新的精确受理时刻，因此不把这个差值说成每轮恰等14400.000000秒。
- 原始聚合 `triad-quota-viewer-events-audit.json`，本地断言退出0；未查询完整事件payload、正文或任何凭据。

当前专用配置经同一b370bfb运行快照解析：visitor_message_limit=100、visitor_retention_seconds=14400，未修改全局配置。领域精确滚动及拒绝/幂等测试仍可复用，未重跑328项旧套件。

## 新主体到期：真实queued停止与HTTPS拒绝

执行 `triad_expiry_boundary.py`，最终退出0；原始证据 `triad-expiry-boundary-evidence.json`。仅新建convo `4b85289d-e5b5-46a8-a5da-a459dd1a86a4`、session `5aa8dd81-ec8f-43b1-b2b7-58dc1f11d2a7`、guest `343d4c8c-e575-46bf-a5ad-d6ccba00100d`。

先正常受理一条并生成文件，原HTTPS下载200。第二条正常受理生成run `59c4fd9b-c6f7-46d6-a6ea-d71125f5eff9`、框架job427：

| UTC时间 | 实际事实 |
| --- | --- |
| 09:37:39.817 | 新记录NOWAIT锁下确认真实queued；只更新该session.expires_at为09:37:40.813754 |
| 09:37:41.320 | 持锁1.5秒后提交并释放，仅涉及新UUID |
| 09:37:41.482 | 原worker授权检查使业务run=expired、holds_slot=false |
| 09:37:41.645 | 同Cookie读取、发送、幂等重试、重开、原文件五项均410 VISITOR_EXPIRED，无新Set-Cookie |
| 09:38:00.740 | 原周期维护写入viewer.expired |
| 09:38:01.026 / 09:38:03.720 | cleanup_stopped_at / purged_at可见，完成安全回收 |

观察到停止后无新增assistant或ready版本，框架job为succeeded：它正常处理授权过期后返回，不代表业务生成完成。到期后再次重开与下载仍410，session计数保持2、limit100/retention14400不变，没有复活访客。

证据边界：这是受控推进一个新主体的时间，未等待真实四小时；实际捕获的是queued，不写成运行中图取消。运行中/等待中停止沿用 `test_visitor_expiry.py` 的真实框架/PG专项。没有伪造run状态、修改旧资料、手动调用维护或重启服务。

## 两个真实系统之间的拒绝

执行 `triad_task8_isolation.py`，最终退出0。原始 `triad-task8-isolation-evidence.json`；前期两个harness失败分别保留为preflight-red、readiness-red，不描述为首次即通过。

| 新资源 | 边界 |
| --- | --- |
| 数据库 / role | xiaohe_task8_isolation_20261104 / xiaohe_task8_isolation，仅新库迁移及两个System行 |
| 私有bucket | xiaohe-task8-isolation-20261104，专用key只能访问该桶，旧triad桶head403 |
| API / worker | 独立loopback18091与独立worker，复用b370bfb真实业务、saver/store、PG/MinIO及受控handler |
| HTTPS | 临时Nginx18445/18446，两Host固定注入不同system；私有临时CA仅测试client信任 |
| 系统映射 | task8_a↔91001、task8_b↔91002，仅新库测试数据；SSO关闭，不登记真实SSO应用 |

两侧先正常创建匿名会话、实际worker生成v1文件并下载200。双向交换conversation、file、发送目标以及另一个系统Cookie，八项均确切403 FORBIDDEN；随后正常本系统对话/文件仍200。不是stub API、内存身份或同系统不同guest替代。

原脚本normal报告的system/tenant取自测试常量，未直接从首个响应断言这两个字段。最终验收据此提出缺口，AI没有重启或恢复Cookie，而是以 `triad-task8-isolation-mapping-audit.json` 做独立READ ONLY补证并严格断言：

- convo6689e074-2216-4f43-83ad-f89f14ee8014，其session/registry/artifact实际均task8_a/91001，文件c7c1f9bc-1ebe-4b1f-ab21-097a7bef2ad8。
- convob33612fb-b533-4685-937a-a965db581e05，其session/registry/artifact实际均task8_b/91002，文件149da423-ae65-4e43-9332-81741ecbbac4。
- 两system和tenant确实不同，文件归属与原网络报告ID一致，排除同系统不同guest的假阳性。另 `triad-task8-isolation-readonly-audit.json` 证明两个scope各一completed run/ready文件，用户账号数0。

收尾已核验：本次API3539963、worker3539964及临时Nginx停止，18091/18445/18446均释放；新MinIO key撤销后head403、新PGrole设NOLOGIN；新库桶脱敏数据保留。旧API/worker仍存活，18090、原库桶、正式Nginx、SSO登记及实际.env均未修改。旧runner guard未放宽，新入口只接受固定新资源。

前期harness修复：S3客户端没有context manager，实际TypeError后改用标准库closing；临时API的core导入约18秒、app创建约3秒，原20秒就绪预算不足，保留失败后改有界75秒并记录502→405，再执行真实业务验证。没有降低TLS验证或把就绪失败当隔离成功。

## 追加至已有历史canonical：独立于首次归并

早先首个会员用例创建user/canonical/claim均在UTC08:48:39，目标此前没有历史，不能据其刷新同canonical推导第二次追加。只读证据 `triad-member-existing-history-audit.json` 已明确该边界。

门户随后在保留的原窗口提供人工操作helper，用户确认“已完成并回到原账号”后，AI才做一次READ ONLY：`triad-existing-history-append-audit.json` 与逐字段断言摘要 `triad-existing-history-append-summary.json`，均通过。

- 原user79b8e008-fbb1-479d-b140-f27007a2a4a9及canonical3f7e9399-8897-4ff4-8d1f-b671b9f40ba9保持唯一；canonical创建08:48:39.700243，早于新source5aac40c8-af44-4022-8454-f6d70b5734cd创建09:49:33.037501。
- 新claim0cd8d459-961e-4100-8b26-12a966d6ca83/op d69e9a38-6f7d-4b1d-89d6-28f7f5fd07f2于09:49:53 merged，新来源已撤销；两来源各一次claim，同一目标，未新增账号/canonical。
- 目标6条消息为旧3+新3；旧3的ID/时间与前期audit完全一致。新用户消息f31b43d1-8bbf-46f5-8312-47ddface788a具有指定client_message_id，创建早于新claim。
- 原a972文件的v1、version_id、key指纹和摘要不变；新f66f29c5-06bc-4892-9e2b-e4eacde0f633也为v1/ready，其上传早于claim，新旧文件均归原canonical。

这份DB审计证明已有历史追加和当前所有权，不替代原窗口实际HTTP读取、文件下载或自动刷新回归。AI未获取新来源的归并前公有receipt，不能独立声称新文件key/ID前后已逐字段比对；门户应以人工helper的公有receipt及用户操作证据补齐或明确此限制。没有读取正文/subject、Cookie或凭据，没有发起新登录、短信、用户退出或直接资料写入。
