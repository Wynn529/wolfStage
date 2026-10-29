from pathlib import Path
import json
from app.ui_presenter import failure_notice
from app.ui_scene import scene_model
from app.ui_metrics import display_call_rows


def test_protocol_failure_is_distinct_from_unknown_or_timeout():
    known={'type':'model_failure','error':"action 'check' is not allowed in phase night"}
    notice=failure_notice(known)
    assert notice['title']=='行动格式未通过规则检查' and '请求已返回' in notice['summary']
    unknown=failure_notice({'type':'model_failure','error':'unrecognized exception'})
    assert '已返回' not in unknown['summary'] and '不足以确认' in unknown['summary']
    timeout=failure_notice({'type':'model_failure','error':'timed out'})
    assert '待核对' in timeout['title'] and '不要重复' in timeout['next_step']
    classify=failure_notice({'type':'utterance_classification_failed','error':'test error'})
    assert classify['title']=='小模型分类未完成' and 'A/B' in classify['next_step']


def test_failed_replay_shows_no_future_error_and_makes_no_request(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from app.ui_client import GameClient
    root=Path(__file__).resolve().parents[1]
    source=root/'tests/fixtures/c_real_failure_events.jsonl'
    # Use a copy of the actual failure record; all network access is forbidden.
    original=source.read_bytes()
    folder=tmp_path/'runs'/'8eec5a101f12';folder.mkdir(parents=True)
    (folder/'events.jsonl').write_bytes(original)
    monkeypatch.setenv('C_RUNS_DIR',str(tmp_path/'runs'))
    monkeypatch.setenv('C_JOURNAL_DIR',str(tmp_path/'journal'))
    monkeypatch.setenv('C_LIVE_MODE','1')
    monkeypatch.delenv('C_SERVICE_CONTRACT',raising=False)
    def forbidden(*args,**kwargs):raise AssertionError('readonly view requested B')
    monkeypatch.setattr(GameClient,'_request',forbidden)
    app=AppTest.from_file(str(root/'app/ui.py')).run()
    app.radio[0].set_value('历史回放').run()
    app.slider[0].set_value(6).run()
    assert not app.exception
    assert any('历史回放：查看已保存的过程' in e.value for e in app.info)
    assert any('行动格式未通过规则检查' in e.value for e in app.error)
    assert any("action 'check'" in e.value for e in app.code)
    events=[json.loads(v) for v in original.decode().splitlines()]
    assert '请求已返回' in scene_model(events)['current']['body']
    assert scene_model(events)['current']['title']=='行动格式未通过规则检查'
    app.slider[0].set_value(5).run()
    assert not app.exception and not app.error
    assert "action 'check'" not in ' '.join(e.value for e in app.code)
    assert (folder/'events.jsonl').read_bytes()==original and source.read_bytes()==original


def test_call_display_does_not_rewrite_source_counts():
    raw=[{'caller_type':'player','attempts':4,'first_attempts':3,'retries':1,'success':2,'failed':2,'fallback':0}]
    row=display_call_rows(raw)[0]
    assert row['调用对象']=='玩家大模型'
    assert row['调用尝试（含重试）']==4 and row['B记录通过']==2
    assert raw[0]['caller_type']=='player'
