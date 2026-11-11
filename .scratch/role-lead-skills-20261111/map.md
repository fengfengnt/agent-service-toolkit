Labels: wayfinder:map

## Destination

形成采购、组织机构、供应商三份留资 Skill 的设计，明确步骤、表单要求、话术边界及共用执行机制。设计决策已闭环，后续实施进展见[实施计划](../../docs/superpowers/plans/2026-11-11-role-lead-skills.md)。

## Notes

- 使用 superpowers:brainstorming、mattpocock-skills:wayfinder、grilling、domain-modeling。
- 使用本地 Markdown tracker；开放决策在 issues 目录查询，不在本索引重复正文。
- 延续现有 LangGraph／YAML／Pydantic 与角色草稿隔离设计，认证、权限、提交确认、幂等和真实受理结果仍由服务端保证。
- 设计阶段保留现有未提交工作和生产状态，不写产品代码；用户后续选择本对话逐项实施，开发与隔离验证另按计划记录，未授权部署、短信或真实模型调用。

## Decisions so far

- [自然表达与关键提示的边界](issues/01-critical-notices.md)：普通表达可自然组织，关键提示统一且不可省略或编造。
- [三方向正式提交的最低资料门槛](issues/02-minimum-required-materials.md)：保留现有必填配置，后续增减由产品经理另行确认。
- [正式提交的确认入口与约束](issues/03-submission-confirmation.md)：口语也可构成确认，以 LLM 对上下文中提交意图的识别为准，不设固定确认句式。
- [尚未展示汇总时的提交顺序](issues/04-summary-before-confirmation.md)：资料齐备且识别为同意提交时直接提交，不强制先展示汇总并再次确认。
- [Skill 更新后既有草稿的字段规则](issues/05-existing-draft-version.md)：新草稿用新版字段规则，已有草稿保留原字段要求。
- [通用交互规范更新对既有草稿的影响](issues/06-interaction-policy-updates.md)：后续用户轮次使用新版交互规范；字段不变，执行中动作与已发出的提交不换版本。
- [三方向留资 Skill 设计整体定稿](issues/07-design-approval.md)：用户以“定稿”批准整体设计，接续制定实施计划；未授权开始编码、迁移或部署。

## Not yet specified

当前范围已收敛到[三方向留资 Skill 定稿设计](../../docs/superpowers/specs/2026-11-11-role-lead-skills-design.md)，用户已整体批准；独立审阅未发现必须补答的重要问题。本节无未决设计问题，后续开发拆解见[实施计划](../../docs/superpowers/plans/2026-11-11-role-lead-skills.md)，不在此追加实现任务。

## Out of scope

- 本地图不记录实施任务；部署或真实模型／短信／业务验收仍不包含在本轮实施范围。
- 本轮不增减必填项；产品经理后续确认的资料门槛变更另行处理。
