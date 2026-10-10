"""
novel_trends/crawlers/qidian_crawler.py
起点中文网 (Qidian) 真实榜单爬虫
支持四大榜单：月票榜、潜力榜、签约新书榜、未签约新书榜 (各 TOP 50)
"""
from typing import Dict, List, Optional
import json
import re
from .base_crawler import BaseNovelCrawler
from ..models import NovelBook


class QidianCrawler(BaseNovelCrawler):
    platform_name = "起点中文网"
    channel = "男频"

    # 四大指定榜单映射: (榜单中文名, URL slug)
    BOARDS = [
        ("月票榜", "yuepiao"),
        ("潜力榜", "newauthor"),    # 起点移动端官方新人作者潜力榜
        ("签约新书榜", "sign"),       # 起点移动端签约作者新书榜
        ("未签约新书榜", "newbook"),  # 起点移动端公众/未签约新书榜
    ]

    # 分类后备列表，确保当总榜单页记录不足50条时，可按热门分类递进补全至 TOP 50
    CATEGORY_SLUGS = [
        "",          # 全站/总榜 (默认前20名)
        "catid21/",  # 玄幻
        "catid4/",   # 都市
        "catid22/",  # 仙侠
        "catid5/",   # 历史
        "catid9/",   # 科幻
        "catid12/",  # 轻小说
        "catid1/",   # 奇幻
        "catid7/",   # 游戏
        "catid10/",  # 悬疑灵异
        "catid15/",  # 现实
        "catid2/",   # 武侠
    ]

    def fetch_board(self, board_name: str, slug: str, max_count: int = 50) -> List[NovelBook]:
        """
        抓取指定榜单的 TOP 作品 (默认 50 部)
        优先从主榜拉取，不足 50 部时跨主分类精准补足
        """
        books: List[NovelBook] = []
        seen_titles = set()

        for cat_slug in self.CATEGORY_SLUGS:
            if len(books) >= max_count:
                break

            url = f"https://m.qidian.com/rank/{slug}/{cat_slug}"
            try:
                html = self.fetch_url(url, is_mobile=True, encoding="utf-8")
                records = self._parse_page_context_records(html)

                # 若 SSR JSON 缺失则后备正则
                if not records:
                    records = self._parse_html_regex(html)

                for r in records:
                    title = r.get("title", "").strip()
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)

                    rank_pos = len(books) + 1
                    heat = round(max(60.0, 100.0 - (rank_pos - 1) * 0.8), 1)

                    book = NovelBook(
                        book_id=str(r.get("bid", "")),
                        title=title,
                        author=r.get("author", "起点知名作家"),
                        platform=self.platform_name,
                        channel=self.channel,
                        rank_name=board_name,
                        rank_position=rank_pos,
                        category=r.get("category", "男频爽文"),
                        intro=r.get("intro", f"起点中文网{board_name}前列作品《{title}》"),
                        book_url=f"https://www.qidian.com/book/{r.get('bid', '')}/" if r.get("bid") else "",
                        heat_score=heat
                    )
                    books.append(book)

                    if len(books) >= max_count:
                        break

            except Exception as e:
                # 捕获单个分类页异常，不中断整体抓取
                continue

        return books

    def fetch_top_books(self, max_count: int = 200, top_per_board: int = 50) -> List[NovelBook]:
        """
        全量抓取四大指定榜单 (月票榜、潜力榜、签约新书榜、未签约新书榜)，各 TOP 50 (合计至多 200 部)
        """
        all_books: List[NovelBook] = []
        for board_name, slug in self.BOARDS:
            board_books = self.fetch_board(board_name, slug, max_count=top_per_board)
            all_books.extend(board_books)
            if len(all_books) >= max_count:
                break

        return all_books[:max_count]

    def _parse_page_context_records(self, html: str) -> List[Dict[str, str]]:
        """从移动端 SSR 页面提取 pageContext.pageProps.pageData.records"""
        results = []
        scripts = [s for s in re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL) if 'pageContext' in s]
        if not scripts:
            return results

        try:
            raw = json.loads(scripts[0])
            pc = raw.get("pageContext", raw)
            pdata = pc.get("pageProps", {}).get("pageData", {})
            records = pdata.get("records", [])
            for item in records:
                bname = item.get("bName", "").strip()
                if not bname:
                    continue
                cat = item.get("cat", "").strip() or item.get("subCat", "").strip() or "综合"
                desc = item.get("desc", "").strip()
                results.append({
                    "bid": item.get("bid", ""),
                    "title": bname,
                    "author": item.get("bAuth", "").strip() or "起点作家",
                    "category": cat,
                    "intro": desc
                })
        except Exception:
            pass

        return results

    def _parse_html_regex(self, html: str) -> List[Dict[str, str]]:
        """后备 HTML 正则匹配"""
        results = []
        matches = re.findall(r'<a[^>]+href="[^"]*book/(\d+)[^"]*"[^>]*title="([^"]+)"', html)
        if not matches:
            matches = re.findall(r'<h[234][^>]*><a[^>]+href="[^"]*book/(\d+)[^"]*"[^>]*>([^<]+)</a>', html)

        for bid, raw_title in matches:
            clean_title = (
                raw_title.replace("最新章节在线阅读", "")
                .replace("最新章节阅读", "")
                .replace("最新章节", "")
                .strip()
            )
            if clean_title:
                results.append({
                    "bid": bid,
                    "title": clean_title,
                    "author": "起点作家",
                    "category": "男频爽文",
                    "intro": f"起点中文网作品《{clean_title}》"
                })
        return results
