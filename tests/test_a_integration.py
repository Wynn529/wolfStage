from app.engine import GameEngine


class FakeA:
    model_name = "small-intent-v0.1"

    def __call__(self, game_id, event_id, speaker_id, public_text):
        return {"game_id": game_id, "event_id": str(event_id), "speaker_id": speaker_id, "public_text": public_text, "intent": "discussion", "model_version": "small-intent-v0.1", "latency_ms": 0.1}


def test_public_speech_is_sent_to_a_and_logged(tmp_path):
    engine = GameEngine(str(tmp_path / "runs"), classifier=FakeA())
    gid = engine.create_game(42)["game_id"]
    for _ in range(20):
        result = engine.advance_one_event(gid)
        if any(e["type"] == "public_speech" for e in engine.store.read(gid)):
            break
    events = engine.store.read(gid)
    calls = engine.store.read_calls(gid)
    speech = next(e for e in events if e["type"] == "public_speech")
    classification = next(e for e in events if e["type"] == "utterance_classification")
    assert classification["source_event_id"] == speech["event_id"]
    assert any(c["caller_type"] == "small_model" and c["event_id"] == speech["event_id"] for c in calls)
