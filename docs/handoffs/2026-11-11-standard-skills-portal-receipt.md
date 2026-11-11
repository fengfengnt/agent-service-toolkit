# 门户标准 Skill v2 固定提交交付

- 前端固定 SHA：`485a0e3a4ae23fddd2bede4d68220aaa4038014b`
- 后端实现基点：`ea499d5019e0cab373f1ef87ab59903c995ad891`
- 分支：`dev-fengfeng`。本地提交；工作区干净；未推送，未部署，版本保持 `0.1.27`。
- 最终结果：637 项单测、36 项安全检查、38 项留资浏览器和 20 项 SSO 浏览器通过，typecheck／build 通过。
- [留资浏览器报告](C:/Users/70367/AppData/Local/Temp/xiaohe-standard-skills-portal-20261111-evidence/browser-report-reviewed/index.html)
- [SSO 浏览器报告](C:/Users/70367/AppData/Local/Temp/xiaohe-standard-skills-portal-20261111-evidence/sso-report-reviewed/index.html)
- 用户此前明确要求单一提交入口，本轮不恢复旧文档提到的额外按钮。当前验收为隔离协议桩，不能标记真实新后端组合通过。

---
# 标准 Skill 与动态表单：门户交接回执

日期：2026-11-11。范围：门户 v2 接入与隔离验证；不修改后端、Java、Runtime、生产配置或正式开关。保留已有 v1 适配与单一“提交信息”入口。

## 固定依据

- 门户基线：`9cdadb3dd74b97e073765938b05bd5ab7074b0f8`，分支 `dev-fengfeng`。最终实现 SHA 随外部交付回执提供。
- 后端基点：`ea499d5019e0cab373f1ef87ab59903c995ad891`，交接 `agent-service-toolkit/docs/handoffs/2026-11-11-standard-skills-portal.md`；协议 `docs/contracts/xiaohe-lead-policy-v2.md`。
- 完整无个人资料 fixture 原样复制到 `frontend/tests/fixtures/lead-policy-v2.json`，两端 SHA256 均为 `9bc7c5f113d888b9015c04e3e4bafd04423a61cc92d3f75c59cef10e3988c69c`。
- 版本保持 `0.1.27`，本轮没有发布或新增依赖。

## 行为与协议

1. 接待状态和独立表单 GET 各发送一个 `X-Xiaohe-Lead-Policy: 2`。识别缺键、null、v1、v2与未知版本；缺键／未知版本回退已有集中表单，已支持版本的畸形投影明确拒绝。v2 校验 scope、方向、修订及分组引用。
2. 动态 `groups` 复用已有字段组件，支持四组及重排，不新增 round4。必填只取有效规则，组外必填不会因隐藏而消失；既有组外资料只读保留。
3. v2 聊天保存使用 `/drafts/{id}/form`，传展示所绑定的 `formId`、真实 expectedRevision 和修改字段，成功后 GET 更新。v1／旧后端继续原 PATCH。DRAFT_CONFLICT 和 ACTION_STALE 均保留输入、读取最新版本并提示核对，不自动重试覆盖。
4. 脏输入遇到新 formId、groups 或 revision 时，编辑器保留原分组、值及焦点，缓存最新服务端状态；核对后保留或放弃。移出新分组的脏字段需要先留副本再放弃，不能静默删除或向新表单强行提交。
5. 聊天内存编辑按 owner＋会话＋角色隔离，回到原主体先核对；独立表单按 owner＋来源申请＋mode 隔离。认证恢复保留 formId，内存核对标记不写入持久存储。成功保存只清除服务端已确认的对应编辑。
6. 独立 v2 提交增加 `policyFormId` 与能力头，原附件会话 UUID `formId` 保持独立；保留 definitionBundleId、expectedApplicationRevision、files、clientSubmissionId。mode=new 的投影修订为 0，请求修订仍是 null。只发送当前组内字段，后端合并组外历史资料。
7. 未知提交先查原 clientSubmissionId，仅服务端明确 not_found 时复用原请求重发。actions 和真实接待状态控制操作；真实汇总／修订校验、状态提示、可信 system_notice 去重及 Cookie／Origin／CSRF／no-store 策略沿用。模型或工具输出不能推导受理。

