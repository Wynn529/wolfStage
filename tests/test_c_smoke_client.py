import json
import pytest
from app.c_smoke_client import SmokeClient
import app.c_smoke_client as module


def make_client(tmp_path):
    return SmokeClient(tmp_path / 'budget.json', api_key='TEST_ONLY', model='deepseek-flash')


def test_failed_requests_remain_reserved_after_restart(tmp_path, monkeypatch):
    sent=[]
    def fail(request, **kwargs):
        sent.append(request)
        raise TimeoutError('test timeout')
    monkeypatch.setattr(module, 'urlopen', fail)
    for _ in range(12):
        with pytest.raises(TimeoutError):
            make_client(tmp_path).complete([], agent_id='1', prompt_version='v1')
    with pytest.raises(RuntimeError, match='budget exhausted'):
        make_client(tmp_path).complete([], agent_id='1', prompt_version='v1')
    assert len(sent)==12
    ledger=json.loads((tmp_path/'budget.json').read_text())
    assert ledger['reserved_cny']==.48 and ledger['authorized_cny']==1
    assert len((tmp_path/'smoke_usage_20261029.jsonl').read_text().splitlines())==12


def test_oversized_input_refused_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(module,'urlopen',lambda *a,**k: pytest.fail('network'))
    with pytest.raises(RuntimeError,match='input limit'):
        make_client(tmp_path).complete([{'role':'user','content':'x'*9000}],agent_id='1',prompt_version='v1')
    assert not (tmp_path/'budget.json').exists()


class Response:
    finish='stop'
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):
        return json.dumps({'model':'deepseek-flash','usage':{'prompt_tokens':10,'completion_tokens':20},'choices':[{'finish_reason':self.finish,'message':{'content':'{"action":"kill","target":2}'}}]}).encode()


def test_request_limits_and_usage_evidence(tmp_path,monkeypatch):
    def send(request,**kwargs):
        body=json.loads(request.data)
        assert body['max_tokens']==512 and body['thinking']['type']=='disabled'
        assert body['model']=='deepseek-flash'
        assert json.loads((tmp_path/'budget.json').read_text())['attempts']==1
        return Response()
    monkeypatch.setattr(module,'urlopen',send)
    action,meta=make_client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    assert action['target']==2 and meta['input_tokens']==10 and meta['output_tokens']==20
    assert meta['success'] and meta['latency_ms']>=0
    assert 'TEST_ONLY' not in (tmp_path/'smoke_usage_20261029.jsonl').read_text()


def test_truncated_response_stays_failed(tmp_path,monkeypatch):
    response=Response();response.finish='length'
    monkeypatch.setattr(module,'urlopen',lambda *a,**k:response)
    with pytest.raises(ValueError,match='output token limit'):
        make_client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    assert json.loads((tmp_path/'budget.json').read_text())['attempts']==1
    assert not json.loads((tmp_path/'smoke_usage_20261029.jsonl').read_text())['success']


def test_legacy_model_refused(tmp_path):
    with pytest.raises(ValueError,match='requires deepseek-flash'):
        SmokeClient(tmp_path/'budget.json',api_key='TEST_ONLY',model='deepseek-chat')


def test_continuation_keeps_existing_reservation_and_count(tmp_path,monkeypatch):
    path=tmp_path/'budget.json'
    path.write_text(json.dumps({'attempts':4,'reserved_cny':.6,'authorized_cny':1,'model':'deepseek-flash'}))
    monkeypatch.setattr(module,'urlopen',lambda *a,**k:Response())
    for _ in range(8):
        make_client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    ledger=json.loads(path.read_text())
    assert ledger['attempts']==12 and ledger['reserved_cny']==.92
    with pytest.raises(RuntimeError,match='budget exhausted'):
        make_client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    assert json.loads(path.read_text())==ledger


def test_money_guard_refuses_before_request_even_if_call_count_remains(tmp_path,monkeypatch):
    path=tmp_path/'budget.json'
    path.write_text(json.dumps({'attempts':5,'reserved_cny':.96,'authorized_cny':1}))
    monkeypatch.setattr(module,'urlopen',lambda *a,**k:pytest.fail('network'))
    with pytest.raises(RuntimeError,match='budget exhausted'):
        make_client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    assert json.loads(path.read_text())['reserved_cny']==.96
