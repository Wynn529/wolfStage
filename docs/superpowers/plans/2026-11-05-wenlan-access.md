# 文斓接入实施与交接

2026-11-06续作：九家配置、适配和验证入口见 `docs/model_access_guide.md`。本文“当前生产工厂仅支持deepseek”描述11月5日首批状态，已由九家工厂扩展取代；真实调用及公共引擎接线仍待验证。

日期：2026-11-05。依据已确认的十人升级设计及项目根目录《文斓.md》。

## 本批实现

- `app/seat_config.py`：冻结十席及模型配置，校验模式、模型引用和真人人数；允许模型重复。`human_role=null` 表示随机，实际身份分配由引擎完成。
- `app/seat_router.py`：每局建立一个 SeatRouter；每次按席位选模型，传入已过滤 context、规则方提供的 contract 和完整 Prompt。独立返回 ActionResult.action / meta。不保存对话历史、不自动重试、不自动替换模型。真人席位拒绝模型调用。
- `app/human_service.py`：SQLite 持久化会话绑定、待操作及提交队列；令牌只存哈希，提交不允许 reason/player_id/view 字段；游标、操作编号与唯一约束防止重复入队。队列本身不执行规则。
- DeepSeekClient：保留旧调用兼容性，新增地址配置；元数据优先记录响应实际型号。
- `config/seat_match.example.json`：一真人九 AI 的可加载配置；全 AI 示例由下方说明转换。

现有 app/agents.py、app/engine.py 已有本地修改，本批保留其内容。没有将新模块直接插入旧六人推进流程，也未挂载 HTTP 路由。

## 调用约定

```python
config = MatchConfig.from_dict(json.loads(config_path.read_text(encoding="utf-8")))
router = SeatRouter(config)
# 开局把 config.snapshot() 写入该 game_id 的状态快照，恢复时从快照加载。
result = router.act(actor, context=filtered_context, contract=current_contract,
                    prompt=role_prompt_with_protocol, prompt_version=version)
# 规则检查 result.action；日志逐次合并 result.meta，不将 meta 塞入行动。
```

元数据：provider、model_profile_id、requested_model、model_name、model_version、agent_id、prompt_version、latency_ms、input_tokens、output_tokens、success。供应商未返回用量时保持 None。success 表示请求/解析成功，规则校验结果由引擎另记。ModelAttemptError.meta 给出失败元数据，异常正文经过脱敏；每次尝试都应记录，包括失败。当前失败解析后无法获取的 usage 仍未知。

模型配置只能由可信本机启动配置加载，网页只能引用已登记 profile_id，不开放任意接口地址或密钥环境变量的写入。当前生产工厂仅支持 deepseek。其他供应商确认后增加独立适配器，再接工厂；离线注入的模拟客户端不算真实接入证据。

## 真人最小链路与执行边界

1. 开局由可信服务调用 `token = human.bind(game_id, human_seat)`，绑定不能由网页自行重设。令牌通过本机会话安全保存，不放 URL 或日志。
2. 引擎到真人回合，在同局锁内调用 `publish(game_id, pending_id, cursor, contract=current_contract)`，存储 waiting_human 后结束本次推进。contract 只含本人可见合法操作，不能携带完整状态。
3. 刷新调用 `pending(game_id, token)` 恢复相同待操作。当前真人状态接口仍由公共服务接线：必须先认证会话，再按绑定席位过滤状态和事件。
4. 网页提交 operation_id、pending_id、expected_cursor、action。`submit` 仅返回 submitted，表示已入队。该服务不声称行动合法或技能已执行。
5. 引擎同局锁内读取 `queued(game_id)`，检查当前游标和行动合法性，走与 AI 一致的执行入口；执行结果连同 operation_id 持久化，再调用 `complete`。重启可重取队列，执行端必须依据已保存 operation_id 返回原结果，防止重复消耗技能。
6. 规则拒绝时也持久化明确错误并 complete，推进状态游标后以新 pending_id 发布修正操作。新操作必须采用更大的游标；不能重新使用旧编号。

`queued/publish/complete/bind` 都是可信服务端方法，禁止直接映射成可任意调用的网页端点。SQLite 文件置于 `.local/` 等非公开目录，不进入日志下载包。

## 给王玥琳的模拟响应

全 AI 开局：读取 JSON 样例，将 mode 改为 all_ai，将 10 号改为 ai 并引用 deepseek_default；human_role 保持 null。配置可以重复，也可以逐席引用另一个已登记型号。

真人待操作（动作名仅演示现有 vote，新增技能枚举以吴林峤发布版本为准）：

```json
{"seat_id":10,"status":"waiting_human","pending_id":"turn-20-seat-10","cursor":20,"contract":{"allowed_actions":["vote"],"allowed_targets":[1,2,3,4,5,6,7,8,9,null]}}
```

提交示例与成功响应：

```json
{"operation_id":"click-unique-id","pending_id":"turn-20-seat-10","expected_cursor":20,"action":{"action":"vote","target":2,"text":""}}
```

```json
{"status":"submitted","operation_id":"click-unique-id"}
```

建议 HTTP 层将 PermissionError 映射 401/403，HumanInputError 映射 409 或字段错误 422。例如过期响应：

```json
{"error":{"code":"stale_operation","message":"操作已过期，请刷新当前待操作"}}
```

这些是待公共 API 合入的映射约定，当前没有可请求的 HTTP 路由。提交后显示 submitted，等待引擎推进或返回规则错误；不能显示“投票已执行”。真人没有依据输入框。

## 验证与后续接线

本批离线测试覆盖配置、路由、元数据、会话、幂等、并发和恢复。测试命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_seat_access.py tests/test_action_protocol.py tests/test_visibility.py tests/test_service.py tests/test_b_update_c.py -q --basetemp=.local/pytest-access-unique-directory
```

每次使用新临时目录，避免 pytest 清理其他测试目录。

尚待吴林峤：十人状态、角色 Prompt/契约；接入路由及真人统一校验执行；操作结果持久化；将 meta 逐次写入调用日志，修复未知用量聚合；等待状态及公共 API。

尚待双方共同验证：真人状态、事件、调用详情、回放、下载在未结束时都执行服务端权限检查。本批会话队列测试不能证明旧 API 已具备权限保护，暂不开放真人模式。

尚待文斓后续：确认其他型号与适配器；有凭据后进行真实协议联调、全 AI 多模型局和真人完整局；五身份定向验证；冻结版本录制正式视频。
