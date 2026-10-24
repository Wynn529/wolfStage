"""Conservative public-text rules. No label lookup, training data, or hidden state."""
from __future__ import annotations
import re
from model.common import RULE_VERSION

NUM = r'(?<!\d)([1-6])\s*号?(?!\d)'
ROLE = r'(预言家|普通村民|普通平民|小村民|村民|平民|狼人|狼牌)'
ROLE_MAP = {'预言家':'seer', '狼人':'werewolf', '狼牌':'werewolf',
            '普通村民':'villager','普通平民':'villager','小村民':'villager','村民':'villager','平民':'villager'}
RESULT = r'(非狼人|不是狼人|不是狼|非狼|不是好人|好人|金水|狼人|查杀|狼)'
RESULT_MAP = {'非狼人':'non_wolf','不是狼人':'non_wolf','不是狼':'non_wolf','非狼':'non_wolf','好人':'non_wolf','金水':'non_wolf','不是好人':'wolf','狼人':'wolf','查杀':'wolf','狼':'wolf'}
VOTE_VERB = r'(?:改投|投票给|投给|投票|投|放逐|出掉|抗推|归票|票给|把票给|把票交给|把票投给|将票交给|把票从)'
CLAUSE_SPLIT = re.compile(r'[，,。；;！!？?\n]+')


def normalize(text):
    return re.sub(r'([一二三四五六])(?=\s*号)', lambda m:str('一二三四五六'.index(m[1])+1), text)


def public_player_ids(text):
    cleaned = re.sub(r'第?[一二三四五六1-6](?:轮|夜|天|次)', '', normalize(text))
    # Supports 1、4号 and 六号; quantities/night numbers are deliberately excluded.
    return sorted({int(m) for m in re.findall(r'(?<![\d第])([1-6])(?![\d])', cleaned)})


def _clauses(text):
    # Quoted material can belong to another speaker or be a counterexample.
    # Retain its numeric mentions in player_ids, but do not promote its assertions.
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', lambda m:' ' * len(m[0]), text)
    return [x.strip() for x in CLAUSE_SPLIT.split(text) if x.strip()]


def _third_party(clause):
    return bool(re.match(r'(?:[1-6]号(?:玩家)?(?:说|声称|报|认为|建议|希望|劝|刚才)|有人说|他说|他说的|据[1-6]号)', clause))


def _unasserted(clause):
    return bool(re.search(r'如果我是|假如我是|假设我是|若我是|我不是在说我是|我没有(?:认|声称)|我没说|只是举例|这不是我的决定', clause))


def _role(clauses, speaker_id):
    found = None
    for c in clauses:
        if _third_party(c) or _unasserted(c):
            continue
        for m in re.finditer(ROLE, c):
            before = c[:m.start()]
            after = c[m.end():]
            if re.search(r'我(?:认为|相信|希望|觉得|判断)|你是|他是|是不是', before):
                continue
            if re.search(r'(?:不是|并非|不认|不是在认|不接受.*是)\s*(?:一个|一张|那张)?$', before):
                continue
            # Explicit self declarations only; "我是个狼人的话" is hypothetical.
            if re.match(r'(?:的话|时|就|才会)', after) and re.search(r'如果|假如|我是个', before):
                continue
            prefix = bool(re.search(r'我(?:是|真是|就是|才是|认|报|声明|自称|拿|作为|身为|这张|手里|以|自己认|本人声明|可以公开的身份)|我的(?:身份|底牌|.*声明)|(?:作为|身为)(?:一名|一个|普通)?$', before))
            reverse = bool(re.match(r'(?:是我|是我的身份|的身份我认|牌在我手里|是我的|这个身份我认|这个身份上)', after))
            if before.endswith('我的') and after.startswith('声明'):prefix=True
            if speaker_id and re.match(rf'{speaker_id}\s*号\s*(?:是|玩家是)?\s*$', before):
                prefix = True
            if prefix or reverse:
                found = ROLE_MAP[m[1]]
        if re.search(r'预言家在我这里|现在正式起跳.*预言家|我的身份声明是预言家|我这边亮出的身份是预言家', c):
            found = 'seer'
        if re.search(r'我的(?:身份|底牌).*村民|我的(?:身份|底牌).*平民', c):
            found = 'villager'
    return found


