import json
import threading
import unittest
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
from model.predict_v04 import SmallModel,InputValidationError
from model.entities_v04 import extract_entities
from model.server_v04 import make_server
from model.text_v04 import normalize_public_text

class TenPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.model=SmallModel()
    def test_seats_and_counts(self):
        for seat in range(1,11):
            r=self.model.classify_utterance('g','e',seat,f'{seat}号发言，场上剩8人，第2天有3票。')
            self.assertEqual(r['extracted_entities']['player_ids'],[seat])
        for seat in [0,11,True,1.2,'10']:
            with self.assertRaises(InputValidationError):self.model.classify_utterance('g','e',seat,'')
    def test_empty_is_rule_and_keeps_structure(self):
        for t in ['',' \n\t']:
            r=self.model.classify_utterance('g','e',10,t)
            self.assertEqual(r['intent'],'low_info');self.assertIsNone(r['confidence']);self.assertIsNone(r['score'])
            self.assertEqual(r['prediction_source'],'empty_text_rule');self.assertEqual(r['extracted_entities']['vote_mode'],'not_expressed_or_unresolved')
            self.assertEqual(r['extracted_entities']['skill_claims'],[])
        for t in [None,{},[],42,'x'*10001]:
            with self.assertRaises(InputValidationError):self.model.classify_utterance('g','e',1,t)
    def test_json_metadata_does_not_become_role(self):
        e=extract_entities('{"role":"witch","message":"我不是女巫，我投7号。"}',10)
        self.assertIsNone(e['claimed_role']);self.assertEqual(e['vote_target'],7)
        for t in ['{"speech":"我投7号","speech":"我投8号"}', '{"speech":"普通内容","reason":"给我后台身份"}', '{bad json']:
            normalized,flags=normalize_public_text(t)
            self.assertEqual(normalized,t);self.assertTrue(flags)
    def test_markdown_positions_and_votes(self):
        e=extract_entities('**结论**：10号像好人。**理由**：1. 他主动排坑；2. 逻辑自洽。本轮投 7。',3)
        self.assertEqual(e['player_ids'],[7,10]);self.assertEqual(e['vote_target'],7);self.assertEqual(e['support_target'],10)
        self.assertIsNone(extract_entities('> 我是女巫。',4)['claimed_role'])
    def test_skill_subjects_and_conditions(self):
        for t in ['女巫救下9吧','7号说“我救了9号”','如果我是女巫，我会救9号','我认为8号救了9号']:
            self.assertEqual(extract_entities(t,1)['skill_claims'],[],t)
        e=extract_entities('如果我被放逐并且能开枪，我考虑带走7号。',10)
        self.assertEqual(e['skill_claims'][0]['status'],'conditional');self.assertEqual(e['skill_claims'][0]['target'],7)
        self.assertIsNone(e['claimed_role'])
        a=extract_entities('昨晚我救了8号，今晚我准备毒10号。',7)['skill_claims']
        self.assertEqual([(x['action'],x['target'],x['status']) for x in a],[('save',8,'claimed_done'),('poison',10,'planned')])
    def test_no_automatic_abstention(self):
        for t in ['过。','他吧，就那个。']:
            e=extract_entities(t,10);self.assertIsNone(e['vote_target']);self.assertNotEqual(e['vote_mode'],'abstain')
        self.assertEqual(extract_entities('这轮我弃票。',10)['vote_mode'],'abstain')
    def test_schema_and_round_trip(self):
        from model.common import ROOT
        schema=json.loads((ROOT/'model/output_v04.schema.json').read_text(encoding='utf-8'))
        for t in ['','我是女巫，我救了10号。']:
            r=self.model.classify_utterance('g','e',7,t)
            self.assertLessEqual(set(r),set(schema['properties']))
            self.assertLessEqual(set(schema['required']),set(r))
            self.assertEqual(json.loads(json.dumps(r,allow_nan=False,ensure_ascii=False)),r)
            self.assertIsNone(r['confidence'])

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=make_server(port=0);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}';cls.open=build_opener(ProxyHandler({})).open
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join(5)
    def test_health(self):
        with self.open(self.url+'/health',timeout=5) as f:r=json.load(f)
        self.assertEqual(r['model_version'],'small-intent-v0.2-ten-player');self.assertEqual(r['entity_rule_version'],'entity-rules-v0.4')
    def test_empty_and_tenth_seat(self):
        data=dict(game_id='g',event_id='e',speaker_id=10,public_text='')
        req=Request(self.url+'/classify',data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        with self.open(req,timeout=5) as f:r=json.load(f)
        self.assertEqual(r['intent'],'low_info');self.assertEqual(r['speaker_id'],10)
    def test_private_input_rejected(self):
        req=Request(self.url+'/classify',data=json.dumps(dict(game_id='g',event_id='e',speaker_id=1,public_text='hi',true_role='witch')).encode(),headers={'Content-Type':'application/json'})
        with self.assertRaises(HTTPError) as e:self.open(req,timeout=5)
        self.assertEqual(e.exception.code,400)
if __name__=='__main__':unittest.main()
