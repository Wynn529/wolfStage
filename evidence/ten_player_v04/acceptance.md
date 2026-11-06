# A 十人小模型本地验证

新增可选入口 `model.predict_v04` / `model.server_v04`，支持1—10号、新角色和公开技能宣称、Markdown/JSON正文及空文本规则回退。部署方式及消费者约定见 `model/ten_player_v04/interface.md`。本PR不修改B的调用入口或既有服务默认配置。

模型：small-intent-v0.2-ten-player；schema：small-intent-schema-v0.4；实体：entity-rules-v0.4。用354条旧训练材料和102条审核后的开发材料训练，未以旧测试/探针调参。

| 材料 | 旧模型macro-F1 | 新模型macro-F1 | 固定关键词基线 |
|---|---:|---:|---:|
| 已知旧场景120条 | 0.870475 | 0.870433 | 0.669721 |
| 已知模拟风格50条 | 0.829151 | 0.845428 | 0.551536 |

新增102条内部组CV的OOF macro-F1为0.751760，使用同一CV选参，仅属模型选择开发指标。没有新增独立盲测或真实十人/真人/多供应商对局结果。

102条开发案例的已标注实体字段及技能主要语义均匹配；未标注字段、证据边界和独立抽取泛化不在该数字中。旧120/50条的六组实体字段计数与v032一致，原有错误仍在。完整逐类指标、混淆矩阵、预测、实体检查范围和训练分组均在本目录。

验证命令：

```bash
python -m unittest tests.test_a_ten_player_v04 tests.test_a_entities_v032 tests.test_a_predict_v032 -v
python scripts/evaluate_entities_v04.py
python scripts/train_ten_player_v04.py --output-dir outputs/ten-player-reproduction/model --evidence-dir outputs/ten-player-reproduction/evidence
```

22项单元和本地HTTP测试已通过。独立目录重训的词表、SVM系数及截距与交付模型完全一致。环境未安装外部JSON Schema验证器，协议仅作键集合与JSON往返检查，未宣称完整外部schema验证。

剩余集成：B需要显式切换入口并按版本接收新字段、空分数、实体消费策略；C核对显示；真实十人公开发言与遗言需按对局、真人/AI来源和实际型号留证。角色/技能宣称不等于真实游戏状态，low_info不触发弃票。
