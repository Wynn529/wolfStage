# C 网页基线验收

版本：c-ui-v0.1。开发回归使用离线玩家与真实 A 本地模型，不算真实 DeepSeek 对局。

| ID | 操作/条件 | 预期 | 验证方式 |
|---|---|---|---|
| C01 | 新建 seed=42 | 返回真实 game_id，显示六人身份 | AppTest + B HTTP |
| C02 | 暂停后继续 | B状态与按钮同步 | AppTest + B HTTP |
| C03 | 多次单步到白天 | 发言与真实A分类通过source_event_id关联 | AppTest + A/B HTTP |
| C04 | 刷新同一游标 | 无重复事件，无新增模型调用 | AppTest + B HTTP |
| C05 | 历史事件前缀投影 | 不展示未来分类、死亡和胜方 | helper回归 |
| C06 | 本地导出 | 文件字节与B原件一致，无state.json，不改calls | helper回归 |
| C07 | 增量含重复/跨局/同号冲突 | 重复去重，跨局与冲突报错 | helper回归 |
| C08 | 请求超时/服务断开 | 错误提示，禁止盲目重试 | 已有故障注入/AppTest回归，见test_ui_control.py；真实外部网络另验 |
| C09 | 只读回放浏览器操作 | 滑块显示回放，调用无增量 | 已验：本机历史stub局滑块0→1→7、分类与存活显示、下载ZIP逐字节核对，8个原件哈希/修改时间不变；另有完整stub局AppTest禁止所有HTTP并前后拖动。见evidence/c_replay_browser_check.json；非真实API对局 |
| C10 | 自动运行/并发暂停 | 事件边界可靠，操作不重复 | C已实现并通过参考服务/AppTest；旧B能力未确认，实服自动仍锁定 |

本清单不能作为全部通过的声明；自动结果以pytest日志为准，人工结果另记录。
