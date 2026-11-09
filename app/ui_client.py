"""C的HTTP适配；任何POST都不自动重试。"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from app.ui_contract import ServiceContract


class ServiceError(RuntimeError):
    def __init__(self, message, *, code=None, ambiguous=True):
        super().__init__(message)
        self.code = code
        self.ambiguous = ambiguous


class GameClient:
    def __init__(self, base_url: str, timeout: float = 90, contract=None, read_timeout: float = 3):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('B地址须为无凭据、查询参数或片段的HTTP(S)地址')
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.read_timeout = read_timeout
        self.contract = contract or ServiceContract()

    def _request(self, method: str, path: str, payload=None, headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
        request = Request(self.base_url + path, data=data, method=method,
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urlopen(request, timeout=self.timeout if method == 'POST' else self.read_timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            raise ServiceError(f'B返回HTTP {exc.code}；控制结果需查询确认。', code=exc.code,
                               ambiguous=exc.code not in {400, 404, 405, 422}) from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise ServiceError('请求未确认完成；不会自动重发控制请求，请只读查询恢复。') from exc

    def create(self, seed: int, *, role_config=None, seat_controllers=None, seat_model_configs=None):
        payload = {'seed': seed}
        if role_config is not None: payload['role_config'] = role_config
        if seat_controllers is not None: payload['seat_controllers'] = seat_controllers
        if seat_model_configs is not None: payload['seat_model_configs'] = seat_model_configs
        result = self._request('POST', '/games', payload)
        if not isinstance(result, dict) or not isinstance(result.get('game_id'), str) or result.get('status') not in {'running', 'paused'}:
            raise ServiceError('建局响应结构不正确；结果需人工核对，不自动重建。')
        return result

    def control(self, game_id: str, action: str, *, operation_id=None, expected_event_id=None):
        if action not in {'advance', 'pause', 'resume'}:
            raise ValueError('unsupported control')
        payload = None
        headers = {}
        if self.contract.enhanced:
            if not self.contract.ready or not operation_id or type(expected_event_id) is not int:
                raise ValueError('增强控制缺少已确认契约、操作ID或期望游标')
            payload = {self.contract.fields.get('operation_id', 'operation_id'): operation_id,
                       self.contract.fields.get('expected_event_id', 'expected_event_id'): expected_event_id}
            if self.contract.idempotency_header:
                headers[self.contract.idempotency_header] = operation_id
        result = self._request('POST', f'/games/{quote(game_id, safe="")}/{action}', payload, headers)
        if not isinstance(result, dict) or result.get('game_id') != game_id:
            raise ServiceError('控制响应局号不匹配或格式无效；结果需查询确认。')
        return result

    def events(self, game_id: str, after: int = 0, *, view: str = 'god', player_id: int | None = None):
        query_values = {'view': view, 'after_event_id': after}
        if player_id is not None:
            query_values['player_id'] = player_id
        query = urlencode(query_values)
        result = self._request('GET', f'/games/{quote(game_id, safe="")}/events?{query}')
        if not isinstance(result, list):
            raise ServiceError('事件响应必须是列表')
        return result

    def metrics(self, game_id: str):
        result = self._request('GET', f'/games/{quote(game_id, safe="")}/metrics')
        if not isinstance(result, dict) or result.get('game_id') != game_id:
            raise ServiceError('统计响应局号不匹配或格式无效')
        return result

    def pending_action(self, game_id: str):
        result = self._request('GET', f'/games/{quote(game_id, safe="")}/pending-action')
        if result is not None and not isinstance(result, dict):
            raise ServiceError('待操作响应必须是对象或空值')
        return result

    def game_has_human(self, game_id: str):
        """只读探测席位控制器，用于识别真人局的权限过滤事件。"""
        result = self._request('GET', f'/games/{quote(game_id, safe="")}/status')
        controllers = result.get('seat_controllers') if isinstance(result, dict) else None
        return isinstance(controllers, dict) and any(value == 'human' for value in controllers.values())

    def game_access(self, game_id: str):
        result = self._request('GET', f'/games/{quote(game_id, safe="")}/status')
        if not isinstance(result, dict): raise ServiceError('权限状态无效')
        return result

    def identity(self, game_id: str, player_id: int):
        result = self._request('GET', f'/games/{quote(game_id, safe="")}/players/{player_id}/identity')
        if not isinstance(result, dict) or result.get('game_id') != game_id:
            raise ServiceError('本人身份响应无效')
        return result

    def models(self):
        """读取可用模型清单；服务未提供时由页面使用本地配置兜底。"""
        result = self._request('GET', '/models')
        if not isinstance(result, (list, dict)):
            raise ServiceError('模型列表响应必须是列表或对象')
        return result

    def human_action(self, game_id: str, player_id: int, action: dict, *, action_id: str, expected_cursor: int, operation_id: str):
        return self._request('POST', f'/games/{quote(game_id, safe="")}/players/{player_id}/action', {
            'action': action, 'action_id': action_id, 'expected_cursor': expected_cursor, 'operation_id': operation_id
        })

    def wolf_discussion(self, game_id: str, player_id: int, text: str, *, expected_cursor: int):
        return self._request('POST', f'/games/{quote(game_id, safe="")}/players/{player_id}/wolf-discussion', {
            'text': text, 'expected_cursor': expected_cursor
        })

    def state(self, game_id):
        if not self.contract.ready:
            raise ServiceError('现有B未提供已确认状态查询，不能解除操作歧义。')
        path = self.contract.paths.get('state', '/games/{game_id}/state').format(game_id=quote(game_id, safe=''))
        try:
            return self.contract.normalize_state(self._request('GET', path), game_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError('状态响应不符合已确认契约。') from exc

    def operation(self, game_id, operation_id):
        path = self.contract.paths.get('operation', '/games/{game_id}/operations/{operation_id}').format(
            game_id=quote(game_id, safe=''), operation_id=quote(operation_id, safe=''))
        result = self._request('GET', path)
        if (not isinstance(result, dict) or result.get('game_id') != game_id
                or result.get('operation_id') != operation_id or result.get('action') not in {'advance', 'pause', 'resume'}
                or result.get('state') not in {'pending', 'succeeded', 'failed', 'rejected'}):
            raise ServiceError('操作查询响应无效或ID不匹配。')
        return result


class BRevisionClient(GameClient):
    """B 3898b95 的部分兼容；推进可查询，暂停/继续没有操作查询保证。"""
    advance_query_supported = True
    # 真人局进行中的上帝视角会过滤私密事件，因此返回事件编号可能有间隔；
    # 控制游标仍以 /status 返回的服务端 cursor 为准。
    allows_filtered_event_gaps = True

    def control(self, game_id, action, *, operation_id=None, expected_event_id=None):
        if action not in {'advance', 'pause', 'resume'} and not (
                action == 'retry-model' and getattr(self, 'integration', False)):
            raise ValueError('unsupported control')
        payload = None
        if action in {'advance', 'retry-model'}:
            if not operation_id or type(expected_event_id) is not int:
                raise ValueError('推进缺少操作编号或游标')
            payload = {'operation_id': operation_id, 'expected_cursor': expected_event_id}
        result = self._request('POST', f'/games/{quote(game_id, safe="")}/{action}', payload)
        if not isinstance(result, dict) or result.get('game_id') != game_id:
            raise ServiceError('B控制响应局号不匹配；请只读核查。')
        if result.get('status') == 'conflict':
            raise ServiceError('游标已变化，本次推进被B拒绝；请刷新后核对。', ambiguous=False)
        if action == 'retry-model' and result.get('status') == 'rejected':
            raise ServiceError('当前没有可重试的模型超时。', ambiguous=False)
        if result.get('status') == 'waiting_model':
            result = {**result, 'status': 'pending'}
        return result

    def service_status(self, game_id):
        value = self._request('GET', f'/games/{quote(game_id, safe="")}/status')
        if (not isinstance(value, dict) or value.get('game_id') != game_id
                or value.get('status') not in {'running','waiting_model','waiting_human','paused','finished','failed','incomplete'}
                or type(value.get('cursor')) is not int or value['cursor'] < 0
                or not isinstance(value.get('phase'), str) or not value['phase']):
            raise ServiceError('B状态响应无效。')
        # paused 仅为B报告值；本版本不能据此证明当前业务操作已停稳。
        return {**value, 'status': 'pending' if value['status']=='waiting_model' else value['status']}

    def operation(self, game_id, operation_id):
        value = self._request('GET', f'/games/{quote(game_id, safe="")}/operations/{quote(operation_id, safe="")}')
        if (not isinstance(value, dict) or value.get('game_id') != game_id
                or value.get('operation_id') != operation_id or value.get('status') not in {'completed','unknown'}):
            raise ServiceError('B操作查询响应无效。')
        result = value.get('result')
        if value['status'] == 'completed' and (not isinstance(result,dict)
                or result.get('game_id') != game_id or result.get('operation_id') != operation_id):
            raise ServiceError('B已完成操作缺少匹配的结果。')
        # unknown 不能解释成未执行；持续只读等待，绝不自动重发。
        return {'game_id':game_id,'operation_id':operation_id,'action':'advance',
                'state':'succeeded' if value['status']=='completed' else 'pending','result':result}
