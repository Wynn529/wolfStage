"""Launch C's integration UI; offline preview is separate from real runs."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--offline-preview',action='store_true')
    mode.add_argument('--connect-real',action='store_true',help='UI only; start authenticated model service separately')
    parser.add_argument('--backend-port',type=int,default=18085)
    parser.add_argument('--ui-port',type=int,default=18517)
    parser.add_argument('--service-url',default='http://127.0.0.1:18083')
    args=parser.parse_args()
    environment=dict(os.environ)
    # The UI never needs provider keys. Offline backend uses PreviewAgent only.
    from_keys=json.loads((ROOT/'config/nine_models.example.json').read_text(encoding='utf-8'))['profiles']
    for profile in from_keys:environment.pop(profile['api_key_env'],None)
    environment.pop('C_SERVICE_CONTRACT',None)
    environment.update(C_SERVICE_MODE='integration',C_LIVE_MODE='0' if args.offline_preview else '1',
        C_OFFLINE_PREVIEW='1' if args.offline_preview else '0',
        B_API_URL=f'http://127.0.0.1:{args.backend_port}' if args.offline_preview else args.service_url,
        C_RUNS_DIR=str(ROOT/'runs'),C_JOURNAL_DIR=str(ROOT/'.local/c_integration_operations'))
    commands=[]
    if args.offline_preview:
        commands.append(('service',args.backend_port,['-m','uvicorn','app.c_integration_preview:create_preview_app','--factory',
            '--host','127.0.0.1','--port',str(args.backend_port)],'/model-profiles'))
    commands.append(('ui',args.ui_port,['-m','streamlit','run','app/ui.py','--server.address','127.0.0.1',
        '--server.port',str(args.ui_port),'--server.headless','true','--browser.gatherUsageStats','false'],'/_stcore/health'))
    if len({c[1] for c in commands}) != len(commands):raise ValueError('ports must be distinct')
    for _,port,_,_ in commands:
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',port))==0:raise RuntimeError(f'Port {port} occupied; no process stopped')
    logs=ROOT/'.local/c_integration_ui';logs.mkdir(parents=True,exist_ok=True)
    children=[];opener=build_opener(ProxyHandler({}))
    try:
        for name,port,command,health in commands:
            with (logs/f'{name}.out.log').open('ab') as out,(logs/f'{name}.err.log').open('ab') as err:
                proc=subprocess.Popen([sys.executable,*command],cwd=ROOT,env=environment,stdout=out,stderr=err,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            children.append((name,port,proc))
            for _ in range(100):
                if proc.poll() is not None:raise RuntimeError(f'{name} exited; inspect local logs')
                try:
                    with opener.open(f'http://127.0.0.1:{port}{health}',timeout=1):break
                except OSError:time.sleep(.2)
            else:raise RuntimeError(f'{name} not ready')
        (logs/'processes.json').write_text(json.dumps([dict(name=n,port=p,pid=c.pid) for n,p,c in children]),encoding='utf-8')
        print(f'C integration UI ready: http://127.0.0.1:{args.ui_port}/',flush=True)
        print('OFFLINE fixture only; no paid model requests.' if args.offline_preview else 'Real service connected; startup sends no model request; automatic run disabled.',flush=True)
        while all(proc.poll() is None for _,_,proc in children):time.sleep(.5)
    except KeyboardInterrupt:pass
    finally:
        for _,_,proc in children:
            if proc.poll() is None:proc.terminate()


if __name__=='__main__':main()
