"""C只读圆桌场景数据投影；不触发模型或控制请求。"""
from __future__ import annotations
from pathlib import Path
from app.ui_helpers import project, LABELS, ROLES
from app.ui_presenter import describe_public, EVENT_NAMES, WINNERS
import streamlit.components.v1 as components

_SCENE_COMPONENT = components.declare_component('c_robot_scene', path=str(Path(__file__).parent/'scene'))

def scene_model(events, *, status='unknown', phase=None, metrics=None, game_id=None,
                replay=False, busy=False, message=''):
    state = project(events)
    latest = events[-1] if events else {}
    phase = phase or latest.get('phase') or 'night'
    current = next((e for e in reversed(events) if e['type'] not in
                    {'utterance_classification','game_paused','game_resumed'}), {})
    speech = next((e for e in reversed(events) if e['type']=='public_speech'), None)
    label = state['classifications'].get(str(speech['event_id'])) if speech else None
    kind = current.get('type', '')
    title = EVENT_NAMES.get(kind, kind or '等待开局')
    body = describe_public(current) if current else None
    if kind == 'public_speech':
        body = current.get('text') or '发言内容未提供'
    elif kind == 'wolf_proposal':
        body = f'我提议今晚选择 {current.get("target", "未提供")} 号作为击杀目标。'
    elif kind == 'inspection':
        role = ROLES.get(current.get('result'), current.get('result', '未提供'))
        body = f'我查验了 {current.get("target", "未提供")} 号，结果是：{role}。'
    elif kind == 'game_created':
        body = '身份已分配。六名机器人准备就绪，点击“单步推进”观察第一步。'
    elif kind in {'model_failure','utterance_classification_failed'}:
        body = '本次调用未完成，已停止推进。请展开检查记录查看原因。'
    if not body:
        body = '点击“开始对局”，看六名机器人围桌推理。' if not events else '等待下一条服务记录。'
    actor, target = current.get('actor'), current.get('target')
    if type(actor) is not int or actor not in range(1,7): actor = None
    if type(target) is not int or target not in range(1,7): target = None
    source = '离线联调' if any(e.get('source')=='stub' for e in events) else '服务记录' if events else '准备开始'
    return {
        'game_id':game_id or '', 'cursor':latest.get('event_id',0), 'day':latest.get('day',0),
        'night':phase=='night', 'phase':phase, 'status':status,
        'replay':replay, 'busy':busy, 'message':message, 'source':source,
        'players':[{'id':i,'role':state['roles'].get(i),
                    'role_name':ROLES.get(state['roles'].get(i),'等待发牌'),
                    'alive':i in state['alive'] if events else True} for i in range(1,7)],
        'current':{'id':current.get('event_id',0),'kind':kind,'title':title,'body':body,
                   'actor':actor,'target':target,'speech':kind=='public_speech',
                   'private':bool(current and current.get('visibility')!='public'),
                   'reason':current.get('reason') or ''},
        'analysis':{'speech':speech.get('text') if speech else '',
                    'speaker':speech.get('actor') if speech else None,
                    'event_id':speech.get('event_id') if speech else None,
                    'intent':LABELS.get((label or {}).get('intent'),(label or {}).get('intent','等待分类')),
                    'classified':label is not None, 'entities':(label or {}).get('extracted_entities') or {},
                    'model':(label or {}).get('model_version',''),
                    'entity_version':(label or {}).get('entity_rule_version',''),
                    'confidence':'未校准，不显示概率',
                    'evidence_note':'分类结果已记录；是否进入后续真实请求，需结合B的输入证据核验。'},
        'winner':WINNERS.get(state['winner'],state['winner']) if state['winner'] else '',
        'metrics':metrics or {},
    }

def render_scene(events, **kwargs):
    _SCENE_COMPONENT(model=scene_model(events,**kwargs), key='c_robot_scene', default=None)
