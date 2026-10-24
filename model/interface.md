# A 部分接口与复现

分类模型：small-intent-v0.1；推荐实体规则：entity-rules-v0.3.1；输出结构：small-intent-schema-v0.3。

在仓库根目录使用 Python 3.12，安装：

```sh
python -m pip install -r model/requirements.txt
```

Python 调用：

```python
from model.predict_v031 import classify_utterance
result = classify_utterance(
    game_id="g1", event_id="speech-001", speaker_id=1,
    public_text="我是预言家，昨晚验3号是狼，今天我投4号。",
)
```

可选 HTTP：`python -m model.server_v031 --port 8765`。
`GET /health` 检查服务；`POST /classify` 接收同名四字段 JSON，不能包含隐藏身份或其它私密字段。
输入说话人编号为整数 1—6。输入输出字段见 input.schema.json、output_v031.schema.json。

每条公开发言调用一次。B 保留并传递原文、主标签、完整实体、版本及事件编号；C 按事件展示。
confidence 为 null，class_scores 是 LinearSVC 决策分数，不能显示成概率。
claimed_role、check_claims 都只是本人的公开宣称，不是真实身份或已核实的查验。
vote_target 是意向，不是正式选票；B 的行动接口与规则引擎负责校验正式行动。
疑似违规标签是提示，不能代替权限校验。混合发言仍可能包含实体，需结合原文解释。

多次查验读取 check_claims 数组；多对象关系读取 agreement_targets、suspect_targets、support_targets。
否定投票读取 excluded_vote_targets；改口读取 withdrawn_vote_targets；跟票读取 follow_target。
存在 vote_condition 或 conditional_vote_alternatives 时，不能擅自认定条件已满足。
不从查验、投票或单条赞同推断其它立场，缺少明确指代时保留空值。
完整标注口径见 label_guide.md。

旧入口 model.predict 保留首次分类与实体实验行为。最新接入必须使用 model.predict_v031。
model_manifest.json 中的 rule_version 属于原始训练快照；当前运行版本以返回的 entity_rule_version 为准。

训练与原分类实验复现（输出到新目录，不覆盖交付权重或原结果）：

```sh
python -m model.train --train data/train.csv --output outputs/retrained/classifier.joblib
python -m model.evaluate --output outputs/reproduced_v01
python -m unittest discover -s tests -p "test_a_*.py" -v
```

依赖版本包含运行和训练所需库；上述测试仅使用标准库与运行依赖。
训练重新执行分组交叉验证；评估入口复现旧规则，不能当成新版实体规则成绩。
上传用测试集是从同作者数据池按来源组留出的120条，未满足C/E独立作者测试要求。
50条探针是助手手写模拟代理风格，不是真实DeepSeek输出；真实对局验证目前为0。
domain_check.csv 是空模板，待真实B/C对局日志；不可将模拟数据填入并称为真实域内验证。
旧分类结果见 evidence/small_model_report.md；当前实体诊断见 evidence/entity_v031/report.md。
新版的已知数据回归结果不能当成新盲测准确率。
