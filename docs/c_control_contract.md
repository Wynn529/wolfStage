# C控制适配契约草案（交B确认）

本文件定义C已经完成并以受控服务验证的适配形状，不是B已签收的现行协议。不改interface_protocol.md，也不要求B必须采用相同字段名。

## 兼容旧服务

未配置C_SERVICE_CONTRACT时为legacy。create/advance/pause/resume/events/metrics沿用现有路径；单步按一次业务操作处理，new_event可为末尾整数或事件对象。0/1/多事件均通过after_event_id拉全量增量。旧服务无在途状态与操作查询，无法可靠消除超时歧义，因此歧义控制请求后保持锁定；刷新事件不能擅自解除锁定。自动运行关闭，旧服务在途请求期间不允许发送冲突控制。

## 待B确认的增强契约

- 每个控制动作生成UUID operation_id；一个动作只提交一次，不因页面重运行、轮询或超时自动重发。请求同时带expected_event_id，B自行约定旧游标拒绝策略。
- 控制路径仍为POST /games/{game_id}/{action}，action为advance/pause/resume；请求字段operation_id、expected_event_id可在配置fields中改名；可配置idempotency_header补充操作ID。
- 只读状态GET /games/{game_id}/state：game_id、status、phase、last_event_id、in_flight、pause_requested。状态字段可在配置state_fields中改名。status允许running/pending/paused/finished/failed/incomplete/degraded；phase为非空字符串；ID非负整数；两个布尔值必须是实际JSON布尔值。
- 只读操作GET /games/{game_id}/operations/{operation_id}：game_id、operation_id、action、state（pending/succeeded/failed/rejected）。成功时含result，失败含error_code。此响应字段暂按本草案；若B采用其他结构，由C按最终样例补映射，不猜测。
- 控制200表示结果返回；202表示已接收未完成；409/503等的具体策略由B选择。C对含歧义的响应都先查询同operation_id，不重发POST。只有明确succeeded且状态/事件已追平才允许下一次advance。
- pause在模型等待中可接收，返回/状态应区分pause_requested与paused。C先停本地调度，再申请暂停；已有advance结束后B在边界确认暂停。
- failed/incomplete/finished/degraded均停自动。degraded需要人工核对，不自动继续。未知状态、缺字段、跨局、事件缺口、同号冲突都停机。
- metrics保留B原件；C从本机calls只读重算各caller_type、attempt、success/fallback和token缺失数，差异只报告不改原日志。费用未约定前显示未计算。

## 配置启用

C_SERVICE_CONTRACT指向本机JSON。必须包含：version、confirmed_by、confirmed_commit、capabilities，后者含per_game_serialization/idempotency/boundary_pause/state_query/operation_query五项true。此声明必须由B修复与共同回归后提供，不能把测试替身声明当真实服务确认。

可选paths控制state/operation路径；fields控制operation_id/expected_event_id请求名；state_fields控制状态字段映射；idempotency_header控制请求头。默认接口与本草案相同。页面显示契约版本与确认人，缺确认/能力时拒绝启用。

配置声明不能替代B的锁、幂等持久化与故障恢复实现。未来B变更commit后要重新确认。多浏览器/多进程隔离由B保障；C会话控制器不是全局锁。

## B收到后的配合

请给确认后的配置与实际返回样例；确认游标/0事件推进单位；实现互斥、操作去重、等待/暂停状态和查询。C的自动功能在明确标记的参考服务上验证，现有B依然只开放手动兼容模式。真实API、多进程重启及线上网络行为另验收。