def _checks(clauses, claimed_role):
    checks = []
    report_context = False
    current_night = None
    for c in clauses:
        if _third_party(c) or _unasserted(c):
            continue
        if re.search(r'今晚|以后|下一夜|将来|打算', c) and not re.search(r'昨|已经|亲验|验出|查杀|金水|结果|查到', c):
            continue
        if re.search(r'我没验|我没有查验|没验过|我还没验|不是我的结果|不是我的查验', c):
            continue
        night_match = re.search(r'(?:第)?([一二三四五六1-6])夜|首夜|次夜', c)
        if night_match:
            token = night_match[1]
            current_night = int(token) if token and token.isdigit() else ('一二三四五六'.index(token)+1 if token else (1 if '首夜' in night_match[0] else 2))
        elif not report_context:
            current_night = None
        self_report = bool(re.search(r'我.*(?:查验|验过|验出|验到|查到|验了|亲验|报过.*(?:金水|查杀))|我验|我查|我昨|我有两份|我的(?:两次)?查验|我的验人|报.*验人|我补报|我给|本人.*查验|我的金水|我的查杀|我报', c))
        if self_report:
            report_context = True
        # Ownership may appear after the target: "4号是我验出的金水".
        owner_post = bool(re.search(r'是我(?:昨晚|昨夜)?(?:亲自)?(?:验|查)|是我的(?:金水|查杀)|来自我的查验', c))
        short_report = len(c)<=28 and not re.search(r'认为|觉得|眼里|好人或|可能|是不是|是否',c) and bool(re.search(NUM+r'(?:玩家)?(?:是|为|的)?(?:金水|查杀|好人|非狼人|狼人|狼)',c))
        can_report = self_report or owner_post or isinstance(report_context,tuple) or ((report_context or claimed_role=='seer') and short_report)
        if not can_report:
            continue
        patterns = [
            NUM + r'(?:玩家)?(?:是|为|的)?(?:我(?:昨晚|昨夜)?(?:亲自)?(?:验出|验到|查出|查验得到|已经验出)的)?(?:查验)?(?:结果)?(?:显示|是|为|得到|得到的是|拿到的是|验出的是|验到的是|我查过了|我验出来是)?(?:一张)?\s*' + RESULT,
            r'(?:验|查验|查到|验了|验过|查验了|查验的|验到|亲验)\s*' + NUM + r'(?:玩家)?(?:得到|为|是|结果是|得到的是|得到的结果是|查验结论是|查验结果是)?\s*' + RESULT,
        ]
        hits = []
        for p in patterns:
            for m in re.finditer(p, c):
                prefix = c[:m.start()]
                if re.search(r'(?:没验|未验|没有验|今晚.*验|准备.*验|打算.*验|是不是|是否)$', prefix):
                    continue
                hits.append((m.start(), int(m[1]), RESULT_MAP[m[2]]))
        if not hits and re.search(r'查验|验人|我验|我查|我的金水|我的查杀', c):
            target = re.search(NUM, c)
            result = list(re.finditer(RESULT, c))
            if target and result and not re.search(r'[1-6]号.*(?:的|报的).*结果不是我的', c):
                hits.append((target.start(), int(target[1]), RESULT_MAP[result[-1][1]]))
        # Deliberate conservative state for split reports: "我验了3号，结果是好人".
        if not hits and can_report and re.search(r'结果|我验|查验', c):
            target = re.search(r'(?:验|查验|(?:对象|目标)(?:为|是))\s*(?:的是)?' + NUM, c)
            if target:
                report_context = (int(target[1]), current_night)
            elif isinstance(report_context, tuple):
                result = re.search(RESULT, c)
                if result:
                    hits.append((0, report_context[0], RESULT_MAP[result[1]]))
        for _, target, result in sorted(set(hits)):
            if re.search(r'不是好人.*是狼人', c):result = 'wolf'
            if re.search(r'不是查杀|不是狼人|不是狼|非狼', c):
                # A clause with several targets needs each local result, not a global override.
                if len({x[1] for x in hits}) == 1:result = 'non_wolf'
            if re.search(r'不是.*(?:而是|是).*金水|金水.*不是查杀', c):result = 'non_wolf'
            item = {'target':target, 'result':result, 'night':current_night}
            if re.search(r'更正|纠正|口误|念反|不是.*而是', c):
                checks = [x for x in checks if x['target'] != target]
            if item not in checks:checks.append(item)
    return checks


