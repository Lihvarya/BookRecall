"""Shared factual quality gates for static and dynamic structured indexes."""

from __future__ import annotations


RELATION_CUES = {
    "冲突": ("攻击", "袭击", "刺", "杀", "追杀", "对峙", "交战", "重创", "打伤", "敌"),
    "同伴/协作": ("帮助", "相助", "同行", "合作", "联手", "救", "同伴", "并肩"),
    "师徒/传承": ("师父", "师尊", "弟子", "传授", "教导", "传承"),
    "亲缘/家族": ("父", "母", "兄", "弟", "姐", "妹", "夫妻", "家族", "血脉"),
    "隶属/组织": ("加入", "隶属", "麾下", "成员", "长老", "掌门", "门派", "组织"),
    "交易/利用": ("交易", "交换", "利用", "条件", "筹码", "合作", "报酬"),
    "因果/线索": ("因为", "导致", "因此", "所以", "线索", "真相", "发现", "揭示"),
    "关系变化": ("不再", "转而", "从此", "并非", "意识到", "反目", "和解", "背叛"),
}

EVENT_CUES = {
    "获得/失去": ("得到", "获得", "拿到", "失去", "夺走", "交出", "归还", "转交"),
    "冲突/危机": ("攻击", "袭击", "刺", "杀", "追杀", "对峙", "交战", "重创", "危机", "受伤"),
    "揭示/真相": ("发现", "揭示", "真相", "得知", "明白", "暴露"),
    "选择/决定": ("决定", "选择", "拒绝", "答应", "放弃"),
    "协作/同行": ("帮助", "相助", "同行", "合作", "联手", "救", "并肩"),
    "身份/关系变化": ("身份", "关系", "不再", "转而", "从此", "背叛", "和解"),
    "转折/后果": ("导致", "结果", "终于", "从此", "却", "后果"),
    "因果链": ("因为", "导致", "因此", "所以", "使得", "为了"),
    "道具流转": ("得到", "获得", "交给", "转交", "夺走", "失去", "归还", "拿到"),
    "伏笔/回收": ("线索", "伏笔", "回收", "呼应", "原来", "终于"),
    "关系变化": ("不再", "转而", "从此", "并非", "意识到", "反目", "和解", "背叛"),
}


def relation_supported(relation_type: str, evidence: str) -> bool:
    cues = RELATION_CUES.get(relation_type)
    return bool(cues) and any(cue in evidence for cue in cues)


def event_supported(event_type: str, summary: str, evidence: str) -> bool:
    cues = EVENT_CUES.get(event_type)
    if not cues or not any(cue in evidence for cue in cues):
        return False
    if any(claim in summary for claim in ("死亡", "死去", "死了", "身亡", "丧命", "杀死", "杀害", "击毙")):
        direct_death = ("他死了", "她死了", "身亡", "丧命", "断了气息", "尸躯", "杀死", "杀害", "击毙")
        if not any(marker in evidence for marker in direct_death):
            return False
    claim_groups = (
        (("获得", "得到", "拿到"), ("获得", "得到", "拿到")),
        (("失去", "丢失", "被夺"), ("失去", "丢失", "夺走", "被夺")),
        (("背叛",), ("背叛", "反目")),
    )
    for claims, support in claim_groups:
        if any(claim in summary for claim in claims) and not any(marker in evidence for marker in support):
            return False
    return True
