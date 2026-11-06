# 九家模型接入实施

沿用已批准十人升级设计及文斓接入边界，依据用户逐家提供的官方调用示例实施。

顺序：登记九个实际型号与环境变量 → 扩展席位工厂 → 适配八家 Chat Completions 和方舟 Responses → 离线模拟回归 → 本机隐藏输入密钥并显式执行单次真实验证 → 十人引擎联调。

配置不包含密钥；模型、协议、地址和参数在开局配置中冻结。使用标准库 HTTP 避免增加九套 SDK。请求每次无状态，不使用 previous_response_id 或跨席共享对话。最终行动仅从 content 或 Responses 的 output_text 内容解析；忽略 reasoning_content。不修编模型输出，不静默换模型，不自行重试。

按官方示例配置参数：MiniMax low 思考，MiMo 使用 max_completion_tokens，DeepSeek 使用其 thinking/reasoning_effort；其余先传最小参数。用户提供的方舟示例为 Responses，使用 /responses 和 input。不向尚未确认支持的模型强行添加 response_format。Prompt 明确要求单个行动 JSON，实际游戏仍由统一规则校验。

HTTP 错误、无正文、截断和非法 JSON 记录脱敏失败；解析失败仍保留已收到的 usage。未知型号/用量不猜测，分别保存 requested_model、响应 model；响应未提供型号则保持未知。

本批仅请求结构和解析离线验证；不执行付费请求。确认供应商可调用、角色契约与定向技能成功后，才可计入真实接入数量。旧六人默认入口保持不变，待十人公共服务接线。
