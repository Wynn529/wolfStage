# 完整发言标注规范 v0.3

当前推荐实现为entity-rules-v0.3.1，见interface.md。下文是已经批准的标注口径；没有为提高回归数字而改写原数据。

输入只含公开文本与公开说话人编号。禁止读隐藏真实身份、私密行动、私有推理。每条发言一个主标签，优先级：suspected_violation > check_claim > role_claim > vote_intent > discussion > low_info。实体是另外的人工标注目标；当前实现entity-rules-v0.3.1确定性抽取器；原实验保留v0.1，其预测与本标注规范的实际差距见分版本实测报告，不能把人工标注直接当作抽取结果。

|标签|属于|不属于|
|---|---|---|
|suspected_violation|明确要求越权获取秘密、改写裁决、删改公共事件、剥夺法定行动、明显跑题或辱骂|查询已公开票型、讨论规则、引用越权语句并反对；模糊请求不能仅因有“系统/主持”就判违规|
|check_claim|本人声称已经得到具体查验结果，包括重申；不判断真伪|转述别人、询问、假设或未来查验计划|
|role_claim|本人肯定声明具体角色；以明确更正后的当前声明为准|“我是好人”仅阵营；转述角色；“如果我是预言家”|
|vote_intent|当前肯定、否定、条件、跟票、改票、撤回或弃票；明确号召他人投票也属于|历史票型描述、单纯怀疑、泛泛说以后再决定|
|discussion|具体证据分析、规则澄清、提问、附议观点或站边，且无更高标签|没有具体内容的礼貌套话、本人身份/查验被忽略后的片段|
|low_info|没有前述有效信息，即使长、书面、带分析框架仍可低信息|“我作为平民暂时没判断”应为role_claim；“3号的分析漏证据”应为discussion|

一条完整发言不得按意图拆成多条后随机分到两边。恢复来源全句后必须重新判主标签。例如“我是村民，5号论据不足”整体为role_claim；“我是预言家，验6号狼，今天投6号”整体为check_claim，三个实体均保留。

## 实体及范围

|字段|口径|
|---|---|
|player_ids|文本提及的编号集合1—6，不是怀疑对象集合；夜次不算编号|
|claimed_role|本人当前明确自称的villager/seer/werewolf；werewolf只代表自称，不能推断玩笑、自爆或真实角色。本版主池不以主动认狼填充身份类|
|check_claims|本人查验宣称的对象数组：target、result(wolf/non_wolf)、night。未明确夜次为null；不从昨晚猜第几夜；不自动把查验者补成seer|
|check_target/check_result|仅一个查验宣称时同步旧接口；多个则null，必须读取check_claims|
|vote_target|本人当前生效的唯一肯定投票意向/号召对象；不是正式有效票。只有条件意向时配合vote_condition解释|
|follow_target|明确承诺跟随谁的投票建议；请1号归票不等于听1号，站边不等于跟票|
|excluded_vote_targets|明确当前不投的对象；不从过去支持、撤回旧意向自动推出永久排除|
|withdrawn_vote_targets|本句明确撤回的旧目标；撤回不表示已决定其他目标，也不表示永远不投|
|vote_condition|非空时本句肯定/排除意向受此条件限定；由调用方保留原条件，不能擅自认定已满足|
|conditional_vote_alternatives|存在当前默认票和未来切换条件时记录[{target,condition}]。例N013当前2，条件满足才改4；vote_target仍2，不提前选4|
|agreement_targets|当前本人明确赞同某人的观点、分析或建议；不据此认定其身份或跟票。引述别人赞同、历史已撤回赞同不计|
|suspect_targets|当前本人明确表示怀疑、偏狼等的对象；仅询问、列假设、说证据不足不自动抽取；怀疑不补投票|
|support_targets|当前本人明确站边/认可偏好人或认可预言家身份的对象；认可单条理由只记agreement，不自动支持身份；是立场而非阵营事实|
|agreement_target/suspect_target/support_target|各自数组仅一人时返回编号；多人时单值为null且数组完整保留，不能把多人写成字符串塞进编号字段|
|advisory_flags|疑似异常提醒。混合违规仍保留文本实体，均不得提升为核实事实；整句是否有效由B规则策略决定|

vote_mode：affirmative、negative、conditional、follow_unresolved、multiple_unresolved、abstain、withdrawn_unresolved、not_expressed_or_unresolved。条件备选有独立字段时当前默认肯定票可保持affirmative。空值意味着没有唯一可支持的肯定抽取，不能代替阅读原文。

只允许本句内有唯一明确先行对象的指代消解；“我同意3号，所以投4号”抽agreement=3、vote=4。“我同意他”没有前文编号时均不猜。说话人以第三人称报自己的编号，须结合公开speaker_id；其它玩家的“2号是预言家”不能默认成自己的角色。

该轮新增三种社会关系是公开文本中的态度标注，不是社交图真相。65条主池样本有非空社会关系；多目标/否定/转述/历史反例同样需要保留。字段增多会增加规则抽取难度，后续应逐字段报告非空样本数、精确率/召回或集合匹配率，不以大量空值抬高正确率。
