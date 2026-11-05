# 小鹤接待业务运维说明

小鹤保存采集草稿，正式档案、服务申请、入驻申请及人工跟进由独立 Collaborator 后端管理。正式提交必须绑定当前汇总版本和真实用户的确认消息；未知受理结果按原提交键核实。

## 接口与配置

接待接口位于 `/ai-api/v1/reception`，沿现有 Cookie、Origin、CSRF 和对象授权。分轮保存只更新草稿；选择服务方向不改变认证身份。文件关联 DELETE 的 JSON 正文为 `{expectedRevision}`。

后台调用配置为 `XIAOHE_RECEPTION_BASE_URL`、`XIAOHE_RECEPTION_APPLICATION_ID`、`XIAOHE_RECEPTION_APPLICATION_SECRET`。地址只接受服务端固定 HTTP(S) origin，禁止重定向。不完整配置时不发送正式受理请求。

运营材料下载单独配置 `XIAOHE_RECEPTION_FILE_APPLICATION_ID`、`XIAOHE_RECEPTION_FILE_SECRET`、`XIAOHE_RECEPTION_FILE_SYSTEM_ID`。首版一个下载凭据绑定一个已登记 system，只有 `reception.file.read` 对应的内部下载路径可用。凭据与运行时和后台提交凭据分开，不传给浏览器或模型。

后台使用 POST `/internal-api/reception-files/v1/files/{id}/download`，正文为 `{applicationId,ownerAiUserId}`。小鹤核对已受理申请、文件快照及实际文件归属，原提交回执尚未恢复时不放宽关联检查。部署时将内部路径留在服务网络，不加入门户公开代理。

## 私有材料

仅接受 PDF、PNG、JPEG、DOCX、PPTX，单文件上限 32 MiB。扩展名和签名同时校验，Office 只检查包目录并保存原文件，不解压正文、执行、OCR 或送入模型。下载先校验大小与摘要，使用附件响应、`private, no-store` 和 `nosniff`。

并发上传使用不同的不可变对象键，最终就绪登记仍检查当前归属。未就绪文件不进入正式申请，用户可以跳过选填材料。修改申请时可移除本次草稿的继承引用，历史已受理材料保留。

所有上传尝试的对象键都登记在文件记录中。未认领访客过期后复用原持久化清理回执，包含晚到对象的再次回收；已归并资料按登录用户保留。已登录用户的失败尝试目前随草稿保留，独立回收期限待运营确定。

## 验证边界

测试必须显式配置独立 `test_xiaohe_*` PostgreSQL 与 `XIAOHE_TEST_MINIO_CONFIG`，缺少配置直接失败。材料检查可运行 `python -m pytest tests/xiaohe/test_reception_files.py tests/xiaohe/test_reception_file_access.py tests/xiaohe/test_reception_file_lifecycle.py -q`。

隔离数据库、真实对象存储、受控业务客户端、真实 Java 链路和浏览器验收分别记录。现有测试不代表短信、付费模型、正式部署或业务数据迁移已完成。
