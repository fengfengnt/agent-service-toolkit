# P9 私人对话认领与接续

认证上下文和中心适配见[SSO适配](integrations/sso-required-inputs.md)。认证成功与归并完成分开：complete首次可信结果由中心claim确认，AI再生成/复用全局user_id，建立绝对8小时、不滑动的本地会话；中心authenticatedAt保留原语义。

## 持久认领

有效guest首次认领，在同一事务保存唯一GuestClaim、绑定用户/canonical，撤销旧guest访问和来源writer、保护资料并登记协调job。目标queued/running时返回202 user快照、MERGE_PENDING及Retry-After:1。目标运行可继续写；新用户写入被merge_pending挡住，不阻塞原运行的安全收尾。终态/等待态持久唤醒协调，30秒维护扫描补偿。

来源会话的条件UPDATE以实际clock_timestamp判定归属争用；等待锁跨过截止则SKIPPED_EXPIRED，不把过期资料迁入账号。pending先赢后，来源会话已撤销并有用户绑定，即使原匿名TTL随后到达也不归匿名清理。guest_claims保存权属，后台依据已绑定账号继续协调，不因原SSO事务到期重新认证。停用账号不再重建执行任务，资料仍受用户权属保护。

协调和普通run共享框架conversation执行锁，复用PG任务；claim已todo/doing时不重复排队。失联doing claim由原恢复扫描标failed，持久pending扫描继续完成或重投。目标忙时按updated_at轮转批次，避免前100条长期占住扫描。没有引入新队列或通用工作流引擎。

## 消息、文件和执行状态

归并保留原消息ID/时间、文件ID/版本/object key，只搬业务关联。四条依赖消息/run复合归属的外键在0005变为DEFERRABLE，平时仍IMMEDIATE，仅归并事务临时延迟检查；不改旧迁移或关闭完整性约束。跨来源client key偶遇冲突时，历史消息清除冲突的内部提交key、命令使用来源命名空间摘要；原消息ID仍不变。原提交幂等作用域为原conversation，客户端不得把旧请求自动改投新canonical路径。

原来源事件不改写（有不可变触发器），保留无正文cursor tombstone后删除正文；canonical追加持久message.upsert，快照及其cursor在同一锁定对话视图下恢复。事件ID仍属于其原会话，不把来源cursor改造成目标cursor。

目标waiting_input保留原问题、run和checkpoint，暂让slot；以后的显式动作在空闲时恢复原run，忙时拒绝且不消费。来源未完成run先撤销写资格，接续使用最新command/message和问题，从业务快照创建用户拥有的新run/thread，不重复扣额度或新增用户消息，不拼checkpoint、不改匿名namespace的user metadata。后台迟到结果仍须通过原fence。

P10已在隔离环境实现未认领过期guest的物理清理；pending/merged GuestClaim明确排除，实际认领保护、迟到对象回收及失败重试见[到期与清理](expiry.md)。已认领guest的旧匿名namespace本批保留，其停止访问与新用户namespace接续不等于物理空间已回收。

## 响应全部丢失与会话截止

v1.0.3的受限例外允许原有效认证上下文+认证CSRF，在原操作窗口内重试同complete。若用户Cookie完全没收到或还留着原guest Cookie，可重新领取同账号会话；created_at/expires_at保持首次anchor/+8小时，不重新换码、OTP或发送短信。当前已有另一有效AI账号、已撤销/无效用户Cookie、错context/system/Origin/CSRF、取消或越过恢复窗口均拒绝。

有正确有效用户Cookie时，普通轮询复用现有会话，避免每次complete增加一条session；新认证上下文本身不授权旧操作，必须有匹配账号证明。该已认证路径不依赖旧中心事务截止。首次会话截止不可修改，并发首登先锁身份再读取首次anchor，再INSERT会话；保留原不可变触发器。

首次complete也检查浏览器当前账号：如果另一标签页已切到既非操作建立时账号、也非本次可信目标的账号，返回409 IDENTITY_MISMATCH，不覆盖Cookie。操作在原账号下建立、再经中心明确认证另一个目标的主动切换仍允许；本地失败重试沿用同一检查。

本地logout还撤销同账号/同首次anchor的操作及原认证上下文，并使当前认证上下文失效（包括尚未绑定账号的新操作），防止退出后用旧complete能力重新领取会话；不触碰其他登录会话或中心Cookie。

## 证据

用例覆盖：并发首次完成、响应连Cookie全丢、错账号/新上下文、固定截止/取消/退出、目标忙202后自动归并、旧等待问题原checkpoint恢复、来源最新动作新namespace重建、等待锁跨截止跳过、中心已完成后本地事务失败重试。真实私有MinIO验证消息ID/时间和对象key/版本保留；真实框架验证同conversation锁、正常claim投递及失联维护补偿。完整业务回归与独立审查结果在[组合验收](testing/xiaohe-backend-acceptance.md)更新。

尚未完成：真实Portal/SSO会员换码、短信、不同site浏览器、实际生产权限/网络、物理清理及最终产品验收。正式开关关闭，不推送/合并/部署。
