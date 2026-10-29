"""C的只读展示：解释已有事件，不计算票数、合法性或胜负。"""
from __future__ import annotations

WINNERS = {'wolves': '狼人胜', 'villagers': '好人胜'}
EVENT_NAMES = {
    'game_created': '建立对局', 'night_death': '天亮死亡公告',
    'public_speech': '公开发言', 'utterance_classification': '发言分类',
    'vote': '投票', 'vote_tally': '计票结果', 'exile': '放逐公告',
    'game_over': '对局结束', 'game_limit': '达到对局上限',
    'game_paused': '暂停记录', 'game_resumed': '继续记录',
    'agent_reason': '角色私密自述', 'wolf_negotiation': '狼人分歧裁决',
    'wolf_proposal': '狼人击杀提案', 'inspection': '预言家查验',
    'model_failure': '模型调用失败',
    'utterance_classification_failed': '发言分类失败',
}


def player(value):
    return f'{value} 号' if value is not None else '目标未提供'


def vote_validity(value):
    if value is True:
        return '有效（B标记）'
    if value is False:
        return '无效／弃权（B未细分）'
    return '有效性未提供'


def describe_public(event):
    """公开区不会将完整未知对象或reason改写进说明。"""
    kind = event['type']
    if event.get('visibility') != 'public':
        return None
    if kind == 'vote':
        target = player(event.get('target'))
        return f'{player(event.get("actor"))} → {target}；{vote_validity(event.get("valid"))}'
    if kind == 'vote_tally':
        round_name = '重投' if event.get('revote') is True else '首轮' if event.get('revote') is False else '轮次未提供'
        target = event.get('target')
        result = f'计票目标：{player(target)}' if target is not None else '本次未产生唯一目标'
        return f'{round_name}计票；{result}。是否放逐以放逐公告为准。'
    if kind == 'night_death':
        return f'{player(event.get("target"))}在夜间死亡。'
    if kind == 'exile':
        return f'{player(event.get("target"))}被放逐。'
    if kind == 'game_over':
        winner = event.get('winner')
        return WINNERS.get(winner, '胜方未提供' if winner is None else f'服务返回胜方：{winner}')
    if kind == 'game_limit':
        return '已达到服务对局上限，本局未完成；不计为任一阵营获胜。'
    return None


def tally_rows(event):
    """不从逐票记录推测缺失票数，保留B提供的目标与数量。"""
    counts = event.get('counts')
    if not isinstance(counts, dict):
        return None
    return [{'目标': player(target), '票数（B原件）': count} for target, count in counts.items()]


def vote_rounds(events):
    """只分组当前事件前缀；每个计票事件关闭一组，不提前拉后续事件。"""
    groups = []
    current = None
    for event in events:
        if event.get('visibility') != 'public' or event.get('type') not in {'vote', 'vote_tally'}:
            continue
        if current is None or current['day'] != event.get('day'):
            current = {'day': event.get('day'), 'votes': [], 'tally': None}
            groups.append(current)
        if event['type'] == 'vote':
            current['votes'].append(event)
        else:
            current['tally'] = event
            current = None
    return groups


def failure_notice(event):
    """解释已记录的错误；不把未知失败推断成网络或模型内容错误。"""
    if not event or event.get('type') not in {'model_failure', 'utterance_classification_failed'}:
        return None
    raw = str(event.get('error') or event.get('validation_error') or '')
    result = {'title':'本步未能完成',
              'summary':'当前记录不足以确认失败发生在哪一环，请查看原始错误。',
              'next_step':'保留本局记录，核对后再复测；不要反复点击推进。', 'raw':raw}
    if event['type'] == 'utterance_classification_failed':
        result.update(title='小模型分类未完成', summary='这条发言没有取得可用分类结果，不能当作已分类。',
                      next_step='保留原句和错误记录，请A/B核对分类服务与接口后复测。')
    elif ((raw.startswith('action ') and 'is not allowed in phase' in raw)
          or raw in {'action must be an object', 'target must be an integer',
                     'target must be alive player 1..6', 'self target is not allowed',
                     'only wolves can kill', 'only seer can inspect', 'speak target must be null',
                     'text/reason must be strings within length limits'}):
        result.update(title='行动格式未通过规则检查',
                      summary='模型已返回内容，但行动名称、目标或字段不符合当前游戏协议。',
                      next_step='本局已停止。请B核对提示词与行动协议，修复后用新局复测。')
        if raw.startswith("action 'check' is not allowed"):
            result['summary']='模型返回了“check”，当前规则不接受这个行动名称；请求已返回，行动未通过检查。'
    elif any(value in raw.lower() for value in ('timed out', 'timeout')):
        result.update(title='模型请求超时，结果待核对', summary='等待返回超过时限；不能据此认定服务端没有执行。',
                      next_step='先核对操作状态与原始记录，不要重复发送这一步。')
    elif any(value in raw.lower() for value in ('401', 'unauthorized', '402', 'insufficient balance')):
        result.update(title='模型服务拒绝了请求', summary='请在本机核对API密钥或账户额度，具体原因见原始错误。')
    return result


def latest_failure(events):
    return next((failure_notice(e) for e in reversed(events)
                 if e.get('type') in {'model_failure','utterance_classification_failed'}), None)
