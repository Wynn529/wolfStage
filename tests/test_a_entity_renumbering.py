"""Player renumbering must preserve entity meaning; night numbers stay unchanged."""
import re
import unittest
from model.entities_v031 import extract_entities
from tests.a_entity_cases import CASES


class RenumberingTests(unittest.TestCase):
    def test_cyclic_player_permutations(self):
        for shift in range(6):
            remap=lambda n:(n-1+shift)%6+1
            for name,text,speaker,expected in CASES:
                with self.subTest(shift=shift,case=name):
                    transformed=re.sub(r'(?<!\d)([1-6])(?=号)',lambda m:str(remap(int(m[1]))),text)
                    actual=extract_entities(transformed,remap(speaker) if speaker else None)
                    for field,value in expected.items():
                        if field=='check_claims':value=[{**v,'target':remap(v['target'])} for v in value]
                        elif field.endswith('_targets') or field=='player_ids':value=sorted(remap(n) for n in value)
                        elif field.endswith('_target') and value is not None:value=remap(value)
                        self.assertEqual(actual[field],value,(name,shift,field))


if __name__=='__main__':unittest.main()
