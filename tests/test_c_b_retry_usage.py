"""C新增回归：重试也无效时，第二次usage必须保留第二次原值。"""
import pytest
from app.engine import GameEngine
from app.agents import DeepSeekAgent


@pytest.mark.xfail(strict=True, reason='B bc79b60: invalid retry keeps first-attempt usage/latency')
def test_invalid_retry_preserves_each_attempt_usage(tmp_path):
    class Client:
        model='C_OFFLINE_NO_NETWORK'
        def __init__(self):self.attempt=0
        def complete(self,*args,**kwargs):
            self.attempt+=1
            return ({'action':'kill','target':99,'text':'','reason':'C regression only'},
                    {'input_tokens':111*self.attempt,'output_tokens':11*self.attempt,'latency_ms':100*self.attempt})
    engine=GameEngine(str(tmp_path),agent=DeepSeekAgent(client=Client()))
    gid=engine.create_game(42,{1:'wolf',2:'wolf',3:'seer',4:'villager',5:'villager',6:'villager'})['game_id']
    engine.advance_one_event(gid,'invalid-retry',1)
    calls=engine.store.read_calls(gid)
    assert len(calls)==2 and all(not row['success'] for row in calls)
    assert [(c['input_tokens'],c['output_tokens'],c['latency_ms']) for c in calls]==[(111,11,100),(222,22,200)]
