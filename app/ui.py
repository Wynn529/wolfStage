"""C上帝视角：兼容旧B；增强自动控制由已确认契约启用。"""
from __future__ import annotations

import json
import html
import inspect
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from app.ui_client import GameClient, BRevisionClient, ServiceError
from app.ui_integration_client import IntegrationClient
from app.ui_contract import ServiceContract, TERMINAL
from app.ui_controller import SessionController
from app.ui_helpers import (LABELS, ROLES, STATUS, PHASES, project,
                            local_games, read_events, replay_allowed, evidence_zip)
from app.ui_metrics import read_calls, summarize_calls, display_call_rows, display_call_rows_by_seat
from app.ui_scene import render_scene
from app.ui_presenter import EVENT_NAMES, WINNERS, describe_public, player as player_label, tally_rows, vote_rounds, vote_validity, latest_failure
from app.rules import assign_roles

st.set_page_config(page_title='狼人杀', page_icon='🐺', layout='wide', initial_sidebar_state='collapsed')
st.markdown("""
<style>
[data-testid="stMainBlockContainer"]{padding:1.1rem 1.6rem 2rem;max-width:1480px}
[data-testid="stHeader"]{background:transparent;height:2.1rem}
[data-testid="stToolbar"]{display:none}
[data-testid="stMainBlockContainer"]>div{gap:.55rem}
.stButton>button{border-radius:12px;min-height:37px;font-size:12px;border-color:#d9e1d5;background:#fffef9;color:#526750}
.stButton>button[kind="primary"]{background:#326a50;color:white;border-color:#326a50}
.brand{display:flex;align-items:center;justify-content:space-between;margin:0 0 5px}
.brand h1{font-size:24px;letter-spacing:2px;font-weight:700;margin:0;padding:0;color:inherit}
.brand h1 span{font-size:11px;letter-spacing:2px;opacity:.45;font-weight:500;margin-left:12px}
.brand p{font-size:11px;margin:0;opacity:.6}
[data-testid="stSidebar"]{background:#172a3a;color:#d5e3e8}
.stButton>button:disabled{color:#839486!important;border-color:#a7b3a655!important;background:transparent!important}
.stButton>button:hover{border-color:#61947b;color:#326a50}
.stButton>button:focus:not(:active){border-color:#61947b;box-shadow:0 0 0 .1rem #61947b33}
@media(max-width:800px){[data-testid="stMainBlockContainer"]{padding:1rem .7rem}.brand p{display:none}.brand h1{font-size:20px}}
</style><div class="brand"><h1>狼人杀</h1><p>十人协同 · AI 与真人共同推理</p></div>
""",unsafe_allow_html=True)
ss = st.session_state
st.markdown('<style>' + (ROOT/'app/ui_sidebar.css').read_text(encoding='utf-8') + '</style>', unsafe_allow_html=True)
if 'controller' not in ss:
    ss.controller = None
    ss.create_error = ''
    ss.create_uncertain = False
if 'events' not in ss:
    ss.events = []
    ss.game_id = None

runs = Path(os.getenv('C_RUNS_DIR', str(ROOT / 'runs')))
journals = Path(os.getenv('C_JOURNAL_DIR', str(ROOT / '.local/c_operations')))
real_mode = os.getenv('C_LIVE_MODE') == '1'
integration_mode = os.getenv('C_SERVICE_MODE') == 'integration'
Client = IntegrationClient if integration_mode else BRevisionClient if os.getenv('C_B_REVISION')=='3898b95' else GameClient

def make_client(address):
    if integration_mode:
        existing = ss.get('integration_client')
        active = ss.get('controller')
        active_client = active.client if active else None
        if (getattr(active_client, 'integration', False) and active_client.base_url == address.rstrip('/')
                and getattr(active_client, '_sessions', {}) and not getattr(existing, '_sessions', {})):
            existing = active_client
        if getattr(existing, 'integration', False) and existing.base_url == address.rstrip('/'):
            if isinstance(existing, IntegrationClient):
                return existing
            refreshed = IntegrationClient(address, contract=contract)
            for field in ('_sessions', '_human_seats', 'seat_models', 'seat_labels', '_profiles', 'own_roles'):
                setattr(refreshed, field, dict(getattr(existing, field, {})))
            ss.integration_client = refreshed
            return refreshed
        existing = IntegrationClient(address, contract=contract)
        ss.integration_client = existing
        return existing
    return Client(address, contract=contract)
try:
    contract = ServiceContract.load(os.getenv('C_SERVICE_CONTRACT'))
except (OSError, TypeError, ValueError) as exc:
    st.error('增强契约加载失败，自动控制已锁定：' + str(exc))
    st.stop()

mode = st.radio('观看方式', ['现场对局', '历史回放'],index=1 if st.query_params.get('view')=='history' else 0,horizontal=True,label_visibility='collapsed')

if mode=='历史回放':
    st.info('历史回放：查看已保存的过程，播放或移动进度条都不会重新调用模型，也不会产生新的API费用。')
elif real_mode:
    st.info('真实模型模式：建局只分配身份；单步推进才可能调用付费模型。一次点击可能含多次请求。')
