"""
novel_trends
网文头部榜单（起点、纵横、晋江、潇湘）真实数据抓取、智能增量AI分析与短剧风向标匹配模块
"""
from .models import NovelBook, TrendingTropePattern, TrendingTropesSummary, NovelKnowledgeBase, NovelDnaProfile
from .cache_manager import NovelCacheManager
from .crawlers.sync_service import NovelSyncService
from .ai_analyzer import NovelAIAnalyzer
from .trend_matcher import TrendMatcher
from .novel_dna_extractor import NovelDnaExtractor
from .dna_matcher import DnaTrendMatcher

__all__ = [
    "NovelBook",
    "TrendingTropePattern",
    "TrendingTropesSummary",
    "NovelKnowledgeBase",
    "NovelDnaProfile",
    "NovelCacheManager",
    "NovelSyncService",
    "NovelAIAnalyzer",
    "TrendMatcher",
    "NovelDnaExtractor",
    "DnaTrendMatcher"
]

