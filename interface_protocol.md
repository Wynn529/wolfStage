# 十人狼人杀服务协议 v2

## 建局

`POST /games` 请求可包含：

```json
{
  "seed": 42,
  "role_config": null,
  "seat_controllers": {"1": "human", "2": "ai"},
  "seat_model_configs": {"2": {"provider": "deepseek", "model": "deepseek-chat", "prompt_version": "wolf-v1"}}
}
```

未指定时创建十人局、全部 AI。旧六人角色配置仍可读取并以 `rules-v1.0` 标记。

## 推进与真人行动

`POST /games/{game_id}/advance` 仍以 `operation_id` 和 `expected_cursor` 幂等推进。轮到真人时返回 `status=waiting_human` 及 `pending_action`：

```json
{"action_id":"g:4:3","actor":3,"phase":"night"}
```

真人通过 `POST /games/{game_id}/players/{player_id}/action` 提交：`action_id`、`expected_cursor`、`operation_id` 和结构化 `action`。重复提交、游标冲突或待操作不匹配不会改变状态。

`GET /games/{game_id}/pending-action` 查询当前待真人行动；`GET /games/{game_id}/status` 返回状态、阶段、游标、控制器和待操作。

## 事件、权限和日志

事件包含 `game_id,event_id,timestamp,phase,day,type,visibility,rules_version,source`。`view=god` 在终局前只返回公开事件；按 `player_id` 查询只返回公开事件和该席位可见的私密事件。终局后才开放完整上帝回放。

AI 每次尝试单独记录模型、供应商、席位、Prompt、token、延迟、失败和重试；真人操作不计入模型调用。`GET /games/{game_id}/metrics` 同时返回总计和 `by_seat` 分组，未知 token 与已知合计分开。