if integration_mode and os.getenv('C_OFFLINE_PREVIEW') == '1':
    st.warning('九模型接口离线联调：名牌是配置型号，行动由固定测试替身产生；未发送真实模型请求，不产生API费用。')

with st.expander('第一次使用：怎么看、怎么操作', expanded=False):
    st.markdown('1．开始前：选“现场对局”，确认测试额度，再点“开始对局”分配十个身份。')
    st.markdown('2．看一局：每点一次“单步推进”，等待处理完成。上方看行动、发言与出局，下方看小模型对公开发言的分类；夜间等待发言属于正常情况。')
    st.markdown('3．查过程：展开“检查记录”查看投票、私密记录和统计；切到“历史回放”可查看已保存过程，不会重新调用模型。')
    st.caption('你是观察者，不需要替机器人发言或投票。身份卡和私密信息只供上帝视角查看；分类只判断话语表达，不证明身份真假。')
    st.caption('等待是状态，不需要点击。暂停会等当前一步完成，不能取消已发出的请求；继续只解除暂停，不会自行开始下一步。')

with st.sidebar:
    st.header('运行与回放')
    seed = st.number_input('随机种子 seed',min_value=0,max_value=2147483647,value=42,step=1,
                           help='相同 seed 会复现同一局；更换 seed 可改变身份和首夜目标。')
    play_mode = st.radio('对局模式', ['全 AI', '真人参与'], horizontal=True)
    human_seat = st.selectbox('真人席位', list(range(1, 11)), disabled=play_mode == '全 AI')
    human_role_choice = st.selectbox('真人身份（新局生效）', ['random', 'wolf', 'seer', 'witch', 'hunter', 'villager'],
        format_func=lambda value: '随机分配' if value == 'random' else ROLES[value],
        disabled=play_mode == '全 AI', help='选择后点击“开始对局”新建一局；不会改变已经开始的对局身份。')
    human_role_mode = '随机' if human_role_choice == 'random' else '指定'
    human_role = human_role_choice if human_role_mode == '指定' else None
    human_seats = [human_seat] if play_mode == '真人参与' else []
    if play_mode == '真人参与' and human_role_mode == '随机':
        st.caption('随机模式：真人席位从完整十人角色池随机分配，不固定为平民。')
    url = st.text_input('B 服务地址', os.getenv('B_API_URL', 'http://127.0.0.1:18083' if integration_mode else 'http://127.0.0.1:8000'))
    profiles = []
    if integration_mode:
        try:
            profiles = make_client(url).models()
        except (ServiceError, ValueError) as exc:
            st.error('无法读取新版服务模型列表：' + str(exc))
            st.stop()
        configured_models = [p['profile_id'] for p in profiles]
        profile_names = {p['profile_id']: f"{p['display_name']} · {p['provider']}" for p in profiles}
        st.caption(f'已登记 {len(profiles)} 个型号；登记不等于本局已成功调用。')
    else:
        configured_models = [item.strip() for item in os.getenv('MODEL_LIST', os.getenv('DEEPSEEK_MODEL', 'default-model')).split(',') if item.strip()]
        profile_names = {}
    model_name = st.selectbox('AI 默认模型', configured_models, format_func=lambda value: profile_names.get(value, value), help='统一设置全部 AI；重复型号合法。')
    assignment = st.radio('模型分配', ['统一后逐席调整', '按登记列表轮流分配']) if integration_mode else '统一后逐席调整'
    with st.expander('逐席位模型设置', expanded=False):
        seat_models = {}
        for index, i in enumerate(i for i in range(1, 11) if i not in human_seats):
            if integration_mode:
                default = configured_models[index % len(configured_models)] if assignment == '按登记列表轮流分配' else model_name
                seat_models[str(i)] = st.selectbox(f'{i} 号模型', configured_models, index=configured_models.index(default),
                    format_func=lambda value: profile_names[value], key=f'profile_{i}_{assignment}_{model_name}_{human_seats}')
            else:
                seat_models[str(i)] = st.text_input(f'{i} 号模型', model_name, key=f'model_{i}')
    st.caption('默认端口：A分类8765、B游戏8000、网页8501；自定义时以启动输出及B地址为准。')
    st.caption('B适配：九模型整合服务 · 真人会话鉴权' if integration_mode else 'B适配：3898b95（部分能力）' if Client is BRevisionClient else 'B适配：旧版兼容')
    st.caption(f'契约：{contract.version}；确认人：{contract.confirmed_by or "尚无增强能力确认"}')
    if not contract.ready:
        if issubclass(Client, BRevisionClient):
            st.caption('逐步自动运行：每步确认后继续。暂停会等待本步结束；请在一个页面控制当前局。')
        else:
            st.warning('旧版服务尚未确认操作查询，当前仅开放单步；请使用新版联调启动脚本。')
    record_files = sorted(journals.glob('*.jsonl')) if journals.exists() else []
    if record_files:
        record_path = st.selectbox('本机控制记录', record_files, format_func=lambda p:p.stem)
        if st.button('恢复记录（只读查询，不重发）'):
            try:
                if ss.controller and ss.controller.busy:
                    raise ValueError('当前仍有待确认操作，先核对当前局')
                if ss.controller:
                    ss.controller.close()
                ss.controller = SessionController.restore(make_client(url), record_path)
            except (ServiceError, ValueError, OSError, KeyError) as exc:
                st.error(str(exc))

