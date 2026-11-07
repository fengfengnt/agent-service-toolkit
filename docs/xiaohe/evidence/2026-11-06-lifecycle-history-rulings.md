# 生命周期 V2 执行裁定

按执行顺序保留裁定、理由及误判代价。基线前阻塞现已由稳定基线交付解决；历史状态不代表当前阻塞。本记录不授予部署或数据迁移权限。

1. Ruling: 用户已指定dev-fengfeng且主树存在受保护工作，基线前置期间保留该工作区不新建分支、不附着/修改他人工作树 — 当前仅文档/协调，无产品改动 — 错误判断将导致源码覆盖，进入产品步骤前仍需重新确认工作区隔离。

2. Ruling: 遵守计划既有稳定基线门，不以“计划已批准”替代Owner整合确认 — 接待树正在改同一批身份/admission/schema文件 — 绕过会产生平行协议、迁移冲突与集成返工。

3. Ruling: 新的明确WSL授权替代本计划历史的“一律暂不部署”限制，只允许按需的新专用隔离环境验证；保留正式桥接开关、正式入口、真人0008体验库/桶和并行工作树，不在其上跑TRUNCATE fixture或试迁移 — 用户最新指令优先 — 若误扩大到共享环境会影响身份/数据，实际环境动作前仍核对专属资源。

4. Ruling: migration0009已被workflow占用，预算/历史迁移改用0010并以0009为down_revision，待稳定基线再最终验证 — 防止双head或冒用已有revision — 若稳定head再变则先修订编号，不修改已应用迁移。

5. Ruling: 使用用户指定dev-fengfeng当前主目录内联实现，基线交接后只有保留的.codegraph，不创建其他分支或工作树 — 用户指定分支且写入权已移交 — 若其他工程再次写入需立即重新核对，不覆盖并行变化。

6. Ruling: V2 DTO本身复用V1 Part/Viewer/Event payload；schema在新独立机器块声明，V1机器块字节保持a1f5b073149da2d9fa33f5174f9c11821ef0f47a4b239377bb869b6252a14aaa，仅更新其副本来源文档SHA注释；母文档不在Git，仓库镜像字节同步 — 防止第二份手写协议或虚报提交 — 后续任何母文档变更都须重提取来源摘要。

7. Ruling: 并发过期重开需重领同一新周期Cookie而不能存原文token，因此由现有AUTH_SECRET按独立xiaohe.guest-episode.v2域及随机Session UUID派生32字节opaque token，首插入前定值；只有证明同预算的旧内容Cookie且确实过期才可恢复，新budget Cookie本身仍无正文权限 — 保留两标签只建一周期/不打印凭据 — AUTH_SECRET更换会使旧响应丢失恢复失败关闭，不改变已有有效token本身。

8. Ruling: bootstrap按Session(排序)→Conversation(排序)→Budget锁并重读指针；指针变化回滚savepoint释放锁后重试最多3次，耗尽返回原AUTH_NOT_READY，不持Budget追锁新Session — 防止与admission/claim/cleanup倒序死锁 — 极端持续争用时客户端需原键重试。

9. Ruling: quota事实由GuestBudget唯一承担，Session计数只镜像；所有_admit共用charge_guest_message，先Command幂等再预算条件扣减+Session期限镜像+最小Receipt同事务；旧周期Receipt只能拒绝重投，不返回正文。quota满不进入worker权限条件，故100th可完成；原真实expiry仍中断 — 不增加V1/V2各自额度 — 全套V2 HTTP表示仍待Task5。

10. Ruling: 存量guest可绕bootstrap直接完成认证，必须在source尚未revoked且Session/双方Conversation已锁后承接保留计数，完成响应补发/续签同Budget凭据；completed重读复用原Budget，不清零 — 新用例证明仅修改admission不足以覆盖登录路径 — 旧revoked且从未承接又不能证明的源仍失败关闭，不按0补发。

11. Ruling: 固定基线415的buyer文案已经是采购商，test_welcome仍断言采购方；直接git show415确认，因此只同步这个陈旧断言，不改已交付产品文案 — Task4必跑welcome回归发现基线测试漂移 — 不是新增文案需求或V2业务变化。

12. Ruling: 两个旧HTTP/Auth用例仍断言POST/session过期永远410，此规则已被批准§12.5替代；同步测试为满预算429或剩余预算创建新内容周期但保留总计，旧GET/stream仍410且无Cookie重置 — 不回退已批准生命周期 — 原会员绝对8h规则没有改变。

13. Ruling: V2标准MESSAGES_SNAPSHOT也必须maxItems10，Task1机器块漏编码该既有§12.4要求；仅收紧V2该属性、更新陈旧规划状态为引用实际验收；所有DTO字段/fixtures不变，唯一master/mirror rawSHA=3435d508e3401ba42359e191ff0b90a96f18c05102274a78e9dcce1fa45d6ca9。V1机器块原字节SHA仍a1f5b073149da2d9fa33f5174f9c11821ef0f47a4b239377bb869b6252a14aaa；V1派生仅来源$comment文档SHA更新，是本任务“不改V1副本”的必要溯源例外，不改其schema语义。已及时通知Portal/SSO旧6446仅历史raw，候选3435待固定提交。

14. Ruling: 原V1安全测试的_check_scope包装回调只收4参，新可选history_epoch成为第5参；同步测试包装签名并原样转发，而不是放松scope/revocation检查 — 实际Root函数仍兼容4参 — 修正后V1撤销中流仍阻断私有frame且订阅关闭。

15. Ruling: copilot-runtime无.env.example/README，按用户避免改上游要求，将固定版本配置写入已有业务runbook，未新建模板、改现用env/compose/代理或改上游README — 减少重复运维来源 — future部署需显式注入进程配置并配套Python/Portal。

16. 裁定：Task 7 组合已实现能力，不故意破坏产品制造 RED。新测试字段假设修正后既有实现通过；真实 HTTP 外呼仍立即失败。代价是组合测试由同一实现者编写，另以独立整批审查及门户原生浏览器验证补足。

17. 裁定：新预览采用独立 HTTPS Origin portal.lvh.me:18593、合成 System/client 和 FakeBridge，门户已认可；不路由到旧 443。新进程保持 CA 验证并拒绝其他外呼，不改共享 Nginx/SSO。真实回调和登录仍未验证。

18. 裁定：用户授权的可丢弃预览不是替换发布，以固定 Git SHA 标识；不改发布版本、镜像或现有服务。正常部署的版本/归档门仍适用于未来正式替换。

19. 裁定：不放宽一次性迁移的数据库 allowlist；只在本计划独占 PG 内新建原允许的测试库名运行原测试。代价是多一个可丢弃库，不触及既有同名运行库。

20. 裁定：六项 WSL Compose 检查因 wslpath 解码与只读调用超时阻塞；保留失败证据、从可移植门排除，不写为通过。不重启 WSL/共享服务或放松保护，部署/Compose 就绪仍未验证。

21. 裁定：启动隐私测试不是启动延迟基准；两次整批超时及原样独立通过后，仅将测试子进程上限 15→45s，拒绝错误配置及密钥不回显断言保持。代价是挂起测试最多等待45s，产品启动器不变。

22. 裁定：结束时保留本计划 ledger 与新预览目录；门户仍消费这些进程、配置和公开 CA，用户要求保留运行依赖与证据。删除将破坏已批准的隔离验证，后续清理须先核无引用并在授权范围执行。
