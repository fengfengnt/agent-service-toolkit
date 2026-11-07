# 接待申请pending与隔离Java类加载故障

2026-11-07，北京时间。状态：**隔离Java修复后，用户已通过原键正常恢复受理；回执及业务关联只读核对通过，用户确认我的进展可见**。

用户确认提交汇总后出现“正式受理结果尚未确认”，同时要求登录后在“关于我们”后显示“我的进展”。接待Owner正在修复菜单及区分pending/accepted的友好提示。本方不并行改其PortalHeader、HomeNav、SummaryPanel或flow源码。

只读PG结果：现有AI申请pending、receipt空、草稿submitting、提交revision与当前一致。只读MySQL按同一AI用户、system/tenant、submission_key定位：对应co_collaborator、co_submission和co_revision均0。尚无正式受理记录，不能把“已保存、待核对”写成“已受理、已分派专员”。没有重试提交、手工改状态或创建业务绑定。

隔离Java近30分钟日志出现ReceptionJson、Hutool ExceptionUtil、Tomcat RequestUtil的ClassNotFoundException/NoClassDefFoundError。当前运行JAR与宿主target文件SHA相同，三个类在其嵌套库中实际均存在；不是只根据BOOT-INF/classes目录作缺类判断。

该Java容器11月6日06:54:44启动，`/app/app.jar`挂载工作树Maven target中的JAR；该文件11月6日22:20:29被修改，晚于启动。推断运行中包被改写可能破坏嵌套类索引，但根因仍需后端Agent确认。当前JAR SHA为`dd87d6cf448bbf6b8e2122fd91121665f6c98480cd727b5caa6ff938e1f060cb`。

证据在本次隔离归档 `20261107T052651Z-login-capture-v3` 的 `submission-readonly-diagnostic.json` 与 `java-jar-readonly-diagnostic.json`。本方用READ ONLY事务直接查询；没有调用可能通过bindOrRequireSame新增业务身份的 `/applications/result` 来冒充只读检查。首次MySQL探针因Windows文本换行影响容器shell失败，改为UTF-8字节输入后同一READ ONLY查询通过，未改数据库或配置。

按用户既有后端任务交接约定，已使用handoff技能在OS临时目录生成：`C:/Users/70367/AppData/Local/Temp/xiaohe-reception-java-runtime-handoff-20261107.md`。文档包含事实/推断区分、不可变制品恢复建议、身份与申请幂等保护、建议技能及验收条件，不含凭据或用户正文。没有修改JAR、重启Java、更改正式部署/开关、发短信或调用模型；不代替用户重提申请。待后端修复并验证后，由用户按原submission_key正常查询/重试，再核对真实受理回执和我的进展。

## 后端恢复回执与后续只读核对

后端Agent已获用户明确修复及同步授权，完成隔离Java恢复，记录为 `D:/Code/deploy/local-deploy/releases/xiaocai-reception-java/local/runtime-repair-20261107-dd87d6cf448b/release.md`。其无网络诊断实验已复现“覆盖运行中的target JAR导致嵌套类加载失败”，新JVM读取覆盖后同包正常，故先前推断现已由该实验支持。只将同一已校验JAR迁到不可变只读路径并重建专属隔离容器，未改业务代码。

Root于14:40后只读核对：新容器 `932afb96471e50135f788608c136449adb7b6e4ca823023e0ae9ab78d7793282` 14:29:26启动并运行；实际JAR SHA仍dd87d6cf…060cb，挂载已脱离target、只读且在独立releases目录。canonical与现API冻结配置的reception_base_url均为 `http://127.0.0.1:18084`，API仍18097、受控模型模式保留。新容器现有日志无先前缺类异常，会员会话有效；本方没有执行HTTP业务查询或重试。

原锚点对应申请标识和资料指纹与修复前相同，仍pending、receipt空、草稿submitting且revision一致；同scope co_collaborator/co_submission/co_revision仍0。后端恢复不自动产生正式受理，以上不能标为申请受理通过。证据在当前静态更新目录的 `java-recovery-readonly-check.json`、`submission-readonly-diagnostic.json` 和 `member-acceptance-check.json`。

用户已在后端聊天要求继续验证，需人工操作时明确告知并等待。已告知在原隔离Chrome刷新、打开小鹤，于原汇总底部仅点击一次实际按钮“查询受理结果”。该按钮内部发送“查询提交结果”，沿原submission_key先查结果；若not_found，会继续完成原申请，可能创建业务身份和受理记录。因此它不是纯只读检查，本方不代点或后台调用；等待用户实际操作后再用SQL READ ONLY核对原申请、唯一回执及“我的进展”。不新建需求，不再次确认，不连续点击，不发送短信或调用模型。

另更正早期健康证据的解读：此包没有Actuator，`/actuator/health`的HTTP200实际包装业务404，不能当作UP。后端回执的真实控制器401/403/未知字段422及19项H2回归与本方只读运行包核验分别记录，不能冒称正常受理、进展查询或真实MySQL正向业务验收通过。

## 后续原键受理闭环

用户随后明确反馈一次手动“查询受理结果”后显示成功，且“我的进展”可见本次需求。Root只读核对：原申请/资料指纹未变；AI及草稿accepted；AI/业务receipt逐项匹配；同scope co_submission、co_revision、co_application均唯一1条，关联身份、字段、版本均匹配，业务stage为recorded。尚无manager或联系时间，不宣称已分派专员。本方未重放接口或修改真实状态。完整验收及未覆盖项见 `2026-11-07-original-application-acceptance.md`，上文pending均保留为恢复前时间点记录。
