# Runtime Zod v3 与内嵌校验器安全修补

日期：2026-11-05。用户直接授权继续处理剩余依赖风险、功能不变；正式开关关闭，不替换现有部署、不短信或付费模型。分支 `dev-fengfeng`，基线 `3563f90`。

## 已核实的范围与方案

[上游研究](../security/2026-11-05-zod-v3-upstream-research.md)逐项引用官方公告、源码和registry：当前无官方安全v3版本；升级AG-UI到1.0.3或跨到CopilotKit v2 RC都不能独立收口。本轮不迁移SDK版本、不改依赖版本、不把v3 API全局换成v4。

Runtime lock中有4处3.25.76（client/proto/runtime/shared），以及根4.6.7的自带v3；这5个包的 `v3/types.js` 和 `types.cjs` 全部纳入，共10目标。另许可包0.5.0的两份JS内嵌4.3.6，共12目标。该Runtime lock没有KaTeX，不修改门户工程或额外添加KaTeX依赖。

### v3最小回移

保留原类型判断、上限、错误构造和message/path，仅在数组max/exact超限、Set max/size超限后返回原 `INVALID`，避免子项访问/输出分配。固定无rest tuple本身已有上限，但原实现仍spread超大输入；同样在原too_big后提前拒绝。带rest tuple和没有上限的集合保持原行为。

这是本地回移，不是官方v3修复版。合法/短输入继续原验证；非法超限输入仍失败，但错误聚合及无效输入上的refinement副作用会因提前中止而减少。不会截断后接受、不验证前N项后接受、不引入any、不放宽schema。

### 许可包去内嵌

保留全部crypto导入/interop前缀，以及完整catalog/keystore/verify/checker/entitlement后缀；仅删除固定的旧Zod region，改为导入现有4.6.7的8个公开构造器。ESM和CJS独立处理。许可包版本仍0.5.0，不修改信任锚、签名、期限、feature或strict校验，也不使用替身许可模块。

通过本机npm cache读取官方tgz，先按lock integrity校验，再独立与修补结果比较：两个格式的crypto前缀和全部业务后缀逐字节一致。ESM业务SHA256 `7621c503599aa4430059468c592ae3e8aa93ba1e9ec146c0f35ed96846461951`，CJS业务SHA256 `5f6e9867b3a36d6337277412428ac61fc841fe72182700f25197afeee7fb856a`。这只是依赖去重，不重写许可业务。

## 可维护的安装门禁

实现：[补丁脚本](../../../copilot-runtime/scripts/apply-security-patches.mjs)、[原/后SHA清单](../../../copilot-runtime/scripts/security-patches.json)。patch ID为 `xiaohe-dependencies-20261105`，没有新增补丁工具依赖。

- 从实际lock与包manifest核对5个Zod包及许可包版本；任何未审版本、未知原/后内容或目标树数量变化均失败。
- 全批预检完成后才写入。未知后续文件不会导致前面先被修改；已应用文件按after SHA识别，可幂等继续。文件系统写入失败不承诺跨文件事务，重新运行仍以before/after守卫恢复。
- 默认npm postinstall执行；`npm run patch:security`可显式执行。`npm test`与`npm run build`先用 `--check`只读核对，缺补丁直接失败，不偷偷修复。
- Docker保留 `npm ci --ignore-scripts`，但COPY已审scripts后显式执行同一补丁再编译。若手工使用ignore-scripts，须先显式执行补丁；绕过标准安装/构建入口不在此保证中。

依赖锁与版本未变化，lock SHA256仍 `8a0d2227a4d27403556c17dfc5d2fc06795a6ee351f4c4c278151245ab491f4b`。原npm integrity描述上游发布包，本地修补另由SHA清单描述；不伪造版本或integrity来隐藏公告。

## Windows实际验证

