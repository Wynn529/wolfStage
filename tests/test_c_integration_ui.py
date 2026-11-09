import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from streamlit.testing.v1 import AppTest
from app.c_integration_preview import create_preview_app
from app.ui_client import ServiceError
from app.ui_integration_client import IntegrationClient
from app.ui_controller import SessionController

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def integration_http(tmp_path, monkeypatch):
    from app.provider_client import ProviderClient
    def forbidden(*a, **kw):
        raise AssertionError('No external provider request is allowed')
    monkeypatch.setattr(ProviderClient, 'complete', forbidden)
    app = create_preview_app(tmp_path/'games')
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True); thread.start()
    deadline = time.monotonic()+10
    while not server.started and time.monotonic()<deadline: time.sleep(.02)
    assert server.started
    url = f'http://127.0.0.1:{port}'
    monkeypatch.setenv('C_SERVICE_MODE', 'integration')
    monkeypatch.setenv('C_LIVE_MODE', '0')
    monkeypatch.setenv('C_OFFLINE_PREVIEW', '1')
    monkeypatch.setenv('B_API_URL', url)
    monkeypatch.setenv('C_RUNS_DIR', str(tmp_path/'legacy'))
    monkeypatch.setenv('C_JOURNAL_DIR', str(tmp_path/'journals'))
    monkeypatch.delenv('C_SERVICE_CONTRACT', raising=False)
    try: yield app, url, tmp_path
    finally: server.should_exit=True;thread.join(10);sock.close()


def button(app, name): return next(x for x in app.button if x.label == name)
def radio(app, name): return next(x for x in app.radio if x.label == name)
def select(app, name): return next(x for x in app.selectbox if x.label == name)


@pytest.mark.parametrize('role', ['wolf','seer','witch','hunter','villager'])
def test_registered_nine_models_and_human_role_dialog(integration_http, role):
    service, _, path = integration_http
    app = AppTest.from_file(str(ROOT/'app/ui.py')).run(timeout=20)
    assert not app.exception
    radio(app, '对局模式').set_value('真人参与').run(timeout=20)
    select(app, '真人身份（新局生效）').select(role).run(timeout=20)
    radio(app, '模型分配').set_value('按登记列表轮流分配').run(timeout=20)
    button(app, '开始对局').click().run(timeout=20)
    assert not app.exception
    controller = app.session_state['controller'];gid=controller.game_id
    state = service.state.engine.games[gid]
    assert state.roles[1] == role and len(state.seat_model_configs) == 9
    assert len({p['profile_id'] for p in state.seat_model_configs.values()}) == 9
    from app.ui_helpers import ROLES
    assert any(f'你的身份是：{ROLES[role]}' in x.value for x in app.markdown)
    token = controller.client._sessions[gid]
    button(app, '确认进入对局').click().run(timeout=20)
    assert not app.exception and controller.game_id == gid
    assert token not in ''.join(p.read_text(encoding='utf-8') for p in (path/'journals').glob('*'))
    assert token not in str(app.markdown) and token not in str(app.json)
    controller.close()


@pytest.mark.parametrize(('role', 'target', 'save_available', 'message'), [
    ('witch', 5, True, '今晚刀口：5 号'),
    ('witch', None, True, '今晚没有刀口。'),
    ('witch', 5, False, None),
    ('seer', 5, True, None),
])
def test_night_target_is_shown_only_to_authorized_witch(
        integration_http, role, target, save_available, message):
    service, url, _ = integration_http
    app = AppTest.from_file(str(ROOT/'app/ui.py')).run(timeout=20)
    radio(app, '对局模式').set_value('真人参与').run(timeout=20)
    select(app, '真人身份（新局生效）').select(role).run(timeout=20)
    button(app, '开始对局').click().run(timeout=20)
    controller = app.session_state['controller']
    try:
        button(app, '确认进入对局').click().run(timeout=20)
        gid = controller.game_id
        engine = service.state.engine
        state = engine.games[gid]
        state.night_done.update(p for p in state.roles if state.roles[p] == 'wolf')
        if role == 'witch':
            state.night_done.add('seer')
        state.pending['night_kill_target'] = target
        state.witch_save_available = save_available
        assert 'night_kill_target' not in controller.client.identity(gid, 1)

        engine.advance_one_event(gid)
        controller.refresh()
        assert controller.status == 'waiting_human'
        app.run(timeout=20)
        assert not app.exception
        assert select(app, '行动')
        hints = [entry.value for entry in app.info if entry.value.startswith('今晚')]
        assert hints == ([message] if message else [])
        with pytest.raises(ServiceError):
            IntegrationClient(url).identity(gid, 1)
        with pytest.raises(ServiceError):
            controller.client.identity(gid, 2)
    finally:
        controller.close()


def create(client, human=True, role='seer'):
    profiles = client.models()
    return client.create(42, role_config={'10':role} if human else None,
        seat_controllers={str(i):'human' if human and i==10 else 'ai' for i in range(1,11)},
        seat_model_configs={str(i):{'model':profiles[(i-1)%len(profiles)]['profile_id']} for i in range(1,11)})


