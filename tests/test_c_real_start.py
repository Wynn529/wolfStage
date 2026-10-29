"""C真实启动验收：只使用无效测试占位配置，禁止请求外部模型。"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def runtime():
    spec=importlib.util.spec_from_file_location('c_live_runtime_test',ROOT/'scripts/c_live_runtime.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_real_preflight_missing_key_never_uses_network(runtime,monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    def forbidden(*args,**kwargs):raise AssertionError('preflight must be local only')
    monkeypatch.setattr(runtime,'urlopen',forbidden)
    monkeypatch.setattr(runtime.socket,'socket',forbidden)
    value=runtime.check_config('deepseek-chat')
    assert not value['ready'] and not value['key_configured']
    assert value['external_api_requests']==0


def test_real_child_key_only_reaches_b_and_paths_are_separate(runtime,monkeypatch):
    placeholder='TEST_ONLY_NOT_A_VALID_API_KEY'
    monkeypatch.setenv('DEEPSEEK_API_KEY',placeholder)
    monkeypatch.setenv('C_SERVICE_CONTRACT','unrelated-old-contract')
    monkeypatch.setenv('C_REAL_ALLOW_AUTO','1')
    envs=runtime.child_environments('deepseek-chat',18782,18082)
    assert envs['b']['DEEPSEEK_API_KEY']==placeholder
    assert all('DEEPSEEK_API_KEY' not in envs[k] for k in ['a','ui'])
    assert all('C_SERVICE_CONTRACT' not in env for env in envs.values())
    assert all('C_REAL_ALLOW_AUTO' not in env for env in envs.values())
    assert envs['ui']['C_LIVE_MODE']=='1'
    assert envs['ui']['C_RUNS_DIR']==str(ROOT/'runs/real_api')
    assert envs['b']['C_REAL_RUNS_DIR']==envs['ui']['C_RUNS_DIR']
    assert os.environ['DEEPSEEK_API_KEY']==placeholder
    assert os.environ['C_SERVICE_CONTRACT']=='unrelated-old-contract'
    assert 'TEST_ONLY' not in json.dumps(runtime.check_config('deepseek-chat'))


def test_real_ready_route_constructs_deepseek_but_makes_no_model_call(tmp_path,monkeypatch):
    from app import api
    from app.agents import DeepSeekAgent
    monkeypatch.setattr(api,'engine',api.engine)  # 注册恢复点，装配模块覆盖后仍恢复原B引擎。
    monkeypatch.setenv('DEEPSEEK_API_KEY','TEST_ONLY_NOT_A_VALID_API_KEY')
    monkeypatch.setenv('DEEPSEEK_MODEL','deepseek-flash')
    monkeypatch.setenv('A_CLASSIFIER_URL','http://127.0.0.1:1/classify')
    monkeypatch.setenv('C_REAL_RUNS_DIR',str(tmp_path/'real_runs'))
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append(True)
        raise AssertionError('startup must not invoke DeepSeek')
    monkeypatch.setattr(DeepSeekAgent,'act',forbidden)
    import urllib.request
    monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    name='app.c_live_service'
    monkeypatch.delitem(sys.modules,name,raising=False)
    service=__import__(name,fromlist=['app'])
    import asyncio
    messages=[]
    async def receive():return {'type':'http.request','body':b'','more_body':False}
    async def send(message):messages.append(message)
    scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1',
           'method':'GET','scheme':'http','path':'/c/ready','raw_path':b'/c/ready',
           'root_path':'','query_string':b'','headers':[], 'server':('test',80),'client':('test',1)}
    asyncio.run(service.app(scope,receive,send))
    assert next(m['status'] for m in messages if m['type']=='http.response.start')==200
    body=b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
    value=json.loads(body)
    assert value['agent']=='DeepSeekAgent' and value['classifier_configured'] is True
    assert b'TEST_ONLY' not in body and not api.engine.games
    assert not calls
    assert not list((tmp_path/'real_runs').glob('*/events.jsonl'))
    sys.modules.pop(name,None)


def test_real_stop_never_kills_a_reused_pid(runtime,monkeypatch):
    monkeypatch.setattr(runtime,'process_stamp',lambda pid:'new-process-stamp')
    def forbidden(*args,**kwargs):raise AssertionError('must not stop another process')
    monkeypatch.setattr(runtime,'terminate_tree',forbidden)
    assert runtime.stop_records([{'pid':123,'stamp':'old-process-stamp','name':'b'}])==[]


def test_real_stop_only_targets_matching_record(runtime,monkeypatch):
    monkeypatch.setattr(runtime,'process_stamp',lambda pid:'same' if pid==123 else None)
    stopped=[]
    monkeypatch.setattr(runtime,'terminate_tree',lambda item:stopped.append(item['pid']))
    assert runtime.stop_records([{'pid':123,'stamp':'same','name':'b'}])==['b']
    assert stopped==[123]


def test_real_check_only_cli_prints_status_not_credentials(runtime,monkeypatch,capsys):
    monkeypatch.setenv('DEEPSEEK_API_KEY','TEST_ONLY_NOT_A_VALID_API_KEY')
    monkeypatch.setattr(sys,'argv',['c_live_runtime.py','--check-only'])
    monkeypatch.setattr(runtime,'serve',lambda args:pytest.fail('check-only started services'))
    assert runtime.main()==0
    output=capsys.readouterr().out
    assert 'TEST_ONLY' not in output and json.loads(output)['key_configured']


def test_real_stop_without_record_needs_no_key(runtime,monkeypatch,tmp_path):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.setattr(runtime,'RECORD',tmp_path/'no-record.json')
    monkeypatch.setattr(sys,'argv',['c_live_runtime.py','--stop'])
    assert runtime.main()==0


def test_real_mode_ui_disables_auto_and_initializes_without_model_call(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from app.ui_client import GameClient
    from app.agents import StubAgent
    from app.engine import GameEngine
    from app.metrics import summarize
    e=GameEngine(str(tmp_path/'runs'),agent=StubAgent())
    monkeypatch.setenv('C_LIVE_MODE','1')
    monkeypatch.setenv('C_B_REVISION','3898b95')
    monkeypatch.delenv('C_SERVICE_CONTRACT',raising=False)
    monkeypatch.setenv('C_RUNS_DIR',str(tmp_path/'runs'))
    monkeypatch.setenv('C_JOURNAL_DIR',str(tmp_path/'journal'))
    mutations=[]
    def request(self,method,path,payload=None,headers=None):
        if method=='POST':
            mutations.append(path)
            assert path=='/games', 'preparation UI must not advance automatically'
            return e.create_game(payload['seed'])
        gid=path.split('/')[2]
        if '/events?' in path:return e.get_events(gid,view='god')
        if path.endswith('/status'):return e.get_status(gid)
        if path.endswith('/metrics'):return summarize(e.store,gid)
        raise AssertionError(path)
    monkeypatch.setattr(GameClient,'_request',request)
    app=AppTest.from_file(str(ROOT/'app/ui.py')).run(timeout=20)
    assert not app.exception and not mutations
    assert any('真实 DeepSeek 模式' in item.value for item in app.info)
    next(b for b in app.button if b.label=='开始对局').click().run(timeout=20)
    app.run(timeout=20)
    assert not app.exception and mutations==['/games']
    assert next(b for b in app.button if b.label=='自动运行').disabled
    assert not next(b for b in app.button if b.label=='单步推进').disabled
    assert app.session_state['controller'].auto_ready  # 禁用来自真实模式策略，而非接口故障。
    assert not e.store.read_calls(app.session_state['controller'].game_id)
