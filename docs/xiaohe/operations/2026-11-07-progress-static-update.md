# 我的进展、提交提示与滚动栏：隔离静态更新

2026-11-07，北京时间。状态：**静态包切换及技术核验通过；后续用户确认原申请受理成功与我的进展可见，原键回执核对通过；滚动真人效果未单独确认**。

## 范围与来源

本轮只部署Portal静态包。登录后桌面在“关于我们”后显示“我的进展”，移动菜单同步，原头像入口保留；901—1100宽度的已登录五项导航折叠，避免挤压。提交中与已受理提示分别使用真实pending/accepted含义；已存在的两条旧助手提示按完整文本精确映射为友好展示，不改用户消息、带前缀/引用文字或服务端历史。pending只说明资料已保存、正在确认、确认受理后安排跟进，不假报已受理或已分派专员。

按用户另在门户聊天提出的滚动栏遮挡需求，精确合入门户提交 `51699a72fdf166fcbbcd7294a5db4a26ddd95f51` 的5行CSS增量。`chat.css` SHA为 `0da71d04c16c6a30412952891e046df492a857539c982369f73c0d5a85f21f79`，与门户Owner已经检查的文件逐字节一致；没有整包覆盖其他工作树。

最终候选：`D:/Code/yunhezhiyuan-agents/.scratch/manual-reception-20261106/updates/20261107-progress-receipt-scroll-final`。9项源文件（5产品、4测试）及全部222资产与manifest一致；Root额外将前次完整冻结源码与前次增量源码组合成实际旧版基线，对95项当前生产源全量比较，变化仅白名单5项。完整当前src副本保存在本轮更新目录 `frontend-source/src`，全量摘要为 `production-source-sha256.json`，不以Git HEAD替代实际WIP构建来源。

Owner最终388项单元、36项安全检查、typecheck和构建通过。独立只读审阅执行相关4文件44项检查通过，无新增Critical/Important/Minor。滚动修复来源有Chrome 5项RED→GREEN和15项输入/卡片/artifact回归证据；这些来自门户Owner同字节CSS，不能冒称本次合包已完成真人Chrome或完整浏览器几何验收。

Python四处展示字符串仅保留为未来候选，本轮不部署、不改API源码、不重启API或worker，不重放原申请。

## 运行、归档与回退

本轮更新根：`updates/20261107T060800Z-progress-static`。实际静态归档：`D:/Code/deploy/local-deploy/releases/xiaohe-reception-manual/local/20261107T060800Z-progress-static/portal-spa`。

- 原222文件候选ZIP SHA：`2a94cd133a7d93cf4f9df00ef2be69ca7e15d79fefae3cc6b99937be0eab703a`。
- index SHA：`6a2a344591261c9349978ac281f7e87fc251bb4187301abb765a25ddae028f7a`。
- 实际服务包503文件，保留281个未覆盖新文件的旧哈希资产；ZIP SHA为 `918f3339d5bbd130d6d0b3636f165a1f453098e0d501841a2f83c5f9fa357fdf`。CRC、逐文件内容、实际私有运行值未混入客户端包均核对通过。

只有私有canonical配置和现冻结gateway配置中的portal_root变化，其他设置逐项相等；旧配置先备份在WSL手工体验私有目录 `backups/20261107T060800Z-progress-static`，目录700、文件600。没有备份或覆盖数据库，因为本轮不改数据库、API或worker。

仅重启18116专属gateway，当前PID81864、launcher13816。继续运行前次 `20261107T052651Z-login-capture-v3/candidate-service.py` 和0008冻结代码；该冻结launcher读取的私有candidate配置仍在原v3私有目录，静态根已更新。API18097/PID73156、worker/PID78132、Runtime18117/PID63540的PID与UTC启动ticks均相同；Java/MinIO容器、19个正式容器及正式门户HTML摘要均相同。核对进程时先遇到PowerShell将JSON日期自动转换导致字符串比较误报，改按UTC ticks比对确认，未据此重启任何后端。

当前静态入口见 `updates/current-update.txt`；后端依赖根见本更新目录的 `gateway-runtime-root.txt`。回退只需恢复本次两份portal_root配置备份并用同一冻结launcher重启专属gateway，指回前版静态归档；无需重启API/worker/Java、复制Cookie、恢复数据库或清理卷。旧资产和全部旧版本继续保留。

## 原申请及保持项

更新前后仅用READ ONLY事务按私有锚点中的原申请id和submission_key核对，未调用任何result/retry/confirm/submit，也未POST建立测试会话。原申请标识、资料指纹、pending/无receipt/submitting状态均不变；同scope业务co_collaborator、co_submission、co_revision仍0。7条会员session及原截止/撤销时间、会员消息和文件关联指纹相同。此处不公开申请标识、用户资料、Cookie或凭据。

严格TLS与主机名校验下，18116实际index及4个入口JS/CSS与新归档逐字节一致；API18097 ready、Runtime18117 healthz均200。HTTP验收仅GET，不触发业务绑定或申请恢复。

业务后端“数据处理”聊天已收到交接文档，但本轮最终快照仍在等待用户明确修复指令，未恢复Java。原申请受理故障详见 `2026-11-07-reception-pending-runtime-diagnosis.md` 及OS临时目录交接文档。恢复后仍先按原锚点只读核对；用户当前明确“不重复提交”，不得自动调用会INSERT业务身份的 `/applications/result` 或可能实际submit的“查询提交结果”流程。

接待Owner可引导用户在原隔离Chrome按F5，只核对菜单、旧提示和滚动显示，不点击重试/确认提交。进展入口可见不等同于已有正式受理记录。正式部署和开关保持不变，本轮未发短信、未调用模型、未重复提交或修改业务记录。

后续状态更新：上述pending/Java未恢复是静态切换时的基线。后端完成冻结JAR恢复并获用户授权后，由用户亲自对原申请点击一次“查询受理结果”，取得accepted；随后确认“我的进展”能看到本次需求。Root只读核对原键/资料未变、回执匹配及唯一业务记录，详见 `2026-11-07-original-application-acceptance.md`。没有由Agent代点或后台重放，静态包未再修改；不能因此一并标记真实附件或滚动的人工验收完成。
