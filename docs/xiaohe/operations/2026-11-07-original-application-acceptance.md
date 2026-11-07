# 原采购申请受理与我的进展验收

2026-11-07，北京时间。状态：**原申请正常恢复受理、持久化回执及业务关联核对通过；用户确认“我的进展”显示本次需求和进展**。

## 用户操作与证据分层

后端完成隔离Java恢复后，Root先只读确认18084配置、冻结JAR及原申请仍pending，并说明“查询受理结果”会按原submission_key核对；若业务没有记录，会继续完成原申请，可能创建业务身份和受理记录。由用户在原隔离Chrome点击一次该按钮，没有由Agent代点或调用后台result/retry/submit。

用户先回复“显示已提交／已受理成功”，再按步骤打开顶部“我的进展”，回复“能看到本次需求和进展”。这些是**用户手动页面验收**，没有自动化截图或浏览器接管证据。浏览器该次原始HTTP回包未单独采集；本轮核对的是持久化回执与业务记录，未通过重放接口补造网络证据。

Root使用PostgreSQL及MySQL READ ONLY事务，只查私有锚点锁定的原申请，输出脱敏布尔值、计数和指纹，不输出用户标识、Cookie、电话或资料正文。

## 只读核对结果

| 检查 | 结果 |
| --- | --- |
| 原申请身份与submission_key | 与恢复前私有锚点相同，没有换新键 |
| 原资料 | payload指纹与pending时相同 |
| AI申请及草稿 | 均accepted，持久化receipt存在 |
| AI/Java回执 | submissionKey、kind、applicationId、onboardingId、revision、status逐项一致 |
| 业务身份 | 同scope只有1条Collaborator，AI用户/system/tenant/issuer/sub/sourceTenant匹配 |
| 受理幂等性 | 原键co_submission=1、co_revision=1；该scope co_application总数=1 |
| 申请与版本关联 | 回执applicationId、申请、版本记录、entity_id与revision相符 |
| 资料与附件引用 | 申请和版本中的public/request字段及files与原提交payload一致 |
| 当前进展 | recorded（已记录） |
| 专员与联系时间 | manager_id为空、expected_contact_at为空；未分派专员，不声称已联系或已分派 |
| 页面 | 用户确认受理成功，且“我的进展”显示本次需求和进展 |

脱敏证据：当前隔离更新目录 `original-receipt-readonly-verification.json`、`submission-readonly-diagnostic.json`；诊断与验收脚本分别为手工体验目录中的 `diagnose_submission_readonly.py`、`verify_original_receipt_readonly.py`。原申请与资料指纹同恢复前的留存值一致。正常用户操作产生了首次业务受理记录，未观察到重复申请；这不等同于本轮额外执行了重复点击/并发重试压力测试。

## 保持项与剩余范围

本轮Agent没有发送短信、触发模型、创建测试账号、代提交、手工改pending/accepted、制造回执或安排专员；没有更改部署、共享Nginx、SSO或正式开关。用户当前正常登录产生的既有认证记录与本轮只读操作分开记录，不将历史短信计数表述为0。

本次结论仅覆盖**这份原采购申请受理恢复及我的进展显示闭环**。真实附件上传/下载、其他方向完整留资、新用户注册及后续人工跟进未在本轮扩展验收；当前无实际专员分派。相关Java恢复证据见 `2026-11-07-reception-pending-runtime-diagnosis.md`，门户静态更新见 `2026-11-07-progress-static-update.md`，不将这些技术和手动证据合并宣称为正式环境上线。
