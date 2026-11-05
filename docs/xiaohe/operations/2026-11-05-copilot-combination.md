# 新门户与新Runtime组合验收汇总

日期：2026-11-05。**结果：本轮受控匿名组合验收通过，未发布、未切换现有部署或正式开关。** 门户、AI和后端Owner分别在原工程执行，会员G2继续复用历史证据，没有重新登录、自动短信或付费模型调用。

## 固定来源与独立环境

| 项目 | 本轮实际来源 |
| --- | --- |
| 门户最后修补 | `21b8337eb1f983e7136333a18a8810cc3b412a86`，包括MCP v4-mini安全映射；前轮独立审查及该增量RED/GREEN修复pass分别记录 |
| 浏览器驱动 | `b73d5d0637eb4a6c3675db3379c81dcdff972804`，4个全新匿名Edge context |
| 门户候选index SHA256 | `7870722a8d81402aa4f3464274aaea1ccd21d1941c8cfd7894711e920c4d8b93` |
| JS/CSS清单 | 146个逐文件重算通过，manifest SHA256 `1beda9141f2ab495a962c0f76a904e1cf08f5f1a208a43e305086642654c5470` |
| Runtime产品源码 | `af2e6a21d49bbc35fc2adc36dcc1fe243275f457` |
| Runtime测试镜像 | `yunhezhiyuan-aibackend-copilotruntime:spike-af2e6a2` |
| image ID / 本地digest | `sha256:97487c6d389e18136c5c8129f843e28e77f87db6a8b389accad90262966370b4`，OCI revision为上述产品源码 |
| Python候选 | 既有 `spike-0d18e81`，API/worker的受控入口、triad入口及DecisionModel共6个程序SHA与固定提交逐一相符，fake-model标志开启 |

不重复前轮61项Runtime、19项Linux安全门或会员G2。完整候选静态归档实际218文件（含字体/图片），146指JS/CSS清单而非所有静态文件；AI Owner另外重算index、146个摘要及完整树指纹，与公开回执一致。默认门户包已恢复false；true候选只在隔离context静态映射，不替换现网HTML或资源。

AI Owner新增临时Runtime HTTP18112及独立TLS转发18113，固定上述image ID。TLS进程仅监听loopback，以UID1000只读加载已有证书/key，未输出或复制私钥；public CA用于严格验证。HTTPS代理按可信Origin `https://portal.lvh.me` 和路径白名单转发，使用pipe/flushHeaders保持SSE，未修改Nginx。health/info200、错误Origin403、未支持路径404、可信预检204均实测。

门户在独立Edge context中以同协议 `route.continue` 将 `/copilot-api` 指向 `https://portal.lvh.me:18113/copilot-api`；AI REST仍走既有443候选入口。业务API不是fixture返回，未用route.fetch/fulfill缓冲业务SSE；只有静态GET被映射到固定新构建。Python模型和专业处理器则是明确的受控fixture，不代表真实模型/专业Agent业务已验收。

## 本轮新跑结果

浏览器 **4/4通过，0失败/跳过，55.5秒**；AI Owner核对公开JSON、实际exit0执行记录、驱动和归档日志，没有另重跑浏览器：

| 用例 | 实际结果 |
| --- | --- |
| 采购方主旅程 | 欢迎和三方向可见；补充问题/action；5次Runtime写请求、关闭不发stop；后台完成后重开，整页刷新仍为原guest/canonical，消息ID、顺序、时间及parts保持 |
| 组织方向 | 命中organization受控处理器，新私有v1文件200下载，SHA与期望字节相符 |
| 供应商方向 | 命中supplier受控处理器，新私有v1文件200下载，SHA与期望字节相符 |
| 独立停止 | 1次写请求、恰好1次cancel，终态无activeRun、无文件 |

浏览器总计14次实际下载：采购方4个不同artifact的v1，在重开/刷新前后合计12次；组织和供应商各1次。4个采购方文件内容相同，但驱动分别使用快照中的artifactId/version获取详情和下载，并比较精确引用、消息结构及原文件SHA。此项不覆盖同一artifact的v1→v2编辑。

