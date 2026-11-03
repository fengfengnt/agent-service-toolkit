# P3 消息、动作与原子受理

2026-11-03，dev-fengfeng，阶段基线9fa0dd4。已有P0公开DTO保持；此阶段实现业务函数，不声明公开HTTP服务已装配。

`accept_message`、`accept_action` 复用P1对象权限与P2同连接入队。消息、run、命令、配额/期限、持久事件和job在同一外层事务提交后才算受理，函数使用savepoint确保异常即使被调用者捕获也不会留下部分业务写入；不自行commit。

锁序与P1实际鉴权对齐为session→必要user默认对象行→按UUID排序conversation→command/auth_operation→run→artifact。会话/对象权限和数据库时间先检查，再读取幂等命令；新命令在对话锁下创建，无空占位记录。后续认领必须同序，不反向获取session。

## 行为

- 访客第100条允许，第101条拒绝；动作生成的可见user消息同样计数。回复不消耗额度。配置保存在创建的访客会话行，2条/60秒采用相同逻辑。
- 只有新受理消息/动作通过最终 `expires_at > clock_timestamp()` 条件更新后，才增加次数并滚动期限。忙碌、归并写门、非法/陈旧动作、无权限、超限或幂等重试均不更新；入队期间到期仍回滚整个savepoint。
- 已登录用户不消耗访客配额，也不改用户会话created_at/expires_at。
- 同授权对话、提交类型、client key唯一；只存参数摘要及原message/run引用，不缓存整份MessageAccepted。同键改参数409；重试返回原ID、当前run.status与当前viewer，额度达到上限后仍能重试原键。
- 同对话只有一个持写位置的run，数据库部分唯一索引兜底。queued/running必须持位置；waiting_input可在后续归并协调中让出。动作仅恢复绑定问题的同run，核对选项/文本形式与消费记录；陈旧卡片不能启动新run或抢其他run的位置。
- 内部merge_pending仅为输入写门，返回409并不消费动作；P9负责真实归并事实、协调和公开MergeState。

手机号、明确验证码及独立6位数字不作为普通聊天持久化；仅对检测副本做NFKC和数字分隔符归一化，保存的合法原文不trim、不改码点。独立6位数字保守按潜在OTP处理，可能误拦无上下文的业务数字；不声称识别所有隐写或所有个人信息。输入错误统一安全代码，不把原始认证内容放异常。专用认证接口仍归P8，普通消息限额不撤销会话/CSRF，后续认证操作必须独立验证配额边界。

## 表与事件

迁移0002增加runs、commands、events及所需复合唯一键。run使用业务UUID，内部thread来自原conversation；框架job只存run_id。命令关联同对话/系统/租户的message/run，问题动作有唯一消费约束。

受理同事务记录message.upsert、run.updated、viewer.updated；eventId为持久UUID，sequence在持有对话锁时递增。数据库拒绝UPDATE已存事件。快照从同一受控对话读真实activeRun和最后sequence对应的eventId作为cursor；HTTP受理响应不包含cursor。P6再增加fence包装和SSE回放，当前不以原native流代替该协议。

`events.record_event`仅供已持有对话锁且完成授权的内部事务调用；不是公共写事件接口，也不自行证明worker写资格。P4/P6必须先验证attempt/ownership fence。

补齐标准 `migrations/script.py.mako`，真实revision生成在临时目录编译通过，P1遗留小项已解决。新迁移先创建被引用的复合唯一约束，再建FK；降级反序。不改0001，也不管理上游或框架表。

## 验证

- 初始RED为admission不存在；模板缺失、标准格式/全角手机号漏检、queued/running丢写位置分别以失败断言复现后修复。
- P3受理21项、迁移生成1项；P0/P1/P2与指定上游相邻回归合计 **149项通过**，19条既有依赖警告。
- 同键/不同键并发最后一个额度，已有run结束后的重试、动作续接/让出位置竞争、敏感字段不持久化、入队失败/最后时刻到期回滚、真实快照水位和事件不可变均通过。
- PostgreSQL17.11隔离库alembic check无差异；完整迁移进退仍保留上游真实checkpoint/store哨兵。Ruff通过。

仅使用专用test_xiaohe_agent临时库；未接真实SSO/短信/模型，未实现P4执行恢复或P6 HTTP服务，不作为部署验收。
