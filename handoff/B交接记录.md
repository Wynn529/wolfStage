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
