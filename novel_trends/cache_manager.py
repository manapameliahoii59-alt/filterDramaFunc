"""
novel_trends/cache_manager.py
书籍剧情本地知识库与增量缓存管理器
严格执行“已存在/已分析书籍绝不重复丢给 AI，仅针对新书增量分析”的 Token 节约策略
"""
import os
import json
from datetime import datetime
from typing import Dict, List, Optional
from .models import NovelBook, NovelKnowledgeBase, TrendingTropesSummary


DEFAULT_KB_PATH = os.path.join(os.path.dirname(__file__), "data", "novel_knowledge_base.json")


class NovelCacheManager:
    """本地知识库与增量分析缓存管理器"""

    def __init__(self, storage_path: str = DEFAULT_KB_PATH):
        self.storage_path = os.path.abspath(storage_path)
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        self.kb = self.load_knowledge_base()

    def load_knowledge_base(self) -> NovelKnowledgeBase:
        """从本地磁盘读取知识库，若不存在则创建空库"""
        if not os.path.isfile(self.storage_path):
            return NovelKnowledgeBase(books={})
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            if hasattr(NovelKnowledgeBase, "model_validate"):
                return NovelKnowledgeBase.model_validate(raw_data)
            return NovelKnowledgeBase.parse_obj(raw_data)
        except Exception as e:
            print(f"[NovelCacheManager] 读取本地知识库警告 ({e})，初始化空库")
            return NovelKnowledgeBase(books={})

    def save_knowledge_base(self):
        """将当前内存知识库安全落盘"""
        try:
            temp_path = self.storage_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.kb.dict(), f, ensure_ascii=False, indent=2)
            if os.path.exists(self.storage_path):
                os.remove(self.storage_path)
            os.rename(temp_path, self.storage_path)
        except Exception as e:
            print(f"[NovelCacheManager] 保存本地知识库失败: {e}")

    def generate_book_key(self, platform: str, book_id: str, title: str = "") -> str:
        """统一生成书籍在知识库中的唯一键"""
        # 优先使用 platform + book_id，若 book_id 缺失则使用 platform + 书名
        clean_id = (book_id or "").strip()
        if not clean_id and title:
            clean_id = title.strip()
        return f"{platform}_{clean_id}"

    def is_book_known(self, platform: str, book_id: str, title: str = "") -> bool:
        """判断该书是否已在本地知识库中，且已经完成过 AI 剧情套路分析"""
        key = self.generate_book_key(platform, book_id, title)
        if key in self.kb.books:
            book = self.kb.books[key]
            # 必须既标记为已分析，且有核心分析结果
            return bool(book.already_analyzed and book.core_tropes)
        return False

    def get_known_book(self, platform: str, book_id: str, title: str = "") -> Optional[NovelBook]:
        """获取本地已缓存的书籍信息"""
        key = self.generate_book_key(platform, book_id, title)
        return self.kb.books.get(key)

    def add_or_update_book(self, new_book: NovelBook) -> bool:
        """
        录入从网站抓取到的新书信息
        若本地已有且已分析过剧情，保留原有的 AI 分析结果，不覆盖！
        返回: 是否为未曾分析过的新书 (True 表示需要进入 AI 增量分析队列)
        """
        key = self.generate_book_key(new_book.platform, new_book.book_id, new_book.title)
        existing = self.kb.books.get(key)
        
        if existing and existing.already_analyzed and existing.core_tropes:
            # 更新最新榜单排位与更新时间，保留历史 AI 剧情总结，免调 AI
            existing.rank_position = new_book.rank_position
            existing.rank_name = new_book.rank_name
            if new_book.intro and new_book.intro.strip():
                existing.intro = new_book.intro.strip()
            
            # 同步更新真实作者：若新作者有效或旧作者为占位符，立即更新
            placeholder_authors = {
                "起点知名作家", "起点白金/大神作家", "起点作家",
                "晋江知名作者", "晋江签约作者", "纵横作家", "纵横签约作家",
                "潇湘作家", "潇湘知名作家", "未知作者", "未知", ""
            }
            if new_book.author and (new_book.author not in placeholder_authors or existing.author in placeholder_authors):
                existing.author = new_book.author
            if new_book.book_url:
                existing.book_url = new_book.book_url
            if new_book.category:
                existing.category = new_book.category
            if new_book.heat_score:
                existing.heat_score = new_book.heat_score
            return False
        else:
            # 首次入库或此前未分析过
            self.kb.books[key] = new_book
            return True

    def get_books_needing_analysis(self) -> List[NovelBook]:
        """获取所有亟需 AI 分析剧情的新书列表（严格去重）"""
        results = []
        for book in self.kb.books.values():
            if not book.already_analyzed or not book.core_tropes:
                results.append(book)
        return results

    def record_analysis_result(
        self,
        platform: str,
        book_id: str,
        title: str,
        core_tropes: List[str],
        hook_keywords: List[str],
        narrative_arc: str
    ):
        """记录 AI 对书籍的剧情解构结果，标记为 already_analyzed = True"""
        key = self.generate_book_key(platform, book_id, title)
        if key in self.kb.books:
            book = self.kb.books[key]
            book.core_tropes = core_tropes
            book.hook_keywords = hook_keywords
            book.narrative_arc = narrative_arc
            book.already_analyzed = True
            book.analyzed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_knowledge_base()

    def update_channel_summaries(
        self,
        male_summary: Optional[TrendingTropesSummary],
        female_summary: Optional[TrendingTropesSummary]
    ):
        """更新男女频全盘风向总结"""
        self.kb.male_summary = male_summary
        self.kb.female_summary = female_summary
        self.kb.last_sync_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_knowledge_base()
