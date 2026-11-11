# 三方向正式提交的最低资料门槛

Labels: wayfinder:grilling
Type: grilling
Status: resolved
Assignee: 当前对话
Blocked by: 01

## Question

三份留资 Skill 应保留当前最低提交门槛，还是将采购需求、供应商主营产品／服务等业务诉求也纳入必填？组织机构现有六项必填是否保持？

## Context

当前事实从各角色 YAML 的 required_fields 读取：

- 采购：企业名称、联系人、联系电话。
- 组织机构：组织名称、会员规模、覆盖区域、线上渠道、联系人、联系电话。
- 供应商：企业名称、联系人、联系电话。

来源：resources/xiaohe/workflows/buyer.yaml、organization.yaml、supplier.yaml。具体字段不在此复制成第二份可执行配置；本票只记录业务门槛决策。

## Comments

2026-11-11：正在与用户确认，未修改任何 required_fields、前后端表单或生产数据。

## Answer

2026-11-11，用户确认：不新增必填内容，后续由产品经理确认必填项需求。

因此本轮沿用三角色现有 required_fields，不因封装 Skill 而增减必填门槛。Skill 与表单从同一份当前配置读取要求；模型不得把选填资料自行升级为必填，也不得省略当前必填缺项提示。产品经理后续确认的变更另行评审，不作为本轮设计的阻塞。
