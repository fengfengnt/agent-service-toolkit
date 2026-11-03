# P7 私有产出物与版本

`ArtifactService(factory, settings)` 提供 stage_artifact、write_artifact_object、finish_artifact、read_artifact、download_artifact。复用已锁定的 boto3/botocore，无新增 SDK 依赖；网络操作和流读取通过 asyncio.to_thread，不持 ORM 事务等待 S3。

## 版本与发布

stage 在业务 fence 下保存 pending 版本与随机不变 object_key。首次创建按 run+逻辑标题/media_type 收敛；对已有产出物追加版本必须传明确的可选 artifact_id，先验证当前对话权限，不跨 run 根据同名文件自动合并。相同 artifact/run/digest 重试返回同一版本，不同内容生成新版本和新 key；同内容重试更换元数据拒绝。

上传上限 32 MiB，先核服务器 SHA256，再以 IfNoneMatch 条件上传，已有 key 不覆盖。上传后重新读取真实对象核对完整摘要和长度，保存上传证明及预览；finish 再持 fence 校验并原子保存 ready/事件。数据库也拒绝没有完整非空证明的 ready 行。恢复可以采用前一 attempt 已有的同摘要对象，旧 attempt 不能结束当前版本。

失败或到期时 pending 版本保留 cleanup_pending 及随机 key，作为可重试清理意图；P10 再实现清理消费，本阶段没有宣称对象已自动清除。归并通过业务 Artifact 当前 conversation 取得归属，版本的 creator run 是审计；不因未来归并移动或复制对象。

worker 消费既有 AgentOutcome.artifact_version_ids，逐项确认当前对话内实际 ready 版本，从数据库重建 canonical artifact_ref。handler 伪造的标题、媒体类型和状态不会替代真实记录，未声明或越权引用拒绝，消息/事件提交整体回滚。

## 浏览器接口和安全预览

- `GET /ai-api/v1/artifacts/{id}/versions/{version}` 返回既有 ArtifactView。
- `GET .../download` 逐块重验用户/访客会话和当前归属，返回 attachment、RFC5987 文件名、private/no-store、nosniff。
- pending/failed 不提供下载路径。只返回后端 downloadPath，不返回 bucket、object_key、MinIO 地址或凭据。
- text/plain、text/markdown 在 UTF-8 且不超过 128 KiB 时提供 format=text 的纯文本预览；Markdown 首版也作为文本显示，避免额外 HTML 渲染攻击面。其余文件 download_only，不增加图片/PDF/Word 生成或专用预览。

后台写入继续使用 run 的独立授权；浏览器本地 logout 不自动取消已受理用户任务，但该浏览器立即失去文件读取权限。访客到期不能读、下载或把迟到上传标为 ready。

## MinIO 来源与隔离配置

[官方仓库](https://github.com/minio/minio)已归档，官方 Docker Hub/Quay 旧 tag 在本次核验中未返回可用 manifest。[官方安全版本说明](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)改为源码发行；没有执行其中的构建步骤。

本次使用可获取的[社区预编译镜像](https://github.com/coollabsio/minio)，发布方 coollabsio，非 MinIO 官方发布。tag 为 RELEASE.2025-10-15T17-29-55Z，index digest 为 `sha256:69b55a1c1c5dc285ce04db96689f5b2102317fc77a50680a1874ca6efd1c87f9`，linux/amd64 manifest 为 `sha256:4f75fd76598afa23919555d1363e1fb13c632d9bcd4ce8edcf21d3cca2ed0579`，记录在 images.lock.json。已核验实际二进制版本；它报告 commit-id=DEVELOPMENT.GOGET，没有验证具体源 commit、签名或生产安全性，不能从 image label 的 MinIO 字样推定官方来源。保留归档及社区供应链风险，本次仅用于隔离集成。

测试容器 xiaohe-agent-test-minio，仅绑定 127.0.0.1:35418，关闭浏览器控制台、只读 rootfs、丢弃 capabilities、禁止提权；数据保存于本次贴有测试标签的专用 named volume。配置/凭据在 git-ignored scratch 中由测试进程随机生成，不输出、不提交、不读取生产 .env。应用账号仅可访问测试 bucket 及 xiaohe/* 对象，根凭据只用于 fixture 初始化。现有 PostgreSQL 容器和用户卷保持。

```powershell
uv run --frozen --python 3.14.7 python tests/xiaohe/integration/provision_minio.py
# 将脚本输出的配置文件路径赋给 XIAOHE_TEST_MINIO_CONFIG，勿打印配置内容。
# 同时显式指定 test_xiaohe_ 前缀的 XIAOHE_TEST_DATABASE_URL。
uv run --frozen --python 3.14.7 pytest tests/xiaohe/test_artifact_versions.py -q -m pg
uv run --frozen --python 3.14.7 pytest tests/xiaohe/integration/test_minio_private.py -q -m minio
```

产品配置使用 XIAOHE_MINIO_ENDPOINT、XIAOHE_MINIO_BUCKET、XIAOHE_MINIO_ACCESS_KEY、XIAOHE_MINIO_SECRET_KEY；测试凭据不作为生产默认值。未配置时对象 I/O 安全失败，不切换成本地目录或假 S3。生产镜像选择、凭据、TLS、网络隔离及运维权限仍属 P11/正式部署验收。

## 证据范围

真实 MinIO 已验证匿名 403、应用越桶 403、条件写重试、完整摘要检查、坏对象不 ready、上传中到期与可重试清理意图、线程等待不阻塞独立数据库事务、HTTP 私有下载及元数据不泄漏存储路径、容器和客户端重启后实际对象仍可读取，以及 ready 引用重建/越权 worker 拒绝。另有元数据/SQL约束和下载分块到期检查。

完整业务及原 client/threads 回归 218 项通过（新增分块到期测试另行核验），Ruff/Alembic 检查通过，19 条既有依赖警告。未连接生产 MinIO，没有部署生产 AI 服务、调用真实模型或发送短信。
