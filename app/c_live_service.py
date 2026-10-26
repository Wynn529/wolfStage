"""C真实模式装配入口；复用B实现，不在启动/健康检查中调用模型。"""
from pathlib import Path
import os

if not os.getenv('DEEPSEEK_API_KEY', '').strip():
    raise RuntimeError('Real mode requires a local DEEPSEEK_API_KEY; it will not fall back to stub.')
if not os.getenv('A_CLASSIFIER_URL', '').strip():
    raise RuntimeError('Real mode requires the A classifier URL.')
if not os.getenv('C_REAL_RUNS_DIR', '').strip():
    raise RuntimeError('Real mode requires an explicit runs directory.')

from app import api
from app.agents import DeepSeekAgent
from app.classifier import AHttpClassifier
from app.engine import GameEngine

_real_runs = Path(os.environ['C_REAL_RUNS_DIR']).resolve()
api.engine = GameEngine(str(_real_runs), agent=DeepSeekAgent(), classifier=AHttpClassifier())
app = api.app

@app.get('/c/ready')
def c_ready():
    return {'mode':'real_deepseek', 'agent':type(api.engine.agent).__name__,
            'model':api.engine.agent.model_version,
            'classifier_configured':api.engine.classifier is not None,
            'runs_dir':str(_real_runs)}
