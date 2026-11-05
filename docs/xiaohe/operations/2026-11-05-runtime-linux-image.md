# Runtime 独立 Linux 镜像兼容验证

日期：2026-11-05。结论：**新测试镜像的 Linux 断网解析与最小 HTTPS 只读链路均通过；没有替换现有部署。** 这是源码修补的镜像兼容门，不是正式发布或真人 G2 重跑。

## 授权和范围

直接读取门户对话核实人类在恢复后再次要求“继续”，沿用“复用 G2、继续剩余回归/依赖风险/最终审查；正式开关关闭、不替换现有部署、不自动短信、不付费模型”的范围。新建独立测试 tag 与临时验证容器；不执行 Compose，不修改现有容器、Nginx、SSO、.env、凭据或已有数据，不更新已审阅的 P3，不重复派源码审查。

使用 `deploy-yunhezhiyuan` 的身份、资源和验证要求，但本轮是独立兼容测试，不是正常版本升级。`VERSION`/Runtime package 的0.1.0均未修改；`spike-4a55b1e`是测试 tag，不宣称正式版本，未生成正式发布归档，也未推送镜像。

## 构建身份

| 项目 | 实测值 |
| --- | --- |
| 分支 | `dev-fengfeng` |
| 产品修补提交 | `1510f25dc056dc626667721c7b7ec204b83ac76b` |
| 实际构建来源 / OCI revision | `4a55b1ef68892018dddba51ba17d93a002638d6d`，仅比产品提交多审查文档 |
| 新测试镜像 | `yunhezhiyuan-aibackend-copilotruntime:spike-4a55b1e` |
| 本地 image ID / manifest-list digest | `sha256:b77a2b38fa6b317f02ef3ae2bc5acdd1d41cac6abfbfa1f39c166a69c8c8cf2f` |
| 平台 / Node | `linux/amd64` / `v24.21.0` |
| 基础镜像 | `node:24-bookworm@sha256:64af3819f9275802414d7cdc38c27e9d82bd564dec4d4da87d008255d36c63b4` |
| lock SHA-256 | `8a0d2227a4d27403556c17dfc5d2fc06795a6ee351f4c4c278151245ab491f4b` |
| Git archive SHA-256 | `ff9497fd4db1071d5d85fd8aabcc660555ca08be18b5b8bacb408a86e0cba341` |

构建前确认新 tag 不存在，使用 `git archive` 固定来源而非带并行修改的工作树；沿用项目 Dockerfile 与 .dockerignore，构建上下文约283KiB，不包含 .env/证书/密钥。构建前 WSL 可用内存约4767MiB，8 CPU；没有调整 WSL 配置、重启服务或清理缓存。`npm ci --ignore-scripts`、严格编译和 production prune 均完成。Docker inspect 中的 RepoDigests 是本地镜像身份记录，不是远程 registry 发布证明。

## Linux 断网门

临时容器 `yunhezhiyuan-aibackend-copilotruntime-verify-4a55b1e` 使用新镜像的固定 image ID，`--network=none`、非root uid1000、只读文件系统、drop ALL capabilities、no-new-privileges、512MiB/1CPU/128 PID，无凭据和证书。

只读挂载测试程序，实际派生进程运行 `/app/dist/index.js`；观察器不安装 resolver，必须由正式入口安装。结果：

- health/info均200；实际 SDK ESM加载与 client CJS消费均为4.6.7。
- `.max(2)` 对3元素仅产生1个issue，元素refine访问0。
- 5个官方公共v4子路径可用；5个私有/正反斜杠穿越样本均拒绝。
- 根Zod4.6.7、嵌套根/v3的3.25.76保持原状；v3旧行为仍明确存在。
- 实际AG-UI输入与公开事件schema通过，CopilotKit1.84.1。已审阅尾斜杠导入P3保持待办，不能称完全等价于所有原生解析错误。

## 新镜像到候选 Python 的最小链路

另一个临时容器 `yunhezhiyuan-aibackend-copilotruntime-network-4a55b1e` 使用同一固定 image ID与资源/权限限制。只读挂载公开CA，`NODE_EXTRA_CA_CERTS`启用证书链/主机名校验，通过 `portal.lvh.me:host-gateway` 访问既有候选HTTPS。不发布新宿主端口，不改Nginx；新Runtime只监听自身容器命名空间内的端口。

全新匿名bootstrap200；Cookie与CSRF仅进程内持有，不放命令行、环境、文件或报告。随后通过新镜像真实 `dist/index.js` 执行只读connect，Runtime fetch守卫仅允许固定候选网关的 `POST /agui/status` 与 `/agui/connect`。health/info/connect/最终快照读取均200，收到 `MESSAGES_SNAPSHOT` 和 `STATE_SNAPSHOT`，无 RUN_ERROR。

前后快照比较确认：欢迎消息1条及其身份/parts保持，用户消息0、业务run未创建、viewer及额度/原截止时间完全一致。没有调用 `/run`、发送消息、模型或短信；没有复用任何既有登录会话或重跑G2。候选库只新增了这次允许的新匿名测试会话及其欢迎消息，不删除测试数据，沿用自身正常保留期。

## 既有部署保留与清理

前后核对原API/worker、旧Runtime、候选API/worker/PG/MinIO及共享Nginx共8个容器：ID、image ID、StartedAt、RestartCount、running、挂载集合全部一致。首次直接比较 Mounts 数组因 Docker 返回顺序不同失败；逐字段诊断证实仅顺序变化，按Destination排序后集合完全一致，未忽略任何实际挂载字段。

旧Runtime仍为 `spike-2c8132e`、image ID `sha256:eb33944b55f673918a5b6bf8f5cf636e05842579e920041ec689ffeee6e241f8`，旧tag内容没有变化；**既有服务没有采用新补丁**。两个临时容器均通过 `--rm` 清理，另行检查残留数量0；保留新测试镜像与验收证据，不执行全局清理或删除卷。

公开脱敏摘要：[JSON回执](../evidence/2026-11-05-runtime-linux-image.json)。本地脚本、源码归档和完整日志保留在 `.superpowers/sdd/2026-11-05-runtime-linux-image/`，包括 prepare/build/offline/network/finalize、image身份与前后容器记录。产品源码没有新增改动。

## 尚未解除的门禁

本轮通过镜像平台兼容与匿名只读链路，不代替新镜像的发送消息、停止/重连、会员绑定/归并、浏览器或真实模型验收；已通过的历史G2继续复用，不能说本轮在新镜像重验了G2。正式部署与发布归档未执行，既有Runtime仍旧镜像。Zod根/v3及门户KaTeX残留继续阻塞正式发布，P3保留，不称npm audit全绿。
