import json
import pytest
from app.rules import GameState, check_action, winner
from app.agents import legal_actions_for, StubAgent, build_context
from app.engine import GameEngine

ROLES={1:'wolf',2:'wolf',3:'wolf',4:'seer',5:'witch',6:'hunter',7:'villager',8:'villager',9:'villager',10:'villager'}

def game(tmp_path, human=None, agent=None, classifier=None):
    e=GameEngine(str(tmp_path),agent=agent or StubAgent(),classifier=classifier)
    gid=e.create_game(42,ROLES,seat_controllers={human:'human'} if human else None)['game_id']
    return e,e.games[gid]

@pytest.mark.parametrize('day,knife,allowed',[(0,5,True),(1,5,False),(7,5,False),(1,7,True),(0,None,False)])
def test_save_boundaries(day,knife,allowed):
    s=GameState('g',1,ROLES);s.day=day;s.pending['night_kill_target']=knife
    assert (check_action(s,5,{'action':'save','target':None}) is None)==allowed
    assert ('save' in [x['action'] for x in legal_actions_for(s,5)])==allowed

@pytest.mark.parametrize('save,poison,used,expected',[(True,True,False,{'save','poison','pass'}),(False,True,False,{'poison','pass'}),(True,False,False,{'save','pass'}),(False,False,False,{'pass'}),(True,True,True,{'pass'})])
def test_witch_legal_actions_match_validator(save,poison,used,expected):
    s=GameState('g',1,ROLES);s.pending.update(night_kill_target=7,witch_used=used);s.witch_save_available=save;s.witch_poison_available=poison
    hints=legal_actions_for(s,5);assert {h['action'] for h in hints}==expected
    for h in hints:
        for target in h['targets'] or [None]:assert check_action(s,5,{'action':h['action'],'target':target}) is None

@pytest.mark.parametrize('target',[4,True,[],{},99,7])
def test_seer_invalid_and_repeat_targets_are_controlled(target):
    s=GameState('g',1,ROLES);s.inspection_history.add(7)
    assert check_action(s,4,{'action':'inspect','target':target}) is not None
    assert all(t not in {4,7} for t in legal_actions_for(s,4)[0]['targets'])

class SkillAgent:
    model_name='scripted-test';model_version='fixture';last_metadata={}
    def __init__(self):self.phases=[]
    def act(self,state,actor,context):
        self.phases.append((actor,state.phase,context))
        if state.phase=='hunter_action':return {'action':'shoot','target':1,'text':'','reason':'choose a legal target'}
        return {'action':'last_words','target':None,'text':'我的遗言。','reason':''}

@pytest.mark.parametrize('human',[False,True])
@pytest.mark.parametrize('poison',[False,True])
def test_night_hunter_and_poison_priority(tmp_path,human,poison):
    agent=SkillAgent();e,s=game(tmp_path,6 if human else None,agent)
    s.pending={'kill_proposals':{1:6,2:6,3:6}}
    if poison:s.pending['poison_target']=6
    e._resolve_night(s)
    deaths=[x for x in e.store.read(s.game_id) if x['type']=='night_death'];assert [x['target'] for x in deaths]==[6]
    if poison:
        assert s.phase=='day_speak' and not s.hunter_triggered
        assert s.death_reasons[6]=='witch_poison'
        return
    assert s.phase=='hunter_action' and s.hunter_triggered
    if human:
        assert s.status=='waiting_human';pending=s.pending_action
        assert {x['action'] for x in e.get_player_identity(s.game_id,6)['legal_actions']}=={'shoot','pass'}
        e.submit_human_action(s.game_id,6,{'action':'shoot','target':1},action_id=pending['action_id'],expected_cursor=s.event_id)
    else:e.advance_one_event(s.game_id)
    assert 1 not in s.alive and s.phase=='day_speak' and s.day==1
    assert s.hunter_processed
    assert sum(x['type']=='hunter_shot' for x in e.store.read(s.game_id))==1
    assert len(e.store.read_calls(s.game_id))==(0 if human else 1)
    assert bool(agent.phases)==(not human)

