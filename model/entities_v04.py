"""Ten-seat public claims; authoritative game state is never read."""
import re
from model import entities_v04_core as parser
from model.text_v04 import normalize_public_text

RULE_VERSION='entity-rules-v0.4'

def skill_claims(text,speaker_id,role):
    text=re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"',lambda m:' '*len(m[0]),text)
    claims=[];condition=None;own_context=False;role_hypothesis=False
    for piece in re.split(r'([。；;\n])',parser.normalize(text)):
        if piece in ['。','；',';','\n']:
            own_context=False;condition=None;role_hypothesis=False;continue
        for c in re.split(r'[，,]',piece):
            if not c.strip():continue
            c=c.strip()
            if re.search(r'(?:如果|假如|假设|若)我是',c):role_hypothesis=True
            if role_hypothesis:continue
            if parser.third_party(c) or parser.hypothetical(c) or parser.negated_assertion(c):
                own_context=False;continue
            if re.search(r'我(?:认为|觉得|听说|猜测|怀疑)',c):own_context=False;continue
            if re.search(r'如果|要是|假如|只有|只要',c):condition=c
            own=bool(re.search(r'我|本人',c)) or bool(speaker_id and re.match(rf'^{speaker_id}号(?:是我|本人)',c))
            if re.match(r'(?:女巫|猎人|他|她|(?:10|[1-9])号)',c) and not own:
                own_context=False;continue
            if own:own_context=True
            if not (own or own_context):continue
            if re.search(r'建议|让女巫|请女巫|女巫.*(?:救|毒)',c) and not re.search(r'我是女巫|我作为女巫',c):continue
            for action,verb in [('save',r'自救|救'),('poison',r'毒'),('shoot',r'开枪|带走')]:
                m=re.search(verb,c)
                if not m:continue
                if action=='shoot' and not ('开枪' in text or role=='hunter'):continue
                if action=='poison' and re.search(r'被毒|中毒|毒药还在|毒药已经',c):continue
                if re.search(r'没(?:有)?决定.*(?:救|毒|带走)|不确定.*(?:救|毒)|没拿到',c):continue
                target_match=re.search(r'(?:救|毒|带走|开枪(?:打|带走)?|打)(?:了|的是|的|是)?\s*'+parser.NUMBER,c)
                target=int(target_match[1]) if target_match else speaker_id if action=='save' and '自救' in c else None
                status='claimed_done'
                if re.search(r'放弃|不开枪',c):status='declined'
                elif re.search(r'没有(?:救|毒|开枪)|没(?:救|毒|开枪)|未(?:救|毒|开枪)',c):status='denied'
                elif condition:status='conditional'
                elif re.search(r'准备|计划|考虑|打算|将|会|今晚',c):status='planned'
                if target is None and status not in ('declined','denied'):continue
                claims.append(dict(role=role,action=action,target=target,status=status,night=parser.night_index(c),
                    condition_text=condition if status=='conditional' else None,evidence_text=c))
    return list({tuple(x.items()):x for x in claims}.values())

def extract_entities(public_text,speaker_id=None):
    text,_=normalize_public_text(public_text)
    result=parser.extract_entities(text,speaker_id)
    parts=parser.segments(text)
    guarded=[parser.Segment('有人说'+s.text.replace('我','对方'),s.sentence,s.index) if parser.third_party(s.text) else s for s in parts]
    checks=parser.completed_claims(guarded,result['claimed_role'])
    shared=re.search(r'我的(?:两次|多次)验人.*?分别是([^。；;]+?)[，,]都是(好人|狼)',','.join(s.text for s in guarded))
    if shared:
        checks=[{'target':n,'result':'non_wolf' if shared[2]=='好人' else 'wolf','night':None} for n in parser.player_ids(shared[1])]
    for s in guarded:
        m=re.search(r'我的(?:两次|多次)验人.*?分别是(.+?)[，,]?都是(好人|狼)',s.text)
        if m:
            checks=[{'target':n,'result':'non_wolf' if m[2]=='好人' else 'wolf','night':None} for n in parser.player_ids(m[1])]
    result.update(check_claims=checks,check_target=checks[0]['target'] if len(checks)==1 else None,check_result=checks[0]['result'] if len(checks)==1 else None)
    result.update(parser.social_entities(parts,checks))
    for index,s in enumerate(guarded):
        c=s.text
        if parser.third_party(c) or parser.hypothetical(c):continue
        m=re.fullmatch(r'不是'+parser.NUMBER+r'(?:你们记清楚)?',c)
        if m and result['vote_target'] is not None:
            result['excluded_vote_targets']=sorted(set(result['excluded_vote_targets']+[int(m[1])]))
        m=re.match(parser.NUMBER+r'撤回',c)
        if m:result['withdrawn_vote_targets']=sorted(set(result['withdrawn_vote_targets']+[int(m[1])]))
        m=re.match(r'(?:Vote[:：]\s*)'+parser.NUMBER,c,re.I)
        if m:result.update(vote_target=int(m[1]),vote_mode='affirmative')
        m=re.match(r'(?:结论[:：]\s*)?'+parser.NUMBER+r'像好人',c)
        if m:result['support_targets']=sorted(set(result['support_targets']+[int(m[1])]))
        m=re.search(r'我觉得'+parser.NUMBER+r'不太对',c)
        if m:result['suspect_targets']=sorted(set(result['suspect_targets']+[int(m[1])]))
        m=re.search(r'我(?:有点|比较|更)?怀疑'+parser.NUMBER,c)
        if m:result['suspect_targets']=sorted(set(result['suspect_targets']+[int(m[1])]))
        focus=parser.local_focus(guarded,index)
        if focus and re.search(r'我(?:有点|比较|更)?怀疑[你他]',c):result['suspect_targets']=sorted(set(result['suspect_targets']+[focus]))
    if result['vote_target'] is None and len(checks)==1 and checks[0]['result']=='wolf' and re.search(r'全票打飞',text):
        result.update(vote_target=checks[0]['target'],vote_mode='affirmative')
    for k in ('agreement','suspect','support'):
        values=result[k+'_targets'];result[k+'_target']=values[0] if len(values)==1 else None
    result['skill_claims']=skill_claims(text,speaker_id,result['claimed_role'])
    return result
