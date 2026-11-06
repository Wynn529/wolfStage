# 文斓 → 王玥琳：新版页面接线

先阅读本目录README和docs/integration_runbook.md。当前app/ui.py仅做了兼容修复，不代表九模型/真人页面已完成。请在新服务接口上做页面，保留旧六人回放。

## 接口及开局

- GET /model-profiles：供应商、实际型号与profile_id。网页只提交model_profile_id，不允许用户提交任意接口地址或真实Key。
- POST /games：mode=all_ai或human，十席seats，human_role=null或指定身份，seed。全AI10席、真人1+9，模型允许重复；参考config/nine_models.example.json，但提交时删除profiles（服务端加载注册表）。
- 真人建局响应包含human_token和human_seat，只保存在当前网页服务端会话，不写URL、日志、录屏或导出包；以后请求用Authorization: Bearer <token>。
- GET /games/{id}/status：真人本人player_context；轮到本人时action_contract给出允许动作。waiting_human时停止自动推进，渲染合法目标，不要求reason。
- POST /games/{id}/players/{seat}/action：action_id、expected_cursor、operation_id及action对象（action/target/text）。action_id来自pending_action，提交结果要区分rejected/conflict和实际执行成功。
- GET events/metrics/operations同样携带会话；未结束真人局/replay返回403，终局才开放上帝回放。新服务没有原始日志下载接口。

## 请你交付

- [ ] 全AI/单真人、真人身份指定/随机、统一设置模型后逐席覆盖；禁止多选真人。
- [ ] 真人发言/投票/夜间技能/遗言按服务端契约生成控件。刷新恢复绑定席位和pending，不重复行动。
- [ ] 界面不能从C_RUNS_DIR绕过新服务直接读取未结束真人局日志、调用原文、身份或回放；需要下载时先和文斓约定有鉴权的导出接口。
- [ ] 全AI现场观战与终局回放显示AI依据，正文20—22px以上按实际录屏验收；真人仅显示实际行动。
- [ ] 显示实际模型、失败和用量，未知值显示未知；不要把角色Prompt数量当作模型数量。
- [ ] 更新ServiceContract/SessionController，真人waiting_human停止调度，提交后读状态确认。按钮快速连点、过期操作和刷新均需测试。
- [ ] 截图与完整局录制前，向文斓给出启动命令、测试结果和提交号。

新版服务默认18083；不要使用旧app.api真人入口，该入口已主动拒绝真人游戏以避免越权。
