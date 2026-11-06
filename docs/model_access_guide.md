# 九家模型配置与本机验证

日期：2026-11-06。配置由本会话中用户逐家提供的官方代码和型号确认整理，不包含真实密钥。

## 当前完成情况

`config/nine_models.example.json` 是一真人九AI样例，九个AI分别引用九个不同型号。每个 profile 有供应商、型号、接口、协议、超时、密钥环境变量及请求选项；请求选项用 JSON 字符串保存以保持冻结配置不可变。复制配置后可重复引用同一型号，全AI十席可将第10席设为ai并引用任一profile。

`app/provider_client.py` 已实现非流式 Chat Completions 与方舟 Responses 的请求/解析；`app/seat_router.py` 的工厂已支持九个供应商。旧六人默认 DeepSeekAgent 服务入口仍使用原有客户端，不自动换成这份配置；公共十人引擎需要按此前交接说明接入。

| profile_id | 型号 | 密钥环境变量 |
|---|---|---|
| mimo | mimo-v2.6-pro | MIMO_API_KEY |
| tokenhub | hy4-preview | TOKENHUB_API_KEY |
| minimax | MiniMax-M3.1-Flash | MINIMAX_API_KEY |
| kimi | kimi-k3.1-pro | KIMI_API_KEY |
| ark | doubao-seed-2-1-lite-260915 | ARK_API_KEY |
| qianfan | ernie-5.1 | QIANFAN_API_KEY |
| zhipu | glm-5.3 | ZHIPU_API_KEY |
| deepseek | deepseek-flash | DEEPSEEK_API_KEY |
| bailian | qwen3.8-max-0902 | DASHSCOPE_API_KEY |

千帆使用普通API Key对应的 `https://qianfan.baidubce.com/v2`；用户在首次401后澄清密钥实际来自普通API Key管理页，故替换此前误配的个人Token Plan地址，普通地址已复测通过。百炼使用用户提供的北京业务空间专属域名。腾讯旧泄露密钥已由用户确认更换，不复制旧值。

## 第一步：无请求检查

在 PowerShell 运行：

```powershell
Set-Location -LiteralPath 'D:\狼人杀小组合作\wolfStage\wolfStage'
.venv/Scripts/python.exe scripts/check_model_access.py
```

它只显示配置与当前进程能否读取对应环境变量，不打印密钥、不发请求。key_present=false 不代表密钥无效，也不代表其他终端/旧服务没有设置。

## 第二步：指定一家，隐藏输入密钥

以下命令会发出一次真实请求，可能计费。建议先只测MiMo：

```powershell
.venv/Scripts/python.exe scripts/check_model_access.py --live --profile mimo --prompt-key
```

出现 MIMO_API_KEY 提示后粘贴密钥，输入时不显示字符，按回车。密钥仅保存在此次脚本进程内存，不写文件。必须使用本机交互终端，脚本拒绝在重定向输入时降级为明文输入。

测试其他家只替换 --profile，例如 tokenhub、minimax、kimi、ark、qianfan、zhipu、deepseek、bailian。每次只发一次请求，无隐式重试，无自动切换型号；不自动批量测试。配置的timeout为120秒，推理服务耗时和费用以实际响应及平台账单为准。

成功需同时满足 meta.success=true 与 action_fields_valid=true；仅连接成功不算游戏协议通过。脚本测试“投2号”的模拟动作，不是一场真实对局。action_fields_valid=false 时不要反复付费重跑，先检查接口/参数和失败元数据。

结果保存在 `.local/model_checks/`，只含型号、时间、返回用量、耗时、结果，不保存密钥、完整请求/回复或思考内容；.local已被git忽略。可以将结果JSON发回本会话排查。HTTP错误只保存状态码和异常类型，不保存供应商错误正文；401/403需检查凭据和权限，429需检查额度/流控，400需检查参数。实际原因仍以控制台诊断为准。

## 验证边界

2026-11-06用户确认方舟改用已开通Lite。ark当前引用doubao-seed-2-1-lite-260915，保留原Pro失败日志及登记，不把Pro的ModelNotOpen当作Lite测试结果；Lite已通过单次真实验证。

2026-11-06诊断补充：HTTP失败新增provider_error_code，只保存官方已知错误枚举，不保存message或原始错误正文。方舟404可进一步区分ModelNotOpen（未开通）、InvalidEndpointOrModel.NotFound（型号/接入点不存在或无权限）、ModelIDAccessDisabled（需使用自定义接入点）和PathNotFound（路径）。没有子码时不直接断定原因。

2026-11-06更新：当前配置的九家所选型号均通过一次真实JSON投票测试，返回型号匹配、行动字段通过，且各有token和耗时记录。详见 evidence/model_access_summary.md、evidence/model_access_successes.json 及 evidence/model_validation.csv。九次成功证据不能替代完整多模型局、所有角色技能验证或稳定性能评估。脚本不会自动改登记表，后续按证据路径更新状态。

下一阶段还需要：按新角色的真实行动契约测试、AI/真人统一执行接线、失败尝试日志与未知用量聚合、同局多模型行动、真人权限、五身份场景和正式完整局。所有规则继续归统一规则引擎负责。

## 协议依据

- [MiMo首次调用](https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call)：Chat接口与凭据。
- [MiniMax OpenAI兼容](https://platform.minimax.cn/docs/api-reference/text-openai-api)：思考参数/生成长度；Flash配置low且不传temperature。
- [方舟Responses创建](https://docs.volcengine.com/docs/ark/create-model-responses-api?lang=en)、[上下文管理](https://docs.volcengine.com/docs/ark/responses-api-text-generation?lang=en)：input消息、output_text、完成状态和store=false。每次完整传入本人已过滤上下文，不使用previous_response_id。
- 其余地址/型号采用用户本会话确认的官方curl与文档段落，单次真实验证证据已收录至 evidence/model_access_successes.json。