try:
    client = make_client(url)
    if integration_mode and ss.controller and ss.controller.client.base_url == client.base_url:
        ss.controller.client = client
except ValueError as exc:
    st.error(str(exc))
    st.stop()


def render_details(events, status, phase, metrics, game_id, live):
    if not events:
        st.caption('当前游标尚无事件。')
        return
    state = project(events)
    controller = ss.controller if live else None
    human_view = bool(controller and controller.human_game)
    ids = [int(e['event_id']) for e in events]
    if ids != list(range(1, max(ids)+1)) and not human_view:
        st.error('事件编号不连续：页面不会补造事件，请核对B原始记录。')
    sources = sorted({str(e.get('source', 'unknown')) for e in events})
    st.caption('事件来源：' + ' / '.join(sources) + '；含stub的局不能作为全真实API对局证据。')
    cols = st.columns(4)
    cols[0].metric('对局状态', STATUS.get(status, status))
    cols[1].metric('阶段', PHASES.get(phase, str(phase or '按事件回放')))
    cols[2].metric('存活人数', len(state['alive']))
    cols[3].metric('最后显示 event_id', max(ids))
    role_source = state['roles']
    player_count = len(role_source) if isinstance(role_source, dict) and role_source else (
        10 if any(event.get('rules_version') == 'rules-v2.0-ten' for event in events) else 6)
    cards = st.columns(5)
    for player in range(1, player_count + 1):
        card = cards[(player - 1) % len(cards)]
        with card:
            st.subheader(f'{player} 号')
            role = role_source.get(str(player), role_source.get(player)) if isinstance(role_source, dict) else None
            st.write(ROLES.get(role or state['roles'].get(player), '身份未提供'))
            st.caption('存活' if player in state['alive'] else '已出局')
    if state['winner']:
        st.success('终局：' + WINNERS.get(state['winner'],state['winner']))
    elif state['status'] == 'incomplete':
        st.warning('本局达到服务上限，未完成；没有已确认胜方。')
    public_tab, vote_tab, private_tab, stats_tab = st.tabs(['公开事件与分类', '投票与结果', '仅上帝视角', '统计与原始事件'])
    with public_tab:
        for event in events:
            if event.get('visibility') != 'public' or event['type'] == 'utterance_classification':
                continue
            with st.container(border=True):
                st.caption(f"event_id={event['event_id']} · day={event.get('day')} · {EVENT_NAMES.get(event['type'],event['type'])}")
                if event['type'] == 'public_speech':
                    st.write(f"{event.get('actor')} 号：{event.get('text', '')}")
                    label = state['classifications'].get(str(event['event_id']))
                    if label:
                        st.write('意图：' + LABELS.get(label.get('intent'),str(label.get('intent'))))
                        st.caption(f"模型：{label.get('model_version')} · 实体规则：{label.get('entity_rule_version')} · 分数未校准，不是概率")
                        st.json(label.get('extracted_entities',{}), expanded=False)
                    else:
                        st.caption('尚无对应分类，请核对A配置或失败记录。')
                else:
                    description = describe_public(event)
                    if description:
                        st.write(description)
                    if event['type'] == 'vote_tally':
                        rows = tally_rows(event)
                        if rows:
                            st.dataframe(rows,hide_index=True,use_container_width=True)
                        else:
                            st.caption('服务未提供票数。' if rows is None else 'B返回空票数表。')
                    with st.container(border=True):
                        st.json({k:v for k,v in event.items() if k not in {'reason','timestamp'}},expanded=False)
    with vote_tab:
        st.caption('按当前已显示事件整理；票数、有效性与裁决沿用B原件，页面不补算。')
        groups = vote_rounds(events)
        if not groups:
            st.info('当前游标尚无公开投票记录。')
        for index, group in enumerate(groups, 1):
            with st.container(border=True):
                st.subheader(f'投票记录 {index} · day={group["day"]}')
                if group['votes']:
                    st.dataframe([{'事件':e['event_id'], '投票人':player_label(e.get('actor')),
                                   '目标':player_label(e.get('target')), '有效性':vote_validity(e.get('valid'))}
                                  for e in group['votes']],hide_index=True,use_container_width=True)
                else:
                    st.caption('当前事件中没有这组逐票记录。')
                tally = group['tally']
                if tally is None:
                    st.caption('尚未收到这组计票结果。')
                else:
                    st.write(describe_public(tally))
                    rows = tally_rows(tally)
                    if rows:
                        st.dataframe(rows,hide_index=True,use_container_width=True)
                    else:
                        st.caption('服务未提供票数。' if rows is None else 'B返回空票数表。')
                    st.caption(f'计票原件 event_id={tally["event_id"]}')
        results = [e for e in events if e.get('visibility')=='public' and e['type'] in {'exile','game_over','game_limit'}]
        for event in results:
            st.write(f'#{event["event_id"]} · day={event.get("day")} · {describe_public(event)}')
    with private_tab:
        if human_view:
            st.info('真人对局进行中不提供上帝信息，出局后也需等待对局结束。')
        st.caption('只供本地观察者；页面不回传玩家输入。reason是角色自述依据。')
        for event in events:
            if not human_view and (event.get('visibility') != 'public' or event.get('reason')):
                with st.container(border=True):
                    st.caption(f"#{event['event_id']} {event['type']} · {event.get('actor','系统')}")
                    if event.get('reason'):
                        st.write('角色自述依据：' + str(event['reason']))
                    st.json(event)
    with stats_tab:
        if human_view:
            st.info('真人局进行中不提供原始调用、逐席位统计或上帝日志。')
        elif integration_mode:
            st.caption('新版服务统计原件；未知用量保持未知，不能用调用次数推算费用。')
            st.json(metrics, expanded=False)
        elif live:
            st.write('B服务返回原件')
            st.json(metrics, expanded=False)
            try:
                calls = read_calls(runs,game_id) if (runs/game_id).is_dir() else None
                check = summarize_calls(calls, metrics)
                st.caption('一次调用尝试记一条，重试也计数；总数可能同时包含玩家、主持和小模型。')
                st.caption('“B记录通过/失败”沿用B的业务校验标记，不等于HTTP是否返回成功，也不代表策略正确。')
                st.caption(check['notice'])
                if check['rows']:
                    st.dataframe(display_call_rows(check['rows']),hide_index=True,use_container_width=True)
                if calls:
                    st.caption('按席位与实际型号核对调用；缺失用量不会显示为 0。')
                    st.dataframe(display_call_rows_by_seat(calls), hide_index=True, use_container_width=True)
                for key,info in check['usage'].items():
                    usage_name = {'input_tokens':'输入token用量','output_tokens':'输出token用量'}[key]
                    st.write(f'{usage_name}：{info["display"]}；缺失{info["missing"]}次；不适用{info["not_applicable"]}次')
                for difference in check['differences']:
                    st.warning('统计待核对：' + difference)
                st.caption('用量未提供不等于0；B汇总显示0时也要核对原始记录。此表不是账单，不能由调用次数推算费用或正确率。')
            except (OSError, ValueError, KeyError) as exc:
                st.warning('原始统计暂不能重算：' + str(exc))
        else:
            st.caption('这是历史记录，不是重新运行；这里只展示当前回放位置之前的事件。完整原始汇总在导出包中。')
        st.download_button('下载当前已显示事件（查询副本）',json.dumps(events,ensure_ascii=False,indent=2),
                           file_name='displayed_events.json',mime='application/json',key='display_'+str(live))
        st.json(events,expanded=False)


