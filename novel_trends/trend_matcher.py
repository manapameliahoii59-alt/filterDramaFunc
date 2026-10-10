"""
novel_trends/trend_matcher.py
短剧/漫剧与网文大盘爆款题材比对匹配器
计算【市场题材与榜单风向契合度加分 (Trend Bonus)】
"""
from typing import Dict, List, Tuple, Optional, Any
from .cache_manager import NovelCacheManager
from .models import TrendingTropePattern


class TrendMatcher:
    """网文风向标契合度加分匹配引擎"""

    def __init__(self, cache_manager: Optional[NovelCacheManager] = None):
        self.cache = cache_manager or NovelCacheManager()

    def match_drama_trend(self, drama_title: str, drama_content: str) -> Dict[str, Any]:
        """
        将短剧名称与大纲内容与当前四大平台头部书籍及热门母题比对
        返回:
        {
            "bonus_score": float,  # 额外加分 (0 - 20 分)
            "matched_patterns": List[str],  # 命中的爆款母题
            "matched_reference_books": List[str],  # 命中的真实榜单参考书目
            "channel_bias": str,  # 题材偏向: "男频" / "女频" / "通用"
            "evidence_text": str  # 人类可读的诊断加分理由
        }
        """
        full_text = f"{drama_title} {drama_content}".strip()
        kb = self.cache.kb

        # 收集所有已分析过的风向模式
        all_patterns: List[TrendingTropePattern] = []
        if kb.male_summary and kb.male_summary.hot_patterns:
            all_patterns.extend(kb.male_summary.hot_patterns)
        if kb.female_summary and kb.female_summary.hot_patterns:
            all_patterns.extend(kb.female_summary.hot_patterns)

        # 若知识库中尚未聚合，使用默认母题库
        if not all_patterns:
            from .ai_analyzer import MALE_TROPE_DEFINITIONS, FEMALE_TROPE_DEFINITIONS
            for name, conf in MALE_TROPE_DEFINITIONS.items():
                all_patterns.append(TrendingTropePattern(
                    pattern_name=name,
                    channel="男频",
                    description=conf["desc"],
                    keywords=conf["keywords"],
                    representative_books=[],
                    market_bonus_weight=conf["weight"]
                ))
            for name, conf in FEMALE_TROPE_DEFINITIONS.items():
                all_patterns.append(TrendingTropePattern(
                    pattern_name=name,
                    channel="女频",
                    description=conf["desc"],
                    keywords=conf["keywords"],
                    representative_books=[],
                    market_bonus_weight=conf["weight"]
                ))

        best_score = 0.0
        best_pattern: Optional[TrendingTropePattern] = None
        hit_keywords: List[str] = []
        matched_books: List[str] = []

        # 逐个母题进行深度特征词重叠与爽点匹配
        for pattern in all_patterns:
            hits = [kw for kw in pattern.keywords if kw in full_text]
            if not hits:
                continue

            # 计算匹配置信度
            match_confidence = min(1.0, len(hits) / 3.0)
            score = pattern.market_bonus_weight * match_confidence

            # 额外加成：若标题直接含有核心母题特征词，额外 +2 分
            if any(kw in drama_title for kw in pattern.keywords):
                score = min(20.0, score + 2.0)

            if score > best_score:
                best_score = score
                best_pattern = pattern
                hit_keywords = hits
                matched_books = pattern.representative_books[:3]

        # 检查是否直接命中了某部已知大热书籍的特定书名关键字（长度需>=4或3字专名，避免2字通用词泛化匹配）
        for book in kb.books.values():
            b_title = book.title.strip()
            if len(b_title) >= 4 and (b_title in drama_title or b_title in full_text):
                best_score = max(best_score, 18.0)
                if b_title not in matched_books:
                    matched_books.insert(0, b_title)
            elif len(b_title) == 3 and b_title in drama_title:
                best_score = max(best_score, 16.0)
                if b_title not in matched_books:
                    matched_books.insert(0, b_title)

        best_score = round(min(20.0, max(0.0, best_score)), 1)

        if best_score >= 8.0:
            if best_pattern:
                ref_str = f" (同款爆款代表作: 《{'》, 《'.join(matched_books[:2])}》)" if matched_books else ""
                evidence = (
                    f"命中【{best_pattern.channel}头部榜单热门风向】：[{best_pattern.pattern_name}]"
                    f"{ref_str}，触发核心爽点标签: [{', '.join(hit_keywords[:3])}]"
                )
                channel = best_pattern.channel
            elif matched_books:
                evidence = f"命中真实榜单代表作同款设定: 《{'》, 《'.join(matched_books[:2])}》"
                channel = "头部大盘"
            else:
                evidence = "命中网文大盘畅销元素"
                channel = "通用"
        else:
            best_score = 0.0
            evidence = "未显著命中当前网文头部榜单大热风向"
            channel = "通用"

        return {
            "bonus_score": best_score,
            "matched_patterns": [best_pattern.pattern_name] if best_pattern else [],
            "matched_reference_books": matched_books,
            "channel_bias": channel,
            "evidence_text": evidence
        }
