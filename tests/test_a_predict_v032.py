import json
import threading
import unittest
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
from model.predict_v031 import SmallModel as Previous
from model.predict_v032 import SmallModel,InputValidationError
from model.server_v032 import make_server
from tests.test_a_entities_v032 import REAL_TEXT

class VersionedInferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.current=SmallModel();cls.previous=Previous()

    def test_model_prediction_is_not_replaced_by_review(self):
        current=self.current.classify_utterance('regression','case',1,REAL_TEXT)
        previous=self.previous.classify_utterance('regression','case',1,REAL_TEXT)
        self.assertEqual(current['intent'],'role_claim')
        self.assertEqual(current['class_scores'],previous['class_scores'])
        self.assertEqual(current['score'],previous['score'])
        self.assertEqual(current['extracted_entities']['player_ids'],[3])
        self.assertEqual(current['entity_rule_version'],'entity-rules-v0.3.2')
        self.assertEqual(current['model_version'],'small-intent-v0.1')
        self.assertEqual(current['schema_version'],'small-intent-schema-v0.3')
        self.assertEqual(set(current),set(previous))
        self.assertEqual(set(current['extracted_entities']),set(previous['extracted_entities']))
        self.assertIsNone(current['confidence'])
        json.dumps(current,ensure_ascii=False,allow_nan=False)

    def test_invalid_public_input_is_still_rejected(self):
        with self.assertRaises(InputValidationError):self.current.classify_utterance('g','e',True,'我是平民')

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=make_server(port=0)
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.worker.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
        cls.open=build_opener(ProxyHandler({})).open

    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.worker.join(timeout=5)

    def test_health_identifies_new_entity_version(self):
        with self.open(self.url+'/health',timeout=5) as response:
            self.assertEqual(json.load(response)['entity_rule_version'],'entity-rules-v0.3.2')

    def test_full_public_utterance(self):
        body={'game_id':'regression','event_id':'case','speaker_id':1,'public_text':REAL_TEXT}
        request=Request(self.url+'/classify',data=json.dumps(body,ensure_ascii=False).encode('utf-8'),headers={'Content-Type':'application/json'})
        with self.open(request,timeout=5) as response:result=json.load(response)
        self.assertEqual(result['extracted_entities']['player_ids'],[3])
        self.assertEqual(result['entity_rule_version'],'entity-rules-v0.3.2')

    def test_private_extra_field_rejected(self):
        body={'game_id':'g','event_id':'e','speaker_id':1,'public_text':REAL_TEXT,'true_role':'seer'}
        request=Request(self.url+'/classify',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with self.assertRaises(HTTPError) as error:self.open(request,timeout=5)
        self.assertEqual(error.exception.code,400)

if __name__=='__main__':unittest.main()
