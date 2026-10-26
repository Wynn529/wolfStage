"""C控制验收：参考HTTP仅验证C，不代表B已实现增强契约。"""
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.ui_client import GameClient, ServiceError
from app.ui_contract import ServiceContract
from app.ui_controller import SessionController
from app.ui_metrics import summarize_calls
from tests.reference_control_service import reference_contract, serve_reference


def until(controller,predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        controller.tick()
        if predicate():return
        time.sleep(.015)
    raise AssertionError((controller.status,controller.error,controller.message))


def controller_for(url,tmp_path,**kwargs):
    client=GameClient(url,contract=reference_contract(),**kwargs)
    gid=client.create(42)['game_id']
    controller=SessionController(client,gid,journal=tmp_path/'operations.jsonl',interval=0)
    controller.refresh()
    assert controller.auto_ready
    return controller


def test_c_auto_serializes_zero_multiple_events_and_stops_at_finish(tmp_path):
    with serve_reference() as (service,url):
        c=controller_for(url,tmp_path)
        try:
            c.start_auto()
            until(c,lambda:c.status=='finished' and not c.busy)
            assert not c.auto
            assert len(service.executions)==4
            assert len(set(service.executions))==4
            assert [e['event_id'] for e in c.events]==list(range(1,c.cursor+1))
            assert any(e['type']=='utterance_classification' for e in c.events)
            assert [x[3] for x in service.submissions if x[1]=='advance']==[1,1,3,4]
            before=len(service.executions)
            for _ in range(4):c.tick()
            assert len(service.executions)==before
        finally:c.close()


def test_c_pause_during_call_waits_for_boundary_and_resume_same_game(tmp_path):
    with serve_reference(delay=.25) as (service,url):
        c=controller_for(url,tmp_path)
        try:
            c.start_auto();c.tick()
            until(c,lambda:c.snapshot['in_flight'])
            oid=c.submit('pause')
            assert not c.auto
            assert '本步结束后暂停' in c.message
            assert c.submit('pause')==oid
            until(c,lambda:c.status=='paused' and not c.busy)
            assert len(service.executions)==1
            assert len([x for x in service.submissions if x[1]=='pause'])==1
            gid=c.game_id
            c.submit('resume')
            until(c,lambda:c.status=='running' and not c.busy)
            assert c.game_id==gid
            assert not c.auto  # 继续只解除暂停，不暗中开启自动。
        finally:c.close()


def test_c_timeout_queries_same_id_without_reposting_and_restore(tmp_path):
    import threading
    gate=threading.Event()
    with serve_reference(delay=.12,response_delay=.25,advance_gate=gate) as (service,url):
        c=controller_for(url,tmp_path,timeout=.04)
        try:
            oid=c.submit('advance')
            assert c.submit('advance',operation_id=oid)==oid
            until(c,lambda:bool(c.unresolved) and c.unresolved[0].state=='uncertain')
            restored=SessionController.restore(c.client,c.journal)
            gate.set()
            try:
                until(restored,lambda:not restored.busy and restored.status=='running')
                assert len(service.submissions)==1
                assert service.executions==[oid]
                assert not restored.auto
                assert restored.operations[oid].state=='succeeded'
            finally:restored.close()
        finally:
            gate.set()
            c.close()


@pytest.mark.parametrize('status',['finished','failed','incomplete','degraded'])
def test_c_stops_on_every_terminal_status(tmp_path,status):
    with serve_reference(plan=[{'events':[],'status':status}]) as (service,url):
        c=controller_for(url,tmp_path)
        try:
            c.start_auto()
            until(c,lambda:c.status==status and not c.busy)
            assert not c.auto
            assert len(service.executions)==1
        finally:c.close()


def test_c_stops_after_repeated_no_progress(tmp_path):
    with serve_reference(plan=[{'events':[]}]) as (service,url):
        c=controller_for(url,tmp_path)
        c.no_progress_limit=3
        try:
            c.start_auto()
            until(c,lambda:c.fatal)
            assert not c.auto
            assert '连续无进展' in c.error
            assert len(service.executions)==3
        finally:c.close()


def test_legacy_timeout_remains_locked_after_read_refresh(tmp_path):
    class LegacyClient:
        contract=ServiceContract()
        base_url='http://legacy-test'
        def control(self,*args,**kwargs):raise ServiceError('timeout')
        def events(self,gid,after=0):
            return [{'game_id':gid,'event_id':1,'type':'game_created','roles':{},'alive':[]} ] if after==0 else []
        def metrics(self,gid):return {'game_id':gid,'call_count':0}
    c=SessionController(LegacyClient(),'g',journal=tmp_path/'legacy.jsonl')
    try:
        c.refresh()
        c.submit('advance')
        until(c,lambda:c.fatal)
        c.refresh()
        assert c.fatal and c.busy and not c.auto_ready
        with pytest.raises(ValueError):c.submit('advance')
        with pytest.raises(ValueError):c.start_auto()
    finally:c.close()


@pytest.mark.parametrize('patch',[{'game_id':'other'},{'status':'alien'},{'in_flight':'false'},
                                  {'last_event_id':True},{'status':'paused','in_flight':True}])
def test_invalid_status_contract_never_enables_auto(tmp_path,patch):
    with serve_reference(corrupt_state=patch) as (service,url):
        client=GameClient(url,contract=reference_contract())
        gid=client.create(42)['game_id']
        c=SessionController(client,gid)
        try:
            c.refresh()
            assert not c.auto_ready
            with pytest.raises(ValueError):c.start_auto()
            assert not service.submissions
        finally:c.close()


def test_controller_stops_on_gap_and_conflicting_events(tmp_path):
    with serve_reference() as (service,url):
        c=controller_for(url,tmp_path)
        try:
            s=service.games[c.game_id]
            s['events'].append({'game_id':c.game_id,'event_id':3,'type':'vote'})
            s['last_event_id']=3
            c.refresh()
            assert c.fatal and '不连续' in c.error
            assert not service.submissions
        finally:c.close()


def test_metrics_unknown_usage_not_zero_and_raw_counts_are_separate():
    calls=[{'caller_type':'player','model_name':'deepseek','attempt':1,'success':False,'input_tokens':None},
           {'caller_type':'player','model_name':'deepseek','attempt':2,'success':True,'input_tokens':12,'output_tokens':4},
           {'caller_type':'small_model','attempt':1,'success':True},
           {'caller_type':'host','model_name':'deepseek','attempt':1,'success':True,'fallback':True,'input_tokens':None}]
    before=json.dumps(calls)
    check=summarize_calls(calls,{'call_count':99,'input_tokens':0})
    assert check['totals']=={'call_count':4,'successful_calls':3,'failed_calls':1,'retries':1}
    assert check['usage']['input_tokens']['missing']==2
    assert check['usage']['input_tokens']['display']=='已知部分 12'
    assert check['usage']['output_tokens']['not_applicable']==1
    assert check['rows'][0]['retries']==1
    assert next(row for row in check['rows'] if row['caller_type']=='host')['fallback']==1
    assert check['differences']
    assert json.dumps(calls)==before


def test_unconfirmed_contract_cannot_be_loaded(tmp_path):
    path=tmp_path/'contract.json'
    path.write_text(json.dumps({'version':'not-approved','capabilities':{}}),encoding='utf-8')
    with pytest.raises(ValueError):ServiceContract.load(path)


def test_state_field_mapping_and_control_operation_fields():
    contract=replace(reference_contract(),fields={'operation_id':'request_id','expected_event_id':'cursor'},
                     state_fields={'last_event_id':'cursor','in_flight':'busy'},idempotency_header='X-Operation-ID')
    client=GameClient('http://reference.test',contract=contract)
    received=[]
    def request(method,path,payload=None,headers=None):
        received.append((method,path,payload,headers))
        return {'game_id':'g','status':'running'}
    client._request=request
    client.control('g','advance',operation_id='fixed',expected_event_id=7)
    assert received[0][2]=={'request_id':'fixed','cursor':7}
    assert received[0][3]=={'X-Operation-ID':'fixed'}
    state=contract.normalize_state({'game_id':'g','status':'running','phase':'night','cursor':7,
                                    'busy':False,'pause_requested':False},'g')
    assert state['last_event_id']==7 and state['in_flight'] is False


def test_http_failure_is_not_retried_and_operation_unknown_stays_blocked(tmp_path):
    with serve_reference() as (service,url):
        c=controller_for(url,tmp_path)
        original=c.client.control
        calls=[]
        def lost_before_accept(*args,**kwargs):
            calls.append(kwargs['operation_id'])
            raise ServiceError('simulated transport failure')
        c.client.control=lost_before_accept
        try:
            oid=c.submit('advance')
            until(c,lambda:bool(c.error))
            for _ in range(3):c.tick()
            assert calls==[oid] and c.busy and not c.auto
            assert not service.executions and not service.submissions
        finally:c.close()


def test_incremental_conflict_preserves_previously_loaded_events(tmp_path):
    from app.ui_helpers import merge_events
    event={'game_id':'g','event_id':1,'type':'game_created'}
    original=[event]
    with pytest.raises(ValueError):merge_events(original,[dict(event,type='changed')],'g')
    assert original==[event]


def test_pending_snapshot_keeps_polling_after_operation_completed():
    client=GameClient('http://reference.test',contract=reference_contract())
    c=SessionController(client,'g')
    try:
        c.status='pending'
        c.snapshot={'in_flight':True,'pause_requested':False}
        assert not c.busy and c.needs_poll
        c.status='paused'
        c.snapshot={'in_flight':False,'pause_requested':False}
        assert not c.needs_poll
    finally:c.close()


def test_stats_unavailable_and_all_local_usage_not_applicable():
    assert summarize_calls(None,{'input_tokens':0})['usage']=={}
    value=summarize_calls([{'caller_type':'player','model_name':'stub','success':True,'attempt':1}],{})
    assert value['usage']['input_tokens']['not_applicable']==1
    assert '不适用' in value['usage']['input_tokens']['display']


def test_streamlit_reference_auto_pause_and_finish(tmp_path,monkeypatch):
    from dataclasses import asdict
    from streamlit.testing.v1 import AppTest
    contract_path=tmp_path/'test-only-contract.json'
    contract_path.write_text(json.dumps(asdict(reference_contract())),encoding='utf-8')
    monkeypatch.setenv('C_SERVICE_CONTRACT',str(contract_path))
    monkeypatch.setenv('C_JOURNAL_DIR',str(tmp_path/'journals'))
    with serve_reference(delay=.5) as (service,url):
        monkeypatch.setenv('B_API_URL',url)
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app/ui.py')).run(timeout=15)
        def click(label):
            next(b for b in app.button if b.label==label).click().run(timeout=15)
            assert not app.exception
        click('开始对局')
        click('自动运行')
        c=app.session_state['controller']
        assert c.busy
        click('暂停')
        deadline=time.monotonic()+5
        while (c.busy or c.status!='paused') and time.monotonic()<deadline:
            time.sleep(.03);app.run(timeout=15)
        assert not app.exception and c.status=='paused' and not c.auto
        assert len(service.executions)==1
        click('继续')
        while c.busy and time.monotonic()<deadline:
            time.sleep(.03);app.run(timeout=15)
        click('自动运行')
        deadline=time.monotonic()+8
        while (c.status!='finished' or c.busy) and time.monotonic()<deadline:
            time.sleep(.03);app.run(timeout=15)
        assert not app.exception and c.status=='finished' and not c.auto
        assert len(service.executions)==4
        c.close()


def test_streamlit_legacy_ambiguous_timeout_disables_controls(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv('C_SERVICE_CONTRACT',raising=False)
    monkeypatch.setenv('C_JOURNAL_DIR',str(tmp_path/'journals'))
    calls=[]
    def create(self,seed):return {'game_id':'g','status':'running','phase':'night'}
    def events(self,gid,after=0):
        return [{'game_id':'g','event_id':1,'type':'game_created','roles':{},'alive':[]}] if not after else []
    def control(self,gid,action,**kwargs):
        calls.append(action)
        raise ServiceError('simulated timeout')
    monkeypatch.setattr(GameClient,'create',create)
    monkeypatch.setattr(GameClient,'events',events)
    monkeypatch.setattr(GameClient,'metrics',lambda self,gid:{'game_id':gid,'call_count':0})
    monkeypatch.setattr(GameClient,'control',control)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app/ui.py')).run(timeout=15)
    next(b for b in app.button if b.label=='开始对局').click().run(timeout=15)
    next(b for b in app.button if b.label=='单步推进').click().run(timeout=15)
    c=app.session_state['controller']
    deadline=time.monotonic()+3
    while not c.fatal and time.monotonic()<deadline:
        time.sleep(.02);app.run(timeout=15)
    assert not app.exception and c.fatal
    assert next(b for b in app.button if b.label=='单步推进').disabled
    assert next(b for b in app.button if b.label=='自动运行').disabled
    next(b for b in app.button if b.label=='刷新事件').click().run(timeout=15)
    assert c.fatal and calls==['advance']
    c.close()
