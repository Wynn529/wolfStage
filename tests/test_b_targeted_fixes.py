"""授权后的B局部修复：只用替身，禁止真实API。"""
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from app.engine import GameEngine
from app.agents import StubAgent,DeepSeekAgent,build_context
from app.deepseek_client import DeepSeekClient
from app.rules import check_action

ROLES={1:'wolf',2:'wolf',3:'seer',4:'villager',5:'villager',6:'villager'}


def game(tmp_path,agent=None,classifier=None):
    e=GameEngine(str(tmp_path),agent=agent or StubAgent(),classifier=classifier)
    return e,e.create_game(42,ROLES)['game_id']


@pytest.mark.parametrize('phase,seer_only',[('night',False),('night',True),('day_speak',False),('day_vote',False)])
def test_final_failure_has_no_followup_business_events(tmp_path,phase,seer_only):
    class Invalid(StubAgent):
        attempts=0
        def act(self,state,actor,context):
            self.attempts+=1
            if self.attempts==2:assert 'validation_error' in context
            return {'action':'check','target':4}
    classified=[];agent=Invalid()
    e,gid=game(tmp_path,agent,lambda *args:classified.append(args))
    state=e.games[gid];state.phase=phase
    if seer_only:state.night_done={1,2}
    result=e.advance_one_event(gid,'failing',1)
    events=e.store.read(gid)
    assert result['status']=='failed' and agent.attempts==2 and not classified
    assert [v['type'] for v in events]==['game_created','model_failure']
    assert not state.day_speakers and not state.day_voted
    e.advance_one_event(gid,'after-failure')
    assert agent.attempts==2 and e.store.read(gid)==events


class Response:
    def __init__(self,content,tokens=211,finish='stop'):
        self.content=content;self.tokens=tokens;self.finish=finish
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):
        return json.dumps({'id':'test-response','model':'test-model','usage':{'prompt_tokens':self.tokens,'completion_tokens':31},
                           'choices':[{'finish_reason':self.finish,'message':{'content':self.content}}]}).encode()


@pytest.mark.parametrize('content,finish',[('{broken','stop'),('', 'stop'),('{}','length'),('[]','stop')])
def test_bad_output_retry_keeps_usage_and_feedback(tmp_path,monkeypatch,content,finish):
    requests=[]
    def fake(request,**kwargs):
        body=json.loads(request.data);requests.append(body)
        assert body['max_tokens']==1024 and body['thinking']['type']=='disabled'
        return Response(content,211,finish) if len(requests)==1 else Response('{"action":"kill","target":4,"text":"","reason":"test"}',222)
    monkeypatch.setattr('urllib.request.urlopen',fake)
    e,gid=game(tmp_path,DeepSeekAgent(client=DeepSeekClient(api_key='TEST_ONLY',model='test-model')))
    e.advance_one_event(gid,'bad-output',1)
    calls=e.store.read_calls(gid)
    assert [c['input_tokens'] for c in calls]==[211,222]
    assert [c['success'] for c in calls]==[False,True]
    assert 'validation_error' in json.loads(requests[1]['messages'][1]['content'])
    assert calls[0]['failure_stage'] in {'action_validation','output_parsing'}
    assert len(calls[0]['prompt_hash'])==64 and calls[1]['validation_error'] is None


def test_timeout_is_not_automatically_resent_and_unknown_usage_stays_unknown(tmp_path,monkeypatch):
    sent=[]
    def timeout(*args,**kwargs):sent.append(True);raise TimeoutError('timed out')
    monkeypatch.setattr('urllib.request.urlopen',timeout)
    e,gid=game(tmp_path,DeepSeekAgent(client=DeepSeekClient(api_key='TEST_ONLY',model='test-model')))
    e.advance_one_event(gid,'timeout',1)
    calls=e.store.read_calls(gid);metrics=e.get_metrics(gid)
    assert len(sent)==len(calls)==1 and e.games[gid].status=='failed'
    assert calls[0]['input_tokens'] is None and calls[0]['failure_stage']=='request'
    assert metrics['input_tokens'] is None and metrics['input_tokens_missing_calls']==1


def test_second_request_exception_cannot_reuse_first_usage(tmp_path):
    class Client:
        model='offline'
        n=0
        def complete(self,*args,**kwargs):
            self.n+=1
            if self.n==2:raise TimeoutError('unknown second attempt')
            return {'action':'kill','target':99},{'input_tokens':111,'output_tokens':11,'latency_ms':100}
    e,gid=game(tmp_path,DeepSeekAgent(client=Client()))
    e.advance_one_event(gid,'retry-exception',1)
    calls=e.store.read_calls(gid)
    assert [c['input_tokens'] for c in calls]==[111,None]
    assert calls[1]['latency_ms'] is None


def test_metadata_is_scoped_to_request_thread(tmp_path):
    class Client:
        model='offline'
        def complete(self,messages,*,agent_id,**kwargs):
            return {'action':'kill','target':4},{'input_tokens':int(agent_id),'output_tokens':2,'latency_ms':3}
    agent=DeepSeekAgent(client=Client());e,gid=game(tmp_path,agent)
    barrier=threading.Barrier(2)
    def invoke(actor):
        agent.act(e.games[gid],actor,build_context(e.games[gid],[],actor,'wolf'))
        barrier.wait(timeout=3)
        return agent.last_metadata['input_tokens']
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(invoke,[1,2]))==[1,2]


def test_no_votes_skips_impossible_empty_revote(tmp_path):
    e,gid=game(tmp_path);s=e.games[gid];s.phase='day_vote';s.day=1;s.day_voted=set(s.alive)
    e.advance_one_event(gid,'empty',1)
    assert s.phase=='night' and not s.revote and 'revote_candidates' not in s.pending
    assert s.alive==set(range(1,7))
    assert not any(v['type']=='exile' for v in e.store.read(gid))


@pytest.mark.parametrize('winning_vote',[False,True])
def test_eighth_day_runs_before_limit_and_win_takes_priority(tmp_path,winning_vote):
    e,gid=game(tmp_path);s=e.games[gid];s.day=7
    for n in range(4):e.advance_one_event(gid,str(n))
    assert s.day==8 and s.phase=='day_speak' and s.status=='running'
    s.phase='day_vote';s.day_voted=set(s.alive);s.votes={1:5} if winning_vote else {}
    e.advance_one_event(gid,'end-day-eight')
    assert s.status==('finished' if winning_vote else 'incomplete')
    assert e.store.read(gid)[-1]['type']==('game_over' if winning_vote else 'game_limit')


@pytest.mark.parametrize('target',[True,False,1.0,'1'])
def test_non_integer_targets_are_rejected(tmp_path,target):
    e,gid=game(tmp_path)
    assert check_action(e.games[gid],3,{'action':'inspect','target':target})=='target must be an integer'
