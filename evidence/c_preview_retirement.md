# C 在线演示退役检查

日期：2026-10-29。

- 用户确认成员已验收样式，授权删除在线演示专用内容；已删除 14 个专用文件，仓库外 outputs/archive/c_preview_retired_20261029 保留副本。
- 原本 app/ui.py、app/ui_scene.py、app/scene/index.html、start-real/stop-real 入口保留，未修改。
- A/B 实现未修改；B 对 3898b95 的指定源码/协议 diff 为空。
- app/scripts/docs/tests 中未发现已删除构建器、专用资产或在线演示说明的残余引用；MEMORY 的历史记录保留。
- git diff --check 通过。
- 真实启动本机配置/密钥隔离/停止保护相关测试 6 passed、2 deselected。初次执行遇到系统临时目录错误，指定工作区临时目录后完整执行在 ASGI 检查处等待，已中止；本轮没有将该检查与 UI 检查记为通过。未修改真实模式代码，之前的测试证据仍保留，正式接入前需做实际启动核查。
- 未输入真实密钥、未发起付费请求、未创建真实对局。
- 此次删除不自动撤下已发布的 Pages 网站；没有操作远端设置。
