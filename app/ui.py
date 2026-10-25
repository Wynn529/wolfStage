"""C上帝视角：兼容旧B；增强自动控制由已确认契约启用。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from app.ui_client import GameClient, ServiceError
from app.ui_contract import ServiceContract, TERMINAL
from app.ui_controller import SessionController
from app.ui_helpers import (LABELS, ROLES, STATUS, PHASES, project,
                            local_games, read_events, evidence_zip)
from app.ui_metrics import read_calls, summarize_calls
from app.ui_presenter import EVENT_NAMES, WINNERS, describe_public, player as player_label, tally_rows, vote_rounds, vote_validity

st.set_page_config(page_title='AI 狼人杀 · 上帝视角', page_icon='🐺', layout='wide')
st.title('AI 狼人杀 · 上帝视角')
st.caption('C 界面 v0.3 · 2 狼人 / 1 预言家 / 3 平民 · 角色自述依据仅供观察者')
st.info('接口联调页面：显示实际来源，不把stub玩家或参考服务称为真实DeepSeek对局。')
ss = st.session_state
if 'controller' not in ss:
    ss.controller = None
    ss.create_error = ''
    ss.create_uncertain = False
if 'events' not in ss:
    ss.events = []
    ss.game_id = None

runs = Path(os.getenv('C_RUNS_DIR', str(ROOT / 'runs')))
journals = Path(os.getenv('C_JOURNAL_DIR', str(ROOT / '.local/c_operations')))
try:
    contract = ServiceContract.load(os.getenv('C_SERVICE_CONTRACT'))
except (OSError, TypeError, ValueError) as exc:
    st.error('增强契约加载失败，自动控制已锁定：' + str(exc))
    st.stop()

with st.sidebar:
    st.header('运行与回放')
    mode = st.radio('模式', ['实时联调', '本地只读回放'])
    url = st.text_input('B 服务地址', os.getenv('B_API_URL', 'http://127.0.0.1:8000'))
    st.caption('默认端口：A分类8765、B游戏8000、网页8501；自定义时以启动输出及B地址为准。')
    st.caption(f'契约：{contract.version}；确认人：{contract.confirmed_by or "尚无增强能力确认"}')
    if not contract.ready:
        st.warning('现有B仅开放手动兼容模式。自动调度代码已备好，待B增强契约确认后启用。')
    record_files = sorted(journals.glob('*.jsonl')) if journals.exists() else []
    if record_files:
        record_path = st.selectbox('本机控制记录', record_files, format_func=lambda p:p.stem)
        if st.button('恢复记录（只读查询，不重发）'):
            try:
                if ss.controller and ss.controller.busy:
                    raise ValueError('当前仍有待确认操作，先核对当前局')
                if ss.controller:
                    ss.controller.close()
                ss.controller = SessionController.restore(GameClient(url, contract=contract), record_path)
            except (ServiceError, ValueError, OSError, KeyError) as exc:
                st.error(str(exc))

try:
    client = GameClient(url, contract=contract)
except ValueError as exc:
    st.error(str(exc))
    st.stop()


def render_events(events, status, phase, metrics, game_id, live):
    if not events:
        st.caption('当前游标尚无事件。')
        return
    state = project(events)
    ids = [int(e['event_id']) for e in events]
    if ids != list(range(1, max(ids)+1)):
        st.error('事件编号不连续：页面不会补造事件，请核对B原始记录。')
    sources = sorted({str(e.get('source', 'unknown')) for e in events})
    st.caption('事件来源：' + ' / '.join(sources) + '；含stub的局不能作为全真实API对局证据。')
    cols = st.columns(4)
    cols[0].metric('对局状态', STATUS.get(status, status))
    cols[1].metric('阶段', PHASES.get(phase, str(phase or '按事件回放')))
    cols[2].metric('存活人数', len(state['alive']))
    cols[3].metric('最后显示 event_id', max(ids))
    cards = st.columns(6)
    for player, card in enumerate(cards, 1):
        with card:
            st.subheader(f'{player} 号')
            st.write(ROLES.get(state['roles'].get(player), '身份未提供'))
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
                    with st.expander('查看原始公开记录'):
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
        st.caption('只供本地观察者；页面不回传玩家输入。reason是角色自述依据。')
        for event in events:
            if event.get('visibility') != 'public' or event.get('reason'):
                with st.expander(f"#{event['event_id']} {event['type']} · {event.get('actor','系统')}"):
                    if event.get('reason'):
                        st.write('角色自述依据：' + str(event['reason']))
                    st.json(event)
    with stats_tab:
        if live:
            st.write('B服务返回原件')
            st.json(metrics, expanded=False)
            try:
                calls = read_calls(runs,game_id) if (runs/game_id).is_dir() else None
                check = summarize_calls(calls, metrics)
                st.caption(check['notice'])
                if check['rows']:
                    st.dataframe(check['rows'],hide_index=True,use_container_width=True)
                for key,info in check['usage'].items():
                    st.write(f'{key}：{info["display"]}；缺失{info["missing"]}次；不适用{info["not_applicable"]}次')
                for difference in check['differences']:
                    st.warning('统计待核对：' + difference)
                st.caption('费用：未计算。规则/策略正确率不能从调用次数推导。')
            except (OSError, ValueError, KeyError) as exc:
                st.warning('原始统计暂不能重算：' + str(exc))
        else:
            st.caption('回放不提前展示未来整局统计；完整原始汇总在导出包中。')
        st.download_button('下载当前已显示事件（查询副本）',json.dumps(events,ensure_ascii=False,indent=2),
                           file_name='displayed_events.json',mime='application/json',key='display_'+str(live))
        st.json(events,expanded=False)


@st.fragment(run_every=.5)
def live_panel():
    controller = ss.controller
    if controller and (controller.client.base_url != url.rstrip('/') or controller.client.contract != contract):
        controller.stop_auto()
        st.warning('服务地址/契约已变化。恢复原配置后再控制当前局，或重新加载页面。')
        return
    if controller:
        controller.tick(refresh=controller.needs_poll)
    seed = st.number_input('随机种子 seed',min_value=0,max_value=2147483647,value=42,step=1)
    if st.button('新建对局',type='primary',disabled=bool(ss.create_uncertain or (controller and controller.busy))):
        try:
            # 在POST前保存意图；页面重运行不能再次提交结果尚未确认的建局。
            ss.create_operation_id=uuid.uuid4().hex
            ss.create_uncertain=True
            response = client.create(int(seed))
            if controller:
                controller.close()
            controller = SessionController(client,response['game_id'],seed=int(seed),
                journal=journals/(response['game_id']+'_'+uuid.uuid4().hex[:8]+'.jsonl'))
            controller.status=response['status']
            controller.phase=response.get('phase')
            controller.refresh()
            ss.controller=controller
            ss.create_error=''
            ss.create_uncertain=False
        except (ServiceError,ValueError,OSError) as exc:
            ss.create_error=str(exc)
            ss.create_uncertain=not isinstance(exc,ServiceError) or exc.ambiguous
    if ss.create_error:
        st.error(ss.create_error)
        if ss.create_uncertain:
            st.caption('建局结果未确认，禁止自动重建。请用本地历史日志核对后重新加载页面。')
    if not controller:
        st.write('点击“新建对局”，从夜间开始逐步观察。')
        return
    ss.game_id=controller.game_id
    ss.events=controller.events
    st.code(f'game_id: {controller.game_id}   seed: {controller.seed}',language=None)
    st.write(controller.message)
    if controller.error:
        st.error(controller.error)
    if controller.unresolved:
        st.caption('待确认操作：' + ', '.join(op.action+':'+op.operation_id for op in controller.unresolved))
    buttons=st.columns(6)
    action=None
    locked=controller.fatal or bool(controller.error)
    if buttons[0].button('单步推进',disabled=locked or controller.busy or controller.auto or controller.pause_intent or controller.status!='running' or not controller.synced):
        action='advance'
    if buttons[1].button('暂停',disabled=locked or controller.pause_intent or controller.status not in {'running','pending'} or (controller.busy and not contract.ready)):
        action='pause'
    if buttons[2].button('继续',disabled=locked or controller.busy or controller.status!='paused'):
        action='resume'
    if buttons[3].button('刷新事件'):
        controller.collect()
        controller.refresh()
        ss.events=controller.events
        st.rerun()
    if buttons[4].button('自动运行',disabled=not controller.auto_ready or controller.busy or controller.auto or controller.status!='running' or controller.pause_intent):
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
    if not contract.ready:
        st.caption('旧B模式：自动运行锁定。若控制超时，刷新只读事件也不会冒充已确认完成。')
    render_events(controller.events,controller.status,controller.phase,controller.metrics,controller.game_id,True)


if mode=='实时联调':
    live_panel()
else:
    if ss.controller:
        ss.controller.stop_auto()
    st.warning('回放 · 只读取本机日志，不请求玩家或分类模型。自动调度已停止。')
    games=local_games(runs)
    if not games:
        st.write('尚无本地对局。')
    else:
        selected=st.selectbox('选择本地对局',games)
        try:
            all_events=read_events(runs,selected)
            if all_events:
                last=max(int(e['event_id']) for e in all_events)
                cursor=st.slider('回放到 event_id',min_value=0,max_value=last,value=0,key='cursor_'+selected)
                events=[e for e in all_events if int(e['event_id'])<=cursor]
                st.code(f'回放 game_id: {selected}   event_id: {cursor}',language=None)
                st.download_button('导出 B 原始日志包',evidence_zip(runs,selected),file_name=selected+'_raw_logs.zip',mime='application/zip')
                render_events(events,project(events)['status'],None,{},selected,False)
        except (OSError,ValueError,KeyError) as exc:
            st.error('读取回放失败：'+str(exc))
