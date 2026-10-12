"""
novel_trends/ai_analyzer.py
AI 爆款剧情与大盘风向标萃取器
核心策略：
1. 增量分析：已分析/已知情节的书籍直接跳过，零 Token 消耗、零延迟复用缓存；
2. 仅对新入榜书籍调用 AI 解构其核心爽点、人设关系、吸量关键词与叙事弧线；
3. 支持外部大模型 API (DeepSeek/OpenAI/Gemini)，并内置专业级网文语义启发式解析引擎作为即时兜底，确保离线也能百分之百稳定可用。
"""
import os
import re
import json
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any
from .models import NovelBook, TrendingTropePattern, TrendingTropesSummary
from .cache_manager import NovelCacheManager
from .novel_dna_extractor import NovelDnaExtractor


# 男频常见核心母题映射库（用于启发式模式提取）
MALE_TROPE_DEFINITIONS = {
    "家族修仙/词条长生流": {
        "keywords": ["家族", "宗族", "老祖", "修仙", "族学", "词条", "面板", "长生", "稳健", "寿元"],
        "desc": "以家族/宗族为核心纽带，主角利用词条金手指带飞全族，对抗强敌，稳健长生。",
        "weight": 18.0
    },
    "高武反派/天命截胡流": {
        "keywords": ["高武", "武道", "反派", "截胡", "觉醒", "顿悟", "天骄", "拳练百遍", "气运"],
        "desc": "穿越高武世界，以武道极限或反派视角截胡天命主角机缘，极致碾压反杀。",
        "weight": 17.0
    },
    "战神归来/封锁清算流": {
        "keywords": ["战神", "龙帅", "出狱", "三年之期", "封锁全城", "百倍奉还", "十倍奉还", "军团", "寿宴"],
        "desc": "顶级身份隐忍归来，面对曾经背叛陷害，以雷霆万钧之势全城清算、当场打脸。",
        "weight": 16.0
    },
    "败家系统/逆向神豪流": {
        "keywords": ["系统", "败光", "一千亿", "神豪", "鉴宝", "捡漏", "暴富", "反套路"],
        "desc": "绑定逆向惩罚或必须败光资产的奇葩系统，看似胡作非为实则疯狂盈利打脸。",
        "weight": 15.0
    },
    "基层逆袭/年代官场刑侦": {
        "keywords": ["基层", "民警", "重回", "重生98", "从川菜馆开始", "官场", "年代", "抓贼"],
        "desc": "重回黄金九十年代，扎根基层利用先知经验步步高升，烟火气与正能量双爽。",
        "weight": 15.0
    }
}

# 女频常见核心母题映射库（用于启发式模式提取）
FEMALE_TROPE_DEFINITIONS = {
    "真假千金/绝不原谅断亲流": {
        "keywords": ["假千金", "真千金", "断亲", "掉包", "偏心", "绝不原谅", "清算", "法庭", "赶出豪门", "亲子鉴定"],
        "desc": "被调包真千金回归不受待见，主角坚决断绝血缘关系自立门户，手撕偏心全家与绿茶假千金。",
        "weight": 20.0
    },
    "读心吃瓜/全家偷听发疯流": {
        "keywords": ["读心", "吃瓜", "心声", "偷听", "发疯", "吃翻全场", "反派全家", "贵妃吃瓜"],
        "desc": "主角内心疯狂吐槽吃瓜，反派或全家人却能听到其心声，在啼笑皆非中反向规避灭门惨剧。",
        "weight": 19.0
    },
    "年代穿越/空间军婚换嫁流": {
        "keywords": ["年代", "七零", "八零", "九零", "军婚", "空间", "换嫁", "随军", "糙汉", "带崽暴富", "种田"],
        "desc": "穿书或重生回年代文，手握灵泉/超市空间抢占先机，踢开渣男嫁给冷面军官，暴富打脸极品亲戚。",
        "weight": 18.0
    },
    "主母当场和离/搬空侯府流": {
        "keywords": ["和离", "侯府", "宠妾灭妻", "休夫", "搬空", "渣爹", "宅斗", "嫡女", "王爷撑腰"],
        "desc": "渣男宠妾灭妻或娶平妻，女主当场断然和离并搬空嫁妆家产，让白眼狼全府悔恨至死。",
        "weight": 17.0
    },
    "疯批反派一起重生/修罗场流": {
        "keywords": ["疯批", "重生", "火葬场", "修仙搞内卷", "死遁", "白月光", "超爱", "修罗场"],
        "desc": "多位权势滔天的反派大佬集体重生争宠，女主一心搞事业逆天改命，极致拉扯与虐男火葬场。",
        "weight": 16.0
    }
}


