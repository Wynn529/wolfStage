# C在线样式演示版

只发布preview目录中的静态页面，不托管Python游戏服务。演示复用C的圆桌HTML和已有离线对局日志的逐步投影；原句、分类与出局均来自记录，页面不创造真实对局。页面固定标注离线演示，不调用A/B或DeepSeek，不接收API Key。首次进入显示一段已分类的发言，用户可从发牌重新播放。

准备好preview/index.html和.github/workflows/c-preview.yml后：先push到main；由仓库有权限的成员在Settings > Pages选择GitHub Actions；再到Actions选择C UI preview > Run workflow，在main运行。成功后用Pages显示的地址分享。URL预计为https://Wynn529.github.io/wolfStage/，发布前不能称为可用链接。如果没有Settings/Pages权限，由仓库拥有者Wynn529操作该设置。

工作流只手动触发，只上传preview，不发布完整仓库或runs目录；后续改样式后重新构建并手动发布。旧Streamlit本机程序不改，在线演示包含四个只读检查页：公开事件与分类、投票与结果、仅上帝视角、统计与原始事件，随当前回放位置同步；可下载当前事件查询副本。在线演示不提供真实建局/API运行、其他本机对局列表、完整原件ZIP导出或正式实验统计。当前用途是收集样式和操作反馈。

构建时使用明确的stub记录，默认runs/b4e7eaa6a071/events.jsonl；运行scripts/build_c_preview.py。生成结果已保存到preview/index.html，其他成员发布不需要取得本机runs原件。预览页须标明同一源game_id与记录来源，截图不作为真实DeepSeek实验结果。


## 完整检查区补充

用户希望展示下方页面，现将其加入静态演示。事件、投票与身份都沿用源记录；调用统计由C现有summarize_calls按已显示事件可关联的calls重算，不重新预测标签或裁决票数。A分类调用仅在对应分类/失败事件已显示后计入，避免回放先显示未来调用成功信息。B整局汇总只在回放到结局时显示；其它位置标为尚未展示，不拿整局统计冒充当前前缀。unknown/null保持缺失或不适用，不虚构费用。下载只含当前已显示事件，标明查询副本，未改变原件。
