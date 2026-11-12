# 小鹤标准 Skill 唯一执行链验证

版本：0.1.8。设计与范围见[清理设计](../superpowers/specs/2026-11-12-xiaohe-standard-skills-only-design.md)。源码已移除旧资源与执行器；WSL 已完成清理、0015 迁移和发布，见[发布记录](../deployment/2026-11-12-standard-skills-only-wsl.md)。

## 验证方法

数据库测试仅连接本计划专有 PostgreSQL 容器中的 `test_xiaohe_` 库。0014 清理／迁移测试另建随机命名的专用库，结束后只删除该库；没有让可清空 fixture 连接现有业务库。对象删除用隔离替身验证，实际切换再核对 S3 版本状态及目标对象。

完整集合排除 MinIO integration 和两份原已存在但不在本次提交范围的未跟踪测试；不是整个仓库的测试。原生强杀恢复另行执行，模型脚本不会调用真实提供商或正式业务后台。

## 测试调整与独立审阅

旧 DSL 编译、旧正则路由及旧数据回填专属测试随其实现退役；公共身份／额度、API／CSRF、字段、真实回执、只读 child、原生快照、幂等和恢复继续保留。有限评测预算检查保留，依赖已退役 DSL 的旧实时评测入口明确拒绝执行。

新增标准包加载／独立表单测试先出现 4 个配置失败，切换后通过。新增认证续接不能提交测试先复现未拒绝调用，随后由服务端拦截；正常登录合并仍绑定新的原生快照。

唯一一次独立 reviewer 为 gpt-6-astra，无 Critical，2 项 Important 纳入同轮修复：含附件的 pending 提交必须在对象删除前拒绝清理；恢复共享提交层的错误回执、确定冲突及来源不可用保护测试。pending 附件测试先到达错误的删除调用，再修正清理门槛；没有为此调用真实业务后台。

清理测试覆盖显式目标库、拒绝新增原生记录、拒绝已受理／待核实附件引用、版本化 bucket 删除前拒绝、目标线程精确删除、身份／额度及非目标对象保留、删除失败后重试和重复清理。0015 拒绝未清理旧库，新安装升级移除旧结构；正常应用启动不调用清理入口。

## 最终结果

在专有测试 PostgreSQL 上执行：

```powershell
$env:XIAOHE_TEST_DATABASE_URL = (Get-Content .superpowers/sdd/2026-11-12-xiaohe-standard-skills-only/test-db.json -Raw | ConvertFrom-Json).url
.venv/Scripts/python.exe -m pytest tests/xiaohe --ignore=tests/xiaohe/integration --ignore=tests/xiaohe/test_conversion_signals.py --ignore=tests/xiaohe/test_uat_domain_sql.py -m 'not minio' -q --tb=short
```

- 完整非 MinIO 集合：**701 passed、1 skipped、10 deselected**，退出码 0。
- welcome、实际子进程强杀恢复和部署契约集合：**17 passed**。
- 上游 Agent／流式接口集合：**38 passed**。
- 修改范围 Ruff 检查与 `uv lock --check` 通过；锁文件和依赖未改变。
- 冻结源码提交 `72f420ffe120cf0e73cd985112ff45d7977691ad` 的 178 个镜像输入逐一匹配；禁网探针验证标准 Skill 读取、DeepAgents 工具循环和严格返回结果。真实配置的 ChatOpenAI 只验证组装，未推理。
- 隔离空库升级 0015、发布 1 个 native bundle／4 个字段策略通过；实际 WSL 清理后同样升级至 0015。
- 实际 WSL API／Worker 镜像与版本一致，健康及心跳通过；匿名 HTTPS v2 新会话、重复打开幂等、欢迎三方向和未扣消息额度通过。运行容器可读取 21 个标准包及三方向表单策略。

首次切换在任何删除前因应用账号缺少 `GetBucketVersioning` 权限停止，并恢复 0.1.7。随后创建仅限小鹤桶的临时维护账号，完成版本状态核验、精确删除及对象不存在核验后撤销；应用账号权限未扩大。

对象存储集成测试未完整运行；实际桶的维护操作已核验。未测试真实模型推理、会员登录、短信发送或正式留资提交，匿名会话检查不代替这些业务验收。最终发布证据位于归档中的 `verification.json`、`cleanup-verification.json` 和 `smoke-verification.json`。
