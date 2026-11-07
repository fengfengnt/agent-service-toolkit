# 登录后留资候选的隔离更新记录

2026-11-07，北京时间。状态：**隔离切换及技术核验通过，现有会员登录态保留；新版留资与真实附件操作待用户手动验收**。

## 变更范围

前端采用接待Owner冻结的v2候选，后端采用v3的0008兼容补丁。登录后只在视图中收起过期认证通知和旧输入问题，保留服务端聊天；按现有草稿状态续接采集、打开右侧资料面板；采购商标题为“采购商信息”；选填材料可选择或拖拽单文件，再点击上传，仅存储、不解析。

本次不整体替换为新DSL工作树，不迁移数据库，不改变任何wire schema、SSO登记、身份键、业务域或正式开关。后端产品源码仅在旧 `reception/flow.py` 添加确定性标签采集；旧 `ROUNDS` 与原受理/确认逻辑保留。明确标签示例为“企业名称：测试企业；行业：制造业；企业规模：500人；城市：杭州”。拒答、未知或备选身份不写入，字段采集不自动推进或提交。一般自由口语提取继续受现有受控模型限制，不能声称已通过。

## 来源、审阅与回归

- 前端v2目录：`updates/20261107-login-capture-v2`，6个产品源文件及3个新增测试与冻结指纹匹配；与前次退出修复冻结版本的94/95个生产源文件全量对比，仅这6项产品变化。Owner全量380项单元、36项安全检查、typecheck及构建通过；Root另跑3文件10项，独立审阅另跑4文件14项，均通过。
- 后端v3目录：`updates/20261107-login-capture-v3`。0008基础flow SHA为 `31e633c8ebb890a898b8f2289ab07bf516ca80f36e720e02a7cf2c94f6d32ac8`，最终flow SHA为 `a8025082e6f1647f95bd07e9edf6bb6ae94135eda014ae7495c62c40dc7a0f04`。补丁check、实际应用结果与完整候选逐字节匹配。
- v1/v2先后发现未保存输入阻止同草稿续接、拒答/未确定值被当资料保存两项Important。Owner分别补充红→绿回归；最终独立增量审阅两项均关闭，无遗留Critical/Important/Minor。旧候选和失败证据均保留，没有覆盖归档。
- Root在实际最终0008包中运行4文件：`test_reception_labelled_capture.py`、`test_reception_flow.py`、`test_reception_api.py`、`test_reception_files.py`，**86 passed、3真实MinIO用例deselected、19项依赖警告，79.62秒**。新增标签测试使用test-only适配层丢弃DSL的definition参数，原断言保留，产品继续使用旧ROUNDS；不能将DSL工作树结果当作0008结果。
- PostgreSQL17使用本轮独占tmpfs容器，未运行在体验库。验收后比对容器ID及专属label，仅移除该一次性测试容器；体验数据库、Java、MinIO及已有卷均保留。附件新测试使用真实PG与替身对象存储，不冒称真实MinIO新上传通过。

## 当前运行与归档

归档及实际静态目录：`D:/Code/deploy/local-deploy/releases/xiaohe-reception-manual/local/20261107T052651Z-login-capture-v3`，静态根为其中 `portal-spa`。API/worker使用 `updates/20261107T052651Z-login-capture-v3/backend-0008` 与同级 `candidate-service.py`。以手工体验目录 `updates/current-update.txt` 为当前更新入口；不要用已含新DSL的原工作树直接冷启动体验API。

| 服务 | 本轮运行 | 保留项 |
| --- | --- | --- |
| 隔离HTTPS网关 | 18116，PID14312 | 相同Origin、证书、回调登记 |
| 隔离API | 18097，PID73156 | 前次18096/PID45100和原18094/PID9696仍存活供回退 |
| 隔离worker | PID78132，调用18097 | 仅停前次本轮专属worker，不动正式worker |
| Native Runtime | 18117，PID63540 | 原进程未重启 |

实际包已重新打开检查CRC与全部文件内容，并扫描实际私有运行值无命中：

- 前端原候选ZIP SHA：`755d6345b58fec7db45e4013639d5acbaf94f47360b23965346a22ff8401c782`，222文件。
- 实际服务前端包：410文件，含188个不覆盖新文件的旧哈希资产，供已打开客户端续载；包SHA为 `f0d05b6c96a456b26620794c48309b0568f58917162a9eef2e1349907679c363`。
- Python包：375文件，SHA为 `3e2b79df5df137fa8a3cb466081783a29cefc647f3a81bb3dad3b10380e4f771`。
- 实际index SHA：`dcc64a0846aa210cc54fdd37f39b933aafb573db5eff5708449a31bf6914b2b1`。

完整指纹、测试日志、实际入口证明见归档中的 `package-verification.json`、`preparation-proof.json`、`activation-proof.json`、`compatibility-regression.log`。私有配置和数据库dump只在原WSL私有备份目录，未进入发布包。备份复制后一次WSL chmod异常退出，切换前仅重试该权限操作一次，随后核验dump SHA、原配置一致及相关文件600，再继续；失败时尚未动运行服务。

## 切换后核验与身份保护

新API先在18097启动并ready200，确认体验库无queued/running任务、旧进程归属正确及数据指纹保持后，才切换18116和专属worker。仅改私有配置的api_port、api_origin、portal_root和toolkit_base_url，其他设置逐项相等；临时配置先600后原子替换。受控DecisionModel注入继续保留，不启用外部provider。

严格证书链及主机名验证通过；实际HTML和4个入口JS/CSS与归档逐字节匹配；匿名会话POST200并返回1条欢迎语。API18097/回退API18096 ready、Runtime18117 healthz、Java18084 health均200，新worker进程持续存在。正式门户HTML SHA、19个正式容器的ID/镜像/名称、2个体验Java/MinIO容器身份均未变化。数据库仍为0008，无workflow表。

切换前后6条会员session完整指纹（含原截止、撤销时间）、会员消息与文件关联指纹相同。当前有效会员会话1条，12:42:06建立、20:42:06绝对到期；现有身份绑定与system/tenant、issuer登记相符。已有claim为merged，源guest已撤销，目标20条消息（7条user），scope和运行归属异常均0；没有重新登录或重放complete。只有归并后的状态基线，不能据此宣称取得绑定前全部消息逐字节比对证据。

当前采购草稿仍为review/summary、revision2、字段0，提交和文件均0，未重置或代建。用户在接待聊天已选择“新建需求，从首轮验证”；下一步由用户在原隔离Chrome刷新并输入精确口令“新建需求”，通过原流程创建新草稿，保留旧草稿。本方不在后台代建、不自动提交。今日已有1条短信开始记录，发生在本轮更新前；本轮工具没有发送短信，不能把历史总数写成0。

## 回退及待验收

原配置和pg_dump保存在WSL手工体验私有目录的 `backups/20261107T052651Z-login-capture-v3`，目录700、配置和dump600；来源备份及上次完整产物继续保留。若需要回退，只停止本轮已核实归属的新API/worker/gateway，恢复前次私有配置并用前次冻结启动器重开gateway/worker，指回仍运行的18096与前次静态目录；无需覆盖数据库或清理卷。不要从现DSL工作树冷启动旧18094。

新版认证提示隐藏、首轮新建与聊天/右侧同步、草稿刷新恢复、真实选取/拖拽附件及提交回执，仍需用户在当前Chrome手动验收。未接管浏览器，未复制Cookie，未发短信、未调用外部模型、未创建真实账号、未提交业务申请、未改正式部署或开关。
