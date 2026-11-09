"""C的只读展示：解释已有事件，不计算票数、合法性或胜负。"""
from __future__ import annotations

WINNERS = {'wolves': '狼人胜', 'villagers': '好人胜'}
EVENT_NAMES = {
    'game_created': '建立对局', 'night_death': '天亮死亡公告',
    'public_speech': '公开发言', 'utterance_classification': '发言分类',
    'vote': '投票', 'vote_open': '秘密投票', 'vote_received': '收到密封选票',
    'vote_tally': '计票结果', 'exile': '放逐公告',
    'game_over': '对局结束', 'game_limit': '达到对局上限',
    'game_paused': '暂停记录', 'game_resumed': '继续记录', 'night_waiting': '夜间行动',
    'agent_reason': '角色私密自述', 'wolf_negotiation': '狼人分歧裁决', 'wolf_discussion': '狼队讨论',
    'witch_save': '女巫使用解药', 'witch_poison': '女巫使用毒药',
    'hunter_triggered': '猎人触发', 'hunter_shot': '猎人开枪', 'hunter_pass': '猎人放弃开枪',
    'last_words': '遗言', 'waiting_human': '等待真人行动', 'human_action_submitted': '真人提交行动',
    'model_fallback': '规则兜底行动',
    'wolf_proposal': '狼人击杀提案', 'inspection': '预言家查验',
    'model_failure': '模型调用失败',
    'model_retry_started': '重试模型行动',
    'model_retry_interrupted': '重试期间服务中断',
    'model_step_interrupted': '模型请求期间服务中断',
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
    if kind in {'vote_open', 'vote_received'}:
        return '正在秘密投票。收齐选票后统一公布结果。'
    if kind == 'model_retry_started':
        return '正在重试超时的模型行动。'
    if kind in {'model_retry_interrupted', 'model_step_interrupted'}:
        return '服务中断，模型结果未入账。'
    if kind == 'vote_tally':
        resolution = event.get('resolution')
        if resolution == 'exile':
            return f'{player(event.get("target"))}被放逐。弃权 {event["abstentions"]} 人。'
        if resolution in {'revote', 'no_exile'}:
            if resolution == 'revote':
                result = '最高票平票，进入重投。'
            elif any(event['counts'].values()):
                result = '最高票平票，无人出局。'
            else:
                result = '全员弃权，无人出局。'
            return f'{result}弃权 {event["abstentions"]} 人。'
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
    if event and event.get('type') in {'model_retry_interrupted', 'model_step_interrupted'}:
        return {'title': EVENT_NAMES[event['type']], 'summary': '模型响应未入账，上一次请求可能已产生费用。',
                'next_step': '可以手动重试当前席位，保留同一局和此前已入账的行动。', 'raw': ''}
    if not event or event.get('type') not in {'model_failure', 'utterance_classification_failed'}:
        return None
    raw = str(event.get('error') or event.get('validation_error') or '')
    result = {'title':'本步未能完成',
              'summary':'当前记录不足以确认失败发生在哪一环，请查看原始错误。',
              'next_step':'保留本局记录，核对后再复测；不要反复点击推进。', 'raw':raw}
    if event.get('request_timeout') is True:
        return dict(result, title='模型请求超时', summary='当前席位的行动尚未入账。',
                    next_step='现场可用“重试当前席位”时，可在原局重试这一行动。')
    if event['type'] == 'utterance_classification_failed':
        result.update(title='小模型分类未完成', summary='这条发言没有取得可用分类结果，不能当作已分类。',
                      next_step='原句已保留，对局继续推进；请A/B稍后核对分类服务与接口。')
    elif ((raw.startswith('action ') and 'is not allowed in phase' in raw)
          or raw in {'action must be an object', 'action must be a string', 'vote target is required',
                     'target must be an integer',
                     'target must be alive player 1..6', 'self target is not allowed',
                     'only wolves can kill', 'only seer can inspect', 'speak target must be null',
                     'speak text must not be empty',
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
    elif 'budget exhausted' in raw.lower() or 'request budget' in raw.lower():
        result.update(title='受控模型预算已用尽',
                      summary='本次没有发出新的模型请求，当前受控请求预算或次数上限已达到。',
                      next_step='重启B真实服务后新建对局；费用仍受已授权金额上限保护。')
    return result


def latest_failure(events):
    """返回会阻断现场控制的失败；分类失败只影响标注，不阻断对局。"""
    latest = next((e for e in reversed(events)
                   if e.get('type') in {'model_failure', 'model_retry_started', 'model_retry_interrupted',
                                        'model_step_interrupted'}), None)
    return failure_notice(latest)
