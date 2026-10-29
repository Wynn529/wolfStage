"""C对B 3898b95的复验；xfail为可复现的B待修项，不修改B实现。"""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from app.engine import GameEngine
from app.agents import StubAgent, build_context
from app.rules import check_action
from app.ui_client import BRevisionClient, ServiceError
from app.ui_controller import SessionController
from app.ui_scene import scene_model

ROLES={1:'wolf',2:'wolf',3:'seer',4:'villager',5:'villager',6:'villager'}

def make(tmp_path, agent=None):
    e=GameEngine(str(tmp_path),agent=agent or StubAgent())
    gid=e.create_game(42,ROLES)['game_id']
    return e,gid


def test_b_duplicate_and_concurrent_advance_and_restart(tmp_path):
    e,gid=make(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        a,b=list(pool.map(lambda _:e.advance_one_event(gid,'same',1),range(2)))
    assert a==b
    assert len(e.store.read_calls(gid))==1
    before=e.games[gid].event_id
    assert e.advance_one_event(gid,'stale',0)['status']=='conflict'
    assert e.games[gid].event_id==before
    e2=GameEngine(str(tmp_path),agent=StubAgent())
    assert e2.get_operation(gid,'same')['result']==a
    assert e2.advance_one_event(gid,'same')==a
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda oid:e.advance_one_event(gid,oid),['next1','next2']))
    actors=[c['agent_id'] for c in e.store.read_calls(gid)]
    assert actors==[1,2,3]


def test_b_private_reasons_hidden_from_other_roles_and_scene_stays_speech(tmp_path):
    e,gid=make(tmp_path)
    for i in range(8):
        e.advance_one_event(gid,str(i))
        if any(v['type']=='public_speech' for v in e.store.read(gid)):break
    events=e.store.read(gid)
    speech=next(v for v in events if v['type']=='public_speech')
    other=next(i for i in range(1,7) if i!=speech['actor'])
    context=build_context(e.games[gid],events,other,e.games[gid].roles[other])
    assert all(v['type']!='agent_reason' or v['actor']==other for v in context['events'])
    assert 'reason' not in speech
    model=scene_model(events)
    assert model['current']['kind']=='public_speech'
    assert model['current']['body']==speech['text']
    assert model['current']['reason']=='stub decision'
    assert model['analysis']['event_id']==speech['event_id']


def test_b_client_queries_completed_unknown_and_rejects_stale_cursor(tmp_path,monkeypatch):
    e,gid=make(tmp_path)
    c=BRevisionClient('http://test')
    submitted=[]
    def transport(method,path,payload=None,headers=None):
        if path.endswith('/advance'):
            submitted.append(payload)
            return e.advance_one_event(gid,payload['operation_id'],payload['expected_cursor'])
        if '/operations/' in path:return e.get_operation(gid,path.rsplit('/',1)[1])
        if path.endswith('/status'):return e.get_status(gid)
        if '/events?' in path:return e.get_events(gid,view='god')
        if path.endswith('/metrics'):
            from app.metrics import summarize
            return summarize(e.store,gid)
        raise AssertionError(path)
    monkeypatch.setattr(c,'_request',transport)
    assert c.operation(gid,'unknown')['state']=='pending'
    controller=SessionController(c,gid,journal=tmp_path/'journal.jsonl')
    try:
        controller.refresh();assert controller.synced and controller.auto_ready
        assert not c.contract.ready  # C顺序调度不冒充B完整增强契约。
        oid=controller.submit('advance')
        controller.futures[oid].result(timeout=2)
        controller.tick()
        assert controller.operations[oid].state=='succeeded' and not controller.busy
        assert submitted==[{'operation_id':oid,'expected_cursor':1}]
        with pytest.raises(ServiceError) as exc:c.control(gid,'advance',operation_id='stale',expected_event_id=0)
        assert not exc.value.ambiguous
    finally:controller.close()