- 12项实际路径RED：5包的v3 ESM/CJS超限仍访问元素，许可两格式仍4.3.6。修补后12项GREEN，实际legacy根与/v3执行相同patched类；数组/Set max/exact、同步/异步、正常/短输入、嵌套path/custom message、catch/transform与无界基线覆盖。
- 固定tuple另有10路径RED（访问2）→GREEN（访问0），带rest仍接受合法额外项。
- 许可ESM/CJS schema均4.6.7；两项离线真实Ed25519回归在修补前后均通过：合法签名、篡改、未知key、key mismatch、过期、非法feature、清理runtime key及entitlement合并。测试只在临时副本替换一个公钥，私钥仅内存，产品文件与信任锚不变。
- 5项安装门验证：重复执行、只读check不补写、版本漂移拒绝、后续内容漂移导致全批不写入、安全根及许可lock固定。安装链缺postinstall与替换根为v3的反例先RED，接入/收紧后GREEN。
- **61项全通过、零跳过**（原42加19），strict和构建exit0；编译入口四项定向验证通过，包括实际HTTP入口/受控网关。默认postinstall显式调用再次verified12/applied0。

测试位于 `copilot-runtime/tests/{dependency-security,license-compatibility,security-install,zod-v4-resolution}.test.ts`。完整日志保留在 `.superpowers/sdd/2026-11-05-runtime-zod-v3-remediation/`；没有触发真实登录、短信或付费模型，没有重跑G2。

## audit与尚未覆盖范围

首个npm audit网络请求未返回，已只取消本次客户端；随后fetch-retries0、请求10秒/进程18秒界限重试，仍ETIMEDOUT，未得到JSON结论。网络失败不是无风险，也不以旧audit数字代替本轮结果。本地回移保持3.25.76等原版本，版本元数据公告仍可能告警，不宣称audit全绿。

无界array、带rest tuple、v3无原生上限的map/record及其他巨大合法结构不因这次回移获得新预算；许可feature record同样无界，不能声称升级4.6.7自动限量。继续依赖现有64KiB入口和路由/授权边界，不把这些保护当作通用资源上限证明。后续若需新budget属于另外的行为设计。

旧运行Runtime `spike-2c8132e` 没有这些修补，上轮独立测试镜像 `spike-4a55b1e` 也没有本轮v3/许可修补。Linux新镜像验证及本批最终审查另行追加；源码/测试通过不等于已部署。前轮尾斜杠P3仍保留，正式发布门禁没有自动解除。

## 新Linux镜像与只读联调

产品提交 `af2e6a21d49bbc35fc2adc36dcc1fe243275f457`。从该提交的独立Git archive构建 `yunhezhiyuan-aibackend-copilotruntime:spike-af2e6a2`，OCI revision同源；本地image ID/manifest-list digest为 `sha256:97487c6d389e18136c5c8129f843e28e77f87db6a8b389accad90262966370b4`。Linux amd64、Node24.21.0、uid1000，未覆盖旧tag或推送镜像。

全新 `npm ci --ignore-scripts` 后显式补丁实测verified12/applied12，再strict构建、production prune。断网、无秘密的临时容器核对最终生产镜像的12个实际文件after SHA全部通过，并用原3份新测试执行 **19/19安全门**，真实许可schema为4.6.7；独立正式 `dist/index.js` 的health/info200，v4 ESM/CJS与公开子路径、安全拒绝仍通过。测试只读挂载公开源码、清单及锁文件，临时目录使用独立tmpfs，不把源码替换到image的dist/node_modules。

另一临时容器只读挂载公开CA、严格TLS连接既有候选Python；新匿名bootstrap、health/info、connect与最终snapshot均200。新Runtime实际业务fetch只有status/connect两次，欢迎消息1条保持、用户消息0、activeRun为空、quota和原截止完全一致；不发消息、不调用模型或SMS、不使用真人登录会话。Cookie/CSRF只在进程内，不落命令行/环境/文件。

前后8个既有容器（原API/worker、旧Runtime、候选API/worker/PG/MinIO、Nginx）ID/image/StartedAt/RestartCount/running/挂载集合一致。2个新临时容器均清理，残留0；保留测试镜像与证据，新建匿名测试数据沿用自身保留期，不删除既有数据。旧Runtime与旧tag仍保持 `spike-2c8132e` / `eb33944...`。新镜像只读门不等于重验发送消息、stop/reconnect、会员绑定/归并或浏览器；G2继续复用历史证据。

[公开JSON回执](../evidence/2026-11-05-runtime-dependency-security.json)包含原/后摘要、官方tarball业务保持、Windows与Linux/HTTPS结果、版本与镜像、audit网络失败和未覆盖范围。完整构建/测试/容器前后记录保留在本计划忽略目录的 `linux/`；未改 .env/SSO/Nginx/凭据/正式开关，没有推送/合并/部署，没有正式版本升级或发布归档。最终源码审查尚待补充。
