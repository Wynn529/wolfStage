# B 游戏服务

## 安装与启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.api:app --reload
```

默认使用离线 stub，不需要密钥。真实 DeepSeek 只从环境变量读取：

```powershell
$env:DEEPSEEK_API_KEY="轮换后的密钥"
$env:DEEPSEEK_MODEL="deepseek-chat"
```

## API

`POST /games`（可传 `{"seed": 42}`）创建对局；`POST /games/{id}/advance` 单步推进；`POST /games/{id}/pause`、`/resume` 控制暂停；`GET /games/{id}/events?view=god` 获取事件；`GET /games/{id}/metrics` 获取统计；`GET /games/{id}/replay` 只读回放。

原始证据位于 `runs/<game_id>/events.jsonl`、`calls.jsonl`、`errors.jsonl` 和 `summary.json`。回放不会重新调用模型。`app.engine.GameEngine` 也可直接在 Python 中调用。

接口字段和 A/C 联调样例见 `interface_protocol.md`。启动 B 前设置 `A_CLASSIFIER_URL=http://127.0.0.1:8765/classify`，`app.api` 会自动创建 `AHttpClassifier`，每条公开发言调用 A 一次并写入 `calls.jsonl` 与 `utterance_classification` 事件；未设置时明确使用 stub。对局状态持久化在 `state.json`，重启 `GameEngine` 会自动恢复未结束对局。
