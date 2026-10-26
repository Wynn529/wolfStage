"""C真实模式启动/检查/停止；仅健康检查GET，不创建游戏或发模型请求。"""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LOCAL = ROOT / '.local' / 'real_api'
RECORD = LOCAL / 'processes.json'
STOP_REQUEST = LOCAL / 'stop-request.json'
RUNS = ROOT / 'runs' / 'real_api'


def process_stamp(pid):
    """Windows创建时间标识，避免停止复用了同一PID的其他进程。"""
    if os.name != 'nt':
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
            return None
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return None
        return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def record_alive(item):
    return bool(item.get('stamp') and process_stamp(item['pid']) == item['stamp'])


def terminate_tree(item):
    """只回收已核对身份的本次子树；不依赖taskkill的系统进程查询权限。"""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),
                  ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
                  ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),
                  ('exe',wintypes.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot==ctypes.c_void_p(-1).value:
        raise RuntimeError('Cannot inspect the owned process tree.')
    children={}
    try:
        entry=Entry();entry.size=ctypes.sizeof(Entry)
        valid=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while valid:
            children.setdefault(entry.parent,[]).append(entry.pid)
            valid=kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    owned=[];seen=set()
    def visit(pid):
        if pid in seen:return
        seen.add(pid)
        stamp=process_stamp(pid)
        if not stamp or int(stamp)<int(item['stamp']):return
        for child in children.get(pid,[]):visit(child)
        owned.append((pid,stamp))
    if not record_alive(item):return
    visit(item['pid'])
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
    kernel.TerminateProcess.restype=wintypes.BOOL
    for pid,stamp in owned:
        handle=kernel.OpenProcess(0x1001,False,pid)
        if not handle:
            if process_stamp(pid)==stamp:
                raise RuntimeError('Cannot stop a verified service process.')
            continue
        try:
            if process_stamp(pid)==stamp and not kernel.TerminateProcess(handle,0):
                if process_stamp(pid)==stamp:
                    raise RuntimeError('Stopping a verified service process failed.')
        finally:kernel.CloseHandle(handle)


def stop_records(items):
    stopped=[]
    for item in reversed(items):
        if record_alive(item):
            terminate_tree(item)
            stopped.append(item['name'])
    return stopped


def check_config(model):
    missing = [name for name in ('fastapi','uvicorn','streamlit','numpy','sklearn','joblib')
               if importlib.util.find_spec(name) is None]
    prompts = [ROOT/'prompts'/name for name in ('wolf-v1.md','seer-v1.md','villager-v1.md')]
    if not re.fullmatch(r'[A-Za-z0-9._:-]+', model):
        raise ValueError('Invalid model name.')
    ready = bool(os.getenv('DEEPSEEK_API_KEY','').strip()) and not missing and all(p.is_file() for p in prompts)
    return {'ready':ready,'key_configured':bool(os.getenv('DEEPSEEK_API_KEY','').strip()),
            'model':model,'missing_dependencies':missing,
            'missing_prompts':[p.name for p in prompts if not p.is_file()],
            'external_api_requests':0,'note':'Local configuration only; API key validity and balance are not verified.'}


def child_environments(model, a_port, b_port):
    common = dict(os.environ)
    common.pop('DEEPSEEK_API_KEY',None)
    common.pop('C_SERVICE_CONTRACT',None)
    common.update(PYTHONIOENCODING='utf-8', C_REAL_RUNS_DIR=str(RUNS), C_RUNS_DIR=str(RUNS),
                  C_JOURNAL_DIR=str(LOCAL/'c_operations'), C_B_REVISION='3898b95',
                  C_LIVE_MODE='1', A_CLASSIFIER_URL=f'http://127.0.0.1:{a_port}/classify',
                  B_API_URL=f'http://127.0.0.1:{b_port}', DEEPSEEK_MODEL=model)
    # 真模式默认手动，继承的自动开关不得悄悄开启连续付费请求。
    common.pop('C_REAL_ALLOW_AUTO',None)
    backend = dict(common, DEEPSEEK_API_KEY=os.environ['DEEPSEEK_API_KEY'])
    return {'a':dict(common),'b':backend,'ui':dict(common)}


def validate_ports(ports):
    if len(set(ports)) != 3 or any(not 1 <= p <= 65535 for p in ports):
        raise ValueError('A, B and UI need three distinct ports in 1..65535.')
    for port in ports:
        with socket.socket() as probe:
            probe.settimeout(.4)
            if probe.connect_ex(('127.0.0.1',port)) == 0:
                raise RuntimeError(f'Port {port} is occupied; no existing process was stopped.')


def serve(args):
    if os.name != 'nt':
        raise RuntimeError('This launcher currently supports Windows only.')
    if RECORD.exists():
        previous = json.loads(RECORD.read_text(encoding='utf-8'))
        if any(record_alive(item) for item in previous.get('children',[])):
            raise RuntimeError('A real-mode session is already running. Stop it first.')
    validate_ports([args.a_port,args.b_port,args.ui_port])
    session = time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    logs = LOCAL/'sessions'/session
    logs.mkdir(parents=True)
    RUNS.mkdir(parents=True,exist_ok=True)
    environments = child_environments(args.model,args.a_port,args.b_port)
    commands = [
        ('a',['-m','model.server_v031','--port',str(args.a_port)],f'http://127.0.0.1:{args.a_port}/health'),
        ('b',['-m','uvicorn','app.c_live_service:app','--host','127.0.0.1','--port',str(args.b_port)],f'http://127.0.0.1:{args.b_port}/c/ready'),
        ('ui',['-m','streamlit','run','app/ui.py','--server.address','127.0.0.1','--server.port',str(args.ui_port),
               '--server.headless','true','--browser.gatherUsageStats','false'],f'http://127.0.0.1:{args.ui_port}/_stcore/health')]
    children=[]; records=[]; handles=[]
    try:
        for name,argv,health in commands:
            out=(logs/f'{name}.out.log').open('wb');err=(logs/f'{name}.err.log').open('wb')
            handles.extend([out,err])
            proc=subprocess.Popen([sys.executable,*argv],cwd=ROOT,env=environments[name],stdout=out,stderr=err,
                                  creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW)
            children.append(proc)
            item={'name':name,'pid':proc.pid,'stamp':process_stamp(proc.pid)}
            if not item['stamp']:
                proc.terminate()
                raise RuntimeError(f'Cannot verify {name} process identity; startup stopped.')
            records.append(item)
            RECORD.write_text(json.dumps({'session':session,'launcher_pid':os.getpid(),
                'children':records,'logs':str(logs),'runs':str(RUNS),'url':f'http://127.0.0.1:{args.ui_port}'},indent=2),encoding='utf-8')
            deadline=time.monotonic()+45
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f'{name} exited; inspect its local log.')
                try:
                    with urlopen(health,timeout=1) as response:
                        if name=='b':
                            value=json.load(response)
                            if (value.get('mode')!='real_deepseek' or value.get('agent')!='DeepSeekAgent'
                                    or value.get('model')!=args.model or value.get('runs_dir')!=str(RUNS)):
                                raise RuntimeError('Backend real-mode identity check failed.')
                        break
                except (OSError,ValueError):
                    if time.monotonic()>deadline:
                        raise RuntimeError(f'{name} health check timed out; inspect its local log.') from None
                    time.sleep(.25)
        print(f'Real mode ready: http://127.0.0.1:{args.ui_port}',flush=True)
        print('No DeepSeek request has been sent by startup. Manual advance may incur charges.',flush=True)
        print('Automatic run is disabled for this preparation stage. Press Ctrl+C to stop.',flush=True)
        while True:
            if STOP_REQUEST.exists() and json.loads(STOP_REQUEST.read_text(encoding='utf-8')).get('session') == session:
                return
            if any(proc.poll() is not None for proc in children):
                raise RuntimeError('A service stopped; shutting down this real-mode session.')
            time.sleep(.5)
    finally:
        stop_records(records)
        for proc in children:
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:pass
        for handle in handles:handle.close()
        # 保留进程记录和日志；下次启动按身份核对，绝不删除对局原件。
        print('Real-mode services stopped; game records and logs retained.',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--stop',action='store_true')
    parser.add_argument('--model',default=os.getenv('DEEPSEEK_MODEL','deepseek-chat'))
    parser.add_argument('--a-port',type=int,default=18782)
    parser.add_argument('--b-port',type=int,default=18082)
    parser.add_argument('--ui-port',type=int,default=18514)
    args=parser.parse_args()
    try:
        if args.stop:
            value=json.loads(RECORD.read_text(encoding='utf-8')) if RECORD.exists() else {}
            if value.get('session'):
                STOP_REQUEST.write_text(json.dumps({'session':value['session']}),encoding='utf-8')
            items = value.get('children',[])
            # 先由拥有Popen句柄的启动器退出，避免两次taskkill争抢同一进程树。
            for _ in range(30):
                if not any(record_alive(item) for item in items):break
                time.sleep(.2)
            stop_records(items)  # 启动器已退出/崩溃时，仍可按身份回收本次服务。
            if any(record_alive(item) for item in items):
                raise RuntimeError('Real-mode services are still stopping.')
            print('Real-mode stop completed; records and logs retained.')
            return 0
        result=check_config(args.model)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if not result['ready']:return 2
        if args.check_only:return 0
        serve(args)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # 不回显第三方异常/环境，避免包含凭据；日志仅包含本机服务诊断。
        print('Real-mode startup failed. Check local configuration, ports and .local/real_api session logs.',file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
