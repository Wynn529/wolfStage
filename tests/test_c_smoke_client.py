import json
from pathlib import Path
from decimal import Decimal
import pytest
from app.c_smoke_client import SmokeClient,cost_bound
import app.c_smoke_client as module


def client(tmp_path):return SmokeClient(tmp_path/'budget.json',api_key='TEST_ONLY',model='deepseek-flash')


class Response:
    finish='stop'
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):
        return json.dumps({'id':'test-response','model':'deepseek-flash',
            'usage':{'prompt_tokens':10,'completion_tokens':20},
            'choices':[{'finish_reason':self.finish,'message':{'content':'{"action":"kill","target":2}'}}]}).encode()


def test_timeouts_keep_reservations_across_restarts(tmp_path,monkeypatch):
    sent=[]
    def fail(*args,**kwargs):sent.append(True);raise TimeoutError('test timeout')
    monkeypatch.setattr(module,'urlopen',fail)
    for _ in range(64):
        with pytest.raises(TimeoutError):client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    before=json.loads((tmp_path/'budget.json').read_text())
    with pytest.raises(RuntimeError,match='budget exhausted'):
        client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    assert len(sent)==64 and before['attempts']==64
    assert Decimal(str(before['reserved_cny']))==64*cost_bound(2050,512)
    assert all(e['state']=='unconfirmed' for e in before['entries'].values())


def test_oversized_input_refused_before_network(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'urlopen',lambda *a,**k:pytest.fail('network'))
    with pytest.raises(RuntimeError,match='input limit'):
        client(tmp_path).complete([{'role':'user','content':'x'*200001}],agent_id='1',prompt_version='v1')
    assert not (tmp_path/'budget.json').exists()


def test_request_body_evidence_has_no_key_and_settles_known_usage(tmp_path,monkeypatch):
    def send(request,**kwargs):
        body=json.loads(request.data)
        assert body['max_tokens']==512 and body['thinking']['type']=='disabled'
        assert json.loads((tmp_path/'budget.json').read_text())['attempts']==1
        return Response()
    monkeypatch.setattr(module,'urlopen',send)
    action,meta=client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    assert action['target']==2 and meta['input_tokens']==10 and meta['output_tokens']==20
    assert 'TEST_ONLY' not in Path(meta['request_evidence']).read_text()
    assert len(meta['request_sha256'])==64
    ledger=json.loads((tmp_path/'budget.json').read_text())
    assert Decimal(str(ledger['reserved_cny']))==cost_bound(10,20)
    assert ledger['entries']['1']['state']=='usage_known'


def test_truncated_response_keeps_known_usage_but_stays_failed(tmp_path,monkeypatch):
    response=Response();response.finish='length'
    monkeypatch.setattr(module,'urlopen',lambda *a,**k:response)
    with pytest.raises(ValueError,match='output token limit'):
        client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    meta=json.loads((tmp_path/'smoke_usage_20261029.jsonl').read_text())
    assert not meta['success'] and meta['input_tokens']==10
    assert json.loads((tmp_path/'budget.json').read_text())['attempts']==1


def test_legacy_model_refused(tmp_path):
    with pytest.raises(ValueError,match='requires deepseek-flash'):
        SmokeClient(tmp_path/'budget.json',api_key='TEST_ONLY',model='deepseek-chat')


def test_legacy_unknown_calls_keep_entire_previous_reserve(tmp_path,monkeypatch):
    old={'attempts':9,'reserved_cny':.8,'authorized_cny':1,'model':'deepseek-flash'}
    (tmp_path/'budget.json').write_text(json.dumps(old))
    monkeypatch.setattr(module,'urlopen',lambda *a,**k:Response())
    client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    ledger=json.loads((tmp_path/'budget.json').read_text())
    assert ledger['attempts']==10 and ledger['legacy_snapshot']==old
    assert not ledger['legacy_settlement_known']
    assert Decimal(str(ledger['reserved_cny']))==Decimal('.8')+cost_bound(10,20)


def test_only_complete_unique_legacy_usage_can_release_unused_reserve(tmp_path,monkeypatch):
    old={'attempts':2,'reserved_cny':.3,'authorized_cny':1,'model':'deepseek-flash'}
    (tmp_path/'budget.json').write_text(json.dumps(old))
    usage=[{'attempt':n,'response_id':str(n),'model_name':'deepseek-flash','input_tokens':100,'output_tokens':20} for n in (1,2)]
    (tmp_path/'smoke_usage_20261029.jsonl').write_text(''.join(json.dumps(u)+'\n' for u in usage))
    monkeypatch.setattr(module,'urlopen',lambda *a,**k:Response())
    client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    ledger=json.loads((tmp_path/'budget.json').read_text())
    assert ledger['attempts']==3 and ledger['legacy_snapshot']==old
    assert ledger['legacy_settlement_known']
    assert Decimal(str(ledger['reserved_cny']))==2*cost_bound(100,20)+cost_bound(10,20)


def test_money_guard_reserves_worst_case_before_sending(tmp_path,monkeypatch):
    (tmp_path/'budget.json').write_text(json.dumps({'attempts':5,'reserved_cny':.97,'authorized_cny':1}))
    monkeypatch.setattr(module,'urlopen',lambda *a,**k:pytest.fail('network'))
    with pytest.raises(RuntimeError,match='budget exhausted'):
        client(tmp_path).complete([],agent_id='1',prompt_version='v1')
    assert json.loads((tmp_path/'budget.json').read_text())['reserved_cny']==.97
