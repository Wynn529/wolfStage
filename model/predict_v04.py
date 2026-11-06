"""Versioned ten-player inference with optional frozen old weights for comparison."""
import argparse
from functools import lru_cache
import json
from pathlib import Path
import time
from model.predict import SmallModel as BaseModel, InputValidationError, MAX_TEXT_LENGTH
from model.common import ROOT
from model.entities_v04 import extract_entities,RULE_VERSION
from model.text_v04 import normalize_public_text,VERSION

SCHEMA_VERSION='small-intent-schema-v0.4'
DEFAULT_MODEL=ROOT/'model/ten_player_v04/classifier.joblib'

class SmallModel(BaseModel):
    def __init__(self,model_path=DEFAULT_MODEL):super().__init__(model_path)

    def classify_utterance(self,game_id,event_id,speaker_id,public_text):
        for name,value in [('game_id',game_id),('event_id',event_id)]:
            if not isinstance(value,str) or not value.strip() or len(value)>256:raise InputValidationError(name+' must be a nonempty string of at most256 characters')
        if type(speaker_id) is not int or not 1<=speaker_id<=10:raise InputValidationError('speaker_id must be an integer from1 to10')
        if not isinstance(public_text,str) or len(public_text)>MAX_TEXT_LENGTH:raise InputValidationError('public_text must be a string of at most10000 characters')
        return self.analyze(public_text,speaker_id,game_id,event_id)

    def analyze(self,public_text,speaker_id=None,game_id=None,event_id=None):
        started=time.perf_counter();text,flags=normalize_public_text(public_text)
        empty=not public_text.strip()
        if empty:
            intent='low_info';score=gap=None;scores={}
        else:
            values=self.pipeline.decision_function([text])[0]
            winner=int(values.argmax());ordered=sorted(map(float,values),reverse=True)
            intent=str(self.pipeline.classes_[winner]);score=float(values[winner]);gap=ordered[0]-ordered[1]
            scores={str(k):float(v) for k,v in zip(self.pipeline.classes_,values)}
        if intent=='suspected_violation':flags.append('suspected_violation')
        return dict(game_id=game_id,event_id=event_id,speaker_id=speaker_id,public_text=public_text,
            intent=intent,confidence=None,confidence_type='not_calibrated',score=score,score_gap=gap,
            score_type='not_applicable' if empty else 'linear_svc_ovr_decision_function',class_scores=scores,
            extracted_entities=extract_entities(public_text,speaker_id),advisory_flags=flags,
            model_version=self.manifest['model_version'],schema_version=SCHEMA_VERSION,entity_rule_version=RULE_VERSION,
            entity_source='deterministic_public_text_rules',preprocessing_version=VERSION,
            prediction_source='empty_text_rule' if empty else 'trained_classifier',
            normalized_public_text=text,entity_evidence_policy='exclude_from_agent_evidence' if intent=='suspected_violation' else 'unverified_public_claims',
            latency_ms=(time.perf_counter()-started)*1000)

@lru_cache(maxsize=1)
def _default():return SmallModel()
def classify_utterance(game_id,event_id,speaker_id,public_text):return _default().classify_utterance(game_id,event_id,speaker_id,public_text)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--text',required=True);p.add_argument('--speaker-id',type=int,default=1)
    a=p.parse_args();print(json.dumps(classify_utterance('local-demo','speech-1',a.speaker_id,a.text),ensure_ascii=False,indent=2))
