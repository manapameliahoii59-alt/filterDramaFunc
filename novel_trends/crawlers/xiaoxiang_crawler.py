"""
novel_trends/crawlers/xiaoxiang_crawler.py
潇湘书院 (Xiaoxiang) 真实榜单爬虫
负责抓取女频四大高频快节奏榜单（阅读榜、畅销榜、潇湘票榜、收藏榜）
已剔除长周期完结榜，聚焦 7~30 天快节奏新风口与高留存作品
直接解析官方 Vite SSR 结构化数据，单次网络请求即可覆盖全榜单
"""
import re
import json
from typing import List, Optional, Tuple
from .base_crawler import BaseNovelCrawler
from ..models import NovelBook


class XiaoxiangCrawler(BaseNovelCrawler):
    platform_name = "潇湘书院"
    channel = "女频"

    # 潇湘书院四大高频快节奏榜单定义 (已剔除长周期完结榜，专注 7~30 天新风口与高留存作品)
    BOARDS: List[Tuple[str, int]] = [
        ("阅读榜", 4),    # 近 7 天阅读指数排行 (完播留存与快节奏剧情核心指标)
        ("畅销榜", 2),    # 上架 30 天内销量日排行 (新书极速变现风向标)
        ("潇湘票榜", 0),  # 新书月票榜 (新书读者投票)
        ("收藏榜", 5),    # 近 30 天书籍收藏排行 (新书读者期待与追读爆发指标)
    ]

    def __init__(self, timeout: int = 10):
        super().__init__(timeout=timeout)
        self._cached_page_data: Optional[List[dict]] = None

    def _get_page_data(self) -> List[dict]:
        """抓取并解析 www.xxsy.net/rank/ 的 Vite SSR pageData (带实例级缓存)"""
        if self._cached_page_data is not None:
            return self._cached_page_data

        url = "https://www.xxsy.net/rank/"
        try:
            html = self.fetch_url(url, is_mobile=False, encoding="utf-8")
            m_ssr = re.search(r'<script id="vite-plugin-ssr_pageContext"[^>]*>(.*?)</script>', html, re.DOTALL)
            if m_ssr:
                data = json.loads(m_ssr.group(1))
                page_data = data.get("pageContext", {}).get("pageProps", {}).get("pageData", [])
                if isinstance(page_data, list) and page_data:
                    self._cached_page_data = page_data
                    return page_data
        except Exception as e:
            print(f"[{self.platform_name}] 获取或解析官方 SSR 数据异常: {e}")

        return []

    def fetch_board(self, board_name: str, section_idx: int = 0, max_count: int = 20) -> List[NovelBook]:
        """
        抓取潇湘书院指定榜单作品
        :param board_name: 榜单名称（如 '潇湘票榜', '畅销榜', '完结榜', '阅读榜', '收藏榜'）
        :param section_idx: 预期的 section 索引号（作为备选）
        :param max_count: 抓取上限数量（官方每榜展示 20 本）
        """
        page_data = self._get_page_data()
        books: List[NovelBook] = []

        # 1. 精确匹配 columnInfo.name == board_name
        target_section = None
        for s in page_data:
            col_name = (s.get("columnInfo") or {}).get("name")
            if col_name and col_name == board_name:
                target_section = s
                break

        # 2. 如果未命中精确名称，回退使用 section_idx
        if not target_section and 0 <= section_idx < len(page_data):
            target_section = page_data[section_idx]

        if target_section:
            r_name = (target_section.get("columnInfo") or {}).get("name") or board_name
            book_list = target_section.get("bookList", [])
            seen_titles = set()

            for idx, item in enumerate(book_list, 1):
                title = (item.get("title") or "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                cbid = str(item.get("cbid") or "")
                author = (item.get("author") or "潇湘知名作家").strip()
                cat1 = item.get("catel2name") or "古代言情"
                cat2 = item.get("catel3name") or ""
                full_cat = f"{cat1}/{cat2}" if cat2 else cat1
                intro = (item.get("intro") or "").strip()

                book = NovelBook(
                    book_id=cbid or f"xx_{board_name}_{idx}",
                    title=title,
                    author=author,
                    platform=self.platform_name,
                    channel=self.channel,
                    rank_name=r_name,
                    rank_position=idx,
                    category=full_cat,
                    intro=intro,
                    book_url=f"https://www.xxsy.net/book/{cbid}" if cbid else "https://www.xxsy.net/rank/",
                    heat_score=round(max(70.0, 100.0 - (idx - 1) * 1.5), 1)
                )
                books.append(book)
                if len(books) >= max_count:
                    break

        # 3. 备用 DOM 正则兜底解析（当 SSR 不可用时）
        if not books:
            try:
                url = "https://www.xxsy.net/rank/"
                html = self.fetch_url(url, is_mobile=False, encoding="utf-8")
                raw_books = re.findall(
                    r'<li[^>]+data-bookid="(\d+)"[^>]*>.*?<span class="piao">([^<]+)</span>.*?<a[^>]+>([^<]+)</a>',
                    html,
                    re.DOTALL
                )
                for idx, (bid, aname, bname) in enumerate(raw_books[:max_count], 1):
                    books.append(NovelBook(
                        book_id=bid,
                        title=bname.strip(),
                        author=aname.strip(),
                        platform=self.platform_name,
                        channel=self.channel,
                        rank_name=board_name,
                        rank_position=idx,
                        category="女频热门言情",
                        intro="",
                        book_url=f"https://www.xxsy.net/book/{bid}",
                        heat_score=round(max(70.0, 100.0 - (idx - 1) * 1.5), 1)
                    ))
            except Exception as e:
                print(f"[{self.platform_name}] DOM 正则备用解析异常: {e}")

        return books

    def fetch_all_boards(self, max_per_board: int = 20) -> List[NovelBook]:
        """抓取潇湘书院全量五大核心榜单（各榜抓取 TOP max_per_board）"""
        all_books: List[NovelBook] = []
        for board_name, section_idx in self.BOARDS:
            board_books = self.fetch_board(board_name, section_idx=section_idx, max_count=max_per_board)
            all_books.extend(board_books)
        return all_books

    def fetch_top_books(self, max_count: int = 100) -> List[NovelBook]:
        """向下兼容原有聚合接口"""
        all_books = self.fetch_all_boards(max_per_board=20)
        return all_books[:max_count]