def render_events(events, status, phase, metrics, game_id, live, cursor=None, human_player_id=None):
    actual_phase = phase or (events[-1].get('phase') if events else 'night')
    night = actual_phase == 'night'
    bg, fg = ('#101e2e','#e1eaf0') if night else ('#f2f4e9','#314737')
    st.markdown(f'''<style>
.stApp{{background:{bg};color:{fg};transition:background 1s,color 1s}}
[data-testid="stMain"] [role="radiogroup"] p{{color:{fg}!important;font-size:12px}}
[data-testid="stMain"] label p{{color:{fg}!important;font-size:11px}}
[data-testid="stMain"] [role="radiogroup"]{{width:fit-content;padding:5px 12px;border:1px solid #7f9b7833;border-radius:12px}}
[data-testid="stMain"] [data-testid="stExpander"]{{background:#172a3a;color:#d5e3e8;border-radius:14px}}
</style>''',unsafe_allow_html=True)
    if game_id:
        st.caption('当前对局：' + str(game_id) + (' · 现场记录' if live else ' · 历史回放，不调用模型'))
    notice = latest_failure(events)
    if notice:
        st.error(notice['title'] + '：' + notice['summary'])
        st.caption(notice['next_step'])
        if notice['raw']:
            with st.expander('查看本步原始错误', expanded=False):
                st.code(notice['raw'], language=None)
    fallback = next((event for event in reversed(events) if event.get('type') == 'model_fallback'), None)
    if fallback:
        fallback_error = str(fallback.get('error') or '')
        if 'budget exhausted' in fallback_error.lower() or 'request budget' in fallback_error.lower():
            st.error('受控模型预算已用尽：本步没有发出新的 DeepSeek 请求，当前显示的是规则兜底发言。请重启真实服务并使用新的授权预算后再开始新局。')
        else:
            st.warning(f"模型未完成本步，已使用规则兜底继续（{fallback.get('action', 'pass')}）。原始原因已写入对局记录。")
    elif status=='paused':
        st.info('已暂停。点击“继续”解除暂停后，再选择下一步；不会自动发送模型请求。')
    elif status=='finished':
        st.success('本局已结束，可在历史回放中查看过程和导出原始记录。')
    controller = ss.controller if live else None
    render_scene(events,status=status,phase=actual_phase,metrics=metrics,game_id=game_id,
                 instance_key=f':{game_id}:{bool(ss.get(f"identity_ack_{game_id}"))}',
                 seat_labels=getattr(controller.client, 'seat_labels', {}).get(game_id, {}) if controller else {},
                 own_role=getattr(controller.client, 'own_roles', {}).get(game_id) if controller else None,
                 replay=not live,busy=bool(controller and controller.busy), cursor=cursor,
                 message=controller.message if controller else '',
                 human_player_id=human_player_id)
    if live and game_id and not events and isinstance(cursor, int) and cursor > 0:
        successful = metrics.get('successful_calls') if isinstance(metrics, dict) else None
        if isinstance(successful, int) and successful > 0:
            st.success(f'模型已返回 {successful} 次成功调用；当前服务端事件游标 {cursor}。私密行动按真人权限隐藏。')
        else:
            st.info(f'对局已推进到服务端事件游标 {cursor}；当前没有可公开显示的事件。')
    if game_id:
        render_speech_log(events)
        if human_player_id is None:
            render_identity_overview(game_id, events)
    if events and human_player_id is None:
        with st.expander('AI依据详情 · 完整原文与技能线索', expanded=False):
            st.caption('仅查看本次传入的可见事件；自述不等于内部推理，技能宣称不等于技能已执行。')
            for detail in reversed(events):
                if detail.get('type') == 'agent_reason':
                    actor = detail.get('actor')
                    source_id = detail.get('source_event_id')
                    action_event = next((e for e in events if e.get('event_id') == source_id), {})
                    st.markdown(f'**{actor}号 · {PHASES.get(action_event.get("phase"), action_event.get("phase", "阶段未提供"))} · 对应事件 {source_id}**')
                    model_label = action_event.get('returned_model') or action_event.get('model_version') or '本事件未记录实际型号'
                    st.caption('实际模型：' + str(model_label))
                    body = html.escape(str(detail.get('reason') or '未提供'))
                    st.markdown('<div style="font-size:20px;line-height:1.6;overflow-wrap:anywhere;white-space:pre-wrap">'+body+'</div>', unsafe_allow_html=True)
                elif detail.get('type') == 'utterance_classification':
                    entities = (detail.get('result') or {}).get('extracted_entities') or {}
                    result = detail.get('result') or {}
                    for field in ('skill_claims',):
                        values = entities.get(field, result.get(field))
                        if values:
                            st.write(f'发言事件 {detail.get("source_event_id")} · {field}（未核实公开线索）')
                            st.json(values, expanded=True)
    if events:
        with st.expander('检查记录 · 完整事件 / 投票原件 / 调用统计', expanded=False):
            render_details(events,status,phase,metrics,game_id,live)


