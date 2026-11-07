# 会员退出后恢复匿名接待：修复记录

2026-11-07，北京时间。状态：**源码及隔离回归通过，尚未替换运行服务，用户窗口待验收**。

## 原因及范围

用户在小鹤隔离测试窗口报告：门户退出后，再打开小鹤只提示登录，并显示连接中断。前端隔离状态测试复现了旧会员 Cookie 持续返回401，而退出流程跳过 AI 注销、留下该 Cookie 的闭环；认证错误另被映射为网络断开。

正式443入口以独立无Cookie请求 POST `/ai-api/v1/session` 返回200、guest、0/100及一条欢迎语。此检查没有发送聊天或调用模型，证明新匿名入口可用，不能代替用户窗口的退出链路验收。

本工程只修改 `src/xiaohe/identity.py` 的原 `logout_session`：同系统/租户可识别的已过期或已撤销会员，可凭有效独立认证上下文和认证CSRF执行原注销接口，清除SESSION/AUTH Cookie。有效会话仍要求聊天CSRF；未知Cookie、过期guest、跨作用域、错误认证证明不走该例外。保持原revoked_at、绝对8小时截止、会员历史和原认证操作撤销机制；不清预算、不发新额度、不自动建立guest。

门户调用分支、状态提示及发送守卫由门户Owner修复；唯一共享契约在父目录 `docs/contracts/2026-11-03-xiaohe-integration-v1.md` §13，保留§12的V2规划。本次未实现或发布生命周期/历史分页V2。

## 验证

- 新增HTTP/真实PostgreSQL隔离回归12项，覆盖过期/撤销会员清理后匿名欢迎及旧会员会话不可见，活跃会员仍需聊天CSRF、满额过期guest不重置、未知Cookie拒绝、缺失/错误/重复/过期认证证明、错误Origin/作用域拒绝。
- 先观察受限清理尚未实现时有效测试返回401而预期204，再实现公共修复。构造过期fixture时遇到不可变期限保护，改为直接插入满足原8小时约束的历史fixture，不关闭触发器或修改生产保护。
- 扩展回归：`tests/xiaohe/test_logout_guest_recovery.py`、`test_identity_sessions.py`、`test_auth_operations.py`、`test_auth_privacy.py`、`test_http_api.py`、`test_guest_claim.py`，**55 passed，19项依赖弃用/未来兼容警告，39.74秒**。
- 命令：`.venv/Scripts/python.exe -m pytest <上述6文件> -q --tb=short --disable-warnings`；显式测试库 `test_xiaohe_logout_20261107`，独占一次性PostgreSQL17、tmpfs数据，未使用原运行库/体验库。fixture会TRUNCATE，不能复用到真实库。
- Ruff检查通过；一次独立只读安全审阅未发现本次新增的Critical/Important/Minor问题。未运行可能依赖外部模型或其他服务的全仓库测试，不能声称全仓库通过。

## 未验证及保持项

用户允许继续操作原测试窗口，但Computer Use无法可靠确认该Windows浏览器当前URL，工具已停止；没有继续尝试其他控制通道，也没有把浏览器结果记为通过。

未部署、未替换API/worker/门户或修改正式开关；未发送短信、创建真实账号或调用模型。代码通过不表示原窗口已加载修复，需要后续在允许的隔离更新范围内核验。主树原有部署文件、历史证据及未跟踪文件均保留，父目录共享契约并未冒称纳入嵌套Git提交。
