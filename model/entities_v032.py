"""v0.3.2: count-aware player mentions with the preserved reported-speech guard."""
from model import entities_v032_core as parser

RULE_VERSION='entity-rules-v0.3.2'


def extract_entities(public_text,speaker_id=None):
    result=parser.extract_entities(public_text,speaker_id)
    parts=parser.segments(public_text)
    # "2号向我报告" contains the substring "我报告", but the claimant is2号.
    # Mask self pronouns in explicitly attributed clauses before claim parsing.
    guarded=[]
    for seg in parts:
        if parser.third_party(seg.text):
            guarded.append(parser.Segment('有人说'+seg.text.replace('我','对方'),seg.sentence,seg.index))
        else:guarded.append(seg)
    checks=parser.completed_claims(guarded,result['claimed_role'])
    result.update(check_claims=checks,check_target=checks[0]['target'] if len(checks)==1 else None,
                  check_result=checks[0]['result'] if len(checks)==1 else None)
    result.update(parser.social_entities(parts,checks))
    for prefix in ['agreement','suspect','support']:
        targets=result[prefix+'_targets'];result[prefix+'_target']=targets[0] if len(targets)==1 else None
    return result
