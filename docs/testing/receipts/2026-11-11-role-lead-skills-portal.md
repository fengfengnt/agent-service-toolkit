# 门户固定提交交付

- 前端固定 SHA：`9cdadb3dd74b97e073765938b05bd5ab7074b0f8`
- 分支：`dev-fengfeng`；本轮已本地提交，未推送、未部署，工作区干净。
- 版本保持 `0.1.27`；本轮不改变现有部署和正式开关。
- 本回执承接用户提供的交接文件；建议不覆盖用户此前保留单一提交按钮的要求。
- 临时日志已复制到下述归档路径；自动审批拒绝删除原临时目录，仅返回策略阻止，因此原目录一并保留。

---
# 三角色留资规则投影：门户交接回执

日期：2026-11-11。范围：门户 Task 7 开发与隔离验证，不更新后端、Runtime、Java、生产配置或正式开关。

## 固定依据与实现范围

- 门户起点：`f543a0f16dc9eafc243435e23887225277ce3a7d`，分支 `dev-fengfeng`。最终实现 SHA 随本次交付提供。
- 后端交接：`e19cac7ad510c8f45b2aba0e4754bdfe5943b967`；代码基准 `0fa7a1659e335b8d45cc2f19908e2ddff78731b4`。
- 接口依据：`D:/Code/yunhezhiyuan-agents/agent-service-toolkit/docs/contracts/xiaohe-lead-policy-v1.md`。三角色 JSON fixture 原样复制到 `frontend/tests/fixtures/lead-policy-v1.json`，不包含个人资料。
- 只对接待状态和独立表单 GET 加 `X-Xiaohe-Lead-Policy: 1`。不改 create、patch、actions、独立表单 submit 的请求结构，不改 AG-UI viewVersion 或 Runtime。

## 最终行为

1. 缺少 `leadPolicy` 时沿用旧后端展示；显式 null 只允许无 activeDraft 的接待状态，并清空面板，不继承另一账号或角色的投影。投影版本、字段引用、角色、限制与模板不合法时明确拒绝响应，不静默改用当前默认规则。
2. 有投影时，字段描述、分组、必填、枚举及限制来自该投影。继续集中填写，复用现有编辑组件，支持 textarea；按 Unicode 字符数及规则指定的 trim/lengthOnTrimmed 校验，联系电话不增加号码正则。未列入可编辑分组的历史资料继续保留。
3. 沿用用户此前确定的单一“提交信息”入口，不恢复已移除的“保存资料”“查看汇总”。按钮仍使用真实 revision、summaryVersion、requestId 与幂等键；不会伪造版本。已有完整汇总可直接确认，需保存时绑定本次 PATCH 的实际返回版本，外部修订会停止提交并提示核对。
4. “好的”“可以”和同轮修改后提交仍作为原聊天消息/动作发送，留给后端按语境处理，不增加前端强制汇总或二次点击。未保存表单的既有流程保护继续保留，不擅自把本地输入自动写入聊天或丢弃。
5. 保存、受控提交中、待核实、真实受理、明确失败分别显示。只有可信接待 accepted 状态展示受理提示；HTTP 202、模型文本、登录或保存完成均不能推导受理。不额外承诺人工已安排、审核通过或服务开通。标签、分组名和关键模板以普通文本渲染。
6. 可信“开始填写需求资料”按 owner＋会话＋消息去重，并等待匹配草稿、pendingRequestId 与 activeInput/runId。后到的可信系统消息不会提前丢失待处理提示，普通咨询会取消旧待办。重复通知、会话刷新及重连不覆盖当前窗口中的未保存输入或焦点，切换身份清空旧表单。

## 验证及兼容矩阵

| 组合 | 本次证据 | 边界 |
| --- | --- | --- |
| 旧后端／新门户 | 缺投影 GET、旧契约解析及原留资操作回归 | 使用旧形状协议桩，未切换现有部署 |
| 新后端／新门户 | 三角色完整投影 fixture、字段/必填/限制、null 清空、状态、通知与独立表单浏览器回归 | 协议桩和隔离浏览器，不声称已连上未部署的新后端 |
| 新后端／旧门户 | 后端交接中 opt-in 默认保持旧形状的固定 SHA 依据 | 本次未重跑后端 PG 测试或真实组合，保留其独立交接证据 |

实际命令使用本工程已有入口：

```text
npm test -- --maxWorkers=4
npm run typecheck
npm run build
npx playwright test tests/browser/reception-lead-policy.spec.ts tests/browser/reception-summary.spec.ts tests/browser/reception-capture.spec.ts tests/browser/reception-journey.spec.ts tests/browser/reception-files.spec.ts
npx playwright test --config playwright.xiaohe-sso.config.ts tests/browser/role-lead-notice.spec.ts tests/browser/reception-progress-auth.spec.ts tests/browser/reception-journey-auth.spec.ts
```

最终结果：624 项单测、36 项安全检查通过；typecheck、生产构建通过；浏览器 30 项留资回归和 18 项 SSO 组合通过。独立审查修复后，另外复跑相关 SSO 5 项，全部通过。浏览器验证使用隔离协议桩，不访问真实业务提交。

已观察到预期失败再修复：新投影被旧解析器拒绝、能力头缺失、按旧默认规则校验、通知在读取延迟时被丢弃、activeInput 绑定另一 Run、旧模型句式被扩写为业务提示、独立表单叠加旧必填。一次独立审查发现两项重要问题：确认前保存失败与确认后回执未知的状态混淆，以及同账号切换草稿时迟到错误串用；新增 4 项浏览器回归先失败、修复后全部通过。

一次并行检查中的代理测试超时在完整复跑中未再出现，没有调整无关代理逻辑或测试超时。最终浏览器复跑发现一个测试夹具的通知与外部修订顺序存在竞争，已将外部修订置于通知之前，随后 30 项全部通过；没有为此修改生产逻辑。

原始日志及执行记录归档至 `C:/Users/70367/AppData/Local/Temp/xiaohe-role-lead-policy-portal-20261111-evidence/`。固定前端 SHA 及最终交付回执位于 `C:/Users/70367/AppData/Local/Temp/xiaohe-role-lead-skills-portal-receipt-20261111.md`。

## 未验与部署边界

后端本次尚未部署；未执行真人账号登录、短信、真实模型语义评测、真实需求提交、认证重放或归并，不将 JSON 示例／协议桩／匿名健康写为真人窗口通过。没有新增前端依赖或个人资料持久化存储，不恢复整页 F5 前已销毁的未保存内存输入；保留现有登录恢复机制。正式浏览器组合与真实 LLM 对口语确认的判断仍需后端上线后另行验收。