def test_b_timeout_advance_recovers_without_repost(tmp_path,monkeypatch):
    e,gid=make(tmp_path);c=BRevisionClient('http://test');posts=[]
    def transport(method,path,payload=None,headers=None):
        if path.endswith('/advance'):
            posts.append(payload)
            e.advance_one_event(gid,payload['operation_id'],payload['expected_cursor'])
            raise ServiceError('simulated response lost')
        if '/operations/' in path:return e.get_operation(gid,path.rsplit('/',1)[1])
        if path.endswith('/status'):return e.get_status(gid)
        if '/events?' in path:return e.get_events(gid,view='god')
        if path.endswith('/metrics'):
            from app.metrics import summarize
            return summarize(e.store,gid)
        raise AssertionError(path)
    monkeypatch.setattr(c,'_request',transport)
    controller=SessionController(c,gid,journal=tmp_path/'journal.jsonl')
    try:
        controller.refresh();oid=controller.submit('advance')
        with pytest.raises(ServiceError):controller.futures[oid].result(timeout=2)
        controller.tick()
        assert not controller.fatal and not controller.busy
        assert controller.operations[oid].state=='succeeded' and len(posts)==1
    finally:controller.close()


def test_b_paused_means_operation_fully_committed(tmp_path,monkeypatch):
    entered=threading.Event();release=threading.Event()
    class Slow(StubAgent):
        def act(self,*args):
            entered.set();assert release.wait(3)
            return super().act(*args)
    e,gid=make(tmp_path,Slow());observed=[]
    original=e.store.append
    def append(game_id,event):
        if event['type']=='wolf_proposal':observed.append(e.get_status(gid)['status'])
        return original(game_id,event)
    monkeypatch.setattr(e.store,'append',append)
    with ThreadPoolExecutor(1) as pool:
        f=pool.submit(e.advance_one_event,gid,'op',1)
        assert entered.wait(3)
        try:assert e.pause_game(gid)['pause_requested'] is True
        finally:release.set()
        f.result(timeout=3)
    assert observed and observed[0]!='paused'


def test_b_new_events_complete_on_night_resolution(tmp_path):
    e,gid=make(tmp_path)
    for i in range(3):e.advance_one_event(gid,str(i))
    before=e.games[gid].event_id
    result=e.advance_one_event(gid,'resolve')
    assert result['new_events']==list(range(before+1,e.games[gid].event_id+1))


def test_b_rejects_wolf_teammate_kill(tmp_path):
    e,gid=make(tmp_path)
    assert check_action(e.games[gid],1,{'action':'kill','target':2,'text':'','reason':''}) is not None


def test_b_revote_limits_candidates(tmp_path):
    e,gid=make(tmp_path);s=e.games[gid];s.phase='day_vote';s.revote=True
    s.pending['revote_candidates']=[2,3]
    assert check_action(s,1,{'action':'vote','target':4,'text':'','reason':''}) is not None


def test_b_retry_success_log_is_accurate(tmp_path):
    class Retry(StubAgent):
        attempts=0
        def act(self,*args):
            self.attempts+=1
            if self.attempts==1:return {'action':'invalid'}
            return super().act(*args)
    e,gid=make(tmp_path,Retry());e.advance_one_event(gid,'retry')
    last=e.store.read_calls(gid)[-1]
    assert last['attempt']==2 and last['success'] is True and last['validation_error'] is None


def test_b_wolf_reason_links_to_its_proposal(tmp_path):
    e,gid=make(tmp_path);e.advance_one_event(gid,'wolf')
    events=e.store.read(gid);proposal=next(v for v in events if v['type']=='wolf_proposal')
    reason=next(v for v in events if v['type']=='agent_reason')
    assert reason['source_event_id']==proposal['event_id']


def test_b_waiting_and_unknown_operation_are_not_completed(tmp_path):
    entered=threading.Event();release=threading.Event()
    class Slow(StubAgent):
        def act(self,*args):
            entered.set();assert release.wait(3)
            return super().act(*args)
    e,gid=make(tmp_path,Slow())
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(e.advance_one_event,gid,'slow',1)
        assert entered.wait(3)
        try:
            assert e.get_status(gid)['status']=='waiting_model'
            assert e.get_operation(gid,'slow')['status']=='unknown'
        finally:release.set()
        future.result(timeout=3)
    assert e.get_operation(gid,'slow')['status']=='completed'
    assert e.get_operation(gid,'pause-id')['status']=='unknown'

def test_b_real_response_usage_is_preserved(tmp_path):
    from app.agents import DeepSeekAgent
    class FakeClient:
        model='fake-no-network'
        def complete(self,*args,**kwargs):
            return ({'action':'kill','target':4,'text':'','reason':'test'},
                    {'input_tokens':123,'output_tokens':45,'latency_ms':67})
    e,gid=make(tmp_path,DeepSeekAgent(client=FakeClient()))
    e.advance_one_event(gid,'usage')
    call=e.store.read_calls(gid)[0]
    assert (call['input_tokens'],call['output_tokens'],call['latency_ms'])==(123,45,67)


