"""C增强控制协议；默认仅兼容现有B，不自行宣称B能力。"""
from dataclasses import dataclass, field
import json
from pathlib import Path

CAPABILITIES = ('per_game_serialization', 'idempotency', 'boundary_pause', 'state_query', 'operation_query')
TERMINAL = {'finished', 'failed', 'incomplete', 'degraded'}
STATUSES = TERMINAL | {'running', 'pending', 'paused'}


@dataclass(frozen=True)
class ServiceContract:
    version: str = 'legacy'
    confirmed_by: str = ''
    confirmed_commit: str = ''
    capabilities: dict = field(default_factory=dict)
    paths: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)
    state_fields: dict = field(default_factory=dict)
    idempotency_header: str = ''

    @property
    def enhanced(self):
        return self.version != 'legacy'

    @property
    def ready(self):
        return bool(self.enhanced and self.confirmed_by and self.confirmed_commit
                    and all(self.capabilities.get(key) is True for key in CAPABILITIES))

    @classmethod
    def load(cls, path):
        if not path:
            return cls()
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise ValueError('C服务契约字段不正确')
        contract = cls(**value)
        for name in ('version', 'confirmed_by', 'confirmed_commit', 'idempotency_header'):
            if not isinstance(getattr(contract, name), str):
                raise ValueError('契约版本与确认字段必须是字符串')
        for mapping in ('paths', 'fields', 'state_fields', 'capabilities'):
            if not isinstance(getattr(contract, mapping), dict):
                raise ValueError('契约映射字段必须是对象')
        for mapping in ('paths', 'fields', 'state_fields'):
            if any(not isinstance(k,str) or not isinstance(v,str) or not v
                   for k,v in getattr(contract,mapping).items()):
                raise ValueError('契约路径和字段映射必须是非空字符串')
        if not contract.ready:
            raise ValueError('契约尚未确认全部增强能力，自动运行不可启用')
        return contract

    def normalize_state(self, response, game_id):
        if not isinstance(response, dict):
            raise ValueError('B状态响应必须是对象')
        names = ('game_id', 'status', 'phase', 'last_event_id', 'in_flight', 'pause_requested')
        state = {key: response[self.state_fields.get(key, key)] for key in names}
        if state['game_id'] != game_id or state['status'] not in STATUSES:
            raise ValueError('B状态局号不匹配或状态未知')
        if not isinstance(state['phase'], str) or not state['phase']:
            raise ValueError('B状态缺少有效phase')
        if type(state['last_event_id']) is not int or state['last_event_id'] < 0:
            raise ValueError('B状态游标必须是非负整数')
        if any(type(state[key]) is not bool for key in ('in_flight', 'pause_requested')):
            raise ValueError('B在途/暂停申请字段必须是布尔值')
        if state['status'] == 'paused' and state['in_flight']:
            raise ValueError('B同时报告停稳paused和在途调用，请先修复状态契约')
        return state
