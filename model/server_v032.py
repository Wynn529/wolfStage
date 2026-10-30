"""Optional local HTTP adapter; rules and model remain the frozen implementation."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model.common import ROOT, SCHEMA_VERSION
from model.predict_v032 import SmallModel, InputValidationError

MAX_REQUEST_BYTES=65536


def make_server(host='127.0.0.1',port=8765,model_path=ROOT/'model/classifier.joblib'):
    model=SmallModel(model_path)
    inference_lock=threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*args):
            # Access log excludes utterance body; B owns persistent game event logs.
            sys.stderr.write((fmt % args)+'\n')

        def send_json(self,status,value):
            encoded=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(encoded)))
            self.end_headers();self.wfile.write(encoded)

        def do_GET(self):
            if self.path=='/health':
                self.send_json(200,{'status':'ok','model_version':model.manifest['model_version'],
                                    'schema_version':SCHEMA_VERSION,'confidence_type':'not_calibrated',
                                    'entity_rule_version':'entity-rules-v0.3.2'})
            else:self.send_json(404,{'error':{'code':'not_found'}})

        def do_POST(self):
            if self.path!='/classify':
                self.send_json(404,{'error':{'code':'not_found'}});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>MAX_REQUEST_BYTES:
                    self.send_json(413,{'error':{'code':'request_too_large_or_empty','max_bytes':MAX_REQUEST_BYTES}});return
                body=json.loads(self.rfile.read(size).decode('utf-8'))
                keys={'game_id','event_id','speaker_id','public_text'}
                if not isinstance(body,dict) or set(body)!=keys:
                    raise InputValidationError('Exactly game_id,event_id,speaker_id,public_text are required')
                with inference_lock:
                    result=model.classify_utterance(**body)
                self.send_json(200,result)
            except (ValueError,UnicodeError,InputValidationError) as exc:
                self.send_json(400,{'error':{'code':'invalid_input','message':str(exc)}})

    return ThreadingHTTPServer((host,port),Handler)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--model',type=Path,default=ROOT/'model/classifier.joblib')
    a=p.parse_args()
    server=make_server(a.host,a.port,a.model)
    print(f'Local small-model HTTP adapter: http://{a.host}:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
