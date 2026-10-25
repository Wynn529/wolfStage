# C 网页快速开始（Windows / Python 3.12）

在更新后的 `main` 上，从仓库根目录运行。该入口启动离线玩家和 A 的真实本地分类模型，适合 B/C 联调；不会调用付费 DeepSeek。真实 API 演示另行配置、验收。

## 第一次安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r model/requirements.txt -r requirements-ui.txt
.\.venv\Scripts\python.exe -m pip check
```

无需激活虚拟环境，每条命令使用 `.venv` 中的 Python。模型文件使用仓库已有版本，不需要重新训练。

## 启动网页

```powershell
.\scripts\start-demo.ps1 -KeepAlive
```

保持这个终端窗口打开。看到 `Offline demo ready` 后，用浏览器打开 http://127.0.0.1:8501 。A 分类服务为 8765，B 游戏服务为 8000，网页为 8501。若已有服务占用端口，脚本会报错；先确认是什么程序，不要随意结束他人服务。

需要与已有服务并行测试时，可使用 `scripts/start-demo.ps1 -APort 18765 -BPort 18000 -UiPort 18501 -KeepAlive`，以实际输出网址为准。每个仓库副本只保留一份启动进程记录，并行运行请使用单独副本。

如 PowerShell 策略不允许执行脚本，可分别在三个终端中使用以下 Python 命令：

```powershell
# 终端1：A
.\.venv\Scripts\python.exe -m model.server_v031 --port 8765

# 终端2：B（以下变量只影响当前终端及它启动的进程）
Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
$env:A_CLASSIFIER_URL='http://127.0.0.1:8765/classify'
.\.venv\Scripts\python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000

# 终端3：C
Remove-Item Env:C_SERVICE_CONTRACT -ErrorAction SilentlyContinue
$env:B_API_URL='http://127.0.0.1:8000'
$env:C_RUNS_DIR=(Join-Path (Get-Location) 'runs')
$env:C_JOURNAL_DIR=(Join-Path (Get-Location) '.local/c_operations')
.\.venv\Scripts\python.exe -m streamlit run app/ui.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

这些命令保持本机访问；将 127.0.0.1 链接发给同学不会让他们连上你的电脑。代码共享与网页部署是两件事。

## 页面操作

1. 侧栏选“实时联调”，新建 seed=42 对局，记下 game_id。
2. 单步推进，查看阶段、六人存活和发言分类。一次推进可能新增0／1／多条事件，阶段切换时可能没有新事件。
3. “投票与结果”显示逐票、B原始计票、首轮／重投标记、放逐及终局；缺字段会明确提示。页面不会代B修票数或胜负。
4. “仅上帝视角”查看私密动作与角色自述依据；这些内容不作为玩家输入。
5. 暂停／继续适用于当前手动兼容模式。控制请求未确认时不要反复点击；自动按钮需B增强能力确认后启用，不能手改配置跳过。
6. 侧栏切“本地只读回放”，选择game_id并移动游标；原始日志导出不会请求模型，不导出state.json。真实日志保存在该仓库的runs下，请自行备份。

## 验证与停止

```powershell
.\scripts\verify-c-control.ps1 -FullSuite
# 另开终端停止由start-demo启动的本次服务：
.\scripts\stop-demo.ps1
```

测试使用临时本地服务和离线模型调用替身；完整结果见 `evidence/c_control_verify.log` 和 XML。测试不会自动证明真实API对局或所有规则分支已通过。手动启动的三个终端分别按 Ctrl+C 停止。

启动脚本会为子服务固定本机地址、本仓库日志目录和legacy模式，并临时清除其真实API密钥配置；退出时恢复父进程原配置，不修改系统环境变量。增强B联调另按双方确认的契约配置执行。
