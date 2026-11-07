"""Explicit offline preview of integration HTTP; never calls a provider."""
from app.agents import StubAgent, legal_actions_for
from app.integration_service import create_app
from app.rules import check_action
from pathlib import Path


class PreviewAgent(StubAgent):
    model_name = 'offline-fixture'
    model_version = 'offline-fixture'

    def act(self, state, actor, context):
        action = super().act(state, actor, context)
        if check_action(state, actor, action):
            choices = legal_actions_for(state, actor)
            choice = next((c for c in choices if c['action'] == 'pass'), choices[0])
            action = dict(action=choice['action'], target=choice['targets'][0] if choice['targets'] else None,
                          text='离线示例发言' if choice['action'] in {'speak', 'last_words'} else '', reason='离线测试逻辑')
        self.last_metadata = dict(model_name='offline-fixture', model_version='offline-fixture',
                                 provider='offline', requested_model=state.seat_model_configs.get(actor, {}).get('model'),
                                 input_tokens=None, output_tokens=None, latency_ms=0)
        return action


def create_preview_app(runs_dir=None):
    app = create_app(runs_dir or Path(__file__).resolve().parents[1]/'.local/c_integration_preview_runs')
    app.state.engine.configured_agent = PreviewAgent()
    return app
