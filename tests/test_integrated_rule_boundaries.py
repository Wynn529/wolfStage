from app.agents import StubAgent, action_contract
from app.engine import GameEngine
from app.rules import GameState, check_action

ROLES = {1:"wolf",2:"wolf",3:"wolf",4:"seer",5:"witch",6:"hunter",7:"villager",8:"villager",9:"villager",10:"villager"}


class SkillAgent(StubAgent):
    def act(self, state, actor, context):
        if state.phase == "hunter_action":
            return dict(action="shoot", target=1, text="", reason="test")
        if state.phase == "last_words":
            return dict(action="last_words", target=None, text="遗言测试", reason="test")
        return super().act(state, actor, context)


def test_hunter_ai_called_before_night_win_check(tmp_path):
    e = GameEngine(str(tmp_path), agent=SkillAgent())
    gid = e.create_game(role_config=ROLES)["game_id"]
    s = e.games[gid]
    s.pending["kill_proposals"] = {1:6,2:6,3:6}
    e._resolve_night(s)
    assert s.phase == "hunter_action"
    e.advance_one_event(gid)
    assert 1 not in s.alive and s.hunter_processed
    assert e.store.read_calls(gid)[0]["agent_id"] == 6


def test_poison_overrides_knife_for_hunter(tmp_path):
    e = GameEngine(str(tmp_path), agent=SkillAgent())
    gid = e.create_game(role_config=ROLES)["game_id"]
    s = e.games[gid]
    s.pending.update(kill_proposals={1:6},poison_target=6)
    e._resolve_night(s)
    assert s.death_reasons[6] == "witch_poison" and s.phase == "day_speak"
    assert not s.hunter_triggered


def test_exiled_hunter_shoots_then_speaks(tmp_path):
    e = GameEngine(str(tmp_path), agent=SkillAgent())
    gid = e.create_game(role_config=ROLES)["game_id"]
    s = e.games[gid]
    s.phase="day_vote";s.day=1;s.day_voted=set(ROLES);s.votes={1:6,2:6,3:6}
    e.advance_one_event(gid)
    assert s.phase == "hunter_action"
    e.advance_one_event(gid)
    assert s.phase == "last_words" and 1 not in s.alive
    e.advance_one_event(gid)
    assert any(v["type"]=="last_words" and v["text"]=="遗言测试" for v in e.store.read(gid))


def test_witch_second_night_self_save_rejected_and_contract_matches():
    s=GameState("g",1,ROLES,day=1,pending={"night_kill_target":5})
    assert check_action(s,5,dict(action="save",target=None))
    assert "save" not in action_contract(s,5)["allowed_actions"]
    assert {"pass","poison"} == set(action_contract(s,5)["allowed_actions"])


def test_offline_ten_player_game_terminates(tmp_path):
    e=GameEngine(str(tmp_path),agent=StubAgent())
    gid=e.create_game(seed=42)["game_id"]
    for i in range(300):
        result=e.advance_one_event(gid,str(i))
        if result["status"] in {"finished","failed","incomplete"}:
            break
    assert result["status"] == "finished"
