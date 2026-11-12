# 小鹤接待与独立专业 Agent

自 0.1.8 起，业务只使用标准 Skill 与 DeepAgents 原生执行链。外层 LangGraph 保留执行、等待输入、checkpoint 和写栅栏；业务图为 `prepare_context → business_agent → finalize`。接待识别方向并交接，采购、社群组织、供应商各自处理业务。

## 技能与配置位置

| Agent | 标准包数量 | 配置 |
| --- | ---: | --- |
| 接待 | 0 | `deep-runtime/reception-policy.md` 与受控交接工具 |
| 采购 | 8 | 七份答疑与 `buyer-lead-capture` |
| 社群组织 | 8 | 七份答疑与 `organization-lead-capture` |
| 供应商 | 5 | 四份答疑与 `supplier-lead-capture` |

资源根为 `resources/xiaohe/`，标准包位于 `skills/<name>/SKILL.md`，包含 name／description frontmatter、执行说明及包内 references。节点候选技能、认证要求和工具依赖由 `deep-runtime.yaml` 显式绑定；`deep-runtime/common-policy.yaml` 管理关键状态提示。

答疑正文在各包的 `references/answer.md`；留资字段规则在三份留资包的 `references/field-policy.yaml`。字段、存储范围、必填和正式业务接口仍由代码和可信策略校验。现有字段可由主 Agent 通过 `compose_form` 自由组合为 v2 groups，不需要增加相应 workflow 节点。

旧嵌套 Skill、catalog、lead-skills 资源、workflow YAML、旧编译器和旧执行器已移除。没有 `XIAOHE_DEEPAGENTS_ENABLED` 开关或旧执行器回退；缺少合法标准包时阻止启动。

## 修改与恢复

修改标准包或绑定后需要正常发布／重启，没有热重载接口。新 Command 选择当前原生包，已执行 Command 和重试沿原不可变快照恢复，不能替换为最新文件。草稿固定 `field_policy_id`，后续修改引导不改写已有草稿的字段规则。

草稿读取、保存、汇总、提交及独立申请表单均读取标准字段策略。现有 wire `definitionBundleId` 表示原生包指纹，字段名保持；`fieldLayout` 支持现有界面，v2 `leadPolicy` 提供实际展示分组和版本。冲突返回现有错误码，客户端保留脏输入并重新核对。

字段候选需要本轮证据，未登录复用认证。登录不是提交同意，认证续接不能直接调用 submit_lead；随后用户的新操作才可提供提交确认。用户在明确提交语境中的“好的”“可以”可以确认提交；同轮明确修改并同意时提交最终已校验资料，不强制先展示汇总二次确认。

结果未知沿原提交键核对；错误回执不能把草稿标为受理，确定冲突回到可编辑状态并使旧确认失效。只有真实业务回执产生受理状态，不把受理说成审核通过或人工已联系。

## 权限与宿主边界

主业务 Agent 统一调用授权工具保存和提交。child 显式绑定技能和只读工具，只返回处理结果，不能继承父 Agent 写入权限、执行 Shell 或自行再次委派。Skill 内容不作为权限判断来源。

消息、动作和专业入口继续使用认证、CSRF、可信系统／租户、当前任务所有权与修订检查，不能由浏览器自报身份。交接只包含目标方向必要资料；认证凭据、内部推理和私有附件原文不进入模型或外部助手消息。

`Actor`／`TrustedSystem` 与 BusinessBackend 保留身份和正式业务的宿主边界；不改变其他 toolkit Agent 或独立 Copilot Runtime。报价、政策、案例与效果承诺需要已确认资料，专业常识标明一般建议。

## 清理与版本边界

0015 要求先显式清理旧数据，再移除旧结构；正常启动不会自动销毁业务记录。当前实施范围和隔离验证见[标准 Skill 唯一执行链验证](../testing/xiaohe-standard-only.md)，清理设计见[设计文档](../superpowers/specs/2026-11-12-xiaohe-standard-skills-only-design.md)。旧版本验收文档仅保留历史事实。

清理维护账号须具备目标桶的 `GetBucketVersioning`、`ListBucket`、`GetObject`、`DeleteObject` 权限；版本化桶会在删除前被拒绝。本次 WSL 使用临时桶级账号并在完成后撤销，不能因此扩大应用账号的常驻权限。

清理后的旧会话、草稿、附件和执行状态不恢复；不能把仅支持旧结构的 0.1.7 镜像直接切回。身份、认证、额度、其他业务系统与既有备份不属于本次删除范围。
