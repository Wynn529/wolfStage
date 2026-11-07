# 十人九模型整合与联调说明

日期：2026-11-06。本文件优先于旧六人启动说明。交接索引：handoff/20261106/README.md。

## 本次整合结果

冲突前五个文件和Git索引备份在本机.local/integration_backup_20261106。合并以最新十人引擎为基线，保留原有元数据/预算保护，并接入九家已验证配置。当前state.seat_model_configs冻结各席型号，服务重启从原配置恢复。

新入口app.integration_service:create_app采用进程内SmallModel v04；九家接口按注册配置取得行动，由引擎统一check_action和执行。真人提交同样经过规则入口，不要求reason，不产生玩家模型调用；HumanService仅负责会话绑定，不再增加第二套行动执行逻辑。

## 本机启动

```powershell
cd D:\狼人杀小组合作\wolfStage\wolfStage
.\.venv\Scripts\python.exe scripts\start_integration.py
```

默认只检查配置和当前进程能否读取密钥，不打印密钥、不发请求。真正启动：

```powershell
.\.venv\Scripts\python.exe scripts\start_integration.py --serve
```

按提示隐藏输入九家Key。Key仅通过进程环境传给本机服务，不写入仓库或结果。进程退出后下次需重新输入。建局/查询不调用付费模型；advance和真人提交后的继续推进可能调用模型。当前不应直接用作正式录像版本：先完成下方规则/页面复核。

服务在 http://127.0.0.1:18083/docs 提供交互API文档。不要开放公网；本次按本机单真人范围设计。

## 建局请求

config/nine_models.example.json已经提供一真人九AI配置。POST /games时只提交其中mode、human_role、seats，可另加seed；删掉profiles（供应商注册表只能由服务端读取）。

全AI：mode=all_ai，第10席改为ai并引用任意已注册profile_id，human_role=null。真人：mode=human，恰好一席human且model_profile_id=null；human_role可指定wolf/seer/witch/hunter/villager或null随机。

GET /model-profiles列出网页可用的profile_id。默认例子九种型号均不同，模型也允许重复。响应human_token只交给该真人会话保存，不放在URL、截图、日志或导出包中。

## 状态、控制与权限

- GET /games/{game_id}/status：阶段、游标、pending_action；真人本人player_context和当前action_contract。
- GET /games/{game_id}/events?after_event_id=0：全AI上帝视角；真人只返回本人可见事件，不能通过player_id改看其他人。
- POST /games/{game_id}/advance：{"operation_id":"unique-id","expected_cursor":当前cursor}；每一步完成后再下一步，不并发推进。
- POST /games/{game_id}/pause、resume：暂停和继续；暂停不能取消已经发送的模型请求。
- POST /games/{game_id}/players/{seat}/action：{"action_id":"来自pending_action","expected_cursor":当前cursor,"operation_id":"unique-id","action":{"action":"vote","target":2,"text":""}}。真人无需reason；按服务端契约生成动作和目标。
- GET /games/{game_id}/operations/{operation_id}：只读查询已执行结果，重复点击用原编号查询恢复。
- GET /games/{game_id}/metrics：从原始调用重算；未知用量保留未知。
- GET /games/{game_id}/replay：真人终局前403，完成后开放原始事件。

未结束真人局及真人控制请求须携带Authorization: Bearer <human_token>。出局仍保持本人视角。新服务没有任意原始日志/状态下载路由。API文档Authorize可在带Header的请求项直接填写Authorization值。

新入口默认日志位于.local/integration_runs/{game_id}/。其中state.json含全局私密状态，human_sessions.sqlite含会话哈希，不能通过网页直接暴露或随日志导出。旧app.api拒绝真人局及其数据访问，避免使用旧接口绕过新会话认证。旧c_live_service仍只用于DeepSeek历史路径；其A子进程已切换v04以适配十人编号。

## 验证与边界

tests/test_integration_service.py：新HTTP入口的会话、恢复、越权、十席九型号路由、元数据及真人幂等。模型响应均由替身提供，无外部API。

tests/test_integrated_rule_boundaries.py：AI猎人、刀毒死亡、放逐后技能与遗言、女巫非首夜自救、离线十人终局。完整全仓结果见evidence/integration_tests_20261106.xml。

本次最终本机回归：213 passed，2项依赖弃用警告，无失败。所有外部模型响应均由测试替身提供；另保留此前用户逐家完成的九份真实单次接口测试，二者不可混淆。超时恢复用例的40ms故障注入现只用于控制请求，准备建局使用正常超时，避免把机器调度波动误判为功能故障。

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=.local/pytest-your-unique-directory
```

Windows沙箱可能阻止127.0.0.1/asyncio，普通本机终端运行这些测试不需要沙箱。每次测试使用新临时目录。

真实九家单次接口证据见evidence/model_access_summary.md；新引擎接线目前是离线集成验证，尚未执行真实九模型完整局。

## 接下来交接

吴林峤复核完整十人规则、狼队一轮真实协商、遗言分类、预算与崩溃恢复；王玥琳在新接口上完成模型配置与真人页面，不能直接读取未结束真人局本机日志；潘星玥复核v04消费和真实语料；姚幸怡据实更新报告/PPT。具体操作、回交标准均在handoff/20261106各自文件中。

正式演示须等待上述验收后，再跑一场全AI多模型完整局、一场真人完整局，保存同局日志、截图和原片。不要将接口测试、离线替身或定向技能片段拼成虚构真实完整局。
