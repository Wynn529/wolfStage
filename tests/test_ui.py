import io
import json
import zipfile

import pytest

from app.ui_helpers import merge_events, project, read_events, evidence_zip


def event(number, kind, **fields):
    return {'game_id': 'g', 'event_id': number, 'type': kind, **fields}


def test_incremental_merge_preserves_all_events_and_rejects_conflicts():
    speech = event(2, 'public_speech', text='hello')
    label = event(3, 'utterance_classification', source_event_id=2, result={'intent': 'discussion'})
    assert merge_events([speech], [label, speech], 'g') == [speech, label]
    with pytest.raises(ValueError):
        merge_events([speech], [dict(speech, text='changed')], 'g')
    with pytest.raises(ValueError):
        merge_events([], [dict(speech, game_id='other')], 'g')


def test_replay_projection_does_not_reveal_future_deaths_or_labels():
    events = [event(1, 'game_created', roles={'1': 'wolf', '2': 'seer'}, alive=[1, 2]),
              event(2, 'public_speech'),
              event(3, 'utterance_classification', source_event_id='2', result={'intent': 'role_claim'}),
              event(4, 'exile', target=1), event(5, 'game_over', winner='villagers')]
    early = project(events[:2])
    assert early['alive'] == {1, 2}
    assert not early['classifications']
    assert early['winner'] is None
    final = project(events)
    assert final['alive'] == {2}
    assert final['classifications']['2']['intent'] == 'role_claim'
    assert final['status'] == 'finished'


def test_export_preserves_original_bytes_and_does_not_touch_calls(tmp_path):
    folder = tmp_path / 'g'
    folder.mkdir()
    original = b'{"game_id":"g","event_id":1,"type":"game_created"}\r\n'
    (folder / 'events.jsonl').write_bytes(original)
    (folder / 'calls.jsonl').write_bytes(b'original-calls\r\n')
    (folder / 'state.json').write_text('not-for-export', encoding='utf-8')
    before = (folder / 'calls.jsonl').stat().st_mtime_ns
    assert read_events(tmp_path, 'g')[0]['event_id'] == 1
    data = evidence_zip(tmp_path, 'g')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read('g/events.jsonl') == original
        assert archive.read('g/calls.jsonl') == b'original-calls\r\n'
        assert 'g/state.json' not in archive.namelist()
    assert (folder / 'calls.jsonl').stat().st_mtime_ns == before
    with pytest.raises(ValueError):
        evidence_zip(tmp_path, '../')


def test_streamlit_controls_use_b_and_attach_actual_a_labels(tmp_path, monkeypatch):
    """真实本地 A HTTP、B HTTP、Streamlit AppTest；玩家为 stub。"""
    import socket
    import threading
    import time
    from pathlib import Path
    import uvicorn
    import app.api as api
    from app.agents import StubAgent
    from app.classifier import AHttpClassifier
    from app.engine import GameEngine
    from model.server_v031 import make_server
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv('C_SERVICE_CONTRACT',raising=False)
    monkeypatch.setenv('C_JOURNAL_DIR',str(tmp_path/'journals'))
    monkeypatch.setenv('C_RUNS_DIR',str(tmp_path/'runs'))

    a = make_server(port=0)
    a_thread = threading.Thread(target=a.serve_forever, daemon=True)
    a_thread.start()
    classifier = AHttpClassifier(f'http://127.0.0.1:{a.server_port}/classify')
    engine = GameEngine(str(tmp_path / 'runs'), agent=StubAgent(), classifier=classifier)
    monkeypatch.setattr(api, 'engine', engine)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api.app, log_level='error', lifespan='off'))
    worker = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    worker.start()
    monkeypatch.setenv('B_API_URL', f'http://127.0.0.1:{port}')
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.05)
        assert server.started
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app/ui.py')).run(timeout=20)
        def click(label):
            next(button for button in app.button if button.label == label).click().run(timeout=20)
            deadline=time.monotonic()+5
            while app.session_state['controller'].busy and time.monotonic()<deadline:
                time.sleep(.02)
                app.run(timeout=20)
            assert not app.exception
        click('新建对局')
        gid = app.session_state['game_id']
        assert gid in engine.games
        click('暂停')
        assert engine.games[gid].status == 'paused'
        click('继续')
        for _ in range(5):
            click('单步推进')
        events = app.session_state['events']
        label = next(e for e in events if e['type'] == 'utterance_classification')
        assert label['result']['entity_rule_version'] == 'entity-rules-v0.3.1'
        assert label['result']['confidence'] is None
        before = len(engine.store.read_calls(gid))
        click('刷新事件')
        assert len(engine.store.read_calls(gid)) == before
        assert len({e['event_id'] for e in app.session_state['events']}) == len(app.session_state['events'])

        # 完整离线对局经同一网页控制完成，再切本机回放；不算真实API场次。
        for _ in range(100):
            if engine.games[gid].status != 'running':
                break
            click('单步推进')
        assert engine.games[gid].status == 'finished'
        assert next(b for b in app.button if b.label == '单步推进').disabled
        original_events = engine.store.read(gid)
        folder = tmp_path/'runs'/gid
        originals = {p.name:p.read_bytes() for p in folder.iterdir() if p.is_file()}
        times = {p.name:p.stat().st_mtime_ns for p in folder.iterdir() if p.is_file()}
        calls_before_replay = len(engine.store.read_calls(gid))

        # 切入只读模式后任何B请求都会使测试失败，包括意外的只读轮询。
        from app.ui_client import GameClient
        def forbid_http(*args, **kwargs):
            raise AssertionError('local replay must not request B or a model')
        monkeypatch.setattr(GameClient, '_request', forbid_http)
        app.radio[0].set_value('本地只读回放').run(timeout=20)
        assert not app.exception
        assert app.slider[0].value == 0
        assert not any(m.label == '存活人数' for m in app.metric)
        last = original_events[-1]['event_id']
        speech_id = next(e['event_id'] for e in original_events if e['type']=='public_speech')
        for cursor in (speech_id, last, 0, last):
            app.slider[0].set_value(cursor).run(timeout=20)
            assert not app.exception
            if cursor:
                assert next(m.value for m in app.metric if m.label=='最后显示 event_id') == str(cursor)
            if cursor == speech_id:
                assert not any('终局：' in item.value for item in app.success)
                assert any('尚无对应分类' in item.value for item in app.caption)
            if cursor == last:
                assert any('终局：' in item.value for item in app.success)
        with zipfile.ZipFile(io.BytesIO(evidence_zip(tmp_path/'runs', gid))) as archive:
            expected = {n for n in originals if n in {'events.jsonl','calls.jsonl','errors.jsonl','summary.json'}}
            assert set(archive.namelist()) == {f'{gid}/{n}' for n in expected}
            for name in expected:
                assert archive.read(f'{gid}/{name}') == originals[name]
        assert len(engine.store.read_calls(gid)) == calls_before_replay
        assert {p.name:p.read_bytes() for p in folder.iterdir() if p.is_file()} == originals
        assert {p.name:p.stat().st_mtime_ns for p in folder.iterdir() if p.is_file()} == times
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        sock.close()
        a.shutdown()
        a.server_close()
        a_thread.join(timeout=5)
