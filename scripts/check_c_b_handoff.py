"""C consolidated B handoff checks. Offline only; no network or credentials."""
from pathlib import Path
import json,tempfile,hashlib,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from unittest.mock import patch
from app.engine import GameEngine
from app.agents import DeepSeekAgent,StubAgent
from app.deepseek_client import DeepSeekClient
from app.rules import GameState,check_action

ROOT=Path(__file__).resolve().parents[1]
ROLES={1:'wolf',2:'wolf',3:'seer',4:'villager',5:'villager',6:'villager'}

def run_checks():
    result={'revision':'bc79b60','mode':'offline_only','paid_requests':0,'checks':{}}
    scratch=ROOT/'.local/c_audit'
    scratch.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='b-complete-audit-',dir=scratch) as temp:
        base=Path(temp)
        class Invalid(StubAgent):
            def act(self,*args):return {'action':'check','target':4,'text':'','reason':'C offline probe'}
        classifications=[]
        def classify(*args):
            classifications.append(args)
            return {'intent':'low_info','model_version':'C_OFFLINE_CLASSIFIER','extracted_entities':{}}
        engine=GameEngine(str(base/'failed_speech'),agent=Invalid(),classifier=classify)
        gid=engine.create_game(42,ROLES)['game_id'];engine.games[gid].phase='day_speak'
        engine.advance_one_event(gid,'failed-speech',1)
        events=engine.store.read(gid)
        result['checks']['failed_speech_continues']={
            'status':engine.games[gid].status,'event_types':[e['type'] for e in events],
            'classifier_calls_after_failure':len(classifications),
            'speech_texts':[e.get('text') for e in events if e['type']=='public_speech'],
            'expected':'Stop after final model_failure; no fabricated speech/classification and no reset to running.'}

        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'choices':[{'message':{'content':'{broken'}}],
                                               'usage':{'prompt_tokens':211,'completion_tokens':31}}).encode()
        intercepted=[]
        def fake_open(request,**kwargs):intercepted.append(True);return Response()
        with patch('urllib.request.urlopen',fake_open):
            engine=GameEngine(str(base/'bad_json'),agent=DeepSeekAgent(client=DeepSeekClient(api_key='TEST_ONLY',model='C_OFFLINE')))
            gid=engine.create_game(42,ROLES)['game_id'];engine.advance_one_event(gid,'bad-json',1)
        calls=engine.store.read_calls(gid)
        result['checks']['malformed_json']={'fake_transport_calls':len(intercepted),'recorded_attempts':len(calls),
            'input_tokens':[c['input_tokens'] for c in calls],'latency_ms':[c['latency_ms'] for c in calls],
            'status':engine.games[gid].status,
            'expected':'Known response usage must survive parsing failure; define bounded retry vs timeout without blind resubmission.'}

        engine=GameEngine(str(base/'day_limit'),agent=StubAgent())
        gid=engine.create_game(42,ROLES)['game_id'];engine.games[gid].day=7
        for n in range(4):engine.advance_one_event(gid,str(n))
        state=engine.games[gid]
        result['checks']['eighth_day_boundary']={'status':state.status,'phase':state.phase,'day':state.day,
            'day8_public_speeches':sum(e['type']=='public_speech' and e['day']==8 for e in engine.store.read(gid)),
            'needs_rule_confirmation':True,'observation':'Limit is applied immediately after eighth night before eighth-day speech/vote.'}

        engine=GameEngine(str(base/'empty_votes'),agent=StubAgent())
        gid=engine.create_game(42,ROLES)['game_id'];state=engine.games[gid]
        state.phase='day_vote';state.day=1;state.day_voted=set(state.alive)
        engine.advance_one_event(gid,'no-valid-votes',1)
        result['checks']['empty_votes']={'status':state.status,'phase':state.phase,'revote':state.revote,
            'revote_candidates':state.pending.get('revote_candidates'),
            'allowed_vote_error':check_action(state,3,{'action':'vote','target':4,'text':'','reason':''}),
            'expected':'No valid votes must have a usable documented resolution; do not enter revote with zero eligible targets.'}

        state=GameState('bool-target',42,ROLES)
        result['checks']['boolean_target']={'inspect_target_true_error':check_action(state,3,{'action':'inspect','target':True}),
                                           'expected':'JSON boolean is not a player integer.'}
    result['checks']['source_review']={
        'seer_prompt':(ROOT/'prompts/seer-v1.md').read_text(encoding='utf-8'),
        'retry_validation_feedback':'Original context reused without validation_error feedback.',
        'host_generation':'No host model invocation in engine flow; prompt file alone is not integration.',
        'negotiation':'wolf_negotiation is seeded resolution, not additional agent negotiation calls.',
        'pending_operations':'In-flight and missing operations both report unknown; pause/resume have no operation-id registry.',
        'budget':'B default DeepSeek client has no output-token/per-game-cost bound; C smoke wrapper is separate.',
        'cross_game_metadata':'One DeepSeekAgent.last_metadata shared across games; concurrent safety requires proof or restriction.'}
    original_path=ROOT/'evidence/c_first_real_smoke_20261029.json'
    if original_path.is_file():
        original=json.loads(original_path.read_text(encoding='utf-8'))
        run=ROOT/'runs/real_api'/original['game_id']
        result['old_real_originals_unchanged']=(all(hashlib.sha256((run/name).read_bytes()).hexdigest()==h
            for name,h in original['source_sha256'].items()) if run.is_dir() else 'Local real-run files unavailable; offline probes still ran.')
    budget=ROOT/'.local/real_api/smoke_budget_20261029.json'
    result['live_budget']=json.loads(budget.read_text()) if budget.is_file() else None
    return result

if __name__=='__main__':
    report=run_checks()
    (ROOT/'evidence/c_b_complete_audit_20261029.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='checks'},ensure_ascii=False))
    for key,value in report['checks'].items():
        if key!='source_review':print(key,json.dumps(value,ensure_ascii=False))