def test_auth_waiting_human_and_idempotent_submission(integration_http):
    app,url,path=integration_http; client=IntegrationClient(url);created=create(client)
    gid=created['game_id'];state=app.state.engine.games[gid]
    assert 'human_token' not in created
    unauth=IntegrationClient(url)
    with pytest.raises(ServiceError): unauth.events(gid)
    with pytest.raises(ServiceError): client.identity(gid, 1)
    with pytest.raises(ServiceError): client.replay(gid)
    state.phase='day_vote';state.day_voted=set(range(1,10))
    controller=SessionController(client,gid,journal=path/'control.jsonl',human_game=True,human_player_id=10,interval=0)
    try:
        controller.refresh();controller.start_auto();controller.tick()
        deadline=time.monotonic()+5
        while (controller.busy or controller.status!='waiting_human') and time.monotonic()<deadline:
            time.sleep(.02);controller.tick()
        assert controller.status=='waiting_human' and not controller.auto and not controller.busy
        pending=client.pending_action(gid);cursor=controller.cursor
        action={'action':'vote','target':1,'text':'','reason':'must not be sent'}
        args=dict(action_id=pending['action_id'],expected_cursor=cursor,operation_id='single-human')
        first=client.human_action(gid,10,action,**args)
        assert client.human_action(gid,10,action,**args)==first
        receipts = [e for e in client.events(gid) if e['type'] == 'vote_received']
        assert len(receipts) == 1 and 'actor' not in receipts[0] and 'target' not in receipts[0]
        assert app.state.engine.store.read_calls(gid)==[]
        controller.refresh();assert controller.status=='running'
        restored=SessionController.restore(client,path/'control.jsonl')
        try: assert restored.human_player_id==10 and not restored.error
        finally: restored.close()
    finally:controller.close()


def test_all_ai_pause_resume_and_server_replay(integration_http):
    app,url,_=integration_http;client=IntegrationClient(url);gid=create(client,False)['game_id']
    assert client.control(gid,'pause',operation_id='pause',expected_event_id=1)['status']=='paused'
    assert client.control(gid,'resume',operation_id='resume',expected_event_id=2)['status']=='running'
    assert client.replay(gid)[0]['type']=='game_created'
    assert len(client.models())==9


def test_timeout_retry_button_preserves_previous_ballots(integration_http, monkeypatch):
    service, _, _ = integration_http
    from app.seat_router import ModelAttemptError

    app = AppTest.from_file(str(ROOT/'app/ui.py')).run(timeout=20)
    button(app, '开始对局').click().run(timeout=20)
    controller = app.session_state['controller']
    gid = controller.game_id
    engine = service.state.engine
    state = engine.games[gid]
    state.phase, state.day = 'day_vote', 1
    state.day_voted, state.votes = {1}, {1: 3}
    calls = []

    def vote(state, actor, context):
        calls.append(actor)
        if len(calls) == 1:
            error = ModelAttemptError({'request_timeout': True})
            error.metadata = error.meta
            raise error
        return {'action': 'vote', 'target': 3}

    monkeypatch.setattr(engine.configured_agent, 'act', vote)
    from tests.test_ui_control import until
    button(app, '单步推进').click().run(timeout=20)
    until(controller, lambda: not controller.busy and controller.status == 'failed')
    assert state.votes == {1: 3}
    assert engine.advance_one_event(gid, 'ordinary-advance')['status'] == 'failed'
    app.run(timeout=20)
    assert not app.exception and controller.can_retry_model
    button(app, '重试当前席位').click().run(timeout=20)
    until(controller, lambda: not controller.busy and controller.status == 'running')
    assert not controller.fatal and state.votes == {1: 3, 2: 3} and calls == [2, 2]
    assert engine.get_status(gid)['can_retry_model'] is False
    operation = next(op for op in controller.operations.values() if op.action == 'retry-model')
    repeated = controller.client.control(gid, 'retry-model', operation_id=operation.operation_id,
                                         expected_event_id=operation.expected_event_id)
    assert repeated['status'] == 'running' and calls == [2, 2]
    assert [e['type'] for e in engine.store.read(gid)][-3:] == [
        'model_failure', 'model_retry_started', 'vote_received']
    controller.close()


def test_lost_human_session_discards_private_cache(integration_http):
    _,url,_=integration_http;client=IntegrationClient(url);gid=create(client)['game_id']
    lost=SessionController(IntegrationClient(url),gid)
    lost.events=[{'game_id':gid,'event_id':1,'type':'game_created','roles':{'1':'SECRET'}}]
    lost.metrics={'SECRET':'cached'}
    try:
        lost.refresh()
        assert lost.error and not lost.events and not lost.metrics
    finally:lost.close()


def test_scene_only_reveals_authenticated_own_role():
    from app.ui_scene import scene_model
    scene=scene_model([],human_player_id=10,own_role='wolf',seat_labels={1:'MiMo'})
    assert len(scene['players'])==10
    assert scene['players'][9]['role']=='wolf'
    assert all(p['role'] is None for p in scene['players'][:9])
    assert scene['players'][0]['model_label']=='MiMo'


def test_completed_operation_refreshes_control_cursor_before_unlock():
    from app.ui_controller import Operation
    from app.ui_client import BRevisionClient
    class RaceClient(BRevisionClient):
        current=2
        def game_access(self,gid): return {}
        def events(self,gid,after=0,**kwargs):
            return [dict(game_id=gid,event_id=i,type='public_speech',text='test',visibility='public') for i in range(after+1,self.current+1)]
        def service_status(self,gid):return dict(game_id=gid,status='running',phase='day_speak',cursor=self.current)
        def operation(self,gid,oid):
            self.current=3
            return dict(game_id=gid,operation_id=oid,state='succeeded',action='advance',result={})
        def metrics(self,gid):return dict(game_id=gid)
    c=SessionController(RaceClient('http://127.0.0.1:1'),'race')
    c.operations['step']=Operation('step','advance',1)
    try:
        c.refresh()
        assert c.synced and c.cursor==3 and [e['event_id'] for e in c.events]==[1,2,3]
    finally:c.close()