浏览器主旅程记录4条SSE连接，最大11块/13事件，最长观测窗口2480ms，businessDeltas为0。只读clone观察不替换应用Response；不能称真实LLM逐token输出，也不能称浏览器同一连接完整持续8秒。

AI Owner独立探针通过同一TLS入口验证完整时序：首事件356ms、完成8900ms、11块、受控文件1个。它和上述浏览器4项分开记，不累加为浏览器用例。

首次AI探针在最终quota/deadline断言失败：错误地将会员绝对8小时规则用于成功发送消息的访客。依据合同§49/51/185/213及受理源码修正测试后重验通过；没有修改产品或服务。访客新消息只扣1次额度、受理时滚动4小时，随后AI回复和2次GET不再续期。失败探针保留，不能用后续PASS覆盖第一次失败。AI Owner因此有2个新的匿名探针会话，门户4个新context另记，数据保留正常retention。

## 回收与现有环境保持

浏览器context关闭后，只回收本方2个有精确ID/名称/owner label/image核验的临时容器：TLS `3e7cb51b1834...`、Runtime `8634ea2d31a2...`。18112/18113已关闭，本轮label残留0。原8容器的ID、image、StartedAt、RestartCount、running、挂载集合等保持一致；旧Runtime18110health200。未删除镜像、卷、库桶、新旧测试数据，不把容器保持等同数据库所有行逐字节不变。

后端Owner在回收后07:59:25 UTC独立只读对照共享backend/Nginx/MySQL/Redis四服务ID/image/mounts，Portal443 HTML SHA仍 `6d0c17c0c75eb5edc3588f45e437be8c90569788f2e41d6062e4b4f1f84f4995`，SSO issuer及canonical端点443、公钥摘要保持，tenant/simple-list业务code0。本方已读取其公共JSON；不把4共享服务和8候选相关容器简单相加为无重叠资源数量。

合同全文在期间由并行接待业务设计修订：本轮早时v1.0.5 SHA `d6544d6e...`，收尾v1.0.6 SHA `cbf78e851b73924bf190425ae9a58dbfe3d4da257eb3babc1a85e25fa96d2a88`。**全文字节不保持**，未恢复/覆盖该并行更新。AI和后端均核对现有AG-UI唯一规范JSON与派生schema相等（忽略$comment），派生文件SHA仍 `2c51b56490026013b10aec9b6d92917dad63aaaf1381e2a80e67d6bf95009b83`；因此本轮既有wire无阻塞，不扩到新§11/C0等接待业务验收。

## 证据与边界

- [本工程公开JSON](../evidence/2026-11-05-copilot-combination.json)：组合来源、4项浏览器结果、独立SSE、失败探针、环境保持、回收及后端证据摘要。
- [门户公共回执](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/sso/evidence/2026-11-05-dependency-security.json)与[门户报告](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/sso/2026-11-05-dependency-security.md)。
- [悬浮入口截图](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/previews/xiaohe-copilot-combo-launcher-20261105.png)和[初始欢迎截图](C:/Users/70367/.codex/worktrees/xiaohe-auth-card/yunhezhiyuan-portal/docs/previews/xiaohe-copilot-combo-welcome-20261105.png)已视觉核阅；欢迎图拍于初始连接，不能作为业务完成终态证明。
- [后端Owner只读回执](D:/Code/deploy/local-deploy/docs/deployment/2026-11-05-combination-backend-preservation.json)，包含全文摘要变动及规范wire保持的区别。
- 会员G2引用[既有分层证据](2026-11-05-copilot-candidate-auth.md)，本轮4个context的authComplete均为空，不声称会员绑定/归并在新版本重验。

本轮没有产品源码变更或新一轮源码审查，仅测试驱动和验收记录；此前源码安全审查与mini增量修复pass分别引用。保留 `dev-fengfeng`、并行未提交修改、源码与测试镜像，未推送、合并或发布。

当前运行Runtime仍为 `spike-2c8132e`，没有采用新修补。正式开关保持关闭，真实模型token流、真实会员/短信、同文件v2编辑、容量/UAT/正式发布没有新增验收；原依赖版本告警、无界预算和尾斜杠P3边界仍按各工程安全报告保留。
