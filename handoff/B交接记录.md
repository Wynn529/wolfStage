# B 交接记录

交接日期：待填写
交接人：B
程序版本：1.0.0
规则版本：rules-v1.0
Prompt 版本：wolf-v1 / seer-v1 / villager-v1 / host-v1
DeepSeek 模型名：由 `DEEPSEEK_MODEL` 配置
A 小模型接口：可注入 `classifier(game_id, event_id, speaker_id, public_text)`
HTTP 适配器：`app.classifier.AHttpClassifier`，地址由 `A_CLASSIFIER_URL` 配置
启动命令：`uvicorn app.api:app --reload`
接口协议：`interface_protocol.md`
规则测试：`pytest -q tests/test_service.py tests/test_visibility.py`
代表对局：运行后填写 `game_id`、seed 及 `runs/<game_id>/` 路径
真实完整对局数：待真实 API 联调后填写
已知限制：默认 stub；真实 API 和 A 接口需在环境变量/适配器中配置；服务重启会从 `runs/<game_id>/state.json` 恢复；费用由原始 usage 统计后填写。

## 2026-10-25 交接单修复记录

- 已修：公开发言和投票的 `reason` 不再进入 public 事件；改为 `agent_reason` 私密事件，仅本人、主持和上帝视角可见。
- 已修：`advance` 支持 `operation_id`、同局互斥、重复操作返回原结果，并返回 `new_events` 与 `cursor`。
- 已修：支持 `expected_cursor` 冲突检测及 `/games/{game_id}/operations/{operation_id}` 只读查询。
- 已修：模型调用期间状态标记为 `waiting_model`；慢调用收到暂停请求后在当前事件完成后停稳，并可通过 `/status` 查询。
- 已修：新增只读 `/games/{game_id}/status`，状态快照支持重启恢复。
- 已修：每次玩家/A 调用带 `call_id` 和 `input_event_ids`，可追溯“发言→分类→后续输入”。
- 已修：狼队分歧写入 `wolf_negotiation`，按 seed+day 固定裁决。
- 已测：B 核心、可见性、A 注入联调测试通过；真实 DeepSeek 完整对局和 C 页面复验待外部环境执行。
