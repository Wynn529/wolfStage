"""C adapter for the authenticated 20261106 integration service."""
from urllib.parse import quote
from app.ui_client import BRevisionClient, ServiceError


class IntegrationClient(BRevisionClient):
    integration = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sessions = {}
        self._human_seats = {}
        self.seat_models = {}
        self.seat_labels = {}
        self._profiles = {}
        self.own_roles = {}

    def _request(self, method, path, payload=None, headers=None):
        gid = path.split('/')[2] if path.startswith('/games/') else None
        token = self._sessions.get(gid)
        headers = dict(headers or {})
        if token:
            headers['Authorization'] = 'Bearer ' + token
        return super()._request(method, path, payload, headers)

    def models(self):
        result = self._request('GET', '/model-profiles')
        if not isinstance(result, list) or not result or any(
            not isinstance(p, dict) or not all(isinstance(p.get(k), str) and p[k]
                for k in ('profile_id', 'display_name', 'provider', 'model')) for p in result
        ) or len({p['profile_id'] for p in result}) != len(result):
            raise ServiceError('服务端模型登记列表无效，禁止建局。', ambiguous=False)
        self._profiles = {p['profile_id']: dict(p) for p in result}
        return result

    def create(self, seed, *, role_config=None, seat_controllers=None, seat_model_configs=None):
        controllers = seat_controllers or {}
        configs = seat_model_configs or {}
        humans = [int(k) for k, v in controllers.items() if v == 'human']
        if len(humans) > 1:
            raise ValueError('仅支持一名真人')
        seats = [{'seat_id': i, 'control_type': 'human' if i in humans else 'ai',
                  'model_profile_id': None if i in humans else configs.get(str(i), configs.get(i, {})).get('model')}
                 for i in range(1, 11)]
        result = self._request('POST', '/games', {'mode': 'human' if humans else 'all_ai',
            'seed': seed, 'human_role': (role_config or {}).get(str(humans[0])) if humans else None,
            'seats': seats})
        if not isinstance(result, dict) or not isinstance(result.get('game_id'), str):
            raise ServiceError('建局结果无效，禁止自动重建。')
        gid = result['game_id']
        if humans:
            if result.get('human_seat') != humans[0] or not isinstance(result.get('human_token'), str) or not result['human_token']:
                raise ServiceError('真人会话未确认，禁止自动重建。')
            self._sessions[gid] = result['human_token']
            self._human_seats[gid] = humans[0]
        self.seat_models[gid] = {s['seat_id']: s['model_profile_id'] for s in seats}
        self.seat_labels[gid] = {s['seat_id']: self._profiles.get(s['model_profile_id'], {}).get('display_name', s['model_profile_id']) or '真人'
                                for s in seats}
        return {k: v for k, v in result.items() if k != 'human_token'}

    def game_access(self, game_id):
        value = self.service_status(game_id)
        seat = (value.get('player_context') or {}).get('agent_id', self._human_seats.get(game_id))
        if type(seat) is int:
            self._human_seats[game_id] = seat
        return dict(value, restricted=seat is not None and value['status'] not in {'finished', 'incomplete'}, human_player_id=seat)

    def game_has_human(self, game_id):
        return self.game_access(game_id).get('human_player_id') is not None

    def pending_action(self, game_id):
        return self.service_status(game_id).get('pending_action')

    def identity(self, game_id, player_id):
        value = self.service_status(game_id)
        ctx = value.get('player_context') or {}
        if ctx.get('agent_id') != player_id:
            raise ServiceError('本人会话失效或席位不匹配，不能读取身份。', code=403, ambiguous=False)
        self.own_roles[game_id] = ctx.get('role')
        identity = {'game_id': game_id, 'player_id': player_id, 'role': ctx.get('role'),
                'alive': player_id in ctx.get('alive_players', []),
                'wolf_teammates': ctx.get('wolf_teammates', []),
                'legal_actions': (value.get('action_contract') or {}).get('actions', [])}
        pending = value.get('pending_action') or {}
        if (ctx.get('role') == 'witch' and value.get('status') == 'waiting_human'
                and pending.get('actor') == player_id and pending.get('phase') == 'night'
                and 'night_kill_target' in ctx):
            identity['night_kill_target'] = ctx['night_kill_target']
        return identity

    def human_action(self, game_id, player_id, action, **kwargs):
        clean = {k: action.get(k) for k in ('action', 'target', 'text')}
        return super().human_action(game_id, player_id, clean, **kwargs)

    def wolf_discussion(self, *args, **kwargs):
        raise ValueError('新版服务暂未提供单独讨论接口，请在狼人行动中提交讨论文字。')

    def replay(self, game_id):
        result = self._request('GET', f'/games/{quote(game_id, safe="")}/replay')
        if not isinstance(result, list):
            raise ServiceError('回放响应无效')
        return result

    def human_operation(self, game_id, operation_id):
        return self._request('GET', f'/games/{quote(game_id, safe="")}/operations/{quote(operation_id, safe="")}')