def render_speech_log(events):
    """给真人席位提供持续可查看的公开发言记录。"""
    speeches = [event for event in events
                if event.get('type') in {'public_speech', 'last_words'}
                and event.get('visibility') == 'public'
                and isinstance(event.get('text'), str)]
    with st.expander('公开发言记录（所有玩家）', expanded=True):
        if not speeches:
            st.caption('暂时没有公开发言；夜间私密行动不会显示在这里。')
            return
        for event in speeches:
            day = event.get('day', '?')
            actor = event.get('actor', '?')
            label = '遗言' if event.get('type') == 'last_words' else '公开发言'
            st.markdown(f'**第{day}天 · {actor}号 · {label}**')
            st.write(event['text'].strip() or '（空发言：原始记录未提供文本）')
            st.divider()


def render_identity_overview(game_id, events):
    # Use only the displayed authorized prefix, never the final local state file.
    snapshot = project(events)
    roles = snapshot.get('roles') or {}
    if not roles:
        return
    with st.expander('身份总览（主持视角）', expanded=False):
        st.caption('仅显示当前已授权事件中的身份和存活状态。')
        columns = st.columns(5)
        alive = snapshot.get('alive', set())
        for index, (seat, role) in enumerate(sorted(roles.items(), key=lambda item: int(item[0]))):
            seat_id = int(seat)
            with columns[index % 5]:
                st.markdown(f'**{seat_id}号** · {ROLES.get(role, role)}')
                st.caption('存活' if seat_id in alive else '已出局')


@st.dialog('身份确认')
def show_identity_dialog(game_id, role):
    st.markdown(f'### 你的身份是：{ROLES.get(role, role or "未提供")}')
    st.caption('身份只对你本人显示。请确认后进入对局。')
    if st.button('确认进入对局', type='primary', key=f'identity_ack_button_{game_id}'):
        ss[f'identity_ack_{game_id}'] = True
        st.rerun()


