"""Approved development data training; never draws rows from frozen test/probes."""
from pathlib import Path
import csv
import hashlib
import json
import sys
import platform
import argparse
from collections import Counter
import numpy as np
import joblib
import sklearn
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.metrics import classification_report,confusion_matrix,f1_score
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from model.text_v04 import normalize_public_text,VERSION
from model.baseline import keyword_intent
from model.entities_v04 import extract_entities
SEED=20261105
OUT=ROOT/'model/ten_player_v04'
EVIDENCE=ROOT/'evidence/ten_player_v04'

def write(path,obj):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def report(y,p,labels):return dict(classification_report=classification_report(y,p,labels=labels,output_dict=True,zero_division=0),confusion_matrix=confusion_matrix(y,p,labels=labels).tolist(),labels=labels)
def main():
    global OUT,EVIDENCE
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=OUT);parser.add_argument('--evidence-dir',type=Path,default=EVIDENCE)
    args=parser.parse_args();OUT=args.output_dir;EVIDENCE=args.evidence_dir
    if (OUT/'classifier.joblib').exists():raise RuntimeError('Version already trained; do not overwrite')
    source=ROOT/'data/ten_player_v04/approved_candidates.json'
    candidates=json.loads(source.read_text(encoding='utf-8'))
    with (ROOT/'data/train.csv').open(encoding='utf-8-sig',newline='') as f:legacy=list(csv.DictReader(f))
    rows=[dict(sample_id=r['sample_id'],public_text=r['public_text'],intent=r['intent'],source_group=r['source_group'],pool='legacy_train') for r in legacy]
    rows += [dict(sample_id=r['sample_id'],public_text=r['public_text'],intent=r['intent'],source_group=r['source_group'],pool='approved_ten_player_development') for r in candidates]
    # Exact normalized duplicates are one training record and cannot cross folds.
    seen={};merged=[]
    for r in rows:
        r['normalized_public_text']=normalize_public_text(r['public_text'])[0]
        key=r['normalized_public_text']
        if key in seen:
            assert seen[key]['intent']==r['intent'],'Conflicting labels require review'
            merged.append([seen[key]['sample_id'],r['sample_id']]);continue
        seen[key]=r
    rows=list(seen.values())
    labels=sorted({r['intent'] for r in rows})
    x=np.array([r['normalized_public_text'] for r in rows]);y=np.array([r['intent'] for r in rows]);g=np.array([r['source_group'] for r in rows])
    splits=list(StratifiedGroupKFold(n_splits=3,shuffle=True,random_state=SEED).split(x,y,g))
    trials=[]
    for c in [0.5,1.0,2.0]:
        pred=np.empty(len(rows),dtype=object)
        for a,b in splits:
            assert not set(g[a])&set(g[b])
            model=Pipeline([('tfidf',TfidfVectorizer(analyzer='char',ngram_range=(1,3),min_df=1,max_features=50000,sublinear_tf=True,lowercase=False)),('classifier',LinearSVC(C=c,dual=True,random_state=SEED,max_iter=10000))])
            model.fit(x[a],y[a]);pred[b]=model.predict(x[b])
        trials.append(dict(C=c,macro_f1=float(f1_score(y,pred,average='macro')),oof_predictions=pred.tolist()))
    best=max(trials,key=lambda r:r['macro_f1'])
    model=Pipeline([('tfidf',TfidfVectorizer(analyzer='char',ngram_range=(1,3),min_df=1,max_features=50000,sublinear_tf=True,lowercase=False)),('classifier',LinearSVC(C=best['C'],dual=True,random_state=SEED,max_iter=10000))])
    model.fit(x,y)
    manifest=dict(model_version='small-intent-v0.2-ten-player',schema_version='small-intent-schema-v0.4',preprocessing_version=VERSION,
        training_count=len(rows),class_counts=dict(Counter(y)),data_version='approved-ten-player-v03-plus-legacy-train',
        approval_basis='用户明确确认“这个数据可以了”，随后授权继续实施和训练',
        data_sha256={'legacy_train':sha(ROOT/'data/train.csv'),'approved_102':sha(source)},
        fit_feature_columns=['normalized_public_text'],entity_annotations_used_as_training_features=False,
        selection='3-fold grouped internal development CV; C selected on these folds, not an unbiased independent estimate',
        selected_C=best['C'],random_seed=SEED,confidence_type='not_calibrated',python=platform.python_version(),sklearn=sklearn.__version__,
        real_ten_player_validation_count=0,independent_new_test_count=0,merged_exact_duplicates=merged)
    OUT.mkdir(parents=True,exist_ok=True);joblib.dump(dict(pipeline=model,manifest=manifest),OUT/'classifier.joblib',compress=3)
    manifest['model_sha256']=sha(OUT/'classifier.joblib');write(OUT/'model_manifest.json',manifest)
    write(OUT/'training_rows.json',rows)
    write(EVIDENCE/'training_cv.json',dict(trials=trials,folds=[dict(fold=i,train_ids=[rows[j]['sample_id'] for j in a],validation_ids=[rows[j]['sample_id'] for j in b]) for i,(a,b) in enumerate(splits)]))
    old=joblib.load(ROOT/'model/classifier.joblib')['pipeline'];result={}
    for name in ['test_frozen.csv','synthetic_agent_probe.csv']:
        with (ROOT/'data'/name).open(encoding='utf-8-sig',newline='') as f:test=list(csv.DictReader(f))
        truth=[r['intent'] for r in test];raw=[r['public_text'] for r in test];norm=[normalize_public_text(t)[0] for t in raw]
        predictions={'old_model':old.predict(raw).tolist(),'new_model':model.predict(norm).tolist(),'fixed_keyword_baseline':[keyword_intent(t) for t in raw]}
        result[name]=dict(scope='previously_known_frozen_material_regression_not_new_blind_test',count=len(test),**{k:report(truth,v,labels) for k,v in predictions.items()})
        write(EVIDENCE/(name+'.predictions.json'),[dict(sample_id=r['sample_id'],expected=r['intent'],**{k:v[i] for k,v in predictions.items()}) for i,r in enumerate(test)])
    oof=best['oof_predictions']
    for pool in ['legacy_train','approved_ten_player_development']:
        ids=[i for i,r in enumerate(rows) if r['pool']==pool]
        result[pool+'_internal_oof']=dict(scope='model_selection_development_CV_not_independent',count=len(ids),**report(y[ids],[oof[i] for i in ids],labels))
    failures=[];field_total=Counter();field_pass=Counter();exact=0
    for r in candidates:
        actual=extract_entities(r['public_text'],r['speaker_id']);errors=[]
        for k,v in r['expected_entity_checks'].items():
            field_total[k]+=1
            if actual.get(k)==v:field_pass[k]+=1
            else:errors.append(dict(field=k,expected=v,actual=actual.get(k)))
        expected=r['proposed_skill_claims'];a=actual['skill_claims']
        # Compare semantic fields; evidence span boundaries may differ, and remain inspectable.
        keys=['role','action','target','status','night']
        if [{k:v[k] for k in keys} for v in a]!=[{k:v[k] for k in keys} for v in expected]:errors.append(dict(field='skill_claims',expected=expected,actual=a))
        if errors:failures.append(dict(sample_id=r['sample_id'],errors=errors))
        else:exact+=1
    result['entity_development_regression']=dict(count=len(candidates),records_matching_all_annotated_checks=exact,scope='partial_annotated_fields_and_skill_semantics_on_known_material',field_total=dict(field_total),field_pass=dict(field_pass))
    write(EVIDENCE/'entity_errors.json',failures)
    write(EVIDENCE/'evaluation.json',result)
    print(json.dumps(dict(training_count=len(rows),selected_C=best['C'],internal_cv_macro_f1=best['macro_f1'],entity_matching=exact,entity_errors=len(failures)),ensure_ascii=False))
if __name__=='__main__':main()
