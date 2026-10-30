"""Quantity/seat boundaries and compatibility; development regression, not a blind test."""
import unittest
from model.entities_v032 import extract_entities
from model.entities_v031 import extract_entities as preserved_extract

REAL_TEXT='3号昨晚出局，场上剩5人。目前没有更多公开信息，我先听大家发言。请各位说明自己的身份判断和怀疑对象，尤其关注谁在带节奏或回避问题。'

CASES=[
 ('人数', '3号昨晚出局，场上剩5人。', [3]),
 ('带空格人数', '场上剩 5 人，3 号出局。', [3]),
 ('中文编号', '三号出局，还剩五人。', [3]),
 ('人名位', '剩5名玩家，共5位，3号已出局。', [3]),
 ('天轮', '第2天到了，已经过3轮，4号解释一下。', [4]),
 ('票数', '3号拿了2票，4号拿了1票。', [3,4]),
 ('数量混合', '剩5人，第2天，3票，4分钟，6次，1个问题。', []),
 ('时间', '等3分钟和2小时，5号再发言。', [5]),
 ('数字不能拼接', '1票2人，3号在场。', [3]),
 ('第X号仍是编号', '第3号玩家请说话。', [3]),
 ('省略号', '我投3，但先不投4。', [3,4]),
 ('并列', '我怀疑1、3和5号。', [1,3,5]),
 ('显式编号优先', '3号有2票，2号有3票。', [2,3]),
 ('多位数字', '12人玩过15局，3号发言。', [3]),
 ('小数', '我只用了2.5分钟，3号说话。', [3]),
 ('百分数', '概率5%和6％不能说明身份，2号再解释。', [2]),
 ('分数', '目前剩5/6人，我同意3号。', [3]),
 ('次数', '我已经提问3次，请6号回复。', [6]),
 ('中文序数', '第二夜第3轮，二号请说话。', [2]),
 ('日期版本', '2026-10-30使用v0.3.1，4号发言。', [4]),
]

class QuantityTests(unittest.TestCase):
    def test_numeric_boundaries(self):
        for name,text,expected in CASES:
            with self.subTest(name=name):self.assertEqual(extract_entities(text)['player_ids'],expected)

    def test_quantity_changes_do_not_change_player_identity(self):
        for player in range(1,7):
            for count in range(1,7):
                text=f'{player}号昨晚出局，场上剩{count}人，第{count}天收到了{count}票。'
                with self.subTest(player=player,count=count):
                    self.assertEqual(extract_entities(text)['player_ids'],[player])

    def test_real_case_has_no_fabricated_claim_or_stance(self):
        for speaker in [None,*range(1,7)]:
            result=extract_entities(REAL_TEXT,speaker)
            self.assertEqual(result['player_ids'],[3])
            for k in ['claimed_role','check_target','vote_target','follow_target']:
                self.assertIsNone(result[k])
            for k in ['check_claims','suspect_targets','support_targets','agreement_targets']:
                self.assertEqual(result[k],[])

    def test_vote_quantity_is_not_vote_target(self):
        for text in ['我拿到了3票。','我投了3票。','我支持2个人发言。','我怀疑3人，但未说编号。']:
            with self.subTest(text=text):
                result=extract_entities(text)
                self.assertEqual(result['player_ids'],[])
                self.assertIsNone(result['vote_target'])
                self.assertEqual(result['suspect_targets'],[])
                self.assertEqual(result['support_targets'],[])

    def test_night_and_target_have_separate_meanings(self):
        result=extract_entities('第2夜我验3号是狼，今天投4号；他已经拿了2票。')
        self.assertEqual(result['player_ids'],[3,4])
        self.assertEqual(result['check_claims'],[{'target':3,'result':'wolf','night':2}])
        self.assertEqual(result['vote_target'],4)

    def test_reported_speech_guard_is_preserved(self):
        result=extract_entities('2号向我报告5号金水；我的查验是4号狼人。')
        self.assertEqual(result['check_claims'],[{'target':4,'result':'wolf','night':None}])

    def test_previous_entry_is_unchanged(self):
        self.assertEqual(preserved_extract(REAL_TEXT)['player_ids'],[3,5])

if __name__=='__main__':unittest.main()
