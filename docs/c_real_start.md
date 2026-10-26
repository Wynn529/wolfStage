# C 真实 DeepSeek 模式启动

此入口只启动本机 A 分类、B 游戏服务和 C 网页；不会创建或推进对局，不检查余额，不向 DeepSeek 发请求。B实现不变，C入口只指定真实代理和独立日志目录。现有start-demo.ps1继续用于离线演示。

## 使用顺序

1. 运行scripts/start-real.ps1；若当前进程没有密钥，会在本机终端隐藏输入。不要把密钥发到聊天、存到脚本或提交到Git。此入口不会保存密钥，也不自动读取.env。
2. 启动完成后打开http://127.0.0.1:18514。默认A=18782，B=18082，网页=18514，均只绑定本机。
3. 页面显示“真实DeepSeek模式”。“开始对局”只分配身份；“单步推进”可能产生付费请求，需确认API账户额度及本轮上限后才操作。当前阶段自动运行默认关闭；此设置不构成费用硬上限。不要把登录账号或已有网页会员当成API额度证明。
4. 在启动终端按Ctrl+C结束，或运行scripts/stop-real.ps1。日志与对局原件保留。

仓库已安装环境时可运行：

```powershell
.\scripts\start-real.ps1 -PythonExe .\.venv\Scripts\python.exe
# 只做本机配置检查，不启动服务、不发请求、不提示输入密钥：
.\scripts\start-real.ps1 -PythonExe .\.venv\Scripts\python.exe -CheckOnly
# 停止真实模式，不停止离线演示：
.\scripts\stop-real.ps1 -PythonExe .\.venv\Scripts\python.exe
```

模型名默认沿用仓库的deepseek-chat，可用-Model指定团队确认的名称。脚本不联网验证模型是否可用。若端口已占用则停止启动，不结束已有服务；可通过-APort/-BPort/-UiPort指定三个不同端口。

## 数据和运行方式

真实对局保存在runs/real_api/<game_id>/；控制记录在.local/real_api/c_operations/；每次启动日志在.local/real_api/sessions/<session_id>/，启动记录在.local/real_api/processes.json。未取得真实返回前不计真实场次。

密钥只传给B子进程，A和网页进程不接收该密钥。环境修改只针对本次进程，不写用户/系统环境。停止时按进程编号和创建时间核对，只停止该入口创建的进程树。一次只运行一个真实模式实例。

B服务增加的/c/ready只返回模式、模型名称、分类是否配置和真实日志目录，不返回密钥、Prompt或身份。健康检查只读本机GET，不推进游戏。页面内恢复旧控制记录也不会自动重发。

本入口不修复B已记录的规则、usage和重试缺陷，也不提供整局费用/token硬上限。首次真实请求与正式对局应按C接入前核对清单执行，失败原件必须保留。