def render_human_action(controller):
    """真人席位只提交服务端允许动作；不收集或展示“依据”字段。"""
    if not controller:
        return
    uncertain_key = 'human_uncertain_' + controller.game_id
    uncertain = ss.get(uncertain_key)
    if integration_mode and uncertain:
        try:
            known = controller.client.human_operation(controller.game_id, uncertain)
            if known.get('status') == 'completed':
                del ss[uncertain_key]
                controller.refresh()
                st.rerun()
        except ServiceError:
            pass
        st.warning('上次真人操作结果尚未确认，正在只读查询；不会重新提交。')
        return
    if controller.status != 'waiting_human':
        return
    try:
        pending = controller.client.pending_action(controller.game_id)
    except ServiceError as exc:
        st.warning('真人待操作暂时无法读取：' + str(exc))
        return
    if not isinstance(pending, dict) or not isinstance(pending.get('actor'), int):
        st.warning('服务正在等待真人，但未返回合法待操作信息；请刷新状态。')
        return
    actor = pending['actor']; phase = pending.get('phase', controller.phase)
    controller.human_player_id = actor
    st.subheader(f'你的回合 · {actor} 号玩家')
    try:
        identity = controller.client.identity(controller.game_id, actor)
    except ServiceError:
        st.warning('本人状态暂未读取成功，请刷新待操作。')
        return
    actor_role = identity.get('role')
    legal = identity.get('legal_actions') or []
    if not legal:
        st.warning('没有服务端确认的合法动作，请刷新待操作。')
        return
    st.success(f'你的身份：{ROLES.get(actor_role, actor_role)} · ' + ('存活' if identity.get('alive') else '已出局'))
    if actor_role == 'wolf':
        st.info('你的狼队队友：' + '、'.join(f'{p}号' for p in identity.get('wolf_teammates', [])))
    st.info(f'当前阶段：{PHASES.get(phase, phase)}。提交成功后由服务继续推进；请勿重复点击。')
    if phase in {'day_speak', 'last_words'}:
        kind = 'speak' if phase == 'day_speak' else 'last_words'
        text_value = st.text_area('公开发言' if kind == 'speak' else '遗言', key=f'human_text_{controller.game_id}_{pending.get("action_id")}')
        action = {'action': kind, 'target': None, 'text': text_value, 'reason': ''}
    else:
        options = [item['action'] for item in legal]
        kind = st.selectbox('行动', options, format_func=lambda value: {
            'kill':'狼人击杀提案', 'inspect':'预言家查验', 'save':'女巫使用解药',
            'poison':'女巫使用毒药', 'vote':'投票', 'shoot':'猎人开枪', 'pass':'放弃/弃票'}[value],
            key=f'human_kind_{controller.game_id}_{pending.get("action_id")}')
        target = None
        if kind in {'kill', 'inspect', 'poison', 'vote', 'shoot'}:
            targets = next((item.get('targets', []) for item in legal if item['action'] == kind), [])
            if not targets:
                st.warning('服务端未提供合法目标，请刷新。')
                return
            target = st.selectbox('目标席位', targets, key=f'human_target_{controller.game_id}_{pending.get("action_id")}')
        discussion = ''
        if actor_role == 'wolf' and phase == 'night':
            discussion = st.text_area('狼队讨论（仅狼人和主持可见，可留空）',
                                      key=f'human_wolf_discussion_{controller.game_id}_{pending.get("action_id")}')
            if not integration_mode and st.button('发送狼队讨论', key=f'wolf_discussion_{controller.game_id}_{pending.get("action_id")}'):
                try:
                    result = controller.client.wolf_discussion(controller.game_id, actor, discussion,
                                                               expected_cursor=controller.cursor)
                    if result.get('status') in {'rejected', 'conflict'}:
                        st.error('狼队讨论未发送：' + str(result.get('error', '请刷新状态')))
                    else:
                        controller.refresh(); st.rerun()
                except (ServiceError, ValueError, OSError) as exc:
                    st.error('狼队讨论未确认：' + str(exc))
        action = {'action': kind, 'target': target, 'text': discussion, 'reason': discussion}
    if st.button('提交真人操作', type='primary', key=f'human_submit_{controller.game_id}_{pending.get("action_id")}'):
        try:
            operation_id = uuid.uuid4().hex
            if integration_mode:
                ss[uncertain_key] = operation_id
            result = controller.client.human_action(controller.game_id, actor, action,
                action_id=pending.get('action_id', ''), expected_cursor=controller.cursor,
                operation_id=operation_id)
            ss.pop(uncertain_key, None)
            if result.get('status') in {'rejected', 'conflict'}:
                st.error('服务拒绝本次操作：' + str(result.get('error', '请刷新待操作')))
            else:
                controller.error = ''
                controller.refresh(); st.rerun()
        except (ServiceError, ValueError, OSError) as exc:
            st.error('真人操作未确认：' + str(exc))


