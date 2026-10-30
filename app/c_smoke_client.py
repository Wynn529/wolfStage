"""C受控真实请求：沿用一元授权，保留已知用量与未决预留。"""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen
from app.deepseek_client import DeepSeekClient, ModelOutputError


def cost_bound(input_tokens, output_tokens):
    return ((Decimal(input_tokens)*3 + Decimal(output_tokens)*12)/1000000).quantize(Decimal('0.000001'), rounding=ROUND_CEILING)


class SmokeClient(DeepSeekClient):
    max_attempts = 64
    max_input_bytes = 200000
    max_tokens = 512
    maximum_reserved_cny = Decimal('0.98')

    def __init__(self, ledger: Path, **kwargs):
        super().__init__(**kwargs)
        self.max_tokens = type(self).max_tokens
        if self.model != 'deepseek-flash':
            raise ValueError('C smoke check requires deepseek-flash; restart with the updated launcher.')
        self.ledger = Path(ledger)
        self.lock = threading.Lock()

    def _load(self):
        value = json.loads(self.ledger.read_text(encoding='utf-8')) if self.ledger.exists() else {'attempts':0,'reserved_cny':0,'authorized_cny':1}
        count=value['attempts']
        if type(count) is not int or count<0 or value.get('authorized_cny')!=1:
            raise RuntimeError('C budget ledger is invalid; no request was sent.')
        if value.get('version')!=2:
            old=Decimal(str(value.get('reserved_cny',count*.15)))
            usage_path=self.ledger.with_name('smoke_usage_20261029.jsonl')
            usage=[json.loads(line) for line in usage_path.read_text(encoding='utf-8').splitlines() if line.strip()] if usage_path.exists() else []
            indices=[u.get('attempt') for u in usage]
            known=(len(usage)==count and sorted(indices)==list(range(1,count+1))
                   and len({u.get('response_id') for u in usage})==count
                   and all(u.get('response_id') and u.get('model_name')=='deepseek-flash'
                           and type(u.get('input_tokens')) is int and u['input_tokens']>=0
                           and type(u.get('output_tokens')) is int and u['output_tokens']>=0 for u in usage))
            if not old.is_finite() or old<0:
                raise RuntimeError('C budget ledger is invalid; no request was sent.')
            settled=sum((cost_bound(u['input_tokens'],u['output_tokens']) for u in usage),Decimal(0)) if known else old
            value={'version':2,'attempts':count,'authorized_cny':1,'model':self.model,
                   'legacy_snapshot':value,'legacy_settlement_known':known,
                   'legacy_held_cny':str(settled),'entries':{},'reserved_cny':float(settled)}
        start=value['legacy_snapshot']['attempts']
        if set(value['entries'])!={str(n) for n in range(start+1,count+1)} or Decimal(value['legacy_held_cny'])<0:
            raise RuntimeError('C budget ledger is inconsistent; no request was sent.')
        held=Decimal(value['legacy_held_cny'])+sum((Decimal(e['held_cny']) for e in value['entries'].values()),Decimal(0))
        if not held.is_finite() or held<0 or any(Decimal(e['held_cny'])<0 for e in value['entries'].values()):
            raise RuntimeError('C budget ledger is invalid; no request was sent.')
        value['reserved_cny']=float(held)
        return value,held

    def _save(self,value):
        self.ledger.parent.mkdir(parents=True,exist_ok=True)
        temp=self.ledger.with_suffix('.tmp')
        temp.write_text(json.dumps(value),encoding='utf-8')
        temp.replace(self.ledger)

    def complete(self,messages,*,agent_id,prompt_version):
        if not self.api_key:
            raise RuntimeError('Local API key is missing.')
        size=len(json.dumps(messages,ensure_ascii=False).encode('utf-8'))
        if size>self.max_input_bytes or len(messages)>16:
            raise RuntimeError('C smoke input limit reached; no request was sent.')
        bound=cost_bound(size+2048,self.max_tokens)
        with self.lock:
            value,held=self._load()
            if value['attempts']>=self.max_attempts or held+bound>self.maximum_reserved_cny or value.get('bound_exceeded'):
                raise RuntimeError('C smoke request budget exhausted; no request was sent.')
            attempt=value['attempts']+1
            value['attempts']=attempt
            value['entries'][str(attempt)]={'state':'reserved','held_cny':str(bound),'input_bound':size+2048}
            value['reserved_cny']=float(held+bound)
            self._save(value)
        body=json.dumps({'model':self.model,'messages':messages,'temperature':.2,'response_format':{'type':'json_object'},
                         'max_tokens':self.max_tokens,'thinking':{'type':'disabled'},'stream':False}).encode()
        evidence=self.ledger.parent/'request_evidence'/f'attempt_{attempt:06d}.json'
        evidence.parent.mkdir(parents=True,exist_ok=True)
        evidence.write_bytes(body)  # Only the JSON body, never authentication headers.
        request=Request('https://api.deepseek.com/chat/completions',data=body,
                        headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json'})
        started=time.perf_counter()
        meta={'attempt':attempt,'agent_id':agent_id,'model_name':self.model,'model_version':self.model,
              'prompt_version':prompt_version,'input_tokens':None,'output_tokens':None,'success':False,
              'request_evidence':str(evidence),'request_sha256':hashlib.sha256(body).hexdigest()}
        try:
            with urlopen(request,timeout=self.timeout) as response:
                payload=json.loads(response.read().decode('utf-8'))
            usage=payload.get('usage') or {}
            meta.update(input_tokens=usage.get('prompt_tokens'),output_tokens=usage.get('completion_tokens'),
                        returned_model=payload.get('model'),response_id=payload.get('id'))
            choice=payload['choices'][0]
            if choice.get('finish_reason')=='length':
                raise ModelOutputError('C smoke output token limit reached; keep this failed attempt.',meta)
            try:
                action=json.loads(choice['message']['content'])
            except (ValueError,KeyError,TypeError) as exc:
                raise ModelOutputError('Invalid model JSON output',meta) from exc
            meta['success']=True
            return action,meta
        finally:
            meta['latency_ms']=round((time.perf_counter()-started)*1000,2)
            with self.lock:
                with self.ledger.with_name('smoke_usage_20261029.jsonl').open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(meta)+'\n')
                value,held=self._load()
                entry=value['entries'][str(attempt)]
                known=all(type(meta[k]) is int and meta[k]>=0 for k in ('input_tokens','output_tokens')) and bool(meta.get('response_id'))
                if known:
                    actual=cost_bound(meta['input_tokens'],meta['output_tokens'])
                    value['reserved_cny']=float(held-Decimal(entry['held_cny'])+actual)
                    entry.update(state='usage_known',held_cny=str(actual),response_id=meta['response_id'])
                    if actual>bound:value['bound_exceeded']=True
                else:
                    entry['state']='unconfirmed'
                self._save(value)
