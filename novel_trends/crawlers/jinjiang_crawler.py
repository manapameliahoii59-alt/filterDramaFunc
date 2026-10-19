"""
novel_trends/crawlers/jinjiang_crawler.py
晋江文学城 (Jinjiang) 真实榜单爬虫
支持女频三大高频快节奏榜单：新晋作者榜、月度排行榜、读者栽培榜 (各 TOP 50)
已剔除长周期完结金榜，聚焦 0~30 天新脑洞、当月大盘与高留存付费追更作品
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple
from .base_crawler import BaseNovelCrawler
from ..models import NovelBook


class JinjiangCrawler(BaseNovelCrawler):
    platform_name = "晋江文学城"
    channel = "女频"

    # 三大核心快节奏榜单映射: (榜单中文名, 移动端路由路径, 题材分类标签)
    BOARDS: List[Tuple[str, str, str]] = [
        ("新晋作者榜", "/rank/naturalmore/29", "女频新人/新题材潜力爆款"),
        ("月度排行榜", "/rank/naturalmore/5", "女频言情/纯爱月度爆款"),
        ("读者栽培榜", "/rank/naturalmore/44", "女频高留存付费追更爆款"),
    ]

    def __init__(self):
        super().__init__()
        # 内存缓存，避免跨榜单重复抓取书籍详情
        self._meta_cache: Dict[str, Tuple[str, str]] = {}

    def _fetch_book_meta(self, bid: str) -> Tuple[str, str]:
        """抓取单本书籍的真实作者与分类题材标签"""
        if bid in self._meta_cache:
            return self._meta_cache[bid]

        url = f"https://m.jjwxc.net/book2/{bid}"
        try:
            html = self.fetch_url(url, is_mobile=True, encoding="gb18030")
            if html:
                m_desc = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html)
                if m_desc:
                    parts = m_desc.group(1).split(',')
                    author = parts[1].strip() if len(parts) > 1 else ""
                    tags = parts[2].strip() if len(parts) > 2 else ""
                    if author:
                        res = (author, tags)
                        self._meta_cache[bid] = res
                        return res

                m_author = re.search(r'作者[：:]\s*(?:<[^>]+>)*\s*([^<\n\r]+)', html)
                if m_author:
                    author = re.sub(r'<[^>]+>', '', m_author.group(1)).strip()
                    if author:
                        res = (author, "")
                        self._meta_cache[bid] = res
                        return res
        except Exception:
            pass

        return ("", "")

    def fetch_board(self, board_name: str, path: str, default_category: str, max_count: int = 50) -> List[NovelBook]:
        """抓取晋江指定榜单 TOP 作品 (默认 50 部)，并发补充真实作者信息"""
        url = f"https://m.jjwxc.net{path}"
        books: List[NovelBook] = []
        try:
            html = self.fetch_url(url, is_mobile=True, encoding="gb18030")
            matches = re.findall(r'href="/book2/(\d+)"[^>]*>([^<]+)</a>', html)

            candidate_items = []
            seen_titles = set()
            for bid, raw_title in matches:
                clean_title = raw_title.strip()
                if not clean_title or clean_title.isdigit() or clean_title in seen_titles:
                    continue
                seen_titles.add(clean_title)
                candidate_items.append((bid, clean_title))
                if len(candidate_items) >= max_count:
                    break

            # 并发抓取候选书籍的真实作者与标签信息 (线程池最大 4，降低风控概率)
            bids_to_fetch = [bid for bid, _ in candidate_items if bid not in self._meta_cache]
            if bids_to_fetch:
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {executor.submit(self._fetch_book_meta, bid): bid for bid in bids_to_fetch}
                    for fut in as_completed(futures):
                        try:
                            fut.result()
                        except Exception:
                            pass

            for rank_idx, (bid, clean_title) in enumerate(candidate_items, 1):
                author, tags = self._meta_cache.get(bid, ("", ""))
                final_author = author if author else "晋江签约作者"
                cat = f"{default_category} / {tags}" if tags else default_category
                intro = f"晋江文学城{board_name}前列作品《{clean_title}》，作者：{final_author}。"
                if tags:
                    intro += f" 核心题材标签：{tags}。"

                book = NovelBook(
                    book_id=bid,
                    title=clean_title,
                    author=final_author,
                    platform=self.platform_name,
                    channel=self.channel,
                    rank_name=board_name,
                    rank_position=rank_idx,
                    category=cat,
                    intro=intro,
                    book_url=f"https://www.jjwxc.net/onebook.php?novelid={bid}",
                    heat_score=round(max(65.0, 100.0 - (rank_idx - 1) * 0.7), 1)
                )
                books.append(book)

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
