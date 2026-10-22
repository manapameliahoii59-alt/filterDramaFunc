# -*- coding: utf-8 -*-
"""
novel_trends/dna_matcher.py
基于 6 大黄金维度与 12~18 个分层热词的短剧加权匹配引擎
采用 词汇重合 + 语义模糊重合 + 核心戏剧动力加权计算
"""
import os
import re
from typing import Dict, Any, List, Optional
from .models import NovelDnaProfile
from .novel_dna_extractor import NovelDnaExtractor


class DnaTrendMatcher:
    """网文爆款基因与短剧加权匹配引擎"""

    def __init__(self, extractor: Optional[NovelDnaExtractor] = None):
        self.extractor = extractor or NovelDnaExtractor()

    def match_drama(self, drama_title: str, drama_synopsis: str) -> Dict[str, Any]:
        """
        比对单部待筛短剧与热门小说特征库
        返回标准化的匹配评测与加权分
        """
        full_text = f"{drama_title} {drama_synopsis}".strip()
        all_novels = self.extractor.get_all_profiles()

        if not all_novels:
            return {
                "trend_score": 0.0,
                "matched_novel_title": "无",
                "matched_novel_author": "无",
                "matched_tropes": [],
                "evidence_text": "本地热门小说特征库为空，未进行匹配加权。"
            }

        best_score = 0.0
        best_novel: Optional[NovelDnaProfile] = None
        best_matched_tropes: List[str] = []
        best_breakdown: Dict[str, float] = {}

        # 逐部热门小说比对相关度
        for novel in all_novels:
            score, matched_tropes, breakdown = self._calculate_similarity(full_text, drama_title, novel)
            if score > best_score:
                best_score = score
                best_novel = novel
                best_matched_tropes = matched_tropes
                best_breakdown = breakdown

        if best_novel and best_score >= 50.0:
            evidence = (
                f"高度命中大盘爆款《{best_novel.title}》（{best_novel.author}）的"
                f"【{best_novel.track_and_audience.get('sub_category', '')}】赛道！"
                f"命中核心母题: {', '.join(best_matched_tropes[:5])}。"
                f"人设反差得分: {best_breakdown.get('contrast_score', 0):.1f}分，"
                f"开局冲突得分: {best_breakdown.get('crisis_score', 0):.1f}分。"
            )
        elif best_novel and best_score >= 25.0:
            evidence = (
                f"部分契合大盘爆款《{best_novel.title}》（{best_novel.author}）的题材方向，"
                f"命中母题: {', '.join(best_matched_tropes[:3]) if best_matched_tropes else '基础赛道'}，"
                f"大盘趋势匹配分 {best_score:.1f} 分。"
            )
        else:
            evidence = "未命中当前网文大盘头部畅销母题，缺乏强冲突反差钩子，大盘加分为 0。"

        return {
            "trend_score": round(best_score, 1),
            "matched_novel_title": best_novel.title if best_novel else "无",
            "matched_novel_author": best_novel.author if best_novel else "无",
            "matched_tropes": best_matched_tropes,
            "breakdown": best_breakdown,
            "evidence_text": evidence
        }

    def _calculate_similarity(self, drama_text: str, drama_title: str, novel: NovelDnaProfile) -> tuple:
        """
        计算短剧文本与单本小说的多维匹配度 (0 - 100 分)
        1. 赛道匹配 (20%)
        2. 调性匹配 (15%)
        3. 身份反差匹配 (20%)
        4. 开局冲突匹配 (20%)
        5. 分层母题热词重合 (25%)
        """
        dna = novel
        text = drama_text

        # 0. 负样本快速熔断：如果文本没有任何冲突性动词或强情绪词，直接低分
        action_keywords = ["出狱", "归位", "归来", "入狱", "陷害", "入赘", "清算", "打脸", "退婚", "掉包", "产房", "奉还", "股权", "亲子鉴定", "断绝", "原谅", "下跪", "报复", "复仇", "封锁", "神医", "救命"]
        has_any_conflict = any(k in text for k in action_keywords)
        if not has_any_conflict:
            return 5.0, [], {"track_score": 5.0, "tone_score": 0.0, "contrast_score": 0.0, "crisis_score": 0.0, "tropes_score": 0.0}

        # 1. 赛道契合度 (20分)
        track_info = getattr(dna, "track_dna", None) or dna.track_and_audience or {}
        gender = track_info.get("gender_track", "")
        sub_cat = track_info.get("core_sub_genre") or track_info.get("sub_category", "")
        track_score = 0.0

        is_female_drama = any(w in text for w in ["千金", "真假千金", "嫡女", "庶妹", "夫人", "替嫁", "和离", "带宝", "带崽", "前夫", "总裁", "少夫人", "产房", "继母"])
        is_male_drama = any(w in text for w in ["战神", "龙帅", "龙王", "赘婿", "修仙", "神豪", "武道", "出狱", "三年之期", "外卖员", "保安", "军团", "将士", "寿宴"])

        if gender == "女频" and is_female_drama:
            track_score += 10.0
        elif gender == "男频" and is_male_drama:
            track_score += 10.0
        elif not is_female_drama and not is_male_drama:
            track_score += 5.0

        # 细分子赛道关键词弹性重合（将子赛道分词为 2 字词匹配）
        sub_tokens = [sub_cat[i:i+2] for i in range(len(sub_cat)-1) if sub_cat[i:i+2] not in ["/+", "，", " "]]
        hit_sub = [tok for tok in sub_tokens if tok in text]
        if hit_sub:
            track_score = min(20.0, track_score + min(10.0, len(hit_sub) * 3.5))

        # 2. 情绪调性与叙事烈度 (15分)
        tone_score = 0.0
        high_intensity_keywords = ["打脸", "百倍奉还", "十倍奉还", "绝不原谅", "清算", "当场", "下跪", "撕毁", "反杀", "退婚", "封锁", "狂", "死定了", "霸气"]
        hit_tone = [w for w in high_intensity_keywords if w in text]
        if hit_tone:
            tone_score = min(15.0, len(hit_tone) * 5.0)

        # 3. 身份反差匹配 (20分)
        ident_info = getattr(dna, "protagonist_and_leverage_dna", None) or dna.identity_contrast or {}
        contrast_score = 0.0
        surface_kw = ["入赘", "白眼", "受辱", "被换", "乡下", "土包子", "替嫁", "残疾", "废柴", "弃子", "狱中", "入狱", "当年陷害"]
        hidden_kw = ["首富", "战神", "龙帅", "大权", "全能", "神医", "百亿", "财团", "股权", "大佬", "真实身份", "五十一", "将士", "归位", "龙王", "执掌"]
        has_surface = any(w in text for w in surface_kw)
        has_hidden = any(w in text for w in hidden_kw)
        if has_surface and has_hidden:
            contrast_score = 20.0
        elif has_surface or has_hidden:
            contrast_score = 10.0

        # 4. 开局冲突匹配 (20分)
        crisis_score = 0.0
        conflict_info = getattr(dna, "conflict_and_payoff_dna", None) or dna.opening_crisis_hook or {}
        crisis_type = (
            str(conflict_info.get("inciting_incident", "")) + " " +
            str(conflict_info.get("antagonist_relationship", "")) + " " +
            str(conflict_info.get("conflict_type", "")) + " " +
            str(conflict_info.get("crisis_description", ""))
        )
        # 提取危机特征词
        crisis_feature_words = ["掉包", "产房", "DNA", "亲子鉴定", "法庭", "退婚", "入狱", "陷害", "改嫁", "出狱", "夺走", "伪造", "断绝", "抛弃", "寿宴", "算账", "白眼", "入赘"]
        matched_crisis_words = [w for w in crisis_feature_words if w in text and w in crisis_type]
        if matched_crisis_words:
            crisis_score = min(20.0, len(matched_crisis_words) * 7.0 + 6.0)
        elif any(w in text for w in ["陷害", "入狱", "产房", "掉包", "白眼", "伪造"]):
            crisis_score = 10.0

        # 5. 分层母题热词重合 (25分)
        tropes_score = 0.0
        l1_tropes = dna.hierarchical_tropes.get("l1_macro_tropes", [])
        l2_tropes = dna.hierarchical_tropes.get("l2_micro_elements", [])
        matched_tropes = []

        # 同义词/近义词归一映射
        synonym_map = {
            "龙王归来": ["战神归来", "龙帅归位", "战神出狱", "龙帅", "战神", "龙王"],
            "战神出狱": ["龙帅出狱", "入狱", "出狱", "战神"],
            "真假千金": ["假千金", "真千金", "掉包", "真假千金掉包案"],
            "打脸虐渣": ["狂打脸", "打脸", "百倍奉还", "下跪"],
            "全能马甲": ["执掌财团", "大佬", "全能"],
            "断绝关系": ["绝不原谅", "清算", "断亲"]
        }

        # L1 宏观母题 (占 10分)
        for t in l1_tropes:
            clean_t = t.replace("#", "").strip()
            # 直接匹配或同义匹配
            is_hit = False
            if clean_t in text:
                is_hit = True
            elif clean_t in synonym_map:
                if any(syn in text for syn in synonym_map[clean_t]):
                    is_hit = True

            if is_hit:
                tropes_score += 4.0
                matched_tropes.append(f"#{clean_t}")

        # L2 微观情节热词 (占 15分)
        for t in l2_tropes:
            clean_t = t.replace("#", "").strip()
            # 检查微观动作词
            sub_kws = [clean_t[i:i+2] for i in range(len(clean_t)-1) if clean_t[i:i+2] not in ["的", "了", "在", "被", "把"]]
            hits_sub = [k for k in sub_kws if k in text]
            if len(hits_sub) >= 2 or clean_t in text:
                tropes_score += 3.0
                matched_tropes.append(f"#{clean_t}")

        tropes_score = min(25.0, tropes_score)

        total_score = track_score + tone_score + contrast_score + crisis_score + tropes_score
        total_score = min(100.0, max(0.0, total_score))

        breakdown = {
            "track_score": track_score,
            "tone_score": tone_score,
            "contrast_score": contrast_score,
            "crisis_score": crisis_score,
            "tropes_score": tropes_score
        }

        return total_score, matched_tropes, breakdown