class NovelAIAnalyzer:
    """AI 题材风向标萃取器"""

    def __init__(self, cache_manager: NovelCacheManager, pg_storage: Optional[Any] = None):
        self.cache = cache_manager
        self.pg_storage = pg_storage
        self.extractor = NovelDnaExtractor()

    def analyze_books(self, books: List[NovelBook], force_reanalyze: bool = False) -> int:
        """
        对传入的书籍执行剧情分析（严格执行智能增量跳过策略）
        返回: 实际调用 AI/分析的新书数量
        """
        analyzed_count = 0
        total_count = len(books)

        if total_count == 0:
            return 0

        print(f"\n[AI题材分析] 正在检查待分析书籍队列 (共 {total_count} 部)...")

        for idx, book in enumerate(books, 1):
            # 1. 增量缓存检查：若已知道该书故事情节，绝对不重复调用 AI！
            if not force_reanalyze and self.cache.is_book_known(book.platform, book.book_id, book.title):
                # 已经有分析结论，直接跳过
                continue

            print(f"  -> [{idx}/{total_count}] 新书入榜，启动 AI 剧情套路解构: 《{book.title}》({book.platform} | {book.rank_name} 第{book.rank_position}名)")

            # 2. 调用分析引擎（优先大模型，离线使用高精度启发式引擎）
            tropes, keywords, narrative_arc = self._extract_book_dna(book)

            # 3. 回写本地知识库缓存
            self.cache.record_analysis_result(
                platform=book.platform,
                book_id=book.book_id,
                title=book.title,
                core_tropes=tropes,
                hook_keywords=keywords,
                narrative_arc=narrative_arc
            )
            analyzed_count += 1

        print(f"[AI题材分析] 增量分析完毕！本次新分析: {analyzed_count} 部，免调AI复用缓存: {total_count - analyzed_count} 部")

        # 4. 汇总生成全盘风向标
        self._aggregate_and_update_summaries()

        return analyzed_count

    def _extract_book_dna(self, book: NovelBook) -> Tuple[List[str], List[str], str]:
        """
        提取单本书籍的爆款 DNA（核心梗、付费关键词、叙事弧线）
        优先调用大模型 (Qwen3.8-Flash) 深度提取，并实时入库 PostgreSQL；
        若无网络或大模型失败，则无缝降级为内置网文启发式引擎。
        """
        # 1. 尝试调用大模型提取 6 维结构化 DNA
        author = getattr(book, "author", "") or "未知"
        try:
            profile = self.extractor.get_novel_dna(book.title, author)
            if profile:
                profile_dict = profile.dict() if hasattr(profile, "dict") else profile.model_dump()
                h_tropes = profile_dict.get("hierarchical_tropes", {})
                l1 = h_tropes.get("l1_macro_tropes", [])
                l2 = h_tropes.get("l2_micro_elements", [])
                idc = profile_dict.get("identity_contrast", {})
                gap = idc.get("contrast_gap", "")
                hook = profile_dict.get("opening_crisis_hook", {})
                pay = profile_dict.get("payoff_mode", {})

                arc = gap if gap else f"{hook.get('conflict_type', '开局受困')} -> {pay.get('core_payoff', '反杀打脸')}"
                tropes = [t.lstrip("#") for t in l1]
                keywords = [k.lstrip("#") for k in l2]

                # 实时同步入库 PostgreSQL novel_dna_records
                if self.pg_storage:
                    b_type = "NEW_TREND" if any(w in (book.rank_name or "") for w in ["新", "潜力"]) else "MONTHLY_HOT"
                    self.pg_storage.upsert_novel_dna(
                        profile=profile_dict,
                        source_platform=book.platform or "四大平台",
                        rank_name=book.rank_name or "热榜",
                        rank_position=getattr(book, "rank_position", 1) or 1,
                        board_type=b_type,
                        days_on_rank=3
                    )

                return tropes[:3] if tropes else ["大盘核心爆款"], keywords[:8], arc
        except Exception as e:
            print(f"[AI题材分析] 大模型提取《{book.title}》降级为启发式引擎: {e}")

        # 2. 启发式兜底模式匹配与关键词挖掘
        content_text = f"{book.title} {book.category} {book.intro}"
        channel = book.channel

        matched_tropes: List[str] = []
        matched_keywords: List[str] = []
        patterns = FEMALE_TROPE_DEFINITIONS if channel == "女频" else MALE_TROPE_DEFINITIONS

        for trope_name, conf in patterns.items():
            hits = [kw for kw in conf["keywords"] if kw in content_text]
            if len(hits) >= 2 or any(kw in book.title for kw in conf["keywords"]):
                matched_tropes.append(trope_name)
                matched_keywords.extend(hits)

        # 兜底通用提取
        if not matched_tropes:
            if channel == "女频":
                matched_tropes = ["女频逆袭/搞事业情感流"]
                matched_keywords = ["逆袭", "重生", "大女主", "打脸"]
            else:
                matched_tropes = ["男频热血反杀/逆境崛起流"]
                matched_keywords = ["反杀", "逆袭", "金手指", "无敌"]

        # 提炼叙事弧线
        if "断亲" in content_text or "清算" in content_text or "复仇" in content_text:
            arc = "极致打压陷害 -> 断绝关系自立 -> 掌控绝对底牌 -> 当场清算血债"
        elif "词条" in content_text or "面板" in content_text or "顿悟" in content_text:
            arc = "微末开局发育 -> 获得无上道果 -> 震惊宗门四方 -> 镇压诸天敌手"
        elif "读心" in content_text or "吃瓜" in content_text:
            arc = "无知觉全家听心声 -> 预警危机啼笑皆非 -> 避开灭门深坑 -> 躺赢反杀大反派"
        elif "战神" in content_text or "归位" in content_text:
            arc = "三年受辱蛰伏 -> 龙帅霸气归位 -> 封锁全城机场 -> 百倍偿还仇敌"
        else:
            arc = "矛盾激化受困 -> 绝地觉醒破局 -> 强势打脸反转"

        # 去重
        matched_keywords = list(dict.fromkeys(matched_keywords))[:8]

        return matched_tropes[:3], matched_keywords, arc

    def _aggregate_and_update_summaries(self):
        """聚合全库书籍，生成男频与女频风向标图谱"""
        kb = self.cache.kb

        male_patterns: Dict[str, TrendingTropePattern] = {}
        female_patterns: Dict[str, TrendingTropePattern] = {}

        male_keywords = []
        female_keywords = []

        for book in kb.books.values():
            if not book.already_analyzed:
                continue
            is_female = (book.channel == "女频")
            target_dict = female_patterns if is_female else male_patterns
            target_kw = female_keywords if is_female else male_keywords

            for trope in book.core_tropes:
                if trope not in target_dict:
                    # 寻找定义
                    defs = FEMALE_TROPE_DEFINITIONS if is_female else MALE_TROPE_DEFINITIONS
                    conf = defs.get(trope, {"desc": "当前全网高热度流行创作母题", "weight": 15.0, "keywords": []})
                    target_dict[trope] = TrendingTropePattern(
                        pattern_name=trope,
                        channel=book.channel,
                        description=conf["desc"],
                        keywords=conf.get("keywords", []),
                        representative_books=[],
                        market_bonus_weight=conf["weight"]
                    )
                if book.title not in target_dict[trope].representative_books:
                    target_dict[trope].representative_books.append(book.title)

            target_kw.extend(book.hook_keywords)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        male_summary = TrendingTropesSummary(
            channel="男频",
            hot_patterns=list(male_patterns.values()),
            top_keywords=list(dict.fromkeys(male_keywords))[:15],
            last_updated=now_str
        )
        female_summary = TrendingTropesSummary(
            channel="女频",
            hot_patterns=list(female_patterns.values()),
            top_keywords=list(dict.fromkeys(female_keywords))[:15],
            last_updated=now_str
        )

        self.cache.update_channel_summaries(male_summary, female_summary)
