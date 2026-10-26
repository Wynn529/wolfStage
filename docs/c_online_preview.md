# C在线样式演示版

只发布preview目录中的静态页面，不托管Python游戏服务。演示复用C的圆桌HTML和已有离线对局日志的逐步投影；原句、分类与出局均来自记录，页面不创造真实对局。页面固定标注离线演示，不调用A/B或DeepSeek，不接收API Key。首次进入显示一段已分类的发言，用户可从发牌重新播放。

准备好preview/index.html和.github/workflows/c-preview.yml后：先push到main；由仓库有权限的成员在Settings > Pages选择GitHub Actions；再到Actions选择C UI preview > Run workflow，在main运行。成功后用Pages显示的地址分享。URL预计为https://Wynn529.github.io/wolfStage/，发布前不能称为可用链接。如果没有Settings/Pages权限，由仓库拥有者Wynn529操作该设置。

工作流只手动触发，只上传preview，不发布完整仓库或runs目录；后续改样式后重新构建并手动发布。旧Streamlit本机程序不改，在线演示不提供真实建局/API运行、完整检查区、全部日志导出或正式统计。当前用途是收集样式和操作反馈。

构建时使用明确的stub记录，默认runs/b4e7eaa6a071/events.jsonl；运行scripts/build_c_preview.py。生成结果已保存到preview/index.html，其他成员发布不需要取得本机runs原件。预览页须标明同一源game_id与记录来源，截图不作为真实DeepSeek实验结果。
