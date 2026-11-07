import json
import socket
import threading
import time
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import HTTPError
import pytest
import uvicorn
from app.engine import GameEngine
from app.agents import StubAgent
from app.ui_helpers import local_games, read_events, evidence_zip
from app.ui_client import BRevisionClient
from app.ui_controller import SessionController

ROLES={1:'wolf',2:'wolf',3:'wolf',4:'seer',5:'witch',6:'hunter',7:'villager',8:'villager',9:'villager',10:'villager'}

@pytest.fixture
def setup_game(tmp_path):
    engine=GameEngine(str(tmp_path),agent=StubAgent())
    gid=engine.create_game(42,ROLES,seat_controllers={5:'human'})['game_id']
    state=engine.games[gid]
    engine._event(state,'agent_reason',[2,'host','god'],actor=2,reason='SECRET_WOLF_REASON')
    engine._event(state,'inspection',[4,'host','god'],actor=4,target=1,result='SECRET_SEER_CHECK')
    engine._event(state,'witch_save',[5,'host','god'],actor=5,target=7)
    engine._event(state,'public_speech','public',actor=7,text='公开发言')
    state.pending['night_kill_target']=7
    engine._action_for(state,5)
    engine.store.append_call(gid,{'game_id':gid,'caller_type':'player','agent_id':2,'success':False,'attempt':1,'validation_error':'SECRET_CALL_ERROR'})
    engine.store.write_state(gid,state.to_dict())
    return engine,state,tmp_path

