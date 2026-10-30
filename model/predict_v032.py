"""Same trained classifier with entity-rules-v0.3.2; previous versions retained."""
from __future__ import annotations
import argparse
from functools import lru_cache
import json
from pathlib import Path
import sys
import time
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model.common import ROOT
from model.predict import SmallModel as IntentModelV01, InputValidationError
from model.entities_v032 import extract_entities, RULE_VERSION


class SmallModel(IntentModelV01):
    def analyze(self,public_text,speaker_id=None,game_id=None,event_id=None):
        started=time.perf_counter()
        # Reuse the frozen classifier's decision and public-input contract.
        result=super().analyze(public_text,speaker_id,game_id,event_id)
        result['extracted_entities']=extract_entities(public_text,speaker_id)
        result['entity_rule_version']=RULE_VERSION
        result['latency_ms']=(time.perf_counter()-started)*1000
        return result


@lru_cache(maxsize=1)
def _default_model():return SmallModel()


def classify_utterance(game_id:str,event_id:str,speaker_id:int,public_text:str)->dict:
    return _default_model().classify_utterance(game_id,event_id,speaker_id,public_text)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--model',type=Path,default=ROOT/'model/classifier.joblib')
    p.add_argument('--text',required=True)
    p.add_argument('--game-id',default='local-demo-v032')
    p.add_argument('--event-id',default='speech-001')
    p.add_argument('--speaker-id',type=int,default=1)
    a=p.parse_args()
    try:r=SmallModel(a.model).classify_utterance(a.game_id,a.event_id,a.speaker_id,a.text)
    except InputValidationError as exc:
        print(json.dumps({'error':{'code':'invalid_input','message':str(exc)}},ensure_ascii=False));return 2
    print(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False));return 0


if __name__=='__main__':sys.exit(main())