@pytest.mark.parametrize('human',[False,True])
@pytest.mark.parametrize('kind,target',[('shoot',1),('pass',None)])
def test_exile_hunter_then_last_words_then_same_day_night(tmp_path,human,kind,target):
    agent=SkillAgent();e,s=game(tmp_path,6 if human else None,agent)
    s.phase='day_vote';s.day=2;s.day_voted=list(s.alive);s.votes={p:6 for p in s.alive if p!=6}
    e.advance_one_event(s.game_id)
    assert s.phase=='hunter_action' and s.day==2
    if human:
        pending=s.pending_action;e.submit_human_action(s.game_id,6,{'action':kind,'target':target},action_id=pending['action_id'],expected_cursor=s.event_id)
    else:
        if kind=='pass':agent.act=lambda *args:{'action':'pass','target':None}
        e.advance_one_event(s.game_id)
    assert s.phase=='last_words' and s.day==2
    if human:
        pending=s.pending_action;e.submit_human_action(s.game_id,6,{'action':'last_words','target':None,'text':'我的遗言。'},action_id=pending['action_id'],expected_cursor=s.event_id)
    else:
        agent.act=lambda *args:{'action':'last_words','target':None,'text':'我的遗言。'};e.advance_one_event(s.game_id)
    events=e.store.read(s.game_id);shot=next(x for x in events if x['type'] in {'hunter_shot','hunter_pass'});words=next(x for x in events if x['type']=='last_words')
    assert shot['event_id']<words['event_id'] and words['visibility']=='public'
    assert s.phase=='night' and s.day==2


def test_hunter_pending_survives_restart_without_second_shot(tmp_path):
    e,s=game(tmp_path,agent=SkillAgent());s.pending={'kill_proposals':{1:6}};e._resolve_night(s);e.store.write_state(s.game_id,s.to_dict())
    re=GameEngine(str(tmp_path),agent=SkillAgent());state=re.games[s.game_id];assert state.death_reasons[6]=='wolf_kill'
    re.advance_one_event(s.game_id,operation_id='gun');cursor=state.event_id;re.advance_one_event(s.game_id,operation_id='gun')
    assert state.event_id==cursor and sum(x['type']=='hunter_shot' for x in re.store.read(s.game_id))==1


def test_hunter_does_not_lose_shot_when_last_god_dies(tmp_path):
    e,s=game(tmp_path,agent=SkillAgent());s.alive={1,2,3,6,7};s.pending={'kill_proposals':{1:6}}
    e._resolve_night(s);assert not e._check_end(s) and s.phase=='hunter_action'
    e.advance_one_event(s.game_id);assert 1 not in s.alive and s.status=='finished'


def test_last_words_are_classified_as_public_claim_not_action(tmp_path):
    seen=[]
    def classify(gid,eid,actor,text):
        seen.append((actor,text));return {'intent':'discussion','extracted_entities':{'skill_claims':[{'skill':'poison','target':2}]}}
    e,s=game(tmp_path,agent=SkillAgent(),classifier=classify);s.alive.remove(7);e._start_last_words(s,7);e.advance_one_event(s.game_id)
    assert seen==[(7,'我的遗言。')] and 2 in s.alive
    assert any(x['type']=='utterance_classification' for x in e.store.read(s.game_id))


def test_witch_same_night_action_then_resource_not_reusable(tmp_path):
    e,s=game(tmp_path,5);s.pending={'kill_proposals':{1:5},'night_kill_target':5};s.night_done={1,2,3,'seer'}
    e.advance_one_event(s.game_id);pending=s.pending_action
    e.submit_human_action(s.game_id,5,{'action':'save','target':None},action_id=pending['action_id'],expected_cursor=s.event_id)
    assert not s.witch_save_available and s.pending['saved']
    assert check_action(s,5,{'action':'poison','target':7}) is not None
    e.advance_one_event(s.game_id);assert 5 in s.alive


def test_private_god_information_does_not_become_public(tmp_path):
    e,s=game(tmp_path,5);s.pending={'kill_proposals':{1:7},'night_kill_target':7};s.night_done={1,2,3,'seer'}
    e.advance_one_event(s.game_id)
    assert not any(x.get('roles') or x.get('reason') or 'night_kill_target' in x for x in e.get_events(s.game_id,view='god'))

@pytest.mark.parametrize('alive,expected',[({1,2,3},'wolves'),({7,8},'draw'),({4,7},'villagers')])
def test_ten_winner_conditions(alive,expected):
    assert winner(GameState('g',1,ROLES,alive=alive))==expected


