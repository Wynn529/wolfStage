"""C用既有顺序控制器续跑指定真实局；默认只查状态，--run才推进。"""
from pathlib import Path
import argparse
import json
import sys
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.ui_client import BRevisionClient
from app.ui_controller import SessionController


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-id',required=True)
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    base='http://127.0.0.1:18082'
    ready=json.loads(urlopen(base+'/c/ready',timeout=5).read())
    if ready.get('model')!='deepseek-flash' or ready.get('smoke_max_attempts')!=64:
        raise RuntimeError('Restart the updated controlled service before continuing.')
    journal=ROOT/'.local/real_api/c_operations'/('full_'+args.game_id+'.jsonl')
    client=BRevisionClient(base)
    control=SessionController.restore(client,journal) if journal.exists() else SessionController(client,args.game_id,journal=journal)
    if control.game_id!=args.game_id:raise RuntimeError('Journal game does not match.')
    try:
        control.refresh()
        print(json.dumps({'game_id':control.game_id,'status':control.status,'cursor':control.cursor,'run':args.run}),flush=True)
        if not args.run:return 0
        if control.busy or control.unresolved or control.fatal or control.error:
            raise RuntimeError('An operation is unresolved; query it before submitting anything else.')
        def step(action):
            oid=control.submit(action)
            deadline=time.monotonic()+100
            while time.monotonic()<deadline:
                control.tick()
                if control.error or control.fatal:raise RuntimeError(control.error or 'Control is locked.')
                if not control.busy and control.synced:
                    print(json.dumps({'operation_id':oid,'action':action,'status':control.status,
                                      'cursor':control.cursor,'phase':control.phase}),flush=True)
                    return
                time.sleep(.25)
            raise RuntimeError('Result still unknown; journal retained, no request was repeated.')
        if control.status=='paused':step('resume')
        for _ in range(200):
            if control.status in {'finished','incomplete','failed','degraded'}:break
            if control.status!='running':raise RuntimeError('Unexpected state; stop without resubmission.')
            step('advance')
        if control.status not in {'finished','incomplete'}:
            raise RuntimeError('Game not complete; stop and inspect records.')
        print(json.dumps({'result':control.status,'game_id':control.game_id,'cursor':control.cursor,'metrics':control.metrics}),flush=True)
        return 0
    finally:control.close()

if __name__=='__main__':raise SystemExit(main())
