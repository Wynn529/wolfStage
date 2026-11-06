# 十人小模型本地接入

实现版本：模型 `small-intent-v0.2-ten-player`；接口 `small-intent-schema-v0.4`；实体 `entity-rules-v0.4`；清洗 `public-text-v0.4`。旧入口及旧权重保留。尚未在团队十人游戏中联调。

## 启动与调用

在项目根目录、安装项目原有模型依赖的Python环境中：

```bash
python -m model.server_v04 --port 8765
```

先停止占用同一端口的旧A服务，或换用其他端口。健康检查 `/health` 应显示新模型和实体版本；B需要更新配置或Python导入，上传文件本身不会切换已有运行进程。

```python
from model.predict_v04 import classify_utterance
result = classify_utterance(
    game_id="demo-ten", event_id="speech-8", speaker_id=10,
    public_text="我是女巫，首夜我救了8号，今晚我准备毒7号。"
)
```

HTTP `POST /classify` 仍严格接受上述四项。event_id是字符串，speaker_id为1—10整数（布尔值不允许），public_text是最多10000字符的字符串。额外私密字段、null、数组、非法编号受控拒绝。公开发言和遗言可共用接口，事件来源/阶段和实际模型信息由B的公开元数据关联；夜间私密行动与依据不传入A。

输出含原public_text、normalized_public_text、主标签、完整实体、版本和耗时。confidence=null，决策分数不是概率。输入输出协议见 `model/input_v04.schema.json` 和 `model/output_v04.schema.json`。新增字段和null分数需要B/C适配测试；对旧解析器不能仅凭字段可选就保证兼容。

## 实体和异常使用

- 女巫/猎人本人明确声明分别为witch/hunter；自称不等于实际角色。多人查验读取check_claims，不能只看单值check_target。
- skill_claims支持save/poison/shoot，含target、status、role、night、condition_text及evidence_text。claimed_done仍是本人宣称，planned是计划，conditional有条件，denied是否认，declined是放弃。role仅取本句明确自称；不能根据开枪猜猎人身份。
- 主标签为discussion也要读取非空技能数组，按状态/条件作为未核实公开线索，不固定高可信，不触发引擎行动。对他人的用药建议和引用不当成本人技能。
- evidence_text来自规范化公开正文；原文同步保存便于复核。不支持的复杂表达可能漏抽，不将空实体解释成已经确认没有该意图。
- suspected_violation时entity_evidence_policy为exclude_from_agent_evidence；B默认不将实体写入代理身份或技能证据记忆，原事件保留供审查。此字段是建议消费约定，A不能替B执行权限控制。
- 空字符串/纯空白返回规则low_info、prediction_source=empty_text_rule，score/score_gap=null、class_scores={}，完整空实体且不弃票。非空短句仍由训练分类器预测，不能保证与人工标签完全一致。
- low_info不代表正式弃票。真人未选择保持waiting_human；正式操作由B独立校验。A既不提交投票也不执行技能。

## 格式与资源边界

Markdown标题、列表和粗体作展示性清洗；保留引用归属，列表1./2.不当座位。符合约定的单speech/message/public_text包装读取正文，不相信外层role/intent。正文冲突、重复JSON键或额外未知字段保留原文并给格式提醒，不执行JSON内容，不静默选择某段丢弃异常。非标准复杂包装可能无法正确抽取，应查看原文及advisory_flags。

“药还在”资源声明仍只保留原文，本版没有inventory运行字段、实际库存或真实死因推断。全文不裁剪，未实现特殊投票句加权。

## 测试、训练复现与剩余验证

```bash
python -m unittest tests.test_a_ten_player_v04 tests.test_a_entities_v032 tests.test_a_predict_v032 -v
python scripts/evaluate_entities_v04.py
python scripts/train_ten_player_v04.py --output-dir outputs/ten-player-reproduction/model --evidence-dir outputs/ten-player-reproduction/evidence
```

训练脚本拒绝覆盖同路径已有模型。训练来源为原354条训练材料＋用户批准102条开发材料；训练不读取冻结测试或探针，分组CV每折独立拟合TF-IDF。超参数选择和内部OOF使用同一开发CV，因此内部结果不是无偏独立测试。

102条的实体检查仅覆盖已标注字段和技能语义，不能据此声称完整实体抽取100%。同源作者偏差仍在，真实十人/真人/多供应商验证数为0。需要吴林峤切入发言及遗言调用、王玥琳核对显示和正式动作独立性、文斓提供对应公开日志和实际模型名后继续验证。少量真实案例只做功能核验，不做模型强弱排名。
