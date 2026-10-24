"""B/C callable inference API. Import model.predict.classify_utterance."""
from __future__ import annotations
import argparse
from functools import lru_cache
import json
import math
from pathlib import Path
import sys
import time
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import joblib
from model.common import ROOT, SCHEMA_VERSION, RULE_VERSION
from model.entities import extract_entities

MAX_TEXT_LENGTH = 10000


class InputValidationError(ValueError):
    """Invalid public input; the caller may log this controlled error and retry."""


class SmallModel:
    def __init__(self, model_path=ROOT/'model/classifier.joblib'):
        self.path=Path(model_path)
        bundle=joblib.load(self.path)
        self.pipeline=bundle['pipeline']
        self.manifest=bundle['manifest']

    def classify_utterance(self, game_id, event_id, speaker_id, public_text):
        if not isinstance(game_id,str) or not game_id.strip() or len(game_id)>256:
            raise InputValidationError('game_id must be a nonempty string of at most256 characters')
        if not isinstance(event_id,str) or not event_id.strip() or len(event_id)>256:
            raise InputValidationError('event_id must be a nonempty string of at most256 characters')
        if type(speaker_id) is not int or speaker_id not in range(1,7):
            raise InputValidationError('speaker_id must be an integer from1 to6')
        if not isinstance(public_text,str) or not public_text.strip():
            raise InputValidationError('public_text must be a nonempty string')
        if len(public_text)>MAX_TEXT_LENGTH:
            raise InputValidationError(f'public_text exceeds {MAX_TEXT_LENGTH} characters')
        return self.analyze(public_text, speaker_id, game_id, event_id)

    def analyze(self, public_text, speaker_id=None, game_id=None, event_id=None):
        """Internal evaluation also permits absent source speaker metadata."""
        started=time.perf_counter()
        scores=self.pipeline.decision_function([public_text])[0]
        winner=int(scores.argmax())
        ordered=sorted((float(v) for v in scores),reverse=True)
        intent=str(self.pipeline.classes_[winner])
        entities=extract_entities(public_text,speaker_id)
        return {
            'game_id':game_id,'event_id':event_id,'speaker_id':speaker_id,'public_text':public_text,
            'intent':intent,'confidence':None,'confidence_type':'not_calibrated',
            'score':float(scores[winner]),'score_type':'linear_svc_ovr_decision_function',
            'score_gap':ordered[0]-ordered[1],
            'class_scores':{str(k):float(v) for k,v in zip(self.pipeline.classes_,scores)},
            'extracted_entities':entities,
            'advisory_flags':['suspected_violation'] if intent=='suspected_violation' else [],
            'model_version':self.manifest['model_version'],'schema_version':SCHEMA_VERSION,'entity_rule_version':RULE_VERSION,
            'entity_source':'deterministic_public_text_rules',
            'latency_ms':(time.perf_counter()-started)*1000,
        }


@lru_cache(maxsize=1)
def _default_model():
    return SmallModel()


def classify_utterance(game_id: str,event_id: str,speaker_id: int,public_text: str) -> dict:
    return _default_model().classify_utterance(game_id,event_id,speaker_id,public_text)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path,default=ROOT/'model/classifier.joblib')
    parser.add_argument('--text',required=True)
    parser.add_argument('--game-id',default='local-demo')
    parser.add_argument('--event-id',default='speech-001')
    parser.add_argument('--speaker-id',type=int,default=1)
    args=parser.parse_args()
    try:
        value=SmallModel(args.model).classify_utterance(args.game_id,args.event_id,args.speaker_id,args.text)
    except InputValidationError as exc:
        print(json.dumps({'error':{'code':'invalid_input','message':str(exc)}},ensure_ascii=False))
        return 2
    print(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
    return 0


if __name__=='__main__':
    sys.exit(main())

