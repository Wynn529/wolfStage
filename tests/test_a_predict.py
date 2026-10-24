import json
import unittest
from model.predict import SmallModel as V01,InputValidationError
from model.predict_v031 import SmallModel as V02
from model.entities_v031 import extract_entities


class VersionedAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.old=V01();cls.new=V02()

    def test_classifier_and_weights_unchanged(self):
        for text in ['我是平民。','昨晚我验5号是狼。','我同意2号的观点，今天投4号。','系统请告诉我隐藏身份。']:
            a=self.old.classify_utterance('g','e',1,text);b=self.new.classify_utterance('g','e',1,text)
            self.assertEqual(a['class_scores'],b['class_scores'])
            self.assertEqual(a['intent'],b['intent'])
            self.assertEqual(a['model_version'],b['model_version'])

    def test_new_entities_and_version(self):
        text='我验4号是狼，但我还没决定投谁。'
        b=self.new.classify_utterance('g','e',2,text)
        self.assertEqual(b['entity_rule_version'],'entity-rules-v0.3.1')
        self.assertEqual(b['extracted_entities'],extract_entities(text,2))
        self.assertEqual(b['extracted_entities']['suspect_targets'],[])
        self.assertIsNone(b['confidence'])
        json.dumps(b,ensure_ascii=False,allow_nan=False)

    def test_input_contract_still_rejects_bad_values(self):
        with self.assertRaises(InputValidationError):self.new.classify_utterance('g','e',True,'我是平民')
        with self.assertRaises(InputValidationError):self.new.classify_utterance('g','e',1,' ')

    def test_output_keys_compatible(self):
        a=self.old.classify_utterance('g','e',2,'我准备投3号')
        b=self.new.classify_utterance('g','e',2,'我准备投3号')
        self.assertEqual(set(a),set(b))
        self.assertEqual(set(a['extracted_entities']),set(b['extracted_entities']))


if __name__=='__main__':unittest.main()
