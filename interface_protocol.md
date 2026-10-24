# B/C/A 接口协议 v1

## 创建与推进

`POST /games` 请求 `{ "seed": 42, "role_config": null }`，响应包含 `game_id`, `status`, `phase`, `rules_version`。`POST /games/{game_id}/advance` 每次只推进一个事件，返回 `new_event`, `phase`, `status`。

## 事件

事件统一包含 `game_id`, `event_id`, `timestamp`, `phase`, `day`, `type`, `visibility`, `rules_version`, `source`。公开发言示例：

```json
{"game_id":"g1","event_id":7,"phase":"day_speak","type":"public_speech","visibility":"public","source":"stub","actor":2,"text":"我认为3号可疑","reason":"公开自述"}
```

私密行动、规则裁决、小模型标签和失败示例都保留在 `runs/<game_id>/events.jsonl`，由 `visibility` 限制角色读取。`view=god` 只用于 C 本地展示。

## A 分类调用

B 每条公开发言调用一次 `POST $A_CLASSIFIER_URL`，请求字段为 `game_id,event_id,speaker_id,public_text`。返回结果原样写入 `utterance_classification` 事件；分数 `confidence=null` 时不得显示成概率。A 调用失败写入 `errors.jsonl` 并将对局标记为降级或失败。

## 错误和回放

未知 `game_id` 返回 404；非法行动写入失败原因并最多重试一次；暂停时不推进事件。回放只读取 `events.jsonl`，不会发起新的模型调用。
