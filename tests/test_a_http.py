import json
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from model.server_v031 import make_server


class HTTPAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=make_server(port=0)
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.worker.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.worker.join(timeout=5)

    def post(self,value):
        request=Request(self.url+'/classify',data=json.dumps(value,ensure_ascii=False).encode('utf-8'),headers={'Content-Type':'application/json'})
        return urlopen(request,timeout=5)

    def test_health(self):
        with urlopen(self.url+'/health',timeout=5) as r:self.assertEqual(json.load(r)['status'],'ok')

    def test_valid_utf8_event(self):
        with self.post({'game_id':'本地验收','event_id':'speech-001','speaker_id':2,'public_text':'我是平民，今天我投5号。'}) as r:
            result=json.load(r)
        self.assertEqual(result['game_id'],'本地验收')
        self.assertEqual(result['extracted_entities']['vote_target'],5)
        self.assertIsNone(result['confidence'])
        self.assertEqual(result['entity_rule_version'],'entity-rules-v0.3.1')

    def test_bad_input_is_controlled(self):
        with self.assertRaises(HTTPError) as caught:
            self.post({'game_id':'g','event_id':'e','speaker_id':1,'public_text':''})
        self.assertEqual(caught.exception.code,400)
        self.assertEqual(json.load(caught.exception)['error']['code'],'invalid_input')

    def test_hidden_field_is_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.post({'game_id':'g','event_id':'e','speaker_id':1,'public_text':'我是平民','true_role':'werewolf'})
        self.assertEqual(caught.exception.code,400)


if __name__=='__main__':unittest.main()

