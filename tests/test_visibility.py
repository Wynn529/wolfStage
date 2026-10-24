from app.agents import build_context, visible_events
from app.rules import GameState


def test_private_events_are_filtered_by_viewer():
    events = [
        {"type": "wolf_proposal", "visibility": [1, 2]},
        {"type": "inspection", "visibility": [3, "host", "god"]},
        {"type": "public_speech", "visibility": "public"},
    ]
    assert [e["type"] for e in visible_events(events, 1)] == ["wolf_proposal", "public_speech"]
    assert [e["type"] for e in visible_events(events, 3)] == ["inspection", "public_speech"]
    assert len(visible_events(events, "god")) == 3


def test_context_does_not_include_other_secret():
    state = GameState("g", 1, {1: "wolf", 2: "wolf", 3: "seer", 4: "villager", 5: "villager", 6: "villager"})
    events = [{"type": "inspection", "visibility": [3, "host", "god"], "result": "wolf"}]
    context = build_context(state, events, 4, "villager")
    assert context["events"] == []