@st.fragment(run_every=.5)
def live_panel():
    controller = ss.controller
    if controller and (controller.client.base_url != url.rstrip('/') or vars(controller.client.contract) != vars(contract)):
        controller.stop_auto()
        st.warning('服务地址/契约已变化。恢复原配置后再控制当前局，或重新加载页面。')
        return
    if controller:
        controller.tick(refresh=controller.needs_poll)
        # 旧契约也可通过独立待操作接口识别真人等待，避免前端以持续推进绕过真人输入。
        try:
            pending = client.pending_action(controller.game_id)
            if isinstance(pending, dict) and isinstance(pending.get('actor'), int) and controller.status not in TERMINAL | {'paused'}:
                controller.status = 'waiting_human'
                controller.phase = pending.get('phase') or controller.phase
                controller.human_game = True
                controller.human_player_id = pending['actor']
        except ServiceError:
            pass
    if play_mode == '真人参与':
        st.info(f'新局设置：你坐 {human_seat} 号，身份为' +
                ('随机分配' if human_role_mode == '随机' else ROLES[human_role]) +
                '。点击“开始对局”才会生效，已有局身份不变。')
    if model_name == 'stub' and all(value == 'stub' for value in seat_models.values()):
        st.caption('离线示例：AI使用固定测试逻辑，狼人优先选择编号最小的非狼人，女巫默认不救人；不代表真实大模型策略。')
    if controller and integration_mode and ss.get('human_uncertain_' + controller.game_id):
        render_human_action(controller)
    controls = st.columns([1.35,1,1,1,1,1,1])
    if controls[0].button('开始对局',type='primary',disabled=bool(ss.create_uncertain or (controller and (controller.busy or ss.get('human_uncertain_' + controller.game_id)))),help='创建一场新局并分配身份，不调用付费模型；已有对局记录会保留。'):
        try:
            # 在POST前保存意图；页面重运行不能再次提交结果尚未确认的建局。
            ss.create_operation_id=uuid.uuid4().hex
            ss.create_uncertain=True
            controllers = {str(i): ('human' if i in human_seats else 'ai') for i in range(1, 11)}
            model_configs = {str(i): {'provider': 'configured', 'model': seat_models.get(str(i), model_name),
                                      'model_name': seat_models.get(str(i), model_name),
                                      'model_version': seat_models.get(str(i), model_name)}
                             for i in range(1, 11) if i not in human_seats}
            role_config = None
            if play_mode == '真人参与' and human_role_mode == '指定':
                # 服务端要求完整合法角色表；在本地只做席位交换，不改角色总量。
                configured = assign_roles(int(seed))
                holder = next(seat for seat, role in configured.items() if role == human_role)
                configured[human_seat], configured[holder] = configured[holder], configured[human_seat]
                role_config = {str(seat): role for seat, role in configured.items()}
            create_kwargs = {'role_config': role_config, 'seat_controllers': controllers,
                             'seat_model_configs': model_configs}
            # 兼容旧联调替身只接受 seed 的建局函数；真实客户端始终使用完整配置。
            if 'role_config' in inspect.signature(client.create).parameters:
                response = client.create(int(seed), **create_kwargs)
            else:
                response = client.create(int(seed))
            if controller:
                controller.close()
            controller = SessionController(client,response['game_id'],seed=int(seed),
                human_game=bool(human_seats), human_player_id=human_seat if human_seats else None,
                journal=journals/(response['game_id']+'_'+uuid.uuid4().hex[:8]+'.jsonl'))
            controller.status=response['status']
            controller.phase=response.get('phase')
            controller.refresh()
            ss.controller=controller
            ss.create_error=''
            ss.create_uncertain=False
            if controller.human_game:
                # Dialogs are fragments too: open from the app, never a timed fragment.
                st.rerun(scope='app')
        except (ServiceError,ValueError,OSError) as exc:
            ss.create_error=str(exc)
            ss.create_uncertain=not isinstance(exc,ServiceError) or exc.ambiguous
    if ss.create_error:
        st.error(ss.create_error)
        if ss.create_uncertain:
            st.caption('建局结果未确认，禁止自动重建。请用本地历史日志核对后重新加载页面。')
    if not controller:
        render_events([], 'unknown', 'night', {}, None, True)
        st.caption('下一步：点“开始对局”分配身份。想看已有过程，选择上方“历史回放”。')
        return
    ss.game_id=controller.game_id
    ss.events=controller.events
    # 让等待和暂停状态直接可见，不必打开检查记录。
    if controller.busy or controller.pause_intent:
        st.info(controller.message + ' 当前一步仍在处理，请勿重复点击；页面会自动更新。')
    elif controller.status == 'waiting_human':
        st.warning('现在轮到真人行动。单步推进已暂停，请在下方“你的回合”区域提交行动。')
    else:
        notice = latest_failure(controller.events)
        if notice and (controller.fatal or controller.status in TERMINAL):
            st.caption('本局已停止，请查看下面的具体原因')
        elif notice:
            st.warning(f'{notice["title"]}，原句已保留，对局继续推进。')
        else:
            st.caption(controller.message)
    if controller.error and not latest_failure(controller.events):
        st.error(controller.error)
        st.caption('先刷新事件或核对控制记录；结果未确认前不要重复提交。')
    if controller.unresolved:
        st.caption('待确认操作：' + ', '.join(op.action+':'+op.operation_id for op in controller.unresolved))
    buttons=controls[1:]
    action=None
    locked=controller.fatal or bool(controller.error) or bool(ss.get('human_uncertain_' + controller.game_id))
    if buttons[0].button('单步推进',disabled=locked or controller.busy or controller.auto or controller.pause_intent or controller.status!='running' or not controller.synced,help='推进当前一步；可能调用多次模型并产生费用，等待完成后再操作。'):
        action='advance'
    if buttons[1].button('暂停',disabled=locked or controller.pause_intent or controller.status not in {'running','pending'} or (controller.busy and not contract.ready and not getattr(controller.client,'advance_query_supported',False)),help='停止安排下一步，等待当前一步完成后暂停；不会取消已经发出的模型请求。'):
        action='pause'
    if buttons[2].button('继续',disabled=locked or controller.busy or controller.status!='paused',help='解除暂停后，可选择单步或重新点击自动运行。'):
        action='resume'
    if buttons[3].button('刷新事件',help='只查询已发生的记录，不开始新行动，不重新请求模型'):
        controller.collect()
        controller.refresh()
        ss.events=controller.events
        st.rerun()
    if buttons[4].button('自动运行',disabled=real_mode or not controller.auto_ready or controller.busy or controller.auto or controller.status!='running' or controller.pause_intent,help='按顺序推进，每一步确认完成后才开始下一步；等待是运行状态，无需点击。'):
        try:
            controller.start_auto()
            controller.tick()
        except ValueError as exc:
            controller.fail(str(exc))
        st.rerun()
    if buttons[5].button('停止自动调度',disabled=not controller.auto):
        controller.stop_auto()
        st.rerun()
    if action:
        try:
            controller.submit(action)
        except (ValueError,OSError) as exc:
            controller.fail(str(exc))
        st.rerun()
    render_events(controller.events,controller.status,controller.phase,controller.metrics,controller.game_id,True,
                  cursor=controller.cursor, human_player_id=controller.human_player_id)
    render_human_action(controller)


