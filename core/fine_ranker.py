"""
fine_ranker.py
短剧漫剧精筛 · 商业潜力多维评分与分级决策引擎
对通过粗筛的健康剧目进行故事情节张力、叙事节奏密度与时效微创新打分，输出 S/A/B 级评级
"""
import re
from typing import List, Dict, Tuple
from .models import FineScreeningScore

# 1. 情绪压迫与打压关键词
OPPRESSION_KEYWORDS = [
    "入狱", "陷害", "下跪", "羞辱", "野种", "废物", "欺凌", "虐待", "夺走",
    "扫地出门", "折磨", "逼死", "退婚", "悔婚", "扇巴掌", "毒打", "霸占",
    "抛弃", "小妾", "私生子", "冤枉", "诬陷", "断绝关系", "赶出家门", "嘲笑"
]

# 2. 绝地反击与打脸反杀关键词
TRIUMPH_KEYWORDS = [
    "战神", "龙帅", "首富", "继承人", "亲生", "亲子鉴定", "股权", "执掌",
    "清算", "百倍奉还", "十倍奉还", "千百倍", "跪下", "逆袭", "财团", "阎王",
    "医圣", "绝品", "恢复", "兑现", "翻盘", "底牌", "亮出", "主宰", "金针",
    "震慑", "大佬", "巨鳄", "万亿", "不配", "踏平", "奉还", "主宰"
]

# 3. 付费卡点悬念与核心信息差关键词
CLIFFHANGER_KEYWORDS = [
    "真相", "秘密", "证据", "身世", "究竟是谁", "亲笔供词", "DNA", "真正",
    "揭开", "临界", "生死", "倒计时", "亲妈", "假千金", "真千金", "掉包",
    "协议", "底线", "谁才是", "没想到", "幕后黑手", "翻开"
]

# 4. 经典商业题材模式
GENRE_PATTERNS = {
    "战神归来/打脸爽剧": ["战神", "龙帅", "将士", "军团", "出狱", "三年之期", "主宰"],
    "豪门恩怨/真假千金": ["财团", "顾家", "苏家", "林家", "股权", "亲子鉴定", "千金", "假千金", "掉包"],
    "绝品神医/生死逆转": ["医圣", "阎王", "神针", "心跳恢复", "三更死", "婚约", "神医"],
    "复仇清算/身份反转": ["清算", "法庭", "供词", "十倍奉还", "执掌", "底牌"]
}

# 5. 反套路微创新关键词
NOVELTY_KEYWORDS = [
    "亲笔供词", "法庭", "原始采样", "百分之五十一", "51%", "股权", "九转金针",
    "科研", "科技", "高智商", "心理战", "证据确凿", "公证", "合同"
]


class FineRanker:
    """精筛多维评分与分级引擎"""

    def evaluate(self, text: str) -> FineScreeningScore:
        clean_text = text.strip() if text else ""
        if not clean_text:
            return FineScreeningScore(
                story_tension=0.0,
                narrative_pacing=0.0,
                novelty_bonus=0.0,
                total_score=0.0,
                grade="C",
                highlights=["无文本内容"]
            )

        highlights: List[str] = []

        # ----------------------------------------------------
        # 维度 1: 故事情节张力评分 (0 - 40 分)
        # ----------------------------------------------------
        oppression_hits = [w for w in OPPRESSION_KEYWORDS if w in clean_text]
        triumph_hits = [w for w in TRIUMPH_KEYWORDS if w in clean_text]
        cliffhanger_hits = [w for w in CLIFFHANGER_KEYWORDS if w in clean_text]

        tension_score = 0.0

        # (1) 情绪压迫 -> 反杀打脸 极差跃迁
        if oppression_hits and triumph_hits:
            # 完美具备“打压受辱”与“反杀打脸”强烈反差
            tension_score += 26.0
            highlights.append(f"极致情绪反差({oppression_hits[0]}->{triumph_hits[0]})")
        elif triumph_hits:
            tension_score += 18.0
            highlights.append(f"强反杀爽感({triumph_hits[0]})")
        elif oppression_hits:
            tension_score += 14.0
            highlights.append(f"强压迫蓄势({oppression_hits[0]})")
        else:
            tension_score += 8.0

        # (2) 付费卡点悬念未决张力 (最高 14 分)
        if len(cliffhanger_hits) >= 2:
            tension_score += 14.0
            highlights.append(f"强卡点信息差({','.join(cliffhanger_hits[:2])})")
        elif len(cliffhanger_hits) == 1:
            tension_score += 9.0
            highlights.append(f"带悬念钩子({cliffhanger_hits[0]})")
        else:
            tension_score += 4.0

        tension_score = min(40.0, tension_score)

        # ----------------------------------------------------
        # 维度 2: 叙事节奏与密度评分 (0 - 30 分)
        # ----------------------------------------------------
        pacing_score = 15.0  # 基础分

        # 文本有效长度评分（太短缺乏铺垫，合适长度能传达完整故事）
        char_len = len(clean_text)
        if 40 <= char_len <= 350:
            pacing_score += 8.0
        elif char_len > 350:
            pacing_score += 5.0
        else:
            pacing_score += 3.0

        # 动作推进词与转折词密集度
        action_verbs = ["随我", "封锁", "奔赴", "夺走", "看清楚", "清算", "兑现", "恢复", "睁大", "告诉", "清算"]
        action_hits = [w for w in action_verbs if w in clean_text]
        if len(action_hits) >= 2:
            pacing_score += 7.0
            highlights.append("快节奏剧情推力")
        elif len(action_hits) == 1:
            pacing_score += 4.0

        pacing_score = min(30.0, pacing_score)

        # ----------------------------------------------------
        # 维度 3: 题材时效与微创新加权 (0 - 30 分)
        # ----------------------------------------------------
        novelty_score = 14.0  # 基础分

        # 经典爆款骨架契合度 (+10分)
        matched_genres = []
        for genre_name, keywords in GENRE_PATTERNS.items():
            if any(k in clean_text for k in keywords):
                matched_genres.append(genre_name)

        if matched_genres:
            novelty_score += 9.0
            highlights.append(f"经典骨架({matched_genres[0]})")

        # 反套路微创新加权 (+7分)
        novelty_hits = [w for w in NOVELTY_KEYWORDS if w in clean_text]
        if novelty_hits:
            novelty_score += 7.0
            highlights.append(f"微创新破局设定({novelty_hits[0]})")

        novelty_score = min(30.0, novelty_score)

        # ----------------------------------------------------
        # 综合总分与精准评级 (S / A / B / C)
        # ----------------------------------------------------
        total_score = round(tension_score + pacing_score + novelty_score, 1)

        if total_score >= 80.0:
            grade = "S"
        elif total_score >= 65.0:
            grade = "A"
        elif total_score >= 45.0:
            grade = "B"
        else:
            grade = "C"

        return FineScreeningScore(
            story_tension=round(tension_score, 1),
            narrative_pacing=round(pacing_score, 1),
            novelty_bonus=round(novelty_score, 1),
            total_score=total_score,
            grade=grade,
            highlights=highlights[:4]
        )
