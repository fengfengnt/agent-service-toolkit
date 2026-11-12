# 小鹤聊天失败恢复：AI 0.1.11 生产发布准备

日期：2026-11-13。状态：**已提交、推送、归档并加载生产镜像；等待 1Panel 更新现有 API／Worker 编排**。配套门户 0.1.35 已上线。生产 AI 仍运行 0.1.10，不能据此记录为整批发布完成。

## 修复与验证

生产只读诊断发现，错误资料编号被来源校验拒绝后，外层检查点恢复跳过已完成的初始化节点，导致新的函数调用缺少闭包上下文；还存在一次约 90 秒超时。现在每次进入编排前重建上下文，并向模型明确本轮授权 Skill 的资料编号及普通建议的空来源规则，保留原有来源校验与写入权限。

修复提交 `8079a176c8e1938ee2a6bb45eb6f50068133046c` 已推送 `origin/dev-fengfeng`。模型失败、结果收尾失败后的恢复与授权来源边界等相关 **33 项测试**及 Ruff 通过。冻结制品只更新 VERSION、`runtime.py`、`factory.py`，178 项镜像文件摘要全部匹配；离线标准 Skill／工具循环检查通过，没有调用真实模型。

## 制品

- 版本：`0.1.11`；归档：`D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/prod/0.1.11/`。
- 镜像：`yh-prod-aibackend-api:0.1.11`，linux/amd64；ID `sha256:6a150a50db2a80bcf42c5114b69c33df795fc08d9da2fcf80fb92f75a0cfd480`。
- 归档：`yunhezhiyuan-aibackend-0.1.11.docker.tar.gz`，726,830,873 字节；SHA-256 `8d7d23a28f1eef77b9bb97ff2bce56cfbd3be8d05459f252f58ab7fe44eb21ac`。
- 源码指纹：`fe3b48c6ed7ce3f0bb3a62129bf27528618a71b563cc3c9ae268bb1ec9cb2895`。Docker 归档的配置、架构及层身份已核对。

## 生产准备与边界

生产主机 `123.56.185.111`，现有编排 `yh-prod-aibackend`。活动文件 `/opt/yunhezhiyuan-prod/services/yh-prod-aibackend/docker-compose.yml` 仅改两个 image 字段，其他解析配置完全一致。API、Worker 仍运行旧镜像；18 个容器没有重建或重启。API 旧版本就绪接口、正式门户及 SSO Discovery 返回 200；配置、秘密及 SSO 静态内容摘要保持。

数据库保持 `0015`，本次无表结构或运行配置变更，因此不制作数据库／配置备份，不备份文件存储。远程镜像上传包在导入校验后删除，旧 AI 镜像保留；未部署 WSL／UAT，也未切换 Java、Runtime 或其他服务。

操作单：[手动更新步骤](D:/Code/deploy/local-deploy/releases/yunhezhiyuan-aibackend/prod/0.1.11/手动更新步骤.md)。用户完成面板更新后，继续核验实际镜像、版本、健康、配置及其他容器。真实登录、模型回复及 90 秒超时原因尚未线上验收；不重放生产用户消息或正式提交。