@st.fragment(run_every=1)
def replay_panel():
    if integration_mode:
        source = st.radio('回放来源', ['新版服务', '旧版日志（历史六人局）'], horizontal=True)
        if source == '新版服务':
            game = st.text_input('回放对局编号', value=ss.controller.game_id if ss.controller else '')
            if not game:
                st.info('填写已保存的对局编号；真人局必须结束后才能回放。')
                return
            try:
                original = client.replay(game)
                last = max((e['event_id'] for e in original), default=0)
                cursor = st.slider('回放到 event_id', 0, max(last, 1), value=last, key='integration_cursor_'+game)
                visible = [e for e in original if e['event_id'] <= cursor]
                render_events(visible, project(visible)['status'], None, {}, game, False, cursor=cursor)
            except ServiceError as exc:
                st.warning('新版回放不可用；真人局结束前不开放上帝回放。' if exc.code == 403 else str(exc))
            return
    requested = st.query_params.get('game')
    if requested and not replay_allowed(runs, requested):
        st.warning('该对局尚未结束或无法确认权限，不能打开上帝回放与原始导出。')
        return
    games=local_games(runs)
    if not games:
        render_events([], 'unknown', 'night', {}, None, False)
        st.info('还没有保存的对局，请先开始一局。')
        return
    selection_col,timeline_col,action_col=st.columns([2,4,1.1])
    selected=selection_col.selectbox('选择本地对局',games,index=games.index(st.query_params['game']) if st.query_params.get('game') in games else 0,label_visibility='collapsed')
    try:
        all_events=read_events(runs,selected)
        if all_events:
            last=max(int(e['event_id']) for e in all_events)
            key='cursor_'+selected
            if ss.pop('replay_restart',False):
                ss[key]=0
            if ss.get('replay_playing') and ss.get('replay_selected')==selected:
                ss[key]=min(last,ss.get(key,0)+1)
                if ss[key]>=last: ss.replay_playing=False
            elif ss.get('replay_selected')!=selected:
                ss.replay_playing=False
            ss.replay_selected=selected
            left,right=timeline_col,action_col
            try:
                initial_cursor=max(0,min(last,int(st.query_params.get('event','0')))) if selected==st.query_params.get('game') else 0
            except (ValueError,TypeError):
                initial_cursor=0
            cursor=left.slider('回放到 event_id',min_value=0,max_value=last,value=initial_cursor,key=key,label_visibility='collapsed')
            if right.button('暂停回放' if ss.get('replay_playing') else '重新播放' if cursor>=last else '播放回放'):
                ss.replay_playing=not ss.get('replay_playing',False)
                if ss.replay_playing and cursor>=last:
                    ss.replay_restart=True
                st.rerun()
            events=[e for e in all_events if int(e['event_id'])<=cursor]
            render_events(events,project(events)['status'],None,{},selected,False)
            with st.expander('回放文件与导出',expanded=False):
                st.code(f'回放 game_id: {selected}   event_id: {cursor}',language=None)
                st.download_button('导出 B 原始日志包',evidence_zip(runs,selected),file_name=selected+'_raw_logs.zip',mime='application/zip')
    except (OSError,ValueError,KeyError) as exc:
        st.error('读取回放失败：'+str(exc))

if mode=='现场对局':
    live_panel()
    controller = ss.controller
    if controller and controller.human_game and controller.human_player_id and not ss.get(f'identity_ack_{controller.game_id}'):
        try:
            initial_identity = controller.client.identity(controller.game_id, controller.human_player_id)
            show_identity_dialog(controller.game_id, initial_identity.get('role'))
        except ServiceError:
            st.warning('本人身份暂未读取成功，请刷新页面。')
else:
    if ss.controller:
        ss.controller.stop_auto()
    replay_panel()
