# 正式提交的确认入口与约束

Labels: wayfinder:grilling
Type: grilling
Status: resolved
Assignee: 当前对话
Blocked by: 02

## Question

三份 Skill 是否保留现有“Artifact 按钮确认＋明确文字确认”两种正式提交方式，并统一要求用户先查看且确认当前版本的汇总？

## Context

现有实现支持按钮及语义确认，绑定草稿、修订和汇总版本。来源：src/xiaohe/agents/contracts.py 的 ConfirmationEvidence、src/xiaohe/agents/nodes.py 的 validate_confirmation_evidence、src/xiaohe/reception/submission.py 的 confirm_locked_draft。

原讨论曾建议排除单独“好的／可以”，此限制已由下述用户决策纠正，不能作为后续设计依据。是否必须先展示汇总另见后续决策。

## Answer

2026-11-11，用户确认：“好的”“可以”也可以构成提交确认，用户不需要使用正规句式；以 LLM 识别的用户提交意图为准。

三份 Skill 不设置短语黑名单或固定确认句式。LLM 应结合当前资料、上一轮问题和近期对话判断用户是否同意正式提交；认定为提交时，语义确认即有效，不因措辞简短而拒绝。现有必填资料和真实受理结果约束不因此改变。

本票确定口语确认的有效性，不把“资料齐全但尚未展示汇总时能否直接提交”默认为已确认；该顺序问题另行讨论。仅更新设计记录，未修改实现。
