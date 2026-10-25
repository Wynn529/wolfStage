"""只用于C适配测试的参考HTTP服务，不是B修复实现或真实对局。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs

from app.ui_contract import CAPABILITIES, ServiceContract


def reference_contract():
    return ServiceContract(version='c-reference-test-only',confirmed_by='TEST DOUBLE, NOT B',
                           confirmed_commit='test-only',capabilities={k:True for k in CAPABILITIES})


class ReferenceService:
    def __init__(self, delay=.03, response_delay=0, plan=None, corrupt_state=None):
        self.delay=delay
        self.response_delay=response_delay
        self.plan=plan
        self.corrupt_state=corrupt_state
        self.games={}
        self.submissions=[]
        self.executions=[]
        self.lock=threading.RLock()

    def create(self, seed=42):
        with self.lock:
            gid=uuid.uuid4().hex[:12]
            state={'game_id':gid,'status':'running','phase':'night','last_event_id':1,
                   'in_flight':False,'pause_requested':False,'step':0,'operations':{},'calls':[],
                   'events':[{'game_id':gid,'event_id':1,'type':'game_created','visibility':'host',
                              'source':'REFERENCE_TEST_ONLY','roles':{'1':'wolf','2':'wolf','3':'seer','4':'villager','5':'villager','6':'villager'},
                              'alive':[1,2,3,4,5,6]}]}
            self.games[gid]=state
            return {'game_id':gid,'status':'running','phase':'night','rules_version':'reference-test-only'}

    def view_state(self, gid):
        with self.lock:
            s=self.games[gid]
            result={k:s[k] for k in ('game_id','status','phase','last_event_id','in_flight','pause_requested')}
            if self.corrupt_state:
                result.update(self.corrupt_state)
            return result

    def finish(self,s,op):
        op['state']='succeeded'
        op['result']={'game_id':s['game_id'],'status':s['status'],'phase':s['phase'],'new_event':s['last_event_id']}

    def control(self,gid,action,body):
        with self.lock:
            s=self.games[gid]
            oid=body['operation_id']
            self.submissions.append((gid,action,oid,body['expected_event_id']))
            if oid in s['operations']:
                return s['operations'][oid]
            op={'game_id':gid,'operation_id':oid,'action':action,'state':'pending'}
            s['operations'][oid]=op
            if action=='advance':
                if s['in_flight'] or s['status']!='running' or body['expected_event_id']!=s['last_event_id']:
                    op['state']='rejected'
                    return op
                s['in_flight']=True
                s['status']='pending'
                threading.Thread(target=self.advance,args=(s,op),daemon=True).start()
            elif action=='pause':
                if s['in_flight']:
                    s['pause_requested']=True
                else:
                    s['status']='paused'
                    self.finish(s,op)
            elif action=='resume':
                if s['in_flight'] or s['status']!='paused':
                    op['state']='rejected'
                else:
                    s['status']='running'
                    s['pause_requested']=False
                    self.finish(s,op)
            else:
                op['state']='rejected'
            return dict(op)

    def advance(self,s,op):
        time.sleep(self.delay)
        with self.lock:
            self.executions.append(op['operation_id'])
            step=s['step']
            if self.plan is not None:
                spec=self.plan[min(step,len(self.plan)-1)]
            else:
                spec=({'events':[],'phase':'day_speak'} if step==0 else
                      {'events':[{'type':'public_speech','actor':4,'text':'参考测试发言'},
                                 {'type':'utterance_classification','source_event_id':s['last_event_id']+1,
                                  'result':{'intent':'discussion','model_version':'REFERENCE_TEST_ONLY','confidence':None}}]} if step==1 else
                      {'events':[{'type':'game_over','winner':'villagers'}],'status':'finished'} if step>=3 else
                      {'events':[{'type':'vote','actor':4,'target':1,'valid':True}],'phase':'day_vote'})
            for values in spec.get('events',[]):
                s['last_event_id']+=1
                s['events'].append({'game_id':s['game_id'],'event_id':s['last_event_id'],
                                    'visibility':'public','source':'REFERENCE_TEST_ONLY',**values})
            s['step']+=1
            s['calls'].append({'game_id':s['game_id'],'caller_type':'player','model_name':'reference-test',
                               'attempt':1,'success':True,'input_tokens':11,'output_tokens':7,'fallback':False})
            s['in_flight']=False
            s['phase']=spec.get('phase',s['phase'])
            s['status']=spec.get('status','running')
            if s['pause_requested'] and s['status']=='running':
                s['status']='paused'
            self.finish(s,op)
            if s['pause_requested']:
                for item in s['operations'].values():
                    if item['action']=='pause' and item['state']=='pending':
                        self.finish(s,item)
                s['pause_requested']=False


@contextmanager
def serve_reference(**kwargs):
    service=ReferenceService(**kwargs)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,value):
            encoded=json.dumps(value,ensure_ascii=False).encode('utf-8')
            try:
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError,ConnectionResetError,OSError):pass
        def do_POST(self):
            try:
                body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
                parts=urlsplit(self.path).path.strip('/').split('/')
                if parts==['games']:
                    self.send(200,service.create(body.get('seed',42)));return
                result=service.control(parts[1],parts[2],body)
                if parts[2]=='advance':time.sleep(service.response_delay)
                self.send(202 if result['state']=='pending' else 200, result)
            except (KeyError,ValueError,IndexError):self.send(404,{'error':'not_found'})
        def do_GET(self):
            try:
                parsed=urlsplit(self.path)
                parts=parsed.path.strip('/').split('/')
                gid=parts[1]
                with service.lock:
                    s=service.games[gid]
                    if parts[2]=='state':result=service.view_state(gid)
                    elif parts[2]=='operations':result=dict(s['operations'][parts[3]])
                    elif parts[2]=='events':
                        after=int(parse_qs(parsed.query).get('after_event_id',[0])[0])
                        result=[dict(e) for e in s['events'] if e['event_id']>after]
                    elif parts[2]=='metrics':
                        result={'game_id':gid,'event_count':len(s['events']),'call_count':len(s['calls']),
                                'successful_calls':len(s['calls']),'failed_calls':0,'retries':0,
                                'input_tokens':11*len(s['calls']),'output_tokens':7*len(s['calls'])}
                    else:raise KeyError()
                self.send(200,result)
            except (KeyError,ValueError,IndexError):self.send(404,{'error':'not_found'})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield service,f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