def _votes(clauses):
    target = None
    follow = None
    excluded = set()
    withdrawn = set()
    alternatives = []
    condition = None
    mode = 'not_expressed_or_unresolved'
    pending_condition = None
    for c in clauses:
        if _third_party(c) or _unasserted(c):continue
        if re.fullmatch(r'(?:今天|本轮|本回合)?全票出',c) and target is None:
            continue  # Unique prior check target is handled explicitly in extract_entities.
        if re.match(r'[1-6]号',c) and not re.search(r'我|大家|好人|请',c):continue
        if re.match(r'如果|只有|只要|假如|仅在',c):pending_condition=c
        otherwise=re.search(r'否则(?:还|仍然|依然)?(?:是|投|选)\s*'+NUM,c)
        if otherwise:
            target=int(otherwise[1]);mode='affirmative';condition=None;pending_condition=None;continue
        if re.search(r'昨天|上一轮|上轮|历史|先前|原本|原来|最初|之前|原来的|我本来|我原本', c) and not re.search(r'现在|本轮仍|今天.*仍|当前|最后|最终|改投|改为|改成', c):
            past = re.search(VOTE_VERB + r'\s*' + NUM, c)
            if past and re.search(r'原本|最初|先前|之前|原来|我本来', c):
                # Only mark old intentions withdrawn if the whole utterance explicitly changes them.
                if any(re.search(r'改投|改为|撤回|撤销|收回|作废|取消|目标改', later) for later in clauses):withdrawn.add(int(past[1]))
            continue
        if re.search(r'撤回|撤销|收回|作废', c):
            old = re.search(r'(?:投|对|放逐|原来的)\s*' + NUM, c)
            if old:withdrawn.add(int(old[1]))
            target = None; follow = None; mode = 'withdrawn_unresolved'
        if re.search(r'(?:决定|选择|本轮|这轮|今天).*弃票|我弃票', c) and not re.search(r'不再.*弃票|不是.*弃票|之前.*弃票', c):
            target = None;follow = None;mode = 'abstain';continue
        f = re.search(r'(?:我(?:会|将|准备|愿意|今天|本轮|直接|现在)*\s*(?:听|跟随|跟着|跟)|听从)\s*' + NUM, c)
        if not f:f = re.search(r'我.*跟随\s*' + NUM, c)
        if f and not re.search(r'没有承诺|没承诺|不跟|拒绝|取消|不代表', c):
            follow = int(f[1]);mode = 'follow_unresolved' if target is None else mode
        negative = list(re.finditer(r'(?:不投|不会投|不选|不把票投给|不准备投|排除投|先不要投|不要投)\s*' + NUM, c))
        negative += list(re.finditer(NUM + r'.{0,8}(?:我不(?:会)?把票投给他|我不投|先别投)', c))
        for m in negative:excluded.add(int(m[1]))
        if negative and target is None and mode != 'withdrawn_unresolved':mode='negative'
        if re.search(r'不(?:是|等于|代表).*(?:要求|准备|已经)?投|不是我.*(?:决定|选择)|没(?:有)?(?:形成|作出|确定).*投票|不是.*改票', c):
            continue
        if re.search(r'撤回|撤销|收回|作废', c) and not re.search(r'改投|改为|最后投|最终投', c):continue
        patterns = [
            r'(?:改投|改为(?:投)?|改成(?:投)?|(?:最终|当前|本轮|现在的|最后的)?(?:投票)?目标(?:是|为|改为)|选择放逐|选择(?:投)?|选|投票给|投给|投票|归票|抗推|放逐|出掉|投|出)\s*' + NUM,
            r'(?:把票|将票|票|我的票|本轮票)(?:集中)?(?:交给|投给|给|留给|集中给|就给)\s*' + NUM,
            r'把\s*' + NUM + r'(?:玩家|狼人)?(?:放逐|投出|出局)',
        ]
        candidates = []
        for pattern in patterns:
            for m in re.finditer(pattern, c):
                before = c[:m.start()]
                after = c[m.end():]
                if re.search(r'不|没|别|尚未|不是|不等于|并非|也不是', before[-8:]):continue
                if re.search(r'准备验|查验', before[-6:]):continue
                if re.search(r'说|建议|希望|劝|提议', before) and not re.search(r'我(?:也|现在|自己|本轮)?(?:建议|决定|选择|准备)|好人|大家|请', before) and _third_party(c):continue
                if re.search(r'或|和.*(?:号|重投)|、', after) and not re.search(r'不|改', after):
                    target=None;mode='multiple_unresolved';candidates=[];break
                candidates.append((m.start(),int(m[1])))
        if candidates:
            pos, new = sorted(set(candidates))[-1]
            conditional = re.search(r'如果|只有|只要|假如|仅在|否则', c) or pending_condition
            if conditional and re.search(r'才改|才切换|就改|改投', c):
                alternatives.append({'target':new,'condition':pending_condition or c[:pos].strip('；;：:')})
            else:
                if target is not None and target != new and re.search(r'改|撤|最后|最终', c):withdrawn.add(target)
                target=new;mode='affirmative'
                if conditional:
                    condition=(pending_condition+'，'+c) if pending_condition and pending_condition!=c else c;mode='conditional'
                pending_condition=None
        if re.search(r'本轮仍投|目标仍是|仍然会投', c):
            m=re.search(r'(?:仍投|目标仍是|仍然会投)\s*'+NUM,c)
            if m:target=int(m[1]);mode='affirmative';condition=None
    if target is not None:excluded.discard(target)
    return dict(vote_target=target,follow_target=follow,excluded_vote_targets=sorted(excluded),
                withdrawn_vote_targets=sorted(withdrawn),vote_condition=condition,vote_mode=mode,
                conditional_vote_alternatives=alternatives)


