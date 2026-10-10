"""
novel_trends/crawlers/sync_service.py
四大平台（起点、纵横、晋江、潇湘）榜单全量聚合同步服务
整合真实数据抓取与本地缓存去重
"""
from typing import Dict, List, Tuple
from ..models import NovelBook
from ..cache_manager import NovelCacheManager
from .qidian_crawler import QidianCrawler
from .zongheng_crawler import ZonghengCrawler
from .jinjiang_crawler import JinjiangCrawler
from .xiaoxiang_crawler import XiaoxiangCrawler


class NovelSyncService:
    """小说榜单聚合抓取与增量同步服务"""

    def __init__(self, cache_manager: NovelCacheManager):
        self.cache = cache_manager
        self.qidian_crawler = QidianCrawler()
        self.zongheng_crawler = ZonghengCrawler()
        self.jinjiang_crawler = JinjiangCrawler()
        self.xiaoxiang_crawler = XiaoxiangCrawler()

    def sync_all_platforms(
        self,
        limit_per_platform: int = 15,
        qidian_top_per_board: int = 50,
        zongheng_top_per_board: int = 50,
        jinjiang_top_per_board: int = 50,
        xiaoxiang_top_per_board: int = 20
    ) -> Tuple[int, int, List[NovelBook]]:
        """
        全量同步四大平台排行榜
        针对起点中文网专项抓取：月票榜、潜力榜、签约新书榜、未签约新书榜 各 TOP 50
        针对纵横中文网专项抓取：月票榜、新书榜、新书订阅榜 各 TOP 50
        针对晋江文学城专项抓取：月度排行榜、新晋作者榜、完结金榜 各 TOP 50
        针对潇湘书院专项抓取：潇湘票榜、畅销榜、完结榜、阅读榜、收藏榜 各 TOP 20
        返回: (抓取总数, 新发现需分析书籍数, 新书籍列表)
        """
        total_fetched = 0
        new_books: List[NovelBook] = []

        print("=" * 68)
        print("  正在从真实小说平台抓取最新榜单 (起点 / 纵横 / 晋江 / 潇湘)...")
        print("=" * 68)

        # 1. 起点中文网专项抓取：四大指定榜单 (各 TOP 50)
        print(f"  [抓取中] {self.qidian_crawler.platform_name} (男频主战场)...")
        for board_name, slug in self.qidian_crawler.BOARDS:
            print(f"    -> 抓取【{board_name}】TOP {qidian_top_per_board}...", end="", flush=True)
            try:
                board_books = self.qidian_crawler.fetch_board(board_name, slug, max_count=qidian_top_per_board)
                print(f" 成功抓取 {len(board_books)} 部")
                total_fetched += len(board_books)
                for book in board_books:
                    is_new = self.cache.add_or_update_book(book)
                    if is_new:
                        new_books.append(book)
            except Exception as e:
                print(f" 抓取失败: {e}")

        # 2. 纵横中文网专项抓取：三大核心榜单 (各 TOP 50)
        print(f"  [抓取中] {self.zongheng_crawler.platform_name} (男频核心阵地)...")
        for board_name, rank_type in self.zongheng_crawler.BOARDS:
            print(f"    -> 抓取【{board_name}】TOP {zongheng_top_per_board}...", end="", flush=True)
            try:
                board_books = self.zongheng_crawler.fetch_board(board_name, rank_type, max_count=zongheng_top_per_board)
                print(f" 成功抓取 {len(board_books)} 部")
                total_fetched += len(board_books)
                for book in board_books:
                    is_new = self.cache.add_or_update_book(book)
                    if is_new:
                        new_books.append(book)
            except Exception as e:
                print(f" 抓取失败: {e}")

        # 3. 晋江文学城专项抓取：三大核心榜单 (各 TOP 50)
        print(f"  [抓取中] {self.jinjiang_crawler.platform_name} (女频顶流首府)...")
        for board_name, path, category in self.jinjiang_crawler.BOARDS:
            print(f"    -> 抓取【{board_name}】TOP {jinjiang_top_per_board}...", end="", flush=True)
            try:
                board_books = self.jinjiang_crawler.fetch_board(board_name, path, category, max_count=jinjiang_top_per_board)
                print(f" 成功抓取 {len(board_books)} 部")
                total_fetched += len(board_books)
                for book in board_books:
                    is_new = self.cache.add_or_update_book(book)
                    if is_new:
                        new_books.append(book)
            except Exception as e:
                print(f" 抓取失败: {e}")

        # 4. 潇湘书院专项抓取：五大核心女频榜单 (各 TOP 20)
        print(f"  [抓取中] {self.xiaoxiang_crawler.platform_name} (女频热门旗舰)...")
        for board_name, section_idx in self.xiaoxiang_crawler.BOARDS:
            print(f"    -> 抓取【{board_name}】TOP {xiaoxiang_top_per_board}...", end="", flush=True)
            try:
                board_books = self.xiaoxiang_crawler.fetch_board(board_name, section_idx=section_idx, max_count=xiaoxiang_top_per_board)
                print(f" 成功抓取 {len(board_books)} 部")
                total_fetched += len(board_books)
                for book in board_books:
                    is_new = self.cache.add_or_update_book(book)
                    if is_new:
                        new_books.append(book)
            except Exception as e:
                print(f" 抓取失败: {e}")

        # 落盘保存基础书籍数据
        self.cache.save_knowledge_base()

        already_cached_count = total_fetched - len(new_books)
        print("-" * 68)
        print(f"  抓取完成: 共探测 {total_fetched} 部 | 本地已缓存 (免调AI): {already_cached_count} 部 | 新入榜需分析: {len(new_books)} 部")
        print("=" * 68)

        return total_fetched, len(new_books), new_books