一次独立审查发现的三项重要问题均补充失败测试后修复：聊天核对只合并真实修改，不回退未编辑的服务器字段；回切角色及认证恢复后，移出分组的未保存值仍可查看和复制；独立申请核对时，放弃加载服务器新附件，保留则合并本地增删。没有未处理的审查项。全量 SSO 还发现新增内存缓存清空认证临时编辑的问题，已保留认证过程中的受控临时编辑，主体切换继续隔离。

## 验证

以下命令均在 `D:/Code/yunhezhiyuan-portal/frontend` 运行。最终修复后 637 项单测（59 个文件）、36 项安全检查通过，typecheck 和生产构建通过；38 项留资浏览器回归、20 项 SSO 浏览器回归全部通过。

```text
npx vitest run tests/unit/reception-lead-policy-v2.test.ts tests/unit/reception-form-rebase.test.ts
npm test -- --maxWorkers=4
npm run typecheck
npm run build
npx playwright test tests/browser/reception-lead-policy-v2.spec.ts tests/browser/reception-lead-policy.spec.ts tests/browser/reception-summary.spec.ts tests/browser/reception-capture.spec.ts tests/browser/reception-journey.spec.ts tests/browser/reception-files.spec.ts --reporter=list,html
npx playwright test --config playwright.xiaohe-sso.config.ts tests/browser/role-lead-notice.spec.ts tests/browser/reception-progress-auth.spec.ts tests/browser/reception-journey-auth.spec.ts --reporter=list,html
```

采用本工程现有 `tests/browser` 路径，独立表单 v2 放入既有 `reception-progress-auth.spec.ts` 以复用隔离 SSO 服务。能力头、新 PATCH、独立 policyFormId、缓存核对、角色恢复、同修订组合失效、认证恢复和成功保存清理均观察失败后修复；未知提交浏览器断言服务端仅提交一次。浏览器报告包含四组、焦点／冲突及恢复截图。

测试执行中曾因普通构建与 SSO 回归并行覆盖共用静态产物而中断一轮，已改为先完成普通构建，再独立重建 SSO 测试产物；没有修改业务逻辑或扩大超时来掩盖该问题。

日志和浏览器报告归档：`C:/Users/70367/AppData/Local/Temp/xiaohe-standard-skills-portal-20261111-evidence/`。留资报告为 `browser-report-reviewed/index.html`，SSO 报告为 `sso-report-reviewed/index.html`，对应截图在 `browser-results-reviewed/` 与 `sso-results-reviewed/`。最终固定 SHA 回执：`C:/Users/70367/AppData/Local/Temp/xiaohe-standard-skills-portal-receipt-20261111.md`。

## 执行取舍

- 复用用户已经指定且开始时干净的 `dev-fengfeng`；未新建 worktree，若有并行开发需另外隔离。
- 用户此前明确只保留“提交信息”，本轮不恢复旧交接提及的两个额外入口；原接口汇总校验继续保留。
- Windows 下直接执行本工程命令并保存日志，未运行会操作后端工程的 bash 工作区脚本；不以门户结果代表该脚本通过。

| 组合 | 验证范围 | 限制 |
| --- | --- | --- |
| 旧形状／v1 后端＋新门户 | 缺投影、v1 三角色与原保存／提交回归 | 隔离协议桩 |
| v2 后端协议＋新门户 | 固定 fixture、动态分组、新保存、独立提交、版本核对及未知恢复 | 隔离协议桩与浏览器，未启动新后端 |
| 未知投影版本＋新门户 | 回退旧表单，不展示未知内部信息 | 不尝试使用未知写协议 |

## 未验项

未执行真实后端组合、模型语义、短信、真人生产登录、真实申请提交、部署或认证重放。内存编辑缓存只在当前窗口有效，不增加整页 F5 后的个人资料持久化；登录恢复仍使用既有受控机制。上述隔离证据不能标为真人窗口或已部署的新后端组合通过。

自动审批拒绝删除本任务临时日志目录，仅返回“策略阻止”；原目录保留，归档和代码交付不受影响。
