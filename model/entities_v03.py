"""Versioned public-speech entities: scoped claims, polarity, and ordered intentions.

No sample-ID lookup, classifier-label shortcut, hidden role or external API is used.
The unchanged v0.1 implementation remains available in model.entities.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

RULE_VERSION = 'entity-rules-v0.3'
NUMBER = r'(?<!\d)([1-6])\s*号?(?!\d)'
ROLES = r'(预言家|普通村民|普通平民|小村民|村民|平民|狼人|狼牌)'
ROLE_MAP = {'预言家':'seer','狼人':'werewolf','狼牌':'werewolf',
            '普通村民':'villager','普通平民':'villager','小村民':'villager','村民':'villager','平民':'villager'}
RESULT = r'(不是狼人|不是好人|不是查杀|不是金水|不是狼|非狼人|非狼|好人|金水|狼人|查杀|狼)'
RESULT_MAP = {'不是狼人':'non_wolf','不是狼':'non_wolf','非狼人':'non_wolf','非狼':'non_wolf',
              '好人':'non_wolf','金水':'non_wolf','不是查杀':'non_wolf','不是金水':'wolf','不是好人':'wolf','狼人':'wolf','查杀':'wolf','狼':'wolf'}
MOD = r'(?:现在|目前|暂时|本轮|今天|仍然|仍|也|更|只|自己|本人|确实|明确|直接|反而|因此|已经|愿意|准备|将会|会|就|先|这边|这把|的确|从头到尾|都|真)*'
GROUP = NUMBER + r'(?:(?:和|与|、|及)\s*([1-6])\s*号?)*'


@dataclass(frozen=True)
class Segment:
    text: str
    sentence: int
    index: int


def normalize(text):
    return re.sub(r'(?<![零一二三四五六七八九十百\d])([一二三四五六])(?=\s*号)',lambda m:str('一二三四五六'.index(m[1])+1),text)


def player_ids(text):
    # Bare player numbers are accepted, but ordinary counts and turn indices aren't.
    text=normalize(text)
    text=re.sub(r'(?:第|上|前|下)?[一二三四五六1-6](?:轮|夜|天|次|条|张|个|份|小时|分钟|秒|票)(?!号)','',text)
    return sorted({int(x) for x in re.findall(r'(?<![\d第])([1-6])(?!\d)',text)})


def segments(text):
    text=normalize(text)
    text=re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"',lambda m:' '*len(m[0]),text)
    result=[];sentence=0
    for part in re.split(r'([，,。；;！!？?\n])',text):
        if not part:continue
        if re.fullmatch(r'[，,。；;！!？?\n]',part):
            if part in '。；;！!？?\n':sentence+=1
            continue
        for piece in re.split(r'(?=不过|但是|而是)',part):
            if piece.strip():result.append(Segment(piece.strip(),sentence,len(result)))
    return result


def third_party(c):
    return bool(re.match(r'(?:[1-6]号(?:玩家)?(?:刚才|上一轮|昨天|已经)?(?:向我|告诉|说|声称|报|认为|建议|希望|劝|让我|让|问|想投)|有人说|他说|她说|据[1-6]号)',c))


def hypothetical(c):
    return bool(re.search(r'如果我是|假如我是|假设|若我是|(?:如果|假如|要是|若)[1-6]号|只是举例|仅仅是条件分析',c))


def negated_assertion(c):
    return bool(re.search(r'(?:我|也)?没(?:有)?说(?:自己|我)|我没(?:有)?说|我不是在说|我没有(?:认|声称)|不是我的(?:观点|决定|结果)|这不是我的意思',c))


def role_claim(parts,speaker_id):
    role=None;pending_answer=False;question_role=None
    for seg in parts:
        c=seg.text
        q=re.search(r'问我是不是'+ROLES,c)
        if q:question_role=ROLE_MAP[q[1]]
        if question_role and re.search(r'(?:我的回答|我回答).*(?:肯定|是的)|我认这个身份',c):
            role=question_role
        if hypothetical(c) or negated_assertion(c) or third_party(c):continue
        if re.search(r'我的底牌|我先报身份|我的表水|我先说明身份',c):pending_answer=True
        for m in re.finditer(ROLES,c):
            before=c[:m.start()];after=c[m.end():]
            if re.search(r'是不是|你是|他是|你说我是|他称我是|我'+MOD+r'(?:认为|觉得|相信|希望|判断)',before):continue
            if re.search(r'(?:不是|并非|不认|不再认|没认)(?:个|一张|一个)?$',before):continue
            if re.match(r'的话|就会|才会',after):continue
            personal=bool(re.search(r'我'+MOD+r'(?:是|就是|才是|只是个|只是一个|认(?!为|同|可)|报|声明|自称|拿的是|拿到的是|拿的|作为|身为|以|这张|手里这张)|我这局拿的是|我(?:当前|现在|今天)?的(?:身份|底牌|村民声明|平民声明)|我(?:可以公开|本人声明|自己声明).*身份|(?:作为|身为)(?:普通|一名|一个)?$',before))
            reverse=bool(re.match(r'牌?是我的|是我|的身份我认|这个身份上|牌在我手里',after))
            self_number=bool(speaker_id and re.fullmatch(rf'{speaker_id}号(?:玩家)?(?:是)?',before))
            continuation=pending_answer and (bool(re.fullmatch(r'(?:我回答|答案是|身份是)?[:：]?\s*',before)) or bool(re.search(r'(?:表水|身份|底牌).*[:：]$',before)))
            if before.endswith('我的') and after.startswith('声明'):personal=True
            if seg.index and re.match(r'^是'+ROLES,c) and re.search(r'我(?:拿的|不是)',parts[seg.index-1].text):continuation=True
            if personal or reverse or self_number or continuation:role=ROLE_MAP[m[1]]
        if re.search(r'预言家在我这里|现在正式起跳.*预言家|这张能验人的预言家牌是我的|在预言家这个身份上|我这边亮出的身份是预言家',c):role='seer'
    return role


def night_index(c):
    if '首夜' in c:return 1
    if '次夜' in c:return 2
    m=re.search(r'(?<![上前昨])(?:第)?([一二三四五六1-6])夜',c)
    if m:return int(m[1]) if m[1].isdigit() else '一二三四五六'.index(m[1])+1
    return None


def completed_claims(parts,role):
    claims=[];pending=None;report=False;last_sentence=-1;last_own=None;reported=False
    def add(target,result,night,correction=False):
        nonlocal claims,last_own
        item={'target':target,'result':result,'night':night}
        if correction:claims=[x for x in claims if x['target']!=target]
        if item not in claims:claims.append(item)
        last_own=target
    for seg in parts:
        c=seg.text
        if seg.sentence!=last_sentence:
            pending=None;report=False;reported=False
        last_sentence=seg.sentence
        own=bool(re.search(r'我'+MOD+r'(?:声称)?(?:昨晚|昨夜|昨|昨夜曾|已经|此前|亲自)?(?:查验|亲验|验|查到|查过)|我的(?:两次|两份)?(?:查验|验人|金水|查杀)|我(?:公布|补报|报|给).*?(?:验|查验|金水|查杀)|我报[1-6]号|我说的(?:金水|查杀)|是我.*?验|我维持.*亲验|我有两份.*查验|我(?:昨天|此前|上轮)的验人',c))
        if third_party(c) and not own:
            reported=True;pending=None;report=False;continue
        if re.search(r'[1-6]号.*(?:给我|给我的|发给我).*(?:金水|查杀)|站在[1-6]号的角度',c) and not re.search(r'我.*(?:亲验|验了|验出|查验)',c):
            reported=True;pending=None;continue
        if own:reported=False
        if reported and not own:continue
        if hypothetical(c) or negated_assertion(c) or re.search(r'是不是|是否|谁验|究竟|没验|没有查验|未查验|还没验|不是我的.*结果',c):
            pending=None;report=False;continue
        if re.search(r'今晚|下一夜|以后|将来|打算|准备查验',c) and not re.search(r'昨晚|昨夜|已经.*验|此前.*验|验出的',c):
            pending=None;report=False;continue
        if re.search(r'我(?:认为|觉得|怀疑|猜)|在我眼里|看发言|可能|概率|只是推测|好人或狼',c) and not own:
            pending=None;report=False;continue
        night=night_index(c)
        if own or re.search(r'(?:昨晚|昨夜|首夜|第二夜).*?(?:验|查验)|(?:补报|报).*(?:信息|结果)|我的两次查验|我.*结果.*(?:念反|说反|口误)|报告自己的查验|我这边的验人',c):report=True
        if re.search(r'昨夜摸了.*的底',c):report=True
        bare_jargon=bool(re.search(NUMBER+r'(?:玩家)?(?:是我的|是|为)?(?:金水|查杀)',c))
        enabled=own or report or pending is not None or bare_jargon or (role=='seer' and bool(re.search(r'查验结果|查验|验人|金水|查杀',c)))
        if not enabled:continue
        correction=bool(re.search(r'更正|纠正|口误|念反|不是.*是',c))
        found=[]
        # Target and result must be connected without crossing another player number.
        link=r'(?:(?![1-6]\s*号).){0,30}?'
        for m in re.finditer(NUMBER+link+RESULT,c):
            before=c[:m.start()];middle=c[m.start():m.end()]
            if re.search(r'认为|怀疑|猜|给我发|给我的|别人',before[-12:]) and not re.search(r'不是.*的.*是我的',c):continue
            if re.search(r'没(?:有)?验|还没验|今晚.*验|今晚打算',before[-12:]):continue
            if re.search(r'发言|观点|说法|逻辑|起跳|知道|悍跳|概率',middle) and not re.search(r'(?:我验|我查|查验结果)',c):continue
            result=RESULT_MAP[m[2]]
            local_after=c[m.end():]
            if m[2] in ('不是狼','不是狼人') and re.match(r'人',local_after):result='non_wolf'
            found.append((int(m[1]),result))
        reverse=re.search(r'(?:我的(?:金水|查杀)|我说的(?:金水|查杀))(?:是|为)'+NUMBER,c)
        if reverse:found.append((int(reverse[1]),'non_wolf' if '金水' in reverse[0] else 'wolf'))
        if not found and pending:
            result=re.search(RESULT,c)
            if result and (re.search(r'结果|显示|验到|得到|不是|是好人|是金水',c)):
                found.append((pending[0],RESULT_MAP[result[1]]));night=pending[1]
        if not found and last_own is not None and re.fullmatch(r'(?:不是|是|而是)'+RESULT,c):
            result=re.search(RESULT,c)
            if result:found.append((last_own,RESULT_MAP[result[1]]));correction=True
        if found:
            # A direct correction in a later result for the same target replaces it.
            for target,result in dict.fromkeys(found):add(target,result,night,correction)
            pending=None
        else:
            target=re.search(r'(?:查验过|查验了|查验|验了|验过|验的是|验|查到|摸了|(?:对象|目标)(?:是|为))\s*'+NUMBER,c)
            if not target:
                target=re.search(NUMBER+r'我查过了',c)
            if not target:target=re.search(r'我查过\s*'+NUMBER,c)
            if target and report:pending=(int(target[1]),night)
    return claims


def local_focus(parts,i):
    """Resolve only an immediate, unique player antecedent inside this utterance."""
    if i<=0:return None
    prior=player_ids(parts[i-1].text)
    return prior[0] if len(prior)==1 else None


def vote_entities(parts):
    target=None;follow=None;excluded=set();withdrawn=set();alts=[];condition=None;mode='not_expressed_or_unresolved'
    old=None;proposal=None;pending_condition=None
    full='，'.join(x.text for x in parts)
    has_revision=bool(re.search(r'改投|改为|改到|撤回|收回|撤销|作废|取消.*目标|最后投|最终选|最终目标|现在决定',full))
    vote_patterns=[
        r'(?:改投|改为(?:投)?|改成(?:投)?|改到|换成|换到|最终选|最后投|直接下|选择放逐|选择的是|选择(?:投)?|投票给|投给|投票|投的是|投|选|归票|放逐|抗推|出掉)\s*'+NUMBER,
        r'(?:最终|当前|最后|新的|我的|本轮|现在的)?(?:放逐|投票)?目标(?:改为|是|为)\s*'+NUMBER,
        r'(?:这张票|这轮票|我的票|我这票|这票|本轮票|票)(?:实际|仍|先|想|最终|决定|就|集中|选择|准备)*\s*(?:给|交给|投给|指向|留给|集中给)\s*'+NUMBER,
        r'把(?:自己的)?票(?:交给|投给|给|集中给)\s*'+NUMBER,
        NUMBER+r'(?:是|作为)我的(?:放逐|投票)选择',
        r'(?:本轮|今天|好人们?|大家|全票)(?:请|和我|跟我|们|一起)*出\s*'+NUMBER,
        r'把\s*'+NUMBER+r'(?:玩家|狼人)?(?:放逐|投出|出局)',
    ]
    for i,seg in enumerate(parts):
        c=seg.text;focus=local_focus(parts,i)
        if re.search(r'告诉我.*(?:未公布|当前投票)|有没有人选|(?:修改|改成|替换).*他(?:的)?(?:已经提交的)?票|把[1-6]号.*(?:正式投票|提交的票|已经提交的票)',c):continue
        if re.match(r'如果|只有|只要|假如|仅在',c):pending_condition=c
        suggestion=re.search(r'(?:让我|劝我|建议我|希望我).*?(?:投|改投)\s*'+NUMBER,c)
        if suggestion and third_party(c):proposal=int(suggestion[1]);continue
        if third_party(c) or negated_assertion(c):continue
        if re.match(r'[1-6]号',c) and not re.search(r'我|大家|好人|请|我的放逐|我的投票',c):
            if focus is None and re.search(r'先留着|先别投',c):excluded.update(player_ids(c));mode='negative'
            continue
        if re.search(r'不再坚持投|撤回|撤销|撤掉|收回|作废|取消(?:投|这.*目标|.*投票建议)',c) and re.search(r'票|投|放逐|目标|方案',c) and not re.search(r'没有撤回|没撤回|不撤回',c) and pending_condition is None:
            m=re.search(r'(?:投|放逐|对|原来的|目标是)\s*'+NUMBER,c)
            removed=int(m[1]) if m else old if old is not None else target
            if removed is not None:withdrawn.add(removed)
            target=None;follow=None;mode='withdrawn_unresolved';condition=None
        if re.search(r'不会照做|不接受这个(?:建议|安排)|拒绝这个安排',c) and proposal is not None:
            excluded.add(proposal)
        history=bool(re.search(r'昨天|上一轮|上轮|原本|本来|最初|先前|原来|之前|刚才.*口误',c))
        current_override=bool(re.search(r'现在|当前|本轮仍|今天仍|最终|最后|改投|改为|改到|改成',c))
        if history and not current_override:
            m=re.search(r'(?:投|放逐|对|目标是|从)\s*'+NUMBER,c)
            if m and re.search(r'原本|本来|最初|先前|原来|之前|刚才.*口误',c):old=int(m[1])
            continue
        denial=bool(re.search(r'我不是(?:说|要|准备)|并不是(?:改成|投)|不是把票|不等于.*(?:投|票)|别把.*当成.*(?:投|票)|不代表.*(?:改票|投)|不表示.*(?:投|票)|不能推出.*(?:投|票)|不是我的.*(?:决定|选择)|他今天.*投|他的.*投票目标.*替换',c))
        if re.search(r'把[1-6]号的正式投票目标.*替换',c):continue
        if denial:
            if re.search(r'并不是改成投',c):
                m=re.search(r'投\s*'+NUMBER,c)
                if m:excluded.add(int(m[1]))
            continue
        negs=[];negative_positions=set()
        for m in re.finditer(r'(?:不投|不会投|不选|不考虑投|不把票投给|不准备投|排除投|不想投|不要投|先别投|不支持投)\s*'+NUMBER,c):
            negs.append(int(m[1]));negative_positions.add(m.span(1))
        if focus and re.search(r'不(?:会)?(?:把票(?:给|投给)|投|选)他|不把票投给他|不会把票给他',c):negs.append(focus)
        m=re.search(NUMBER+r'也不是我.*准备投的人',c)
        if m:negs.append(int(m[1]))
        excluded.update(negs)
        if negs and target is None and mode!='withdrawn_unresolved':mode='negative'
        if negs and pending_condition:condition=pending_condition;mode='conditional'
        if re.search(r'(?:决定|选择|本轮|这轮|今天|我).*弃票',c) and not re.search(r'不再.*弃票|之前.*弃票|不是.*弃票|不弃票',c):
            target=None;follow=None;mode='abstain';continue
        follows=re.search(r'(?:我'+MOD+r'(?:听|听从|跟|跟着|跟随)|改为跟随)\s*(?:我心目中的盖世预言家)?'+NUMBER,c)
        if not follows:follows=re.search(r'我.*?(?:跟随|跟着).*?(?<!\d)([1-6])号',c)
        if follows and not re.search(r'不跟|没承诺|没有承诺|取消|拒绝|不代表',c):
            follow=int(follows[1]);mode='follow_unresolved' if target is None else mode
            if old is not None and has_revision:withdrawn.add(old)
        if re.search(r'(?:之前|原先).*跟.*取消|跟票承诺取消',c):follow=None
        candidates=[]
        for pattern in vote_patterns:
            for m in re.finditer(pattern,c):
                before=c[:m.start()];after=c[m.end():]
                if m.span(1) in negative_positions:continue
                if re.search(r'(?:不|没|别|未|不要|不是|不考虑|不代表|不等于)(?:想|会|再|准备)?$',before):continue
                if re.search(r'不把票(?:投给|给)?$',before):continue
                if re.search(r'验|查到',before[-5:]):continue
                if re.search(r'撤回|撤销|收回|作废|取消投|不再坚持投',c) and not re.search(r'改投|改为|最终|最后',c):continue
                candidates.append((m.start(),int(m[1])))
        if focus and re.search(r'(?:我会选他出局|我(?:会|准备)?投他)',c) and not re.search(r'不|没',c):candidates.append((len(c),focus))
        if (re.search(r'(?:投票给|投给|投|选).*?(?:或|或者)',c) or re.search(r'(?:投|归票|选)[1-6]号和[1-6]号(?:PK|重投|二选一)',c) or (re.search(r'或|或者|和.*一起.*投|只能.*[1-6]号.*[1-6]号',c) and len(set(n for _,n in candidates))>1)) and not re.search(r'改|不选',c):
            target=None;mode='multiple_unresolved';continue
        if candidates:
            _,new=sorted(set(candidates))[-1]
            local_cond=re.search(r'如果|只有|只要|假如|仅在',c)
            cond=pending_condition or (c if local_cond else None)
            switch=bool(re.search(r'改投|改为|改成|改到|换成|才切换',c))
            if cond and switch:
                alts.append({'target':new,'condition':cond})
            else:
                if target is not None and target!=new and switch:withdrawn.add(target)
                if old is not None and old!=new and has_revision:withdrawn.add(old)
                target=new;mode='conditional' if cond else 'affirmative';condition=cond
                pending_condition=None
        default=re.search(r'(?:否则|条件没有满足之前)(?:还|仍然|依然|仍)?(?:是|投|选|目标仍是)?\s*'+NUMBER,c)
        if default:target=int(default[1]);mode='affirmative';condition=None;pending_condition=None
    if target is not None:excluded.discard(target)
    return {'vote_target':target,'follow_target':follow,'excluded_vote_targets':sorted(excluded),
            'withdrawn_vote_targets':sorted(withdrawn),'vote_mode':mode,'vote_condition':condition,
            'conditional_vote_alternatives':list({(a['target'],a['condition']):a for a in alts}.values())}


def social_entities(parts,checks):
    agreement=set();suspect=set();support=set();reported=False;sentence=-1
    sets={'agreement':agreement,'suspect':suspect,'support':support}
    check_targets={x['target'] for x in checks}
    for i,seg in enumerate(parts):
        c=seg.text;focus=local_focus(parts,i)
        if sentence!=seg.sentence:reported=False
        sentence=seg.sentence
        if third_party(c) and not re.search(r'我(?:同意|认为|怀疑|支持|站边|认可)',c):reported=True;continue
        if re.search(r'我(?:的|自己|目前|现在|同意|认为|怀疑|支持|站边|认可|相信)',c):reported=False
        if reported or hypothetical(c) or negated_assertion(c):continue
        if re.search(r'原本|昨天|上一轮|之前|过去',c) and not re.search(r'现在|当前|目前|本轮',c):continue
        # A reported completed check is not a separate suspicion judgment.
        check_clause=bool(re.search(r'我'+MOD+r'(?:声称)?(?:昨天|昨晚|昨夜|昨|亲自|已经|此前)?(?:查验|验(?!证)|亲验|查到|查过)|(?:昨晚|昨夜|首夜|第二夜).*验|(?:是我的|我给).*?(?:金水|查杀)|我的(?:金水|查杀)|我的两次查验',c)) and not re.search(r'我.*怀疑|我.*认为|我.*觉得',c)
        if set(player_ids(c)) & check_targets and re.search(r'验到|验过|查验结果|验人结果',c) and not re.search(r'我.*怀疑|我.*认为|我.*觉得',c):check_clause=True
        if check_clause:continue
        for kind,verb in [('agreement',r'同意|认同|赞同|赞成|认可|接受'),('suspect',r'怀疑|更怀疑'),('support',r'支持|站边|信任|相信')]:
            pat=r'(?:我'+MOD+r'|^(?:但|当前|目前|现在|暂时|只是)?'+MOD+r')(?:'+verb+r')\s*'+GROUP
            for m in re.finditer(pat,c):
                before=c[:m.start()];after=c[m.end():]
                ids=player_ids(m[0])
                if re.search(r'不再|不|并不|不是|不代表|没有|收回',before[-8:]):
                    sets[kind].difference_update(ids);continue
                dest=kind
                if kind=='support' and re.search(r'^的?(?:分析|观点|理由|论据|建议|质疑)',after):dest='agreement'
                if kind=='agreement' and re.search(r'^作为好人|^的身份|^是好人|^的好人身份',after):dest='support'
                sets[dest].update(ids)
        # Unique immediate pronouns only: absent or ambiguous antecedents stay empty.
        if focus and re.search(r'我'+MOD+r'(?:认可他|同意他|赞成他|认同他)',c) and not re.search(r'不|没',c):agreement.add(focus)
        if focus and re.search(r'我'+MOD+r'(?:支持[他你]|站边[他你]|相信[他你]|信任[他你])',c) and not re.search(r'不|没',c):support.add(focus)
        if focus and re.search(r'这一点我同意',c):agreement.add(focus)
        for m in re.finditer(GROUP+r'(?:玩家)?(?:是|都|都是|我都|一定是)?(?:一个|一张|两只|两张|最后一只|接到查杀原地起跳的|偏|更|大概率是|很|有点|像是|像|偏像)?(可疑|狼人|狼|双狼|悍跳狼|大铁狼|铁狼|小丑狼|好人|预言家|真预言家|村民)',c):
            before=c[:m.start()];after=c[m.end():]
            if re.search(r'如果|假如|假设|是否|是不是|不一定|不能|不代表|不认为|没认为|没有认定|概率不大',c):continue
            if re.search(r'不是|不再|没有|并非',before[-6:]):continue
            if re.match(r'或|还是|也可能',after):continue
            ids=player_ids(m[0]);result=m.groups()[-1]
            dest=suspect if re.search(r'狼|可疑',result) else support
            # A bare role claim by another player is not the speaker's endorsement.
            if dest is support and not re.search(r'我(?:也|个人|虽然|暂时)?认为|我相信|我觉得|我自己的看法|偏向|公认|更信任|大概率是',c):continue
            dest.update(ids)
        m=re.search(r'我对\s*'+NUMBER+r'(?:的身份)?(?:有|产生|仍有).*(?:怀疑|疑问)',c)
        if m and not re.search(r'没有|不再',c):suspect.add(int(m[1]))
        m=re.search(r'(?:但)?对\s*'+NUMBER+r'的身份只是怀疑',c)
        if m:suspect.add(int(m[1]))
        m=re.search(r'^(?:同时|另外|也)(?:我)?怀疑\s*'+NUMBER,c)
        if m:suspect.add(int(m[1]))
        m=re.search(r'对\s*'+GROUP+r'我都暂时偏向好人',c)
        if m:support.update(player_ids(m[0]))
        m=re.search(NUMBER+r'.*我的怀疑',c)
        if m and not re.search(r'不再怀疑|撤回怀疑',c):suspect.add(int(m[1]))
        m=re.search(NUMBER+r'(?:只是|是)?我怀疑的对象',c)
        if m:suspect.add(int(m[1]))
        mentioned=player_ids(c)
        if len(mentioned)==1 and re.search(r'让我'+MOD+r'支持他',c):support.add(mentioned[0])
        if focus and re.search(r'是全场公认的好人',c):support.add(focus)
        if focus and re.search(r'我的怀疑',c) and not re.search(r'不再怀疑|撤回怀疑',c):suspect.add(focus)
        for kind,word in [('suspect','怀疑'),('support','支持'),('agreement','同意')]:
            m=re.search(r'(?:不再|没有|并不|不)'+word+r'\s*'+NUMBER,c)
            if m:sets[kind].discard(int(m[1]))
    return {'agreement_targets':sorted(agreement),'suspect_targets':sorted(suspect),'support_targets':sorted(support)}


def extract_entities(public_text,speaker_id=None):
    if not isinstance(public_text,str):raise TypeError('public_text must be str')
    parts=segments(public_text)
    role=role_claim(parts,speaker_id)
    checks=completed_claims(parts,role)
    votes=vote_entities(parts)
    if votes['vote_target'] is None and len(checks)==1 and checks[0]['result']=='wolf' and any(re.fullmatch(r'(?:今天|本轮)?全票出',s.text) for s in parts):
        votes['vote_target']=checks[0]['target'];votes['vote_mode']='affirmative'
    result={'player_ids':player_ids(public_text),'claimed_role':role,'check_claims':checks,
            'check_target':checks[0]['target'] if len(checks)==1 else None,
            'check_result':checks[0]['result'] if len(checks)==1 else None,
            **votes,**social_entities(parts,checks)}
    for field in ['agreement','suspect','support']:
        values=result[field+'_targets'];result[field+'_target']=values[0] if len(values)==1 else None
    return result
