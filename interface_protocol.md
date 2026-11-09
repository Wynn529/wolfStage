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

未指定时创建十人局、全部 AI。`seed` 可省略，也可填写整数或 `null`。旧六人角色配置仍可读取并以 `rules-v1.0` 标记。

## 推进与真人行动

`POST /games/{game_id}/advance` 仍以 `operation_id` 和 `expected_cursor` 幂等推进。轮到真人时返回 `status=waiting_human` 及 `pending_action`：

```json
{"action_id":"g:4:3","actor":3,"phase":"night"}
```

真人通过 `POST /games/{game_id}/players/{player_id}/action` 提交：`action_id`、`expected_cursor`、`operation_id` 和结构化 `action`。重复提交、游标冲突或待操作不匹配不会改变状态。

提供 `operation_id` 或 `action_id` 时须使用非空字符串。`expected_cursor` 须为非负整数，布尔值无效。参数类型错误返回 HTTP 422。

行动对象的 `action` 必须是字符串。投票必须显式提交 `target`，其值可以是合法席位或表示弃权的 `null`。
白天发言使用 `speak`，遗言阶段才接受 `last_words`。

`GET /games/{game_id}/pending-action` 查询当前待真人行动；`GET /games/{game_id}/status` 返回状态、阶段、游标、控制器和待操作。

## 事件、权限和日志

事件包含 `game_id,event_id,timestamp,phase,day,type,visibility,rules_version,source`。`view=god` 在终局前只返回公开事件；按 `player_id` 查询只返回公开事件和该席位可见的私密事件。终局后才开放完整上帝回放。

白天秘密投票以 `vote_open` 开始。`vote_received` 只确认收票，省略投票人和目标。收齐后，公开的 `vote_tally` 包含 `counts`（所有存活席位的票数）、`abstentions`（弃权人数）、`target`（放逐席位或 `null`）及 `resolution`（十人局为 `exile` 或 `no_exile`）。放逐仍同时记录为独立的 `exile` 事件。

开票结算先保存待提交记录，再发布同一批事件。服务在发布或保存状态期间中断时，会在重启后补完该批结算。客户端可以用原 `operation_id` 查询已恢复的结果。

收票确认与选票状态一起保存。真人提交事件和密封收票确认属于同一批次。

新版服务的状态响应通过 `can_retry_model` 表示当前模型行动是否因已确认的请求超时或重试期间服务中断而可重试。客户端向 `POST /games/{game_id}/retry-model` 提交新的 `operation_id` 和当前 `expected_cursor`，仅重试一次当前席位的模型请求。原操作记录和已入账行动保留，重试结果可通过操作查询接口核对。

重试开始和行动结果分别持久化。服务重启时会补完已提交的结果；尚未入账的在途操作会标记为 `model_retry_interrupted`，原操作编号可以查到该结果。再次请求模型须提交新的操作编号，原请求可能已产生费用。

中断时缺少调用记录的重试仍占一次调用额度。真人局达到天数上限后，原会话可以读取回放。

普通推进会先保存模型请求及操作编号，收到并校验结果后保存行动，再提交状态和事件。重启时，已保存的行动可以直接入账；结果未保存的在途请求标记为 `model_step_interrupted`，保留调用额度和原操作查询。等待真人事件和待操作状态一并提交。

手动重试使用同一行动保存流程。发言分析结果也会在提交发言前保存，恢复时复用该结果和调用记录。暂停与继续的事件随状态一起提交。

启动时先验证合并后的事件编号、待提交游标及快照能否由规则引擎加载，再写入事件和快照。验证失败的对局会保留原始文件，接口返回 HTTP 409，其他已保存对局与建局接口继续可用。JSONL 按物理换行分隔记录，发言中的 Unicode 分隔符保留在原记录内。

AI 每次尝试单独记录模型、供应商、席位、Prompt、token、延迟、失败和重试；真人操作不计入模型调用。`GET /games/{game_id}/metrics` 同时返回总计和 `by_seat` 分组，未知 token 与已知合计分开。
