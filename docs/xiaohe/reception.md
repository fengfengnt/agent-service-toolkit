# P5 接待图与专业能力边界

首版接待使用 `build_graph(factory=...)`。内部执行路由提供业务 run/attempt/ownership_epoch，节点通过 `build_reception_context(db,fence)` 再次确认权限，从当前 run 的 conversation 取最近至多 20 条消息；不采用浏览器提交的历史、方向状态或任意租户参数。方向持久保存在业务 Conversation，回复仍由 P4 worker 提交。

## 方向与接待

方向使用 buyer（采购方）、organization（协会等组织机构）、supplier（供应商）及 unknown。明确自称、切换指令和方向选择优先于模型推断；临时询问其他方向可以获得本轮回答，保留已明确的当前方向。否定或含糊表达采取保守处理；未确认方向时返回三个选项，继续使用同一 run 的持久等待/动作协议。

模型仅返回 RoutingAnswer 的 direction、intent、answer。intent 只有 reception/professional/clarify/off_topic，不包含认证、保存、调用工具或执行成功等状态。无关请求使用固定引导；结构错误、额外字段、超过 4000 字符或 30 秒超时均转换为不含原始异常的 UPSTREAM_UNAVAILABLE。

认证卡片由服务端从当前用户的明确正向表达产生，例如“我要留资”“我想注册”“请帮我登录”。包含不要、不想、暂时不、以后再说等否定/推迟表达时不产生卡片；含糊表达保守地不自动开启认证。当前识别规则面向简体中文，需要更多表达时按真实用例补充，不声称覆盖全部语言。卡片只是请求，不代表发送短信或认证成功。

## 模型及敏感数据

`reception_model()` 直接使用上游 DEFAULT_MODEL、AVAILABLE_MODELS 与 `core.get_model`，不新增供应商客户端或第二套模型配置。兼容 OpenAI 模型继续使用上游 COMPATIBLE_MODEL/COMPATIBLE_BASE_URL/COMPATIBLE_API_KEY；DeepSeek 使用上游配置。无可用选择或 FakeModel 时产品调用安全失败，测试模型仅通过显式 fixture 注入；没有调用付费模型。

模型输入仅包含接待策略、当前方向和最多 20 条经过过滤的可见文本。auth_request、认证字段、任意 confirmed_facts、内部标识和认证请求的额外字段不序列化给模型；当前还没有可供模型使用的受控业务事实字段，因此只使用已确认方向。标准手机号/验证码及 Bearer/token 标记再次检查，命中时整段替换为认证信息占位。原认证表、短信值、browserVerifier 不参与模型或图输入。

所有模型内部消息流默认不作为公开 token。只有受信 handler 显式标记 xiaohe_public 的用户可见文本才允许增量，首版结构化接待没有使用该标记，只发布受控最终 ChatPart，避免把路由 JSON 或中间分析发给用户。模型原文不能创建 auth_request、产出物、认证结果或后台保存结果。

策略位于 `resources/xiaohe/reception-policy.md`。首版只采用保守的公司与三方向介绍；[历史知识审阅表](../../../docs/knowledge/2026-10-30-source-review.md)及 PPT 演示数字没有直接导入。涉及费用、免费、分成、排期、保证和部分成功断言的输出使用固定中性口径。规则检测和策略提示不能证明模型永不出错，真实模型及对客内容仍需独立验证。

## 专业 handler 接口

```python
ProfessionalHandler = Callable[[GraphState], Awaitable[AgentOutcome]]
await dispatch_direction(direction, state, handlers)
```

P6 在注册图时传入已登记的 handlers 映射，不新增插件框架。业务节点提供 run_id、当前 direction 和 write_fence；handler 的查询、工具、对象与事件写入必须使用该 fence 复核权限，不能仅信任 state 内的文本身份。handler 返回受控 AgentOutcome，只有后台真实执行结果才能表示成功。认证卡片由接待控制层统一决定，专业 handler 自行返回的认证卡片会被移除。

本阶段没有登记三个专业业务流程；方向未登记时说明“专业业务办理尚未开放”，可以继续接待介绍。没有假装注册绑定、人工任务、会员进展或文档生成已经执行成功。真实接口随相应已批准任务装配并分别验收。

测试覆盖三方向、明确/否定切换、临时跨方向问答、澄清、无关引导、正向认证、拒绝留资、敏感字段、非法结构、超时、假模型拒绝，以及真实 PostgreSQL 图执行的范围隔离和内部模型文本不外发。
