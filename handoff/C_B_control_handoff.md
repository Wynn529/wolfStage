# C → B：控制适配完成交接（v0.2）

本次按《B接口核查与待补清单》中的C责任完成，不按原日程等待。B源码及interface_protocol.md未修改。C成果使用独立c-ui-baseline分支交接；本交接没有替B确认契约，也没有代用户发送消息。

## C已经交什么

| 原清单条目 | C实现 | 实际验证 |
|---|---|---|
| G01 多事件/游标 | 按旧游标取全部增量，去重、跨局/冲突/缺口检测；不依赖new_event是整数还是对象 | 0事件只换阶段、2事件发言分类、重复返回、冲突/缺口回归 |
| K01 操作幂等适配 | 控制UUID、提交前日志、同会话同ID仅发一次；超时不补发POST；按操作查询确认；可从日志恢复 | 真实HTTP超时替身、同ID重复提交、未知结果锁定、恢复后不重发 |
| G02/K02 等待和暂停 | 控制POST在后台，页面定时只读轮询；本地先停调度再申请暂停；显示等待/本步后暂停/停稳；终态停机 | 参考HTTP+Streamlit AppTest的自动→在途暂停→继续→终局；旧B不支持时不发冲突控制 |
| K03 状态适配 | 配置映射字段/路径，校验game_id、状态、游标、布尔字段；非法契约拒绝启用 | 跨局、未知状态、错误布尔、非法游标、paused仍在途等异常回归 |
| G04/K04 统计核对 | 保留B汇总原件，按calls重算玩家/主持/小模型/评审、首轮/重试、失败/fallback；未知token不记0，差异提示不修原件 | 部分缺失/全部本地不适用/调用数不一致测试；费用未计算 |
| K05 失败停机 | failed/finished/incomplete/degraded、模型/分类失败、控制歧义、事件错误都停止自动；只读刷新不越过未决控制 | 四类终态、legacy超时后刷新仍锁定、连续无进展上限回归 |
| 第六节1—6 | 单请求顺序自动调度、稳定局号与操作ID、等待/暂停展示、终态停、超时只读恢复、增量消费 | C自动化28项全部通过；同一完整测试中原项目32项也通过 |

后台任务不写Streamlit会话，只有页面轮询收集结果。记录恢复不会自动继续运行。浏览器关闭后不承诺后台持续自动调度，已发请求可能仍会完成；重连应按操作ID核对。每个控制器只管理一个浏览器会话，不能替代B的全局互斥或跨进程幂等。

## 核查结果

- `scripts/verify-c-control.ps1 -PythonExe <python.exe> -FullSuite`：**60 passed，34.61s**，2条上游WebSocket弃用提示（包含完整stub局回放扩展验收）。
- 其中C相关28项，原项目32项；与此前针对性13项检查不是可以直接累加的“全部通过”数字。
- 增强控制在 `tests/reference_control_service.py` 参考服务上通过。它明显标REFERENCE_TEST_ONLY，使用虚构测试事件，不是B实现、DeepSeek游戏或正式场次数。
- 旧服务兼容通过：A真实本地HTTP + B原GameEngine/FastAPI + C AppTest，玩家明确StubAgent；无付费API请求。
- 扩展验收：网页控制完整stub局至终态，再回放多次前进/后退；回放期间禁止全部B HTTP，原件字节/修改时间不变。实际浏览器在已有历史stub局完成0→1→7游标及下载，下载ZIP与B原件逐字节一致；见evidence/c_replay_browser_check.json。
- 初轮全量回归曾有1失败/58通过：C在操作完成但旧snapshot仍pending时可能停止轮询。已修为needs_poll持续查询，并加回归；旧失败日志保留为 `evidence/c_control_initial_failure.log`，最终结果见verify日志/XML。
- B的9项文件与之前核查SHA-256一致，并且Git diff为空；确认结果保存在 `evidence/c_b_source_integrity.json`。
- C实际结果台账：`tests/ui_results.csv`；C修复/B待办：`tests/defect_log.csv`。

## B现在只需确认/补齐什么

本节仅列控制适配所需能力；B全部**当前已知**规则/隔离/主持/证据事项已并入配套《B接口核查与待补清单》第三节R01—R12，请一起处理，不能只修下面五项便宣布全系统完成。

1. G03/K01：同局互斥、同一operation_id不重复执行、状态与日志一致；明确重启后的操作ID保存范围。
2. G02/K02/K03：等待/暂停申请/停稳状态及只读查询；按操作ID查询结果，明确控制成功/失败/忙碌/未知响应。
3. G01：确定一次业务操作可新增0/1/多事件，确认末尾游标的字段和phase变化。
4. G04/K04：保留客户端usage/latency及完整统计字段，未知不归零。
5. K05：修复重试成功记录、分类失败状态和恢复语义。

B可采用自己的字段与路径；C已有可配置映射，按真实样例调整即可。参考字段见 `docs/c_control_contract.md`。示例配置 `config/c_service_contract.example.json` 默认全部未确认，故意不能启用自动。

**当前旧B的“自动运行”继续锁定，这是能力检查的预期结果，不是C没有写自动代码。** 收到B修复commit、配置和返回样例后还须在真实B上共同复验。真实DeepSeek、多进程重启/网络故障与完整真实对局未由参考服务代替。

## B如何复现C验收

在含A/B基线的仓库根目录合入本包的C文件，Python3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r model/requirements.txt -r requirements-ui.txt
.\scripts\verify-c-control.ps1
# 可选跑含原项目的全部测试：
.\scripts\verify-c-control.ps1 -FullSuite
```

脚本临时清除当前进程的API密钥/接口配置，测试后恢复，避免带真实密钥调用。默认仅跑C的28项；FullSuite包含原项目。生成的verify日志/XML会更新，初始失败记录不会被覆盖。

若只运行pytest，命令为 `python -m pytest -q tests/test_ui_control.py tests/test_ui.py`。测试应在离线玩家模式；测试中自行启动临时本地HTTP，不需要外网API。

## 文件边界

交接ZIP只含C命名的模块、C测试/参考服务、配置草案、说明、统计/台账与完整性记录，不含B源码、模型权重、密钥、原正式对局或本机环境。**不要整体覆盖你当前app/目录**；按包内相对路径逐文件合入，若已有同名C文件则正常合并。README/MEMORY的本地改动未装入覆盖包，避免覆盖其他成员最新文档。

B回执仍需：修复commit、能力确认、实际响应、回归命令结果、尚未完成项。C不会把这份参考服务测试当成B已经签收。
