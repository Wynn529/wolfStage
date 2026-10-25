"""C会话级调度器：后台POST、只读恢复、自动循环和停止条件。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from app.ui_client import ServiceError
from app.ui_contract import STATUSES, TERMINAL
from app.ui_helpers import merge_events, project


@dataclass
class Operation:
    operation_id: str
    action: str
    expected_event_id: int
    state: str = 'submitted'


class SessionController:
    def __init__(self, client, game_id, *, seed=None, journal=None, interval=.5,
                 max_steps=500, no_progress_limit=12):
        self.client = client
        self.game_id = game_id
        self.seed = seed
        self.events = []
        self.metrics = {}
        self.status = 'unknown'
        self.phase = None
        self.snapshot = None
        self.operations = {}
        self.futures = {}
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='c-control')
        self.auto = False
        self.pause_intent = False
        self.error = ''
        self.fatal = False
        self.synced = False
        self.interval = interval
        self.next_due = 0
        self.max_steps = max_steps
        self.no_progress_limit = no_progress_limit
        self.steps = 0
        self.no_progress = 0
        self.journal = Path(journal) if journal else None
        self._log('session', game_id=game_id, seed=seed, service=client.base_url,
                  contract=client.contract.version, confirmed_commit=client.contract.confirmed_commit)

    @property
    def cursor(self):
        return max((int(e['event_id']) for e in self.events), default=0)

    @property
    def unresolved(self):
        return [op for op in self.operations.values() if op.state in {'submitted', 'uncertain', 'accepted'}]

    @property
    def busy(self):
        return bool(self.unresolved or any(not f.done() for f in self.futures.values()))

    @property
    def needs_poll(self):
        # 状态快照可能先于操作完成读到，不能因操作已完成就停止查询旧pending。
        return bool(self.busy or self.auto or self.pause_intent or self.status in {'unknown','pending'}
                    or (self.snapshot and (self.snapshot['in_flight'] or self.snapshot['pause_requested'])))

    @property
    def auto_ready(self):
        return bool(self.client.contract.ready and self.snapshot is not None and self.synced
                    and not self.fatal and not self.error)

    @property
    def message(self):
        if self.pause_intent or (self.snapshot and self.snapshot['pause_requested']):
            if self.busy or (self.snapshot and self.snapshot['in_flight']):
                return '已申请暂停：正在等待模型返回，本步结束后暂停'
        if self.busy or (self.snapshot and self.snapshot['in_flight']):
            return '正在等待模型返回或查询操作结果'
        if self.fatal:
            return '控制已锁定：请按证据核对接口'
        if self.status in TERMINAL:
            return '自动调度已停止：' + self.status
        return '自动运行中' if self.auto else '当前无自动调度'

    def _log(self, kind, **fields):
        if self.journal:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),
                                         'kind':kind, 'game_id':self.game_id, **fields}, ensure_ascii=False) + '\n')

    def _operation_log(self, op):
        self._log('operation', operation_id=op.operation_id, action=op.action,
                  expected_event_id=op.expected_event_id, state=op.state)

    def fail(self, message, *, fatal=False):
        self.auto = False
        self.error = str(message)
        self.fatal = self.fatal or fatal
        self.synced = False
        self._log('stopped', reason=self.error, fatal=self.fatal, last_event_id=self.cursor)

    def submit(self, action, operation_id=None):
        if operation_id and operation_id in self.operations:
            if self.operations[operation_id].action != action:
                raise ValueError('同一操作ID不能用于不同动作')
            return operation_id  # Streamlit重复触发同一操作不产生第二个POST。
        if self.fatal or self.status in TERMINAL:
            raise ValueError('当前对局已终止或控制锁定，不能推进')
        if action == 'advance' and (self.busy or self.pause_intent or self.status != 'running' or not self.synced):
            raise ValueError('上一步结果未确认、正在暂停或状态未同步，不能再次推进')
        if action == 'resume' and (self.busy or self.status != 'paused'):
            raise ValueError('仅允许在已停稳且无待确认操作时继续')
        if action == 'pause':
            self.auto = False
            if self.pause_intent:
                return next((op.operation_id for op in self.operations.values() if op.action == 'pause'
                             and op.state not in {'failed', 'rejected'}), None)
            if self.busy and not self.client.contract.ready:
                raise ValueError('旧B不支持在途暂停确认，请等待当前请求结束')
            if self.status not in {'running', 'pending'}:
                raise ValueError('当前状态不能申请暂停')
            self.pause_intent = True
        if action not in {'advance', 'pause', 'resume'}:
            raise ValueError('未知控制动作')
        op = Operation(operation_id or uuid.uuid4().hex, action, self.cursor)
        self.operations[op.operation_id] = op
        # 先持久化操作ID，再交后台线程；后台不接触Streamlit状态。
        self._operation_log(op)
        self.futures[op.operation_id] = self.pool.submit(
            self.client.control, self.game_id, action, operation_id=op.operation_id,
            expected_event_id=op.expected_event_id)
        return op.operation_id

    def start_auto(self):
        if not self.auto_ready or self.busy or self.status != 'running' or self.pause_intent:
            raise ValueError('B增强能力未确认或当前状态不允许自动运行')
        self.auto = True
        self.steps = self.no_progress = 0
        self._log('auto_started', last_event_id=self.cursor)

    def stop_auto(self):
        self.auto = False
        self._log('auto_stopped_locally', last_event_id=self.cursor)

    def _completed(self, op, result=None):
        op.state = 'succeeded'
        if op.action == 'advance':
            self.steps += 1
            # 允许只切换阶段，但连续无事件/阶段变化时有限次停止，防循环。
        if result:
            status = result.get('status')
            if status not in STATUSES:
                raise ValueError('控制响应status未知')
            self.status = status
            self.phase = result.get('phase', self.phase)
        if op.action == 'resume':
            self.pause_intent = False
        self._operation_log(op)

    def collect(self):
        for operation_id, future in list(self.futures.items()):
            if not future.done():
                continue
            op = self.operations[operation_id]
            del self.futures[operation_id]
            try:
                result = future.result()
                if self.client.contract.ready:
                    if op.state not in {'succeeded', 'failed', 'rejected'}:
                        op.state = 'accepted'
                        self._operation_log(op)
                else:
                    self._completed(op, result)
            except (ServiceError, ValueError, TypeError, KeyError) as exc:
                if op.state == 'succeeded':
                    # 已经由只读操作查询确认；晚到的传输超时不撤销已确认事实。
                    continue
                definite = isinstance(exc, ServiceError) and not exc.ambiguous
                op.state = 'rejected' if definite else 'uncertain'
                self._operation_log(op)
                self.fail(str(exc), fatal=not self.client.contract.ready and not definite)

    def refresh(self):
        try:
            snapshot = self.client.state(self.game_id) if self.client.contract.ready else None
            incoming = self.client.events(self.game_id, self.cursor)
            merged = merge_events(self.events, incoming, self.game_id)
            ids = [int(e['event_id']) for e in merged]
            if ids and ids != list(range(1, max(ids)+1)):
                raise ValueError('事件编号不连续，停止控制并保留原局证据')
            self.events = merged
            if snapshot:
                self.snapshot = snapshot
                self.status = snapshot['status']
                self.phase = snapshot['phase']
                self.synced = self.cursor == snapshot['last_event_id']
                for op in list(self.unresolved):
                    try:
                        response = self.client.operation(self.game_id, op.operation_id)
                    except ServiceError as exc:
                        future = self.futures.get(op.operation_id)
                        if exc.code == 404 and future is not None and not future.done():
                            continue  # POST仍在传输，操作尚未登记；只读等候，不重发。
                        raise
                    if response['action'] != op.action:
                        raise ValueError('操作查询action与本地提交不一致')
                    if response['state'] == 'succeeded':
                        result = response.get('result')
                        if not isinstance(result, dict) or result.get('game_id') != self.game_id:
                            raise ValueError('成功操作缺少有效结果快照')
                        self._completed(op)  # 以只读state为最终状态，不用历史操作覆盖新状态。
                    elif response['state'] in {'failed', 'rejected'}:
                        op.state = response['state']
                        self._operation_log(op)
                        self.fail('B确认操作失败或被拒绝：' + op.action, fatal=True)
                if self.status == 'paused' and not snapshot['in_flight']:
                    self.pause_intent = False
            else:
                inferred = project(self.events)
                if inferred['status'] in TERMINAL or self.status == 'unknown':
                    self.status = inferred['status']
                self.synced = not self.fatal and not self.unresolved
            # 统计独立读取；失败不抹去已经保存的事件/操作事实。
            self.metrics = self.client.metrics(self.game_id)
            if not self.fatal and not self.unresolved:
                self.error = ''
            if any(e['type'] in {'utterance_classification_failed', 'model_failure'} for e in self.events):
                self.fail('对局含模型/分类失败，请人工核对后交B处理', fatal=True)
            if self.status in TERMINAL:
                self.auto = False
        except (ServiceError, ValueError, TypeError, KeyError) as exc:
            self.fail(str(exc), fatal=isinstance(exc, ValueError))

    def tick(self, *, refresh=True):
        before = (self.cursor, self.phase)
        previous_steps = self.steps
        self.collect()
        if refresh:
            self.refresh()
        if self.steps > previous_steps:
            self.no_progress = self.no_progress + 1 if before == (self.cursor, self.phase) else 0
        if self.auto and (self.steps >= self.max_steps or self.no_progress >= self.no_progress_limit):
            self.fail('达到C调度上限或连续无进展，已停止；不判定游戏胜负', fatal=True)
        now = time.monotonic()
        if (self.auto and self.auto_ready and not self.busy and self.status == 'running'
                and not self.pause_intent and not self.snapshot['in_flight']
                and not self.snapshot['pause_requested'] and now >= self.next_due):
            self.submit('advance')
            self.next_due = now + self.interval

    def close(self):
        self.auto = False
        self.pool.shutdown(wait=False, cancel_futures=True)

    @classmethod
    def restore(cls, client, journal):
        rows = [json.loads(line) for line in Path(journal).read_text(encoding='utf-8').splitlines() if line.strip()]
        header = next(row for row in rows if row['kind'] == 'session')
        if (header['service'] != client.base_url or header['contract'] != client.contract.version
                or header.get('confirmed_commit','') != client.contract.confirmed_commit):
            raise ValueError('控制记录与当前服务/契约不一致')
        controller = cls(client, header['game_id'], seed=header.get('seed'), journal=journal)
        for row in rows:
            if row['kind'] == 'operation':
                controller.operations[row['operation_id']] = Operation(
                    row['operation_id'], row['action'], row['expected_event_id'], row['state'])
        if controller.unresolved:
            controller.fail('已恢复待确认操作；只读查询结果，不重新提交', fatal=not client.contract.ready)
        controller.refresh()
        return controller
