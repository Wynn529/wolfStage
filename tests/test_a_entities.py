import unittest
from model.entities_v031 import extract_entities
from tests.a_entity_cases import CASES


class OwnershipGuardTests(unittest.TestCase):
    def test_received_report_not_my_check(self):
        for text in ['2号向我报告5号金水，我没有亲自查验。','4号说我验过1号是好人，这是他的转述。']:
            with self.subTest(text=text):self.assertEqual(extract_entities(text)['check_claims'],[])

    def test_self_report_after_other_report_retained(self):
        e=extract_entities('2号向我报告5号金水；我的查验是4号狼人。')
        self.assertEqual(e['check_claims'],[{'target':4,'result':'wolf','night':None}])

    def test_development_cases_compatible(self):
        for name,text,speaker,expected in CASES:
            with self.subTest(name=name):
                result=extract_entities(text,speaker)
                for k,v in expected.items():self.assertEqual(result[k],v,(name,k))


if __name__=='__main__':unittest.main()