@pytest.fixture
def http_game(setup_game,monkeypatch):
    import app.api as api
    engine,state,path=setup_game;monkeypatch.setattr(api,'engine',engine)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(api.app,log_level='error',lifespan='off'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started and time.monotonic()<deadline:time.sleep(.02)
    assert server.started
    opener=build_opener(ProxyHandler({}))
    def call(endpoint,body=None):
        data=None if body is None else json.dumps(body).encode()
        request=Request(f'http://127.0.0.1:{port}'+endpoint,data=data,headers={'Content-Type':'application/json'})
        try:
            with opener.open(request,timeout=5) as response:return response.status,json.load(response)
        except HTTPError as exc:return exc.code,json.load(exc)
    try:yield engine,state,path,call,f'http://127.0.0.1:{port}'
    finally:server.should_exit=True;thread.join(10);sock.close()

@pytest.mark.parametrize('query',['view=god','view=host','view=public','view=999','view=2','view=4','view=god&player_id=2','view=5&player_id=4'])
def test_view_parameter_cannot_grant_private_access(http_game,query):
    _,state,_,call,_=http_game;status,body=call(f'/games/{state.game_id}/events?'+query)
    assert status in {200,403}
    rendered=json.dumps(body,ensure_ascii=False)
    assert 'SECRET_' not in rendered and 'roles' not in rendered
    if status==200:assert all(e['visibility']=='public' for e in body)


def test_own_private_information_is_available_and_no_other_secret(http_game):
    _,state,_,call,_=http_game;status,body=call(f'/games/{state.game_id}/events?view=5&player_id=5')
    assert status==200 and any(e['type']=='witch_save' for e in body)
    assert 'SECRET_' not in json.dumps(body)
    status,identity=call(f'/games/{state.game_id}/players/5/identity')
    assert status==200 and identity['role']=='witch' and 'roles' not in identity

@pytest.mark.parametrize('seat',[1,2,4,6,10,999])
def test_other_identity_is_rejected(http_game,seat):
    _,state,_,call,_=http_game;status,body=call(f'/games/{state.game_id}/players/{seat}/identity')
    assert status==403 and 'roles' not in json.dumps(body)

@pytest.mark.parametrize('status',['running','waiting_human','paused','failed'])
def test_unfinished_human_game_cannot_replay_or_download(setup_game,status):
    e,s,path=setup_game;s.status=status;s.alive.discard(5)
    assert s.game_id not in local_games(path)
    for fn in [read_events,evidence_zip]:
        with pytest.raises(ValueError):fn(path,s.game_id)
    with pytest.raises(PermissionError):e.replay_game(s.game_id)
    assert 'SECRET_' not in json.dumps(e.get_metrics(s.game_id))
    assert 'SECRET_' not in json.dumps(e.get_events(s.game_id,view='god'))

@pytest.mark.parametrize('endpoint',['replay','metrics','status','pending-action','operations/old'])
def test_api_does_not_leak_through_support_endpoints(http_game,endpoint):
    e,s,_,call,_=http_game
    s.operation_results['old']={'game_id':s.game_id,'status':'running','summary':{'private':'SECRET_CACHED_SUMMARY'},'cursor':s.event_id,'roles':s.roles}
    status,body=call(f'/games/{s.game_id}/{endpoint}')
    assert status==(403 if endpoint=='replay' else 200)
    assert 'SECRET_' not in json.dumps(body) and 'roles' not in json.dumps(body)

@pytest.mark.parametrize('status',['finished','incomplete'])
def test_only_ended_human_game_releases_originals(setup_game,status):
    e,s,path=setup_game;s.status=status;e._event(s,'game_over' if status=='finished' else 'game_limit',winner='villagers' if status=='finished' else None)
    before={p.name:p.read_bytes() for p in (path/s.game_id).iterdir() if p.is_file()}
    assert s.game_id in local_games(path)
    assert read_events(path,s.game_id)[0]['roles']
    assert evidence_zip(path,s.game_id) and e.replay_game(s.game_id)[0]['roles']
    assert before=={p.name:p.read_bytes() for p in (path/s.game_id).iterdir() if p.is_file()}


def test_all_ai_observer_is_unchanged(tmp_path):
    e=GameEngine(str(tmp_path),agent=StubAgent());gid=e.create_game(42,ROLES)['game_id']
    assert e.get_events(gid,view='god')[0]['roles'] and e.replay_game(gid)[0]['roles']
    assert gid in local_games(tmp_path) and evidence_zip(tmp_path,gid)


def test_restore_uses_server_human_seat_and_discards_secret_cache(http_game):
    e,s,_,_,url=http_game;c=SessionController(BRevisionClient(url),s.game_id)
    c.events=e.store.read(s.game_id);c.metrics={'private':'SECRET_CACHED_METRICS'}
    try:
        c.refresh()
        assert c.human_game and c.human_player_id==5 and not c.fatal
        assert 'SECRET_' not in json.dumps(c.events) and 'SECRET_' not in json.dumps(c.metrics)
        assert all(x['visibility']=='public' or 5 in x['visibility'] for x in c.events)
    finally:c.close()


def test_second_human_seat_is_rejected(http_game):
    _,_,_,call,_=http_game
    status,_=call('/games',{'seed':42,'seat_controllers':{'1':'human','2':'human'}})
    assert status==422


def test_direct_history_url_never_reads_raw_human_events(setup_game,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    import app.ui_helpers as helpers
    e,s,path=setup_game
    monkeypatch.setenv('C_RUNS_DIR',str(path));monkeypatch.setenv('C_JOURNAL_DIR',str(path/'journals'))
    monkeypatch.delenv('C_SERVICE_CONTRACT',raising=False)
    def forbid(*args,**kwargs):raise AssertionError('raw human stream must not reach renderer')
    monkeypatch.setattr(helpers,'read_events',forbid)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app/ui.py'))
    app.query_params['view']='history';app.query_params['game']=s.game_id;app.run(timeout=15)
    assert not app.exception and app.session_state['events']==[]
    assert any('不能打开上帝回放' in x.value for x in app.warning)
    assert not app.get('download_button')
    assert 'SECRET_' not in ' '.join(x.value for x in app.markdown)


def test_duplicate_control_cannot_return_old_unsanitized_cache(http_game):
    _,state,_,call,_=http_game
    state.operation_results['old-control']={'game_id':state.game_id,'operation_id':'old-control','status':'running','cursor':state.event_id,'summary':{'error':'SECRET_OLD_RESPONSE'},'roles':state.roles}
    status,body=call(f'/games/{state.game_id}/advance',{'operation_id':'old-control','expected_cursor':state.event_id})
    assert status==200 and 'SECRET_' not in json.dumps(body) and 'roles' not in body
    assert state.operation_results['old-control']['summary']['error']=='SECRET_OLD_RESPONSE'


def test_human_identity_ack_keeps_same_game_without_advancing(http_game, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    engine, _, path, _, url = http_game
    monkeypatch.setenv('B_API_URL', url)
    monkeypatch.setenv('C_B_REVISION', '3898b95')
    monkeypatch.setenv('C_RUNS_DIR', str(path))
    monkeypatch.setenv('C_JOURNAL_DIR', str(path/'journals'))
    monkeypatch.delenv('C_SERVICE_CONTRACT', raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app/ui.py')).run(timeout=15)
    next(x for x in app.radio if x.label == '对局模式').set_value('真人参与').run(timeout=15)
    before = len(engine.games)
    next(x for x in app.button if x.label == '开始对局').click().run(timeout=15)
    assert not app.exception
    gid = app.session_state['controller'].game_id
    cursor = engine.games[gid].event_id
    assert len(engine.games) == before+1
    next(x for x in app.button if x.label == '确认进入对局').click().run(timeout=15)
    for _ in range(2):
        app.run(timeout=15)
        assert not app.exception
        assert app.session_state['controller'].game_id == gid
        assert app.session_state[f'identity_ack_{gid}'] is True
        assert not any(x.label == '确认进入对局' for x in app.button)
        assert len(engine.games) == before+1
        assert engine.games[gid].event_id == cursor
    app.session_state['controller'].close()
