# 小鹤留资规则投影 v2

状态：后端开发实现，未部署，门户尚未回执。完整 JSON 示例见 [fixture](../../tests/xiaohe/contracts/lead-policy-v2.json)。

## 读取与兼容

恰好一个 `X-Xiaohe-Lead-Policy: 2` 时，下列 GET 在原响应上增加 `leadPolicy`。值 `1` 继续 v1；缺省、重复头、逗号合并值、未知值保持原形状。

- `/ai-api/v1/reception/conversations/{conversation_id}`：无活动草稿为 `null`，有草稿为 `scope: draft`。
- `/ai-api/v1/reception/application-form?sourceApplicationId=...&mode=edit|new`：`scope: application`。不创建聊天草稿，不调用模型。

`version` 固定为 2；`fieldRuleVersion` 为实际字段规则，旧草稿保持原版本；`interactionVersion`、`skillVersion` 固定展示所依据的版本。`formId` 由服务端生成，绑定主体、scope、修订及组合，客户端视为不透明字符串。草稿有 `draftRevision`、`applicationRevision: null`；独立表单反之。`mode: new` 尚无正式申请修订，`applicationRevision: 0`，原 `expectedApplicationRevision` 仍为 null。

`fields` 为可编辑字段描述，`groups` 为 `{id,name,fields}` 数组，组数、顺序、字段组合不受 round1—round3 限制。`requiredFields`、`notices` 与实际服务端规则一致。组外已有资料继续保留；不能通过新组合修改门槛或只读字段。`pending` 为尚未完成的说明事项，`actions` 来自真实草稿状态。显示分组不等于提交前置门槛。

非字段说明只有实际助手消息落库、文本包含对应说明且关联本 Command 的服务端组合时才记录完成，保留 Message 和 Command 引用。模型自报、未发布候选不算完成；不同说明默认未完成。字段完成依据已存资料，提交依据原业务回执。

## 草稿保存

`PATCH /ai-api/v1/reception/drafts/{draft_id}/form`，仍使用现有 Cookie、Origin、CSRF 和归属检查：

```json
{"formId":"服务端返回值","expectedRevision":2,"fields":{"city":"苏州"}}
```

返回原 DraftView；只合并传入字段，不删除未展示资料。保存后重新 GET 获取修订和 formId。已受理／提交中草稿不能修改。未知字段、组外字段为 `INPUT_INVALID`（422）；旧修订为 `DRAFT_CONFLICT`（409）；formId 不匹配为 `ACTION_STALE`（409）。多个条件均失效时先检查修订。前端保留本地输入并提示刷新合并。

聊天最终提交继续使用原确认动作及实际汇总校验，不增加客户端可伪造的确认来源。

## 独立申请提交

实际既有路径为 `POST /ai-api/v1/reception/application-form/submissions`。声明 v2 时保留完整原请求，并增加必填 `policyFormId`，值取 `leadPolicy.formId`：

```json
{"formId":"客户端附件会话 UUID","policyFormId":"服务端表单版本标识","clientSubmissionId":"稳定客户端请求键","sourceApplicationId":"10","mode":"edit","expectedApplicationRevision":3,"definitionBundleId":"原 GET 返回值","fields":{"productDescription":"更新说明"},"files":[]}
```

保留既有 `formId` 是因为它已用于独立附件上传／清理；不把它改成另一个含义。v1 不要求 `policyFormId`。v2 先校验原 definitionBundleId／expectedApplicationRevision，再校验 policyFormId；后端正式业务载荷不增加该字段。提交结果未知沿同一 clientSubmissionId 查询；不得换键重建。已有请求回执优先恢复，不因后来规则更新重新出站。

公共错误沿用 `{error:{code,message,retryable},requestId}`；其中 `message` 为既有中文提示，`requestId` 为服务端请求标识。认证、CSRF、归属、缺必填和文件校验沿用原服务。
