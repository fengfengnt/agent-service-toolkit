# 私有产出物与文件管理

用户上传的合作附件和 AI 生成的可下载文件统一由 Server 管理。`ArtifactService` 保留对话、运行 fence、版本及展示引用；`ServerFileClient` 复用现有受信接待服务认证，通过 HTTP 请求 Server。AI 不持有对象存储凭据，不直接读写 MinIO，也不保存完整文件预览文本。

## 版本、写入和下载

`stage_artifact` 生成稳定版本 UUID；兼容字段 `object_key` 的值为该文件 ID，不再是对象存储路径。相同运行/摘要重试复用版本，更换元数据拒绝。`write_artifact_object` 校验 32 MiB 上限和 SHA-256，经 Server 初始化、写入并读回核验；`finish_artifact` 继续在运行 fence 下原子发布 ready 和事件。旧 attempt 不能完成当前版本。

Server 负责文件唯一写入、主体隔离、长度/摘要、正式引用保护及删除墓碑。业务正式提交前再次核对材料类型签名。门户与小鹤提交创建独立申请，不共享表单/草稿；文件服务统一不改变这一点。

浏览器接口仍为 `GET /ai-api/v1/artifacts/{id}/versions/{version}` 及 `/download`。下载使用 attachment、private/no-store、nosniff，每块发送前重验当前会话及归属；不返回桶、对象键或凭据。单个下载最多缓冲 32 MiB。Server 对有效 UTF-8 且不超过 128 KiB 的 text/plain、text/markdown 提供纯文本预览，其余为 download_only。

访客认领先幂等转移 Server 文件归属，成功后完成 AI 记录归并；失败保留可重试状态。过期/失败清理由 Server 删除；正式业务保留的文件不能由 AI 清除。删除墓碑拒绝迟到写入，重复删除回收已知定位上的迟到对象。

## 配置与迁移

AI 使用现有 `XIAOHE_RECEPTION_BASE_URL`、`XIAOHE_RECEPTION_APPLICATION_ID`、`XIAOHE_RECEPTION_APPLICATION_SECRET`。API 就绪检查要求 Server 私有文件配置可用。移除所有 `XIAOHE_MINIO_*` 环境变量；启动器拒绝仍注入存储账号的配置。通用 Compose 通过 `XIAOHE_SERVER_NETWORK` 接入已有 Server 网络，不创建或删除历史 MinIO 数据服务及卷。

历史文件迁移保留原 ID、所有权、长度、SHA-256 和正式业务引用。先复制至 Server 专用私有桶并读回验证，再在暂停旧 AI 写入后核对最后增量、规范化 AI 引用。切换及权限验收通过后才删除清单内的已迁移原对象。迁移后不能盲目回退仍使用旧定位的 AI 镜像。

## 验证

HTTP 客户端、运行并发、访客认领和到期清理测试使用可控 HTTP 文件服务替身。`run_reception_journey.py --forms` 使用独占 PostgreSQL、MySQL、真实 MinIO 和 Java 文件接口，身份服务为受控替身，不调用付费模型或发送短信。生产上线及历史迁移结果另记实际发布记录；测试通过不代表生产已切换。

旧对象存储镜像来源记录保留在 `deploy/xiaohe/images.lock.json` 及历史验收文档中；它们不再是当前 AI 的配置要求。
