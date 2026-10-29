"""从B原始calls只读重算，缺失用量不伪装为0。"""
import json
from app.ui_helpers import game_dir


def read_calls(root, game_id):
    path = game_dir(root, game_id) / 'calls.jsonl'
    if not path.exists():
        return None
    calls = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if any(not isinstance(c, dict) or c.get('game_id') != game_id for c in calls):
        raise ValueError('调用日志包含错误局号或无效记录')
    return calls


def summarize_calls(calls, metrics):
    if calls is None:
        return {'rows': [], 'differences': [], 'notice':'没有本机calls原件：服务汇总未经C重算核对', 'usage':{}}
    groups = {kind:{'caller_type':kind, 'attempts':0, 'first_attempts':0, 'retries':0,
                    'success':0, 'failed':0, 'fallback':0} for kind in ('player','host','small_model','reviewer')}
    usage = {key:{'known_sum':0, 'missing':0, 'not_applicable':0, 'known_count':0}
             for key in ('input_tokens','output_tokens')}
    for call in calls:
        kind = call.get('caller_type', 'unknown')
        row = groups.setdefault(kind, {'caller_type':kind, 'attempts':0, 'first_attempts':0, 'retries':0,
                                       'success':0, 'failed':0, 'fallback':0})
        attempt = call.get('attempt', 1)
        if type(attempt) is not int or attempt < 1 or type(call.get('success')) is not bool:
            raise ValueError('calls中的attempt/success无效，不能生成可信统计')
        row['attempts'] += 1
        row['first_attempts'] += attempt == 1
        row['retries'] += attempt > 1
        row['success' if call['success'] else 'failed'] += 1
        row['fallback'] += call.get('fallback') is True
        na = kind == 'small_model' or call.get('model_name') == 'stub'
        for key, info in usage.items():
            value = call.get(key)
            if na:
                info['not_applicable'] += 1
            elif type(value) is int and value >= 0:
                info['known_sum'] += value
                info['known_count'] += 1
            else:
                info['missing'] += 1
    totals = {'call_count':len(calls), 'successful_calls':sum(c['success'] for c in calls),
              'failed_calls':sum(not c['success'] for c in calls),
              'retries':sum(c.get('attempt',1)>1 for c in calls)}
    differences = [f'{key}：B={metrics[key]}，C原始记录重算={value}'
                   for key,value in totals.items() if key in metrics and metrics[key] != value]
    for key, info in usage.items():
        if info['missing']:
            differences.append(f'{key}有{info["missing"]}次API调用缺失用量；B汇总值不能当完整用量')
        elif info['known_count'] and key in metrics and metrics[key] != info['known_sum']:
            differences.append(f'{key}：B={metrics[key]}，C已知原始用量={info["known_sum"]}')
        info['display'] = ('未提供' if info['missing'] and not info['known_count'] else
                           f'已知部分 {info["known_sum"]}' if info['missing'] else
                           str(info['known_sum']) if info['known_count'] else '不适用（本地模型/离线玩家）')
    return {'rows':list(groups.values()), 'usage':usage, 'totals':totals, 'differences':differences,
            'notice':'按原始调用尝试逐条计数；不改B原件，不推断角色决策正确率，费用未计算'}


def display_call_rows(rows):
    """中文展示副本；不改统计值或原始calls。"""
    names = {'player':'玩家大模型', 'host':'主持大模型', 'small_model':'小模型分类', 'reviewer':'评审模型'}
    columns = {'caller_type':'调用对象', 'attempts':'调用尝试（含重试）', 'first_attempts':'首次尝试',
               'retries':'重试尝试', 'success':'B记录通过', 'failed':'B记录失败', 'fallback':'回退尝试'}
    return [{label:(names.get(row[key],row[key]) if key=='caller_type' else row[key])
             for key,label in columns.items()} for row in rows]
