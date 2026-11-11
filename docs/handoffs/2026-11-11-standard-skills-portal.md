# 门户标准 Skill 与动态表单配套交接

状态：**后端与门户各自隔离验证通过，真实新后端组合待验**。已收到并核对维护者的[固定提交回执](2026-11-11-standard-skills-portal-receipt.md)；本聊天仅更新后端仓库记录，未修改门户或 Java，未发送跨聊天任务，未部署。

后端实现基点：`ea499d5`。请保留门户现有 v1 适配与未提交工作，按实际消费链做最小变更。正式业务后端载荷保持原协议，无需额外 Java 改造。

## 接口输入

以[精确协议](../contracts/xiaohe-lead-policy-v2.md)和[完整 JSON fixture](../../tests/xiaohe/contracts/lead-policy-v2.json)为准。两个 GET 在原响应上增加 `leadPolicy`，请求必须恰好一个 `X-Xiaohe-Lead-Policy: 2`：

- `/ai-api/v1/reception/conversations/{conversation_id}`；无活动草稿时为 null。
- `/ai-api/v1/reception/application-form?sourceApplicationId=...&mode=edit|new`；独立表单读取不创建聊天草稿。

支持动态数量的 `groups: [{id,name,fields}]`，复用现有字段输入组件和规则描述。不要为第四步新增 round4 枚举；旧 round1—round3 路径继续兼容。已有字段可组合／重排，组外资料保留。`requiredFields` 为最终门槛，不能因当前分组未展示而清空或放宽。

新路径的聊天保存：

```http
PATCH /ai-api/v1/reception/drafts/{draft_id}/form
Content-Type: application/json
```

```json
{"formId":"leadPolicy.formId","expectedRevision":2,"fields":{"city":"苏州"}}
```

返回原 DraftView，成功后重新 GET。修订失效为 DRAFT_CONFLICT；同修订下展示组合失效为 ACTION_STALE，均 409。两者都必须保留用户输入，提示重新读取并核对合并，禁止自动重试覆盖。

独立申请继续 `POST /ai-api/v1/reception/application-form/submissions`。v2 请求加上述能力头和 `policyFormId: leadPolicy.formId`。**现有 `formId` 仍是附件上传会话 UUID**，两者分别传送；原 definitionBundleId、expectedApplicationRevision、files、clientSubmissionId 均保留。mode=new 的 applicationRevision 为 0，实际请求 expectedApplicationRevision 仍为 null。

结果未知先查询 `/application-form/submissions/{clientSubmissionId}`，原键复用，不创建第二份申请。聊天确认继续走原确认动作，使用实际已展示汇总和修订。登录或打开表单不触发提交。

## 客户端保护

关注 `frontend/src/chat/reception/{contracts,api,fields,store,presentation,application-form}.ts` 与 `ReceptionFieldEditor.vue`、`ReceptionRoundCard.vue`、`ReceptionSummaryPanel.vue`、`ApplicationFormDialog.vue`，实际改动由消费链决定。

- 响应缺少 leadPolicy／version 不受支持：回退已有 v1／集中表单。不要凭空制造分组或要求旧后端提供新字段。
- 有未保存输入时接收新 formId、groups 或 revision：缓存新服务端版本，保留本地值与焦点，用户核对后更新。保存错误保留编辑器内容。
- owner、会话或角色改变：隔离各自脏输入，不能显示或提交前一主体的私有资料；切换回来再提示是否恢复。
- actions 与真实回执控制按钮和状态提示；模型答复、child 返回值不代表受理。遵守现有 system_notice 面板去重，不反复打开或丢失输入。
- 不展示内部 Skill 文件名、工具策略、完整 checkpoint 或授权上下文。请求的 Cookie、Origin、CSRF、no-store 策略保持。

## 交接验收要求（已获得隔离验证回执）

先新增红灯，再实现：`tests/unit/reception-lead-policy-v2.test.ts` 覆盖协议解析、v1 回退、3→4 分组；`reception-form-rebase.test.ts` 覆盖脏输入、owner／方向切换、同修订 formId 失效；浏览器 `reception-lead-policy-v2.spec.ts` 覆盖焦点、冲突保留、面板去重、未知提交沿原键恢复。

固定验收断言：

```ts
expect(editor.values).toEqual(unsavedValues)
expect(serverSubmissionCount).toBe(1)
expect(activeFieldGroups).toHaveLength(4)
```

在门户工作区执行：

```powershell
npm exec vitest run tests/unit/reception-lead-policy-v2.test.ts tests/unit/reception-form-rebase.test.ts
npm run typecheck
npm run build
npm exec playwright test tests/browser/reception-lead-policy-v2.spec.ts
```

Playwright 使用隔离 fixture 服务，脚本路径按门户现有约定调整并在回执注明。回执需包含 commit、实际命令／结果、四组截图或浏览器报告、未验项；后端结合回执才能标记前后端组合通过。真实模型、短信、生产登录和部署另行验收。


## 2026-11-11 回执核验

- 前端固定提交：`485a0e3a4ae23fddd2bede4d68220aaa4038014b`，分支 `dev-fengfeng`。已核对提交存在、内容包含 v2 适配与测试，当前门户 HEAD 与该 SHA 一致且工作区干净。版本为 `0.1.27`，未发布。
- 后端协议基点为 `ea499d5019e0cab373f1ef87ab59903c995ad891`，后续修复基点为 `3a399ef12f07327f5e14ef094cc0f15507e9d3fb`。两端当前完整 fixture 字节一致，SHA256：`9bc7c5f113d888b9015c04e3e4bafd04423a61cc92d3f75c59cef10e3988c69c`。相对最初协议基点仅不透明的 skillVersion 值更新，投影字段、分组和校验规则一致。
- 已读取归档日志，核对 637 项单测（59 个文件）、36 项安全检查、38 项留资浏览器、20 项 SSO 浏览器通过；typecheck 日志无类型错误，构建日志显示 SPA 编译完成。两份 HTML 报告及截图文件存在。本聊天未重新运行门户测试或进行真人界面验收。
- 门户保留单一“提交信息”入口，不恢复额外用户按钮；原汇总／修订检查继续保持。浏览器测试使用本工程的 tests/browser 路径，独立表单用例复用隔离 SSO 服务。
- Task 8 的前端实现、隔离检查和固定提交回执已完成。当前结果证明两端各自实现及协议桩兼容，**未启动真实新后端组合**；真实组合、模型语义、短信、正式业务和部署仍待单独验收。

原件按字节归档，保留维护者关于单一入口、内存缓存、测试环境及未验范围的说明；归档中的命令和操作描述均为交付记录，本聊天未据此执行部署或清理。