def _social(clauses):
    agreement=set();suspect=set();support=set()
    for c in clauses:
        if _third_party(c) or _unasserted(c):continue
        if re.search(r'昨天|上一轮|之前|原本|不再|收回站边',c) and not re.search(r'目前|现在|当前|反而',c):continue
        for m in re.finditer(r'(?:我(?:也|暂时|目前|现在)?(?:同意|赞成|赞同|认同|认可|接受)|我更?信任)\s*'+NUM+r'(?:(?:和|与|、)\s*([1-6])号?)?',c):
            if re.search(r'不(?:并|是)?|并不|不是|没有|不代表',c[:m.start()][-8:]):continue
            ids=[int(x) for x in m.groups() if x]
            if re.search(r'同意|赞成|赞同|认同|认可|接受|分析',m[0]+c[m.end():]) and not re.search(r'身份|作为好人|是真|偏好人',c[m.end():]):agreement.update(ids)
            if re.search(r'作为好人|身份判断',c[m.end():]):support.update(ids)
            if re.search(r'更?信任',m[0]) and not re.search(r'分析|观点|建议',c[m.end():]):support.update(ids)
        m=re.search(r'([1-6])号.{0,12}(?:观点|意见|建议|疑点|分析).{0,10}我(?:也)?(?:同意|认可|赞同|认同)',c)
        if m and not re.search(r'不(?:同意|认可|赞同|认同)',c):agreement.add(int(m[1]))
        for m in re.finditer(r'(?:我(?:也|更|目前|现在|暂时|只|因此|目前更|目前只|目前反而更|这把)?(?:怀疑|更怀疑|站边|支持|信任|更信任|相信)|(?:^|但|当前|目前)(?:暂时|目前)?(?:更)?(?:怀疑|站边|支持|信任))\s*'+NUM+r'(?:(?:和|与|、)\s*([1-6])号?)?',c):
            if re.search(r'不再|没有|不|收回|原本',c[:m.start()][-6:]):continue
            dest=suspect if '怀疑' in m[0] else support
            dest.update(int(x) for x in m.groups() if x)
        for m in re.finditer(NUM+r'.{0,12}(?:很可疑|偏像狼|像狼人|偏狼|是悍跳狼|是狼人|是狼|偏好人|像好人|大概率是好人|大概率是个真预言家|是铁好人|是真预言家|是真正的预言家)',c):
            if re.search(r'如果|假如|假设|是否|是不是|不代表|不能|不是|还不知道|不一定|没有认定|认为.*可能',c):continue
            if not re.search(r'我认为|我相信|我觉得|我判断|在我眼里|我目前|我更|很可疑',c):continue
            if re.search(r'狼|可疑',m[0]) and not re.search(r'好人|真预言家',m[0]):suspect.add(int(m[1]))
            else:support.add(int(m[1]))
        m=re.search(r'我对\s*'+NUM+r'.{0,12}(?:怀疑|疑问)',c)
        if m and not re.search(r'不再|没有',c):suspect.add(int(m[1]))
        m=re.search(r'对\s*'+NUM+r'(?:(?:和|与|、)\s*([1-6])号?).{0,12}(?:偏向好人|偏好人)',c)
        if m and not re.search(r'不|没',c):support.update(int(x) for x in m.groups() if x)
        m=re.search(NUM+r'.{0,16}(?:我(?:也)?(?:认可|同意|赞成)他|这一点我同意)',c)
        if m and not re.search(r'不(?:同意|认可|赞成)',c):agreement.add(int(m[1]))
        m=re.search(NUM+r'(?:玩家)?(?:很可疑|可疑|是大铁狼|是悍跳狼|一定是悍跳狼人|是狼人|是狼|是最后一只狼人|像是.*狼)',c)
        if m and not re.search(r'如果|假设|是不是|是否|不代表|并不是|没认为',c):suspect.add(int(m[1]))
        m=re.search(r'我(?:也|个人|依然|暂时)?认为\s*'+NUM+r'(?:玩家)?(?:是|偏像|大概率是)(?:真)?(?:好人|预言家)',c)
        if m and not re.search(r'如果|不是|不代表',c):support.add(int(m[1]))
    return dict(agreement_targets=sorted(agreement),suspect_targets=sorted(suspect),support_targets=sorted(support))


def extract_entities(public_text, speaker_id=None):
    text=normalize(public_text)
    clauses=_clauses(text)
    role=_role(clauses,speaker_id)
    checks=_checks(clauses,role)
    out={'player_ids':public_player_ids(text),'claimed_role':role,'check_claims':checks,
         'check_target':checks[0]['target'] if len(checks)==1 else None,
         'check_result':checks[0]['result'] if len(checks)==1 else None,
         **_votes(clauses),**_social(clauses)}
    if out['vote_target'] is None and len(checks)==1 and checks[0]['result']=='wolf' and any(re.fullmatch(r'(?:今天|本轮)?全票出',c) for c in clauses):
        out['vote_target']=checks[0]['target'];out['vote_mode']='affirmative'
    for prefix in ['agreement','suspect','support']:
        targets=out[prefix+'_targets'];out[prefix+'_target']=targets[0] if len(targets)==1 else None
    return out

