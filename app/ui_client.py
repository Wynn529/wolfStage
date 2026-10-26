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

    def create(self, seed: int):
        result = self._request('POST', '/games', {'seed': seed})
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

    def events(self, game_id: str, after: int = 0):
        query = urlencode({'view': 'god', 'after_event_id': after})
        result = self._request('GET', f'/games/{quote(game_id, safe="")}/events?{query}')
        if not isinstance(result, list):
            raise ServiceError('事件响应必须是列表')
        return result

    def metrics(self, game_id: str):
        result = self._request('GET', f'/games/{quote(game_id, safe="")}/metrics')
        if not isinstance(result, dict) or result.get('game_id') != game_id:
            raise ServiceError('统计响应局号不匹配或格式无效')
        return result

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

    def control(self, game_id, action, *, operation_id=None, expected_event_id=None):
        if action not in {'advance', 'pause', 'resume'}:
            raise ValueError('unsupported control')
        payload = None
        if action == 'advance':
            if not operation_id or type(expected_event_id) is not int:
                raise ValueError('推进缺少操作编号或游标')
            payload = {'operation_id': operation_id, 'expected_cursor': expected_event_id}
        result = self._request('POST', f'/games/{quote(game_id, safe="")}/{action}', payload)
        if not isinstance(result, dict) or result.get('game_id') != game_id:
            raise ServiceError('B控制响应局号不匹配；请只读核查。')
        if result.get('status') == 'conflict':
            raise ServiceError('游标已变化，本次推进被B拒绝；请刷新后核对。', ambiguous=False)
        if result.get('status') == 'waiting_model':
            result = {**result, 'status': 'pending'}
        return result

    def service_status(self, game_id):
        value = self._request('GET', f'/games/{quote(game_id, safe="")}/status')
        if (not isinstance(value, dict) or value.get('game_id') != game_id
                or value.get('status') not in {'running','waiting_model','paused','finished','failed','incomplete'}
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
