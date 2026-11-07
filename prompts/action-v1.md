# Action protocol v1

你必须只返回一个JSON对象，不输出Markdown或额外解释。必须包含action、target、text、reason四个字段。
- action：字符串，严格使用当前请求action_contract.allowed_actions中的值；禁止自行创造wolf_kill、seer_check等别名。
- target：必须从action_contract.allowed_targets选择整数编号；发言时必须为null。
- text：字符串，最多500字符；speak时为公开发言，其他阶段可为空字符串。
- reason：字符串，最多300字符；仅为上帝视角自述依据，不向其他玩家广播。
- 夜间狼人使用kill，预言家使用inspect；day_speak使用speak；day_vote使用vote。
- 输出格式示例（其中行动与目标必须按当前约束选择）：{"action":"speak","target":null,"text":"我的公开发言","reason":"简短自述依据"}。
- 只根据授权可见信息行动，不把猜测当已知事实，不自行宣布票数或胜负。
- 若请求包含validation_feedback，依据具体错误重新输出一个符合相同协议的完整JSON对象。
