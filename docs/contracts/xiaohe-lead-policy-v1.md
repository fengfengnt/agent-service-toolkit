# 小鹤留资规则投影 v1

状态：后端实现中的 opt-in 扩展；不表示已部署或门户已接入。依据[已批准设计](../superpowers/specs/2026-11-11-role-lead-skills-design.md)。

## 请求与兼容

仅以下两个 GET 在**恰好一个**请求头 `X-Xiaohe-Lead-Policy: 1` 时增加顶层 `leadPolicy`：

- `/ai-api/v1/reception/conversations/{conversation_id}`
- `/ai-api/v1/reception/application-form?sourceApplicationId=...&mode=edit|new`

未声明、未知值、重复头继续原响应形状。会话没有 activeDraft 时 `leadPolicy: null`；已有草稿时投影其固定字段规则。独立表单按返回的 `definitionBundleId` 对应规则投影，不新建聊天草稿。

权限、CSRF、Cookie 与 `private, no-store` 保持原约定。新头仅加在这两类读取请求，不加到 Runtime，不改 AG-UI viewVersion，不给保存／确认／正式提交请求增加字段。旧后端没有 `leadPolicy` 时，门户沿用当前展示。

## leadPolicy 结构

| 属性 | 类型／含义 |
| --- | --- |
| `version` | 固定整数 `1` |
| `direction` | `buyer`、`organization`、`supplier` |
| `fieldRuleVersion` | 有效字段快照的 SHA-256；是规则版本，不是草稿 revision 或服务版本 |
| `interactionVersion` | 交互规范 SHA-256；在途 Command 使用自己的固定版本，读取投影不更新它 |
| `fields` | 当前分组内的字段描述数组，不包含资料值或提示词 |
| `rounds` | 原 FieldRound 结构：`id`、`name`、`fields`；集中展示，不强制聊天逐轮填写 |
| `requiredFields` | 当前规则必填 ID 数组，服务端校验为准 |
| `notices` | 下表的关键模板；只能由实际业务状态选用 |

字段描述包含 `id`、`label`、`inputType`（`text`／`textarea`／`select`／`multiselect`）、`validator`、`options`、`limits`。`limits` 固定为 `maxLength`、`minLength`、`maxItems`、`itemMaxLength`、`trim`、`nonblank`、`lengthOnTrimmed`。

`validator` 是有限的内置校验种类，不是脚本：`generic` 接受字符串或不重复的字符串列表，分别使用长度／列表限制；`text` 仅字符串；`enum` 必须命中 options；`email` 沿用原邮箱校验；`channels` 是非空 options 列表且“暂无”不可与其他项混选。所有字段仍可传 null 表示清空，再由必填检查决定能否提交。`trim` 控制保存时去首尾空白，`lengthOnTrimmed` 控制长度检查是否按去空白后的值计算，`nonblank` 不接受纯空白。联系电话保持原文字符串，不增加号码格式校验。

此版本完全沿用现有字段和必填；门户复用现有编辑组件消费规则，不建设任意字段设计器。后台已有、但未列入旧草稿可编辑分组的资料仍须保留，不因面板未展示而删除。

## 关键模板与状态

| key | 模板 | 前提 |
| --- | --- | --- |
| `prepare` | 请在资料面板填写本次{direction}资料。 | 已准备匹配角色草稿 |
| `missing` | 正式提交还需：{missing}。 | 真实缺项标签，用“、”连接 |
| `saved` | 本次资料已保存，尚未正式提交。 | 真实保存完成 |
| `submitting` | 正在提交本次资料。 | 正在执行受控提交 |
| `unknown` | 正在核实提交结果，请稍后查询，无需重复提交。 | 尚无确定回执 |
| `accepted` | 本次需求已受理，可在“我的进展”查看状态。 | 真实业务回执确认 |
| `failed` | 本次提交未完成。{guidance} | 明确失败及可公开处理提示 |

`direction` 的显示值为采购、组织机构、供应商。模板只允许这些占位符，不执行模板内的任意表达式。不要把资料已保存、模型自由文本、登录完成或 HTTP 202 当成已受理；受理不推导审核通过、服务开通或人工已安排。

## 门户交互

- 保留 `system_notice` 的“开始填写需求资料”作为已有可信采集标记；不从模型答复中识别 Artifact 打开命令。
- 标记到达后，等 owner／会话／角色／草稿匹配的最新接待状态就绪再打开或重开。按 owner＋会话＋消息去重；普通咨询不反复打开。
- 不覆盖未保存输入或焦点；并发修订冲突时保留用户输入并提示重新核对当前状态。不能把另一账号或另一角色的数据搬入当前面板。
- “好的”“可以”等提交意图由后端 LLM 结合上下文识别；资料齐备即可直接提交。前端不再强加先看汇总／二次点击，查看汇总和原确认按钮仍可用。
- 同轮明确补资料并提交时以后端最终校验快照为准；前端不代造 summaryVersion。按钮继续发送用户实际查看的汇总版本。

契约示例见 `tests/xiaohe/contracts/lead-policy-v1.json`；后端行为测试见 `tests/xiaohe/test_lead_policy_api.py`。门户实现、人工窗口、真实模型语义质量分别验收。
