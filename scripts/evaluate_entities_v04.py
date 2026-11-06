from pathlib import Path
import json
import sys
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from model.entities_v04 import extract_entities

def main():
    rows=json.loads((ROOT/'data/ten_player_v04/approved_candidates.json').read_text(encoding='utf-8'))
    failures=[];total=Counter();passed=Counter();matches=0
    for r in rows:
        a=extract_entities(r['public_text'],r['speaker_id']);errors=[]
        for k,v in r['expected_entity_checks'].items():
            total[k]+=1
            if a.get(k)==v:passed[k]+=1
            else:errors.append(dict(field=k,expected=v,actual=a.get(k)))
        keys=['role','action','target','status','night']
        strip=lambda xs:[{k:x[k] for k in keys} for x in xs]
        if strip(a['skill_claims'])!=strip(r['proposed_skill_claims']):errors.append(dict(field='skill_claims',expected=r['proposed_skill_claims'],actual=a['skill_claims']))
        if errors:failures.append(dict(sample_id=r['sample_id'],errors=errors))
        else:matches+=1
    e=ROOT/'evidence/ten_player_v04';evaluation=json.loads((e/'evaluation.json').read_text(encoding='utf-8'))
    evaluation['entity_development_regression']=dict(count=len(rows),records_matching_all_annotated_checks=matches,scope='partial_annotated_fields_and_skill_semantics_on_known_material',field_total=dict(total),field_pass=dict(passed))
    for name,obj in [('evaluation.json',evaluation),('entity_errors.json',failures)]:
        (e/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'matching':matches,'count':len(rows),'failures':failures},ensure_ascii=False))
if __name__=='__main__':main()