def test_b_classifier_failure_has_explicit_service_state(tmp_path):
    def broken(*args):raise RuntimeError('classifier unavailable')
    e,gid=make(tmp_path);e.classifier=broken
    for i in range(8):
        e.advance_one_event(gid,str(i))
        if any(v['type']=='utterance_classification_failed' for v in e.store.read(gid)):break
    assert any(v['type']=='utterance_classification_failed' for v in e.store.read(gid))
    assert e.get_status(gid)['status'] in {'failed','degraded'}


def test_b_wolf_disagreement_is_seeded_and_private(tmp_path):
    class Disagree(StubAgent):
        def act(self,state,actor,context):
            action=super().act(state,actor,context)
            if action['action']=='kill':action['target']=4 if actor==1 else 5
            return action
    e,gid=make(tmp_path,Disagree())
    for i in range(4):e.advance_one_event(gid,str(i))
    event=next(v for v in e.store.read(gid) if v['type']=='wolf_negotiation')
    assert event['resolved_target'] in {4,5} and event['resolution']=='seeded_rule'
    assert not any(v['type']=='wolf_negotiation' for v in e.get_events(gid,view='4'))
    assert any(v['type']=='wolf_negotiation' for v in e.get_events(gid,view='1'))


@pytest.fixture
def b_sequential_controller(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from app.metrics import summarize
    e,gid=make(tmp_path)
    client=BRevisionClient('http://test')
    posts=[]
    def request(method,path,payload=None,headers=None):
        route=urlsplit(path)
        action=route.path.rsplit('/',1)[-1]
        if method=='POST':
            posts.append(action)
            if action=='advance':return e.advance_one_event(gid,payload['operation_id'],payload['expected_cursor'])
            if action=='pause':return e.pause_game(gid)
            if action=='resume':return e.resume_game(gid)
        if action=='status':return e.get_status(gid)
        if action=='events':return e.get_events(gid,int(parse_qs(route.query)['after_event_id'][0]),'god')
        if action=='metrics':return summarize(e.store,gid)
        if '/operations/' in path:return e.get_operation(gid,action)
        raise AssertionError((method,path))
    monkeypatch.setattr(client,'_request',request)
    controller=SessionController(client,gid,journal=tmp_path/'control.jsonl',interval=.001)
    controller.refresh()
    try:yield e,gid,controller,posts
    finally:controller.close()


def test_c_sequential_auto_completes_actual_b_game(b_sequential_controller):
    from tests.test_ui_control import until
    e,gid,c,posts=b_sequential_controller
    c.start_auto()
    until(c,lambda:c.status=='finished' and not c.busy,timeout=8)
    assert not c.auto and not c.error
    assert len([a for a in posts if a=='advance'])==c.steps
    assert len(e.games[gid].operation_results)==c.steps
    assert [v['event_id'] for v in c.events]==list(range(1,c.cursor+1))


def test_c_queues_pause_until_whole_b_operation_finishes(b_sequential_controller):
    from tests.test_ui_control import until
    e,gid,c,posts=b_sequential_controller
    entered=threading.Event();release=threading.Event()
    class Slow(StubAgent):
        def act(self,*args):
            entered.set();assert release.wait(4)
            return super().act(*args)
    e.agent=Slow()
    c.start_auto();c.tick()
    assert entered.wait(3)
    try:
        c.tick()
        assert c.busy and '等待' in c.message
        c.submit('pause');c.submit('pause')
        assert c.deferred_pause and not c.auto
        assert posts==['advance']  # 当前步骤未完成，绝不能调用B的在途pause。
        assert '本步结束后暂停' in c.message
        release.set()
        until(c,lambda:c.status=='paused' and not c.busy)
        assert posts==['advance','pause']
        assert len(e.store.read_calls(gid))==1
        assert e.get_operation(gid,next(iter(c.operations)))['status']=='completed'
        assert not c.deferred_pause and not c.pause_intent
        c.submit('resume')
        until(c,lambda:c.status=='running' and not c.busy)
        assert not c.auto and '准备就绪' in c.message
    finally:release.set()


def test_c_pause_immediately_after_auto_start_prevents_first_advance(b_sequential_controller):
    from tests.test_ui_control import until
    e,gid,c,posts=b_sequential_controller
    c.start_auto();c.submit('pause')
    until(c,lambda:c.status=='paused' and not c.busy)
    assert posts==['pause'] and not c.auto and c.steps==0
