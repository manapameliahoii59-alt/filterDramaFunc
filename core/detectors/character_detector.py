"""
character_detector.py
人物角色主体与戏剧对抗冲突检测算子
负责粗筛一票否决：
1. 缺失核心主角主体（纯风景抒情散文、空洞世界观说明文）
2. 缺失二元戏剧矛盾对抗（平淡记账流水账、无冲突无反派的琐碎日常）
"""
import re
from typing import Optional, List, Set
from .base import BaseDetector
from ..models import DetectorResult

# 1. 核心主角代词与常见短剧主角身份原型词库
PROTAGONIST_PRONOUNS = {"我", "老子", "本尊", "本王", "本座", "在下", "俺", "朕"}

PROTAGONIST_ARCHETYPES = [
    "战神", "龙帅", "龙王", "首富", "千金", "真千金", "假千金", "赘婿", "神医",
    "医圣", "阎王", "少爷", "小姐", "总裁", "顾总", "苏总", "董事长", "继承人",
    "林辰", "叶凡", "萧炎", "楚风", "秦羽", "苏晴", "顾寒", "少主", "尊上", "弟子"
]

# 2. 对立反派/施害方角色词库
ANTAGONIST_ROLES = [
    "后妈", "岳母", "丈母娘", "二叔", "大伯", "表哥", "前夫", "前妻", "情敌",
    "小妾", "私生子", "仇人", "仇家", "债主", "渣男", "贱人", "恶霸", "反派",
    "狗官", "情妇", "假千金", "顾家", "苏家", "林家", "江家", "江城", "对手"
]

# 3. 戏剧压迫与对抗动作词库
CONFLICT_ACTIONS = [
    "陷害", "入狱", "下跪", "羞辱", "退婚", "逼死", "夺走", "掌掴", "扇巴掌",
    "毒打", "霸占", "赶出家门", "扫地出门", "断绝关系", "清算", "百倍奉还", "十倍奉还",
    "千百倍", "翻盘", "跪下", "报仇", "偿命", "打脸", "伪造", "掉包", "欺负",
    "欺凌", "冤枉", "诬陷", "对峙", "法庭", "供词", "清账", "算账", "踏平",
    "封锁", "偿还", "抢走", "弄死", "弄残", "废了", "逐出"
]


class CharacterDetector(BaseDetector):
    name: str = "CharacterDetector"

    def run_detection(
        self,
        dialogue_text: Optional[str] = None,
        **kwargs
    ) -> DetectorResult:
        if not dialogue_text or len(dialogue_text.strip()) < 25:
            return DetectorResult(
                detector_name=self.name,
                triggered_veto=False,
                diagnostic_message="文本过短，跳过人物角色与戏剧冲突检测",
                metric_scores={"has_protagonist": 1.0, "has_conflict": 1.0}
            )

        clean_text = dialogue_text.strip()

        # ----------------------------------------------------
        # 维度 1: 核心主角主体检测
        # ----------------------------------------------------
        has_pronoun = any(p in clean_text for p in PROTAGONIST_PRONOUNS)
        matched_archetypes = [a for a in PROTAGONIST_ARCHETYPES if a in clean_text]
        has_archetype = len(matched_archetypes) > 0

        has_protagonist = has_pronoun or has_archetype

        # ----------------------------------------------------
        # 维度 2: 二元戏剧矛盾对抗检测
        # ----------------------------------------------------
        matched_antagonists = [r for r in ANTAGONIST_ROLES if r in clean_text]
        matched_conflicts = [c for c in CONFLICT_ACTIONS if c in clean_text]

        has_conflict = (len(matched_antagonists) > 0) or (len(matched_conflicts) > 0)

        # ----------------------------------------------------
        # 粗筛一票否决裁决
        # ----------------------------------------------------
        char_len = len(clean_text)
        triggered_veto = False
        reason_code = None
        diagnostic_message = ""

        # 判定 A：文本达到一定长度但完全没有主角实体（纯风景/纯世界观散文流水账）
        if char_len >= 35 and not has_protagonist:
            triggered_veto = True
            reason_code = "DETECTED_NO_CLEAR_PROTAGONIST"
            diagnostic_message = "缺失核心主角：全篇未检测到主角实体、人称代词或角色代号，属于无人物散文或背景说明，非短剧戏剧物料"

        # 判定 B：文本达到一定长度但完全没有矛盾冲突（平淡买菜记账琐碎日常）
        elif char_len >= 35 and not has_conflict:
            triggered_veto = True
            reason_code = "DETECTED_LACK_DRAMATIC_CONFLICT"
            diagnostic_message = "缺乏戏剧矛盾冲突：全篇未检测到任何反派对立面、打压背叛或复仇对抗动作，属于平淡日常流水账，缺乏短剧基本张力"

        else:
            protagonist_tag = "第一人称" if has_pronoun else (matched_archetypes[0] if matched_archetypes else "主角明确")
            conflict_tag = matched_conflicts[0] if matched_conflicts else (matched_antagonists[0] if matched_antagonists else "存在对抗")
            diagnostic_message = f"通过：角色主体明确({protagonist_tag})，具备二元对抗冲突({conflict_tag})"

        return DetectorResult(
            detector_name=self.name,
            triggered_veto=triggered_veto,
            reason_code=reason_code,
            diagnostic_message=diagnostic_message,
            metric_scores={
                "has_protagonist": 1.0 if has_protagonist else 0.0,
                "has_conflict": 1.0 if has_conflict else 0.0,
                "conflict_count": float(len(matched_conflicts)),
                "antagonist_count": float(len(matched_antagonists))
            }
        )
