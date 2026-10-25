# C 基线验证记录

日期：2026-10-25。分支 c-ui-baseline，基于 c4b64e4。Python3.12.10，Streamlit1.41.1，FastAPI0.115.6。

- pip check：No broken requirements found。
- pytest -q：36 passed，2条上游WebSocket弃用提示，11.65秒。
- 新增4个自动测试函数，覆盖事件冲突/跨局、回放前缀状态、原件字节导出、真实A HTTP+B HTTP+Streamlit操作。
- 实际浏览器：建局bb4389d30d23，seed42；五次单步后公开发言event6、真实分类event7，source_event_id=6。页面显示5人存活，模型small-intent-v0.1、实体规则entity-rules-v0.3.1、分数未校准。
- 来源：玩家stub，本地分类真实。此局尚未终局，不算真实DeepSeek局或完整对局证据。
- 自动推进、真实付费API、故障注入、浏览器完整回放与导出仍待验收。B已知规则/信息隔离/统计缺陷未在本分支修复。
- 启动注意：Codex工具中的后台子进程会随启动会话结束被回收，本次以start-demo.ps1 -KeepAlive保持开发会话；普通PowerShell可由启动脚本运行。关闭开发会话后若页面不可用，重新启动即可。
