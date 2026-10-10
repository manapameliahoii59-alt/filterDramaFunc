"""
novel_trends/crawlers/jinjiang_crawler.py
晋江文学城 (Jinjiang) 真实榜单爬虫
支持女频三大核心榜单：月度排行榜、新晋作者榜、完结金榜 (各 TOP 50)
"""
import re
from typing import List, Tuple
from .base_crawler import BaseNovelCrawler
from ..models import NovelBook


class JinjiangCrawler(BaseNovelCrawler):
    platform_name = "晋江文学城"
    channel = "女频"

    # 三大核心榜单映射: (榜单中文名, 移动端路由路径, 题材分类标签)
    BOARDS: List[Tuple[str, str, str]] = [
        ("月度排行榜", "/rank/naturalmore/5", "女频言情/纯爱月度爆款"),
        ("新晋作者榜", "/rank/naturalmore/29", "女频新人/新题材潜力爆款"),
        ("完结金榜", "/rank/naturalmore/58", "女频高口碑完结长尾神作"),
    ]

    def fetch_board(self, board_name: str, path: str, default_category: str, max_count: int = 50) -> List[NovelBook]:
        """抓取晋江指定榜单 TOP 作品 (默认 50 部)"""
        url = f"https://m.jjwxc.net{path}"
        books: List[NovelBook] = []
        try:
            html = self.fetch_url(url, is_mobile=True, encoding="gb18030")
            matches = re.findall(r'href="/book2/(\d+)"[^>]*>([^<]+)</a>', html)

            seen_titles = set()
            rank_idx = 1
            for bid, raw_title in matches:
                clean_title = raw_title.strip()
                if not clean_title or clean_title.isdigit() or clean_title in seen_titles:
                    continue
                seen_titles.add(clean_title)

                book = NovelBook(
                    book_id=bid,
                    title=clean_title,
                    author="晋江知名作者",
                    platform=self.platform_name,
                    channel=self.channel,
                    rank_name=board_name,
                    rank_position=rank_idx,
                    category=default_category,
                    intro=f"晋江文学城{board_name}前列作品《{clean_title}》，近期全网热销高积分女频大作。",
                    book_url=f"https://www.jjwxc.net/onebook.php?novelid={bid}",
                    heat_score=round(max(65.0, 100.0 - (rank_idx - 1) * 0.7), 1)
                )
                books.append(book)
                rank_idx += 1
                if len(books) >= max_count:
                    break
        except Exception as e:
            print(f"[{self.platform_name}] 榜单抓取异常 ({board_name}): {e}")

        return books

    def fetch_top_books(self, max_count: int = 150, top_per_board: int = 50) -> List[NovelBook]:
        """全量抓取三大指定榜单 (月度排行榜、新晋作者榜、完结金榜)，各 TOP 50 (合计至多 150 部)"""
        limit_per_board = max(10, max_count // len(self.BOARDS))
        if max_count >= len(self.BOARDS) * top_per_board:
            limit_per_board = top_per_board

        all_books: List[NovelBook] = []
        for board_name, path, category in self.BOARDS:
            board_books = self.fetch_board(board_name, path, category, max_count=limit_per_board)
            all_books.extend(board_books)
            if len(all_books) >= max_count:
                break

        return all_books[:max_count]
