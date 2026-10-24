"""Fixed naive keyword classifier for comparison; not the trained classifier."""
import re


def keyword_intent(text):
    if re.search(r'后台|隐藏身份|私密|私聊|管理员|废物|蠢货|白痴|广告|促销|洗衣液|蛋糕|旅游|合同|股票',text):return 'suspected_violation'
    if re.search(r'查杀|金水|验.*(?:狼|好人|非狼)|查验.*结果',text):return 'check_claim'
    if re.search(r'我.*(?:村民|平民|预言家)|预言家是我',text):return 'role_claim'
    if re.search(r'投|归票|放逐|抗推|弃票',text):return 'vote_intent'
    if re.search(r'[1-6一二三四五六]号|证据|矛盾|规则|身份|记录|观点',text):return 'discussion'
    return 'low_info'
