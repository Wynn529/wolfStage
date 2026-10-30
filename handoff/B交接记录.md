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

## 2026-10-29 C 首次真实 API 联调修复回执

- B-P01：通过。慢调用期间保持 `waiting_model`，暂停只设置 `pause_requested`；当前业务操作、私密理由、分类和操作结果完成后才生成 `game_paused` 并返回 paused。
- B-P02：通过。操作起始游标独立保存在 `operation_start_event_id`，不会随夜间 pending 清理；`new_events` 覆盖 0/1/多事件和终局事件。
- B-P03：通过。规则层拒绝狼人击杀狼人队友，并保留非法行动日志。
- B-P04：通过。首次平票保存 `revote_candidates`，重投只能选择平票候选。
- B-P05：通过。重试记录独立，第二次成功会写 `success=true`、`validation_error=null`，第一次失败原件仍保留。
- B-P06：通过。狼人 `agent_reason.source_event_id` 绑定刚生成的 `wolf_proposal.event_id`。
- B-P07：通过。DeepSeekAgent 将 input/output token 和 latency 元数据传给 calls 日志；缺失值保持 null，不当作 0。
- B-P08：通过。A 分类异常写入失败调用和错误事件，并将对局置为 failed，不能继续自动推进。
- Prompt：已明确狼人、预言家、平民各阶段 action/target/text/reason 字段、类型、合法值和示例；预言家固定使用 `inspect`，不接受 `check`。
- 回归：`python -m pytest -q tests/test_b_update_c.py tests/test_service.py tests/test_visibility.py tests/test_a_integration.py`，22 项通过；含 C 缺陷回归时 39 项通过。

## 2026-10-29 第三版集中清单复核

- 角色协议、布尔 target、validation_error 反馈和 JSON 解析失败记录已修复；提示词内容 hash 写入调用记录。
- 失败后不会再生成伪 `public_speech`、`agent_reason` 或 A 分类事件；最终状态保持 `failed`。
- 无有效票直接记录 `no_valid_votes` 并无人放逐进入夜晚；非空平票仍只允许候选重投。
- 昼夜口径统一为：首夜 day=0，天亮后 day=1；第八日完成发言/投票后仍无胜负才记 `game_limit`。
- 离线回归：`python -m pytest -q tests/test_b_update_c.py tests/test_c_b_retry_usage.py tests/test_service.py tests/test_visibility.py tests/test_a_integration.py`；本轮结果以实际命令输出为准。
- 未静默宣称：主持模型主流程、跨进程崩溃恢复、正式真实完整局、预算账本和第二轮狼人模型协商仍需团队确认或真实环境证据。
- 已补：空有效票的 `no_valid_votes` 结算、第八日完整边界说明、跨游戏 DeepSeek 调用锁、玩家/A 调用保护上限配置；多进程、主持模型和第二轮协商仍保持未实现声明。