@pytest.mark.parametrize('human',[False,True])
def test_hunter_skip_and_dead_skill_rejection(tmp_path,human):
    e,s=game(tmp_path,6 if human else None);s.pending={'kill_proposals':{1:6}};e._resolve_night(s)
    if human:
        pending=s.pending_action
        bad=e.submit_human_action(s.game_id,6,{'action':'shoot','target':6},action_id=pending['action_id'],expected_cursor=s.event_id)
        assert bad['status']=='rejected' and s.status=='waiting_human'
        e.submit_human_action(s.game_id,6,{'action':'pass','target':None},action_id=pending['action_id'],expected_cursor=s.event_id)
    else:e.advance_one_event(s.game_id)
    assert s.hunter_processed and s.phase=='day_speak'
    assert check_action(s,6,{'action':'shoot','target':1}) is not None
    assert sum(x['type']=='hunter_pass' for x in e.store.read(s.game_id))==1


def test_seer_night_death_still_keeps_private_inspection(tmp_path):
    e,s=game(tmp_path);s.pending={'kill_proposals':{1:4},'night_kill_target':4};s.night_done={1,2,3}
    e.advance_one_event(s.game_id)
    inspections=[x for x in e.store.read(s.game_id) if x['type']=='inspection']
    assert len(inspections)==1 and inspections[0]['actor']==4 and inspections[0]['visibility']==[4,'host','god']
    e.advance_one_event(s.game_id);e.advance_one_event(s.game_id)
    assert 4 not in s.alive and 4 not in s.inspection_history
    assert build_context(s,e.store.read(s.game_id),4,'seer')['inspection_results']
    assert not any(x['type']=='inspection' for x in e.get_events(s.game_id,view='public'))


def test_human_legal_hints_via_http_only_contain_witch_choices(tmp_path,monkeypatch):
    import socket, threading, time, uvicorn
    from urllib.request import Request, build_opener, ProxyHandler
    import app.integration_service as api
    e,s=game(tmp_path,5);s.pending={'kill_proposals':{1:7},'night_kill_target':7};s.night_done={1,2,3,'seer'}
    e.advance_one_event(s.game_id)
    monkeypatch.setattr(api,'GameEngine',lambda *args,**kwargs:e)
    application=api.create_app(tmp_path/'http-service')
    token=application.state.sessions.bind(s.game_id,5)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(application,log_level='error',lifespan='off'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    opener=build_opener(ProxyHandler({}))
    def request(path,body=None):
        data=None if body is None else json.dumps(body).encode('utf-8')
        with opener.open(Request(f'http://127.0.0.1:{port}'+path,data=data,headers={'Content-Type':'application/json','Authorization':'Bearer '+token}),timeout=5) as response:
            return json.load(response)
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        _exercise_witch_http(e,s,request)
    finally:
        server.should_exit=True;thread.join(10);sock.close()


def _exercise_witch_http(e,s,request):
    identity=request(f'/games/{s.game_id}/status')
    assert 'roles' not in identity and {x['action'] for x in identity['action_contract']['actions']}=={'save','poison','pass'}
    pending=identity['pending_action']
    response=request(f'/games/{s.game_id}/players/5/action',{'action_id':pending['action_id'],'expected_cursor':s.event_id,'operation_id':'witch-poison','action':{'action':'poison','target':8,'text':''}})
    assert response['status']=='running' and not s.witch_poison_available
    e.advance_one_event(s.game_id)
    assert 7 not in s.alive and 8 not in s.alive


def test_night_god_agent_receives_seer_and_witch_skill_options(tmp_path):
    class NightAgent(SkillAgent):
        def act(self,state,actor,context):
            self.phases.append((actor,state.phase,context))
            if actor in {1,2,3}:return {'action':'kill','target':7}
            if actor==4:return {'action':'inspect','target':1}
            if actor==5:return {'action':'poison','target':8}
            return super().act(state,actor,context)
    agent=NightAgent();e,s=game(tmp_path,agent=agent)
    for _ in range(6):e.advance_one_event(s.game_id)
    assert 7 not in s.alive and 8 not in s.alive
    assert any(x['type']=='inspection' for x in e.store.read(s.game_id))
    witch=next(c for a,phase,c in agent.phases if a==5)
    assert {x['action'] for x in witch['legal_actions']}=={'save','poison','pass'}
    calls=e.store.read_calls(s.game_id);assert len(calls)==5 and all(c['success'] for c in calls)
