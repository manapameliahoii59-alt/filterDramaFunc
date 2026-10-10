"""
novel_trends/crawlers/zongheng_crawler.py
纵横中文网 (Zongheng) 真实榜单爬虫
支持男频三大核心榜单：月票榜、新书榜、新书订阅榜 (各 TOP 50)
优先调用官方 rank/details 接口快速直达，同时保留 Nuxt SSR HTML 解析兜底
"""
import re
import json
import urllib.parse
from typing import List, Dict, Optional
from .base_crawler import BaseNovelCrawler
from ..models import NovelBook


class ZonghengCrawler(BaseNovelCrawler):
    platform_name = "纵横中文网"
    channel = "男频"

    # 三大核心榜单映射: (榜单名称, rankType)
    BOARDS = [
        ("月票榜", 1),
        ("新书榜", 4),
        ("新书订阅榜", 9),
    ]

    def fetch_board(self, board_name: str, rank_type: int, max_count: int = 50) -> List[NovelBook]:
        """
        抓取指定榜单 TOP 作品 (默认 50 部)
        """
        books: List[NovelBook] = []
        api_url = "https://www.zongheng.com/api/rank/details"

        # 1. 优先调用纵横中文网官方排行榜详情接口 (POST application/x-www-form-urlencoded)
        try:
            params = {
                "cateFineId": 0,
                "cateType": 0,
                "pageNum": 1,
                "pageSize": max_count,
                "period": 0,
                "rankNo": "",
                "rankType": rank_type
            }
            body_bytes = urllib.parse.urlencode(params).encode('utf-8')
            custom_headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.zongheng.com",
                "Referer": "https://www.zongheng.com/rank"
            }

            resp_str = self.fetch_url(
                api_url,
                is_mobile=False,
                encoding="utf-8",
                extra_headers=custom_headers,
                data=body_bytes
            )
            raw = json.loads(resp_str)
            rlist = raw.get("result", {}).get("resultList", []) if isinstance(raw.get("result"), dict) else []

            seen_titles = set()
            for item in rlist:
                bname = item.get("bookName", "").strip()
                if not bname or bname in seen_titles:
                    continue
                seen_titles.add(bname)

                bid = str(item.get("bookId", ""))
                aname = item.get("pseudonym", "").strip() or "纵横签约作家"
                cname = item.get("cateFineName", "").strip() or "男频爽文"
                desc = item.get("description", "").strip()
                order_no = item.get("orderNo") or (len(books) + 1)

                book = NovelBook(
                    book_id=bid,
                    title=bname,
                    author=aname,
                    platform=self.platform_name,
                    channel=self.channel,
                    rank_name=board_name,
                    rank_position=order_no,
                    category=cname,
                    intro=desc or f"纵横中文网{board_name}前列作品《{bname}》",
                    book_url=f"https://www.zongheng.com/book/{bid}.html" if bid else "",
                    heat_score=round(max(65.0, 100.0 - (order_no - 1) * 0.7), 1)
                )
                books.append(book)
                if len(books) >= max_count:
                    break

        except Exception as e:
            # 2. 接口异常时触发兜底解析
            books = self._fallback_fetch_board(board_name, max_count)

        return books

    def fetch_top_books(self, max_count: int = 150, top_per_board: int = 50) -> List[NovelBook]:
        """
        全量抓取三大指定榜单 (月票榜、新书榜、新书订阅榜)，各 TOP 50 (合计至多 150 部)
        若传入限额较小，则均分至三大榜单
        """
        limit_per_board = max(10, max_count // len(self.BOARDS))
        if max_count >= len(self.BOARDS) * top_per_board:
            limit_per_board = top_per_board

        all_books: List[NovelBook] = []
        for board_name, rank_type in self.BOARDS:
            board_books = self.fetch_board(board_name, rank_type, max_count=limit_per_board)
            all_books.extend(board_books)
            if len(all_books) >= max_count:
                break

        return all_books[:max_count]

    def _fallback_fetch_board(self, board_name: str, max_count: int) -> List[NovelBook]:
        """后备 Nuxt SSR HTML 解析"""
        books: List[NovelBook] = []
        try:
            url = "https://www.zongheng.com/rank/details.html?type=1&pageNum=1"
            html = self.fetch_url(url, is_mobile=False, encoding="utf-8")
            m_nuxt = re.search(r'window\.__NUXT__\s*=\s*(.*?);</script>', html, re.DOTALL)
            if m_nuxt:
                nuxt_text = m_nuxt.group(1)
                matches = re.findall(
                    r'bookId:(\d+),bookName:"([^"]+)",authorId:\d+,authorName:"([^"]+)"(?:,[^}]*?cateFineName:"([^"]+)")?',
                    nuxt_text
                )
                seen_titles = set()
                rank_idx = 1
                for bid, bname, aname, cname in matches:
                    bname = bname.strip()
                    if not bname or bname in seen_titles:
                        continue
                    seen_titles.add(bname)

                    desc_m = re.search(r'bookId:' + bid + r'[^}]+description:"([^"]+)"', nuxt_text)
                    intro = desc_m.group(1) if desc_m else ""

                    books.append(NovelBook(
                        book_id=bid,
                        title=bname,
                        author=aname.strip() if aname else "纵横作家",
                        platform=self.platform_name,
                        channel=self.channel,
                        rank_name=board_name,
                        rank_position=rank_idx,
                        category=cname.strip() if cname else "男频爽文",
                        intro=intro,
                        book_url=f"https://www.zongheng.com/book/{bid}.html",
                        heat_score=round(max(65.0, 100.0 - (rank_idx - 1) * 0.7), 1)
                    ))
                    rank_idx += 1
                    if len(books) >= max_count:
                        break
        except Exception:
            pass
        return books
