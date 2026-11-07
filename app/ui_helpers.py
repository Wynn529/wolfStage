"""事件投影、关联和本地只读证据访问。"""
from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

LABELS = {'role_claim': '身份声明', 'check_claim': '查验宣称', 'vote_intent': '投票意向',
          'low_info': '信息不足', 'discussion': '有效讨论', 'suspected_violation': '疑似违规/跑题'}
ROLES = {'wolf': '狼人', 'seer': '预言家', 'witch': '女巫', 'hunter': '猎人', 'villager': '平民'}
STATUS = {'running': '可单步推进', 'paused': '已暂停', 'failed': '失败',
          'finished': '已结束', 'incomplete': '达到上限/未完成', 'unknown': '待刷新确认',
          'pending':'正在等待模型', 'waiting_human':'等待真人行动', 'waiting_model':'正在等待模型', 'degraded':'降级，需人工核查'}
PHASES = {'night': '夜间', 'day_speak': '白天发言', 'day_vote': '白天投票'}


def merge_events(existing, incoming, game_id):
    """允许重复增量，拒绝跨局污染与同号冲突。"""
    by_id = {}
    for event in [*existing, *incoming]:
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise ValueError('事件必须是带type的对象')
        if event.get('game_id') != game_id:
            raise ValueError('返回事件的 game_id 与当前局不一致')
        value = event['event_id']
        if type(value) is not int and not (isinstance(value, str) and value.isdecimal()):
            raise ValueError('event_id必须是整数编号')
        number = int(value)
        if number <= 0:
            raise ValueError('event_id 必须是正整数')
        if number in by_id and by_id[number] != event:
            raise ValueError(f'event_id={number} 出现冲突内容')
        by_id[number] = event
    return [by_id[key] for key in sorted(by_id)]


def project(events):
    # 真人局进行中的事件会按权限过滤，首条 game_created（包含完整 alive）
    # 可能不可见。默认按十人规则初始化存活席位，避免仅凭公开事件把所有人画成出局。
    state = {'roles': {}, 'alive': set(range(1, 11)), 'winner': None, 'status': 'unknown', 'classifications': {}}
    for event in events:
        kind = event['type']
        if kind == 'game_created':
            state.update(roles={int(k): v for k, v in event.get('roles', {}).items()},
                         alive=set(event.get('alive', range(1, 11))), status='running')
        elif kind in {'night_death', 'exile'}:
            state['alive'].discard(event.get('target'))
        elif kind == 'utterance_classification':
            state['classifications'][str(event['source_event_id'])] = event['result']
        elif kind == 'game_over':
            state.update(winner=event.get('winner'), status='finished')
        elif kind == 'game_limit':
            state['status'] = 'incomplete'
        elif kind == 'model_failure':
            state['status'] = 'failed'
        elif kind == 'game_paused':
            state['status'] = 'paused'
        elif kind == 'game_resumed':
            state['status'] = 'running'
    return state


def local_games(root: Path):
    if not root.exists():
        return []
    return sorted([p.name for p in root.iterdir() if p.is_dir() and (p / 'events.jsonl').is_file() and replay_allowed(root, p.name)], reverse=True)


def game_dir(root: Path, game_id: str):
    base = root.resolve()
    path = (base / game_id).resolve()
    if path.parent != base or not path.is_dir():
        raise ValueError('对局目录无效')
    return path


def replay_allowed(root: Path, game_id: str) -> bool:
    """Inspect access metadata server-side; never return the secret event stream."""
    try:
        folder = game_dir(root, game_id)
        human = False; initialized = False; ended = False
        with (folder / 'events.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                if not line.strip(): continue
                event = json.loads(line)
                if event.get('type') == 'game_created':
                    initialized = True
                    human = any(v == 'human' for v in (event.get('seat_controllers') or {}).values())
                elif event.get('type') in {'game_over', 'game_limit'}:
                    ended = True
        return initialized and (not human or ended)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def require_replay_access(root: Path, game_id: str):
    if not replay_allowed(root, game_id):
        raise ValueError('真人对局尚未结束或权限无法确认，不能读取上帝回放和原始导出')


def read_events(root: Path, game_id: str):
    require_replay_access(root, game_id)
    path = game_dir(root, game_id) / 'events.jsonl'
    values = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return merge_events([], values, game_id)


def read_state_snapshot(root: Path, game_id: str):
    """读取本地B状态快照，仅用于主持界面展示，不参与控制。"""
    path = game_dir(root, game_id) / 'state.json'
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    return value if isinstance(value, dict) and value.get('game_id') == game_id else None


def evidence_zip(root: Path, game_id: str):
    """只拷贝 B 原始文件；不读取 state.json 或改写原件。"""
    require_replay_access(root, game_id)
    folder = game_dir(root, game_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ('events.jsonl', 'calls.jsonl', 'errors.jsonl', 'summary.json'):
            path = folder / name
            if path.is_file():
                archive.writestr(f'{game_id}/{name}', path.read_bytes())
    return buffer.getvalue()
