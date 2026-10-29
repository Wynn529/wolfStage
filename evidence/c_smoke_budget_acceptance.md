# C 首轮真实接入准备复核

2026-10-29，用户授权本轮最多花 1 元。

实际只读健康检查：B /c/ready 返回 real_deepseek、DeepSeekAgent、deepseek-chat、分类已配置；A /health 返回 small-intent-v0.1 / schema-v0.3。未创建真实局或发送生成请求。

核对官方资料后发现 deepseek-chat 已退役，C 启动默认改 deepseek-flash。增加 C 专用受限客户端：6 次请求，8000 输入 UTF-8 字节，512 输出 tokens，禁用思考；发送前持久预留 0.15 元/次，共最多 0.90 元，失败不退还。输入按最多 8000 token 加 2048 协议余量、单价 10 元/百万，输出按 50 元/百万估算，每次上界 0.12608 元，小于预留；当日官方 Flash 高峰输入2/输出8元每百万，价格余量充足。限额仅覆盖本入口，不覆盖账号其他消费，实际扣费以平台为准。

13 项本地检查通过（4.70秒），不使用真实凭据或联网生成，涵盖持久限额、失败计数、超长输入拒绝、请求参数、辅助usage、截断失败、旧模型拒绝及真实启动配置/界面。pytest 进程退出时有 sandbox 系统临时目录清理权限提示，测试结果和退出码为成功。B 指定源码及协议相对3898b95 diff为空。git diff --check通过。

旧模式服务已正常停止，原始记录和启动日志保留。用户需重新双击启动入口并本机输入密钥；本轮仍未进行付费生成或真实完整对局。下一步先到第一条公开发言和A分类即停止、核对原件。

官方依据：
- https://api-docs.deepseek.com/news/news260424/
- https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
- https://api-docs.deepseek.com/guides/thinking_mode/
