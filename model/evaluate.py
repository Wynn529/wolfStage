"""One frozen evaluation run; metrics and errors are never fed back into training."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
if __package__ in (None, ''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from model.common import ROOT, LABELS, MODEL_VERSION, file_hash, load_rows, write_json, write_csv
from model.predict import SmallModel
from model.baseline import keyword_intent

ENTITY_FIELDS=['player_ids','claimed_role','check_claims','check_target','check_result','vote_target',
               'follow_target','excluded_vote_targets','withdrawn_vote_targets','vote_mode','vote_condition',
               'conditional_vote_alternatives','agreement_target','agreement_targets','suspect_target',
               'suspect_targets','support_target','support_targets']


def canonical(value):
    if isinstance(value,list):return sorted(json.dumps(v,ensure_ascii=False,sort_keys=True) for v in value)
    return value


def atoms(value):
    if value in (None,'',[]):return set()
    if isinstance(value,list):return {json.dumps(v,ensure_ascii=False,sort_keys=True) for v in value}
    return {json.dumps(value,ensure_ascii=False,sort_keys=True)}


def entity_metrics(rows,predicted):
    metrics={}
    for field in ENTITY_FIELDS:
        expected=[r.get(field) for r in rows];actual=[r['extracted_entities'][field] for r in predicted]
        gold_nonempty=sum(bool(atoms(x)) for x in expected)
        pred_nonempty=sum(bool(atoms(x)) for x in actual)
        exact=sum(canonical(a)==canonical(b) for a,b in zip(expected,actual))
        positive_exact=sum(bool(atoms(a)) and canonical(a)==canonical(b) for a,b in zip(expected,actual))
        tp=sum(len(atoms(a)&atoms(b)) for a,b in zip(expected,actual))
        expected_items=sum(len(atoms(a)) for a in expected);pred_items=sum(len(atoms(a)) for a in actual)
        metrics[field]={'total':len(rows),'exact_match_count':exact,'exact_match_accuracy':exact/len(rows),
            'gold_nonempty_rows':gold_nonempty,'predicted_nonempty_rows':pred_nonempty,
            'nonempty_exact_match_count':positive_exact,'nonempty_exact_match_accuracy':positive_exact/gold_nonempty if gold_nonempty else None,
            'gold_items':expected_items,'predicted_items':pred_items,'true_positive_items':tp,
            'precision':tp/pred_items if pred_items else None,'recall':tp/expected_items if expected_items else None,
            'note':'Set exact match for arrays; scalar exact match otherwise. Conditions compare literal text, not semantic equivalence.'}
    return metrics


def likely_reason(gold,pred,text):
    if gold==pred:return ''
    if gold=='low_info':return '较长但缺少具体内容的发言与有效讨论混淆。'
    if gold=='suspected_violation':return '委婉越权或混合意图中的异常请求未优先识别。'
    if pred=='suspected_violation':return '提及系统、公开记录或引用越权语句，可能触发了过强异常词特征。'
    if gold=='discussion' and pred in ('role_claim','check_claim'):return '转述、假设或否定语境中的身份/查验词被当作本人宣称。'
    if {gold,pred}&{'check_claim','role_claim'}:return '身份与查验表达、主标签优先级或整段意图混淆。'
    if gold=='vote_intent' or pred=='vote_intent':return '当前投票、历史票型、撤回/否定或一般讨论边界混淆。'
    return '词面特征不足以识别该完整发言的主意图。'


def evaluate_one(model,rows,name,output):
    predictions=[];wrong=[]
    # Warm-up excluded from latency statistics and from dataset predictions.
    model.analyze('这是一次计时预热。')
    start=time.perf_counter()
    results=[model.analyze(r['public_text'],r.get('speaker_id')) for r in rows]
    elapsed=time.perf_counter()-start
    true=[r['intent'] for r in rows];pred=[r['intent'] for r in results]
    baseline=[keyword_intent(r['public_text']) for r in rows]
    entities=entity_metrics(rows,results)
    report=classification_report(true,pred,labels=LABELS,output_dict=True,zero_division=0)
    baseline_report=classification_report(true,baseline,labels=LABELS,output_dict=True,zero_division=0)
    for gold,result,base in zip(rows,results,baseline):
        mismatch=[k for k in ENTITY_FIELDS if canonical(gold.get(k))!=canonical(result['extracted_entities'][k])]
        record={'sample_id':gold['sample_id'],'public_text':gold['public_text'],'intent_gold':gold['intent'],'intent_predicted':result['intent'],
            'keyword_baseline':base,'score':result['score'],'score_gap':result['score_gap'],'confidence_type':result['confidence_type'],
            'class_scores':result['class_scores'],'entities_gold':{k:gold.get(k) for k in ENTITY_FIELDS},
            'entities_predicted':result['extracted_entities'],'entity_mismatch_fields':mismatch,
            'source_group':gold['source_group'],'source_type':gold['source_type'],'model_version':MODEL_VERSION,'latency_ms':result['latency_ms']}
        predictions.append(record)
        if gold['intent']!=result['intent'] or mismatch:
            wrong.append({**record,'intent_wrong':gold['intent']!=result['intent'],
                'possible_reason':likely_reason(gold['intent'],result['intent'],gold['public_text']),
                'flow_impact':'可能影响公开上下文摘要；正式行动与规则裁决必须继续独立校验。'})
    metrics={'dataset':name,'count':len(rows),'class_counts':dict(Counter(true)),
             'accuracy':float(accuracy_score(true,pred)),
             'macro_f1':float(f1_score(true,pred,labels=LABELS,average='macro',zero_division=0)),
             'classification_report':report,'confusion_matrix_labels':LABELS,'confusion_matrix':confusion_matrix(true,pred,labels=LABELS).tolist(),
             'keyword_baseline':{'accuracy':float(accuracy_score(true,baseline)),
                 'macro_f1':float(f1_score(true,baseline,labels=LABELS,average='macro',zero_division=0)),
                 'classification_report':baseline_report},
             'entities':entities,'intent_error_count':sum(a!=b for a,b in zip(true,pred)),
             'any_entity_error_count':sum(bool(x['entity_mismatch_fields']) for x in predictions),
             'latency_ms':{'median':float(np.median([x['latency_ms'] for x in results])),
                            'p95':float(np.percentile([x['latency_ms'] for x in results],95)),
                            'batch_seconds':elapsed,'scope':'loaded model,classification+entity extraction; initialization excluded'},
             'limitations':['Same author as training pool; source-group holdout only.','Not real DeepSeek game validation.','Entity condition exact text score is not semantic correctness.']}
    write_json(output/f'{name}_metrics.json',metrics)
    write_json(output/f'{name}_predictions.json',predictions)
    write_json(output/f'{name}_errors.json',wrong)
    fields=['sample_id','public_text','intent_gold','intent_predicted','keyword_baseline','score','score_gap','confidence_type','entity_mismatch_fields','possible_reason','flow_impact','entities_gold','entities_predicted','source_group','source_type','model_version']
    write_csv(output/f'{name}_predictions.csv',predictions,fields)
    write_csv(output/f'{name}_errors.csv',wrong,fields)
    return metrics


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path,default=ROOT/'model/classifier.joblib')
    parser.add_argument('--test',type=Path,default=ROOT/'data/test_frozen.csv')
    parser.add_argument('--probe',type=Path,default=ROOT/'data/synthetic_agent_probe.csv')
    parser.add_argument('--output',type=Path,default=ROOT/'evidence/evaluation_v01')
    args=parser.parse_args()
    if (args.output/'evaluation_lock.json').exists():
        raise SystemExit('Evaluation output already exists. Preserve it; reproduction requires a new --output directory.')
    approved=json.loads((ROOT/'data/approved_manifest.json').read_text(encoding='utf-8'))
    for name,metadata in approved['files'].items():
        assert file_hash(ROOT/'data'/name)==metadata['sha256'],f'Approved data changed:{name}'
    train=load_rows(ROOT/'data/train.csv');test=load_rows(args.test);probe=load_rows(args.probe)
    for a,b in [(train,test),(train,probe),(test,probe)]:
        assert not {r['source_group'] for r in a}&{r['source_group'] for r in b}
        assert not {r['sample_id'] for r in a}&{r['sample_id'] for r in b}
    files=[args.model,args.test,args.probe]+list((ROOT/'model').glob('*.py'))
    lock={'model_version':MODEL_VERSION,'timestamp_utc':datetime.now(timezone.utc).isoformat(),
          'code_and_data_frozen_before_external_scoring':True,
          'hashes':{str(p.resolve().relative_to(ROOT)):file_hash(p) for p in files},
          'protocol':'Select on training group CV; freeze model and rules; evaluate test and probe once; no feedback tuning.'}
    write_json(args.output/'evaluation_lock.json',lock)
    model=SmallModel(args.model)
    results={name:evaluate_one(model,rows,name,args.output) for name,rows in [('test_frozen',test),('synthetic_probe',probe)]}
    write_json(args.output/'small_model_metrics.json',{'model_version':MODEL_VERSION,'results':results,'real_domain_check':{'count':0,'status':'awaiting B/C actual game logs'}})
    print(json.dumps({name:{k:r[k] for k in ['count','accuracy','macro_f1','intent_error_count','any_entity_error_count']} for name,r in results.items()},ensure_ascii=True))


if __name__=='__main__':main()
