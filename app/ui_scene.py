"""C只读圆桌场景数据投影；不触发模型或控制请求。"""
from __future__ import annotations
from pathlib import Path
from app.ui_helpers import project, LABELS, ROLES
from app.ui_presenter import describe_public, EVENT_NAMES, WINNERS, failure_notice
import streamlit.components.v1 as components

_SCENE_COMPONENT = components.declare_component('c_robot_scene', path=str(Path(__file__).parent/'scene'))

def scene_model(events, *, status='unknown', phase=None, metrics=None, game_id=None,
                replay=False, busy=False, message='', cursor=None, human_player_id=None):
    state = project(events)
    latest = events[-1] if events else {}
    phase = phase or latest.get('phase') or 'night'
    ignored = {'utterance_classification','agent_reason','game_paused','game_resumed'}
    if phase == 'night':
        latest_day = max((e.get('day', 0) for e in events), default=0)
        night_events = [e for e in events if e.get('phase') == 'night' and e.get('day', 0) == latest_day]
        current = next((e for e in reversed(night_events) if e['type'] not in ignored), {})
        if not current:
            current = {'event_id': latest.get('event_id', 0), 'type': 'night_waiting',
                       'phase': 'night', 'day': latest_day, 'visibility': 'public'}
            waiting = next((e for e in reversed(events)
                            if e.get('type') == 'waiting_human' and e.get('phase') == 'night'), None)
            if waiting:
                current['actor'] = waiting.get('actor')
    else:
        current = next((e for e in reversed(events) if e['type'] not in ignored), {})
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
    elif kind == 'wolf_discussion':
        body = f'狼队讨论：{current.get("text", "")}'
    elif kind in {'night_waiting', 'waiting_human'} and phase == 'night':
        actor = current.get('actor')
        body = f'{actor}号正在进行夜间行动。' if isinstance(actor, int) else '夜间行动进行中，等待行动结果。'
    elif kind == 'game_created':
        body = '身份已分配。十个席位准备就绪，点击“单步推进”观察第一步。'
    elif kind in {'model_failure','utterance_classification_failed'}:
        notice = failure_notice(current)
        title = notice['title']
        body = notice['summary'] + ' ' + notice['next_step']
    visible_cursor = cursor if isinstance(cursor, int) else (latest.get('event_id', 0) if latest else 0)
    if not body:
        if not events and game_id and visible_cursor:
            calls = metrics.get('successful_calls') if isinstance(metrics, dict) else None
            call_text = f'已确认 {calls} 次模型调用；' if isinstance(calls, int) else ''
            body = f'对局已推进到服务端事件 {visible_cursor}；{call_text}当前行动属于真人不可见的私密阶段。可继续单步推进。'
        else:
            body = '点击“开始对局”，看十个席位围桌推理。' if not events else '等待下一条服务记录。'
    if kind == 'wolf_negotiation':
        body = f'狼人意见不同，B按固定规则选择 {current.get("resolved_target", "未提供")} 号。'
    reason_event = next((e for e in reversed(events) if e.get('type')=='agent_reason'
                         and e.get('source_event_id')==current.get('event_id')
                         and e.get('actor')==current.get('actor')), {})
    actor, target = current.get('actor'), current.get('target')
    if type(actor) is not int or actor not in range(1,11): actor = None
    if type(target) is not int or target not in range(1,11): target = None
    source = '离线联调' if any(e.get('source')=='stub' for e in events) else '真实模型记录' if any(e.get('source')=='deepseek' for e in events) else '服务记录' if events else '准备开始'
    discussion_event = next((e for e in reversed(events) if e.get('type') == 'wolf_discussion'), None)
    return {
        'game_id':game_id or '', 'cursor':visible_cursor, 'day':latest.get('day',0),
        'night':phase=='night', 'phase':phase, 'status':status,
        'replay':replay, 'busy':busy, 'message':message, 'source':source,
        'players':[{'id':i,'role':state['roles'].get(i),
                    'role_name':ROLES.get(state['roles'].get(i),'玩家'),
                    'is_human':i == human_player_id,
                    'alive':i in state['alive'] if events else True} for i in (sorted(state['roles']) if state['roles'] else range(1,11))],
        'current':{'id':current.get('event_id',0),'kind':kind,'title':title,'body':body,
                   'actor':actor,'target':target,'speech':kind=='public_speech',
                   'private':bool(current and current.get('visibility')!='public'),
                   'reason':reason_event.get('reason') or current.get('reason') or ''},
        'wolf_discussion':({'actor': discussion_event.get('actor'), 'text': discussion_event.get('text', '')}
                           if discussion_event and isinstance(discussion_event.get('text'), str) else None),
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
        'metrics':metrics or {}, 'human_player_id': human_player_id,
    }

def render_scene(events, *, instance_key='', **kwargs):
    _SCENE_COMPONENT(model=scene_model(events,**kwargs), key='c_robot_scene'+instance_key, default=None)
