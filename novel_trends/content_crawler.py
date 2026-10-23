# -*- coding: utf-8 -*-
"""
novel_trends/content_crawler.py
章节标题、免费正文与书籍简介抓取编排器。

策略：
- 目录标题：各站静态 SSR / 官方接口 / Playwright 运行时数据；
- 免费正文：优先静态页面解析（起点 SSR、纵横阅读页、晋江移动页、潇湘 SSR）；
- 书籍简介：各站详情页的真实简介字段；
- 只抓免费章节：遇到 VIP/锁定章节自动跳过（不绕过付费墙）。
"""
from __future__ import annotations

import json
import random
import re
import time
from typing import Any, Dict, List, Optional, Sequence

from .models import NovelBook, NovelChapter, NovelIntro
from .config import DEFAULT_CHAPTER_COUNT, DEFAULT_TITLE_COUNT
from .crawlers.qidian_crawler import QidianCrawler
from .crawlers.zongheng_crawler import ZonghengCrawler
from .crawlers.jinjiang_crawler import JinjiangCrawler
from .crawlers.xiaoxiang_crawler import XiaoxiangCrawler


def _clean_text(raw: str) -> str:
    """HTML / 空白清洗为纯文本"""
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    lines = [re.sub(r"[ \t\u3000]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _compact(text: str, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit] if limit else text


class ContentCrawler:
    """章节与评论抓取编排器"""

    def __init__(
        self,
        max_titles: int = DEFAULT_TITLE_COUNT,
        max_chapters: int = DEFAULT_CHAPTER_COUNT,
        use_playwright: bool = True,
        request_delay: float = 0.4,
    ):
        self.max_titles = max(1, max_titles)
        self.max_chapters = max(0, max_chapters)
        self.use_playwright = use_playwright
        self.request_delay = max(0.0, request_delay)
        self._req_count = 0

        self.qidian = QidianCrawler()
        self.zongheng = ZonghengCrawler()
        self.jinjiang = JinjiangCrawler()
        self.xiaoxiang = XiaoxiangCrawler()

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------------ PW
    def _ensure_page(self):
        if not self.use_playwright:
            return None
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
            from changdu_sync.stealth import (
                STEALTH_INIT_SCRIPT,
                browser_context_kwargs,
                launch_playwright_browser,
            )

            self._pw = sync_playwright().start()
            self._browser = launch_playwright_browser(self._pw, headless=True)
            self._context = self._browser.new_context(**browser_context_kwargs())
            self._context.add_init_script(STEALTH_INIT_SCRIPT)
            self._page = self._context.new_page()
            self._page.set_default_timeout(45000)
        except Exception as e:
            print(f"[ContentCrawler] Playwright 不可用，降级为静态抓取: {e}")
            self.use_playwright = False
            self._page = None
        return self._page

    def close(self):
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._context = self._browser = self._pw = self._page = None

    def _sleep(self):
        """请求间随机抖动 + 周期性冷却，降低触发风控概率"""
        self._req_count += 1
        if self.request_delay:
            time.sleep(random.uniform(self.request_delay * 0.6, self.request_delay * 1.8))
        if self._req_count % 25 == 0:
            pause = random.uniform(4.0, 8.0)
            print(f"[ContentCrawler] 已累计请求 {self._req_count} 次，冷却 {pause:.1f}s ...", flush=True)
            time.sleep(pause)

    def _pw_goto(self, url: str, wait_ms: int = 2500):
        page = self._ensure_page()
        if page is None:
            return None
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if resp is not None and resp.status in (429, 502, 503):
                pause = random.uniform(6.0, 12.0)
                print(f"[ContentCrawler] 触发限流 HTTP {resp.status}，冷却 {pause:.1f}s 后重试 ...", flush=True)
                page.wait_for_timeout(int(pause * 1000))
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(wait_ms)
            return page
        except Exception as e:
            print(f"[ContentCrawler] 页面渲染失败 {url}: {e}")
            return None

    @staticmethod
    def _is_story_chapter(title: str) -> bool:
        """过滤公告/感言/求票等非正文条目"""
        t = (title or "").strip()
        if not t:
            return False
        noise_prefixes = (
            "感谢", "上架", "请假", "公告", "单章", "感言", "求票", "求月票",
            "通知", "说明", "加更", "封推", "推荐票", "新书", "致读者", "关于",
        )
        return not t.startswith(noise_prefixes)

    # -------------------------------------------------------------- public
    def crawl_book(self, book: NovelBook) -> Dict[str, Any]:
        platform = (book.platform or "").strip()
        data: Dict[str, Any] = {"chapters": []}
        try:
            if platform == "起点中文网":
                data = self._crawl_qidian(book)
            elif platform == "纵横中文网":
                data = self._crawl_zongheng(book)
            elif platform == "晋江文学城":
                data = self._crawl_jinjiang(book)
            elif platform == "潇湘书院":
                data = self._crawl_xiaoxiang(book)
        except Exception as e:
            print(f"[ContentCrawler] 《{book.title}》({platform}) 抓取异常: {e}")

        intro = self._fetch_intro(book)
        if intro is not None and intro.intro:
            data["intro"] = intro
        return data

    def _fetch_intro(self, book: NovelBook):
        """抓取真实书籍简介（各站字段不同）"""
        platform = (book.platform or "").strip()
        bid = (book.book_id or "").strip()
        if not bid:
            return None
        try:
            if platform == "起点中文网":
                html = self.qidian.fetch_url(
                    f"https://m.qidian.com/book/{bid}/", is_mobile=True, encoding="utf-8"
                )
                if not html or self.qidian.is_waf_challenge(html):
                    print(f"[ContentCrawler] [风控拦截] 起点简介命中 WAF 挑战: bid={bid}")
                    return None
                m = re.search(
                    r'<script id="vite-plugin-ssr_pageContext" type="application/json">(.*?)</script>',
                    html, re.S,
                )
                if m:
                    pd = json.loads(m.group(1))["pageContext"]["pageProps"]["pageData"]
                    text = _clean_text((pd.get("bookInfo") or {}).get("desc") or "")
                    return self._make_intro(book, text, f"https://www.qidian.com/book/{bid}/")

            elif platform == "晋江文学城":
                html = self.jinjiang.fetch_url(
                    f"https://m.jjwxc.net/book2/{bid}", is_mobile=True, encoding="gb18030"
                )
                m = re.search(r'id=["\']novelintro["\'][^>]*>(.*?)</div>', html, re.S)
                if m:
                    text = _clean_text(m.group(1))
                    text = re.sub(r"^简介[：:]\s*", "", text).strip()
                    return self._make_intro(book, text, f"https://www.jjwxc.net/onebook.php?novelid={bid}")

            elif platform == "潇湘书院":
                html = self.xiaoxiang.fetch_url(
                    f"https://www.xxsy.net/book/{bid}", is_mobile=False, encoding="utf-8"
                )
                if not html or self.xiaoxiang.is_waf_challenge(html):
                    print(f"[ContentCrawler] [风控拦截] 潇湘简介命中 WAF 挑战: bid={bid}")
                    return None
                m = re.search(
                    r'<script id="vite-plugin-ssr_pageContext" type="application/json">(.*?)</script>',
                    html, re.S,
                )
                if m:
                    pd = json.loads(m.group(1))["pageContext"]["pageProps"]["pageData"]
                    text = _clean_text(pd.get("intro") or "")
                    return self._make_intro(book, text, f"https://www.xxsy.net/book/{bid}")

            elif platform == "纵横中文网":
                # 榜单接口已返回真实 description，直接用；没有再用书籍页兜底
                text = (book.intro or "").strip()
                if text and not text.startswith(f"纵横中文网"):
                    return self._make_intro(book, _clean_text(text), f"https://www.zongheng.com/book/{bid}.html")
        except Exception as e:
            print(f"[ContentCrawler] 《{book.title}》简介抓取失败: {e}")
        return None

    @staticmethod
    def _make_intro(book: NovelBook, text: str, url: str):
        text = (text or "").strip()
        if not text:
            return None
        return NovelIntro(
            platform=book.platform, book_id=book.book_id,
            title=book.title, author=book.author or "",
            intro=text, url=url,
        )

    def crawl_and_save(self, books: Sequence[NovelBook], pg_storage=None) -> Dict[str, int]:
        stats = {"books": 0, "chapters": 0, "contents": 0, "intros": 0}
        for idx, book in enumerate(books, 1):
            print(f"  [内容抓取 {idx}/{len(books)}] {book.platform} 《{book.title}》...", flush=True)
            data = self.crawl_book(book)
            chapters = data.get("chapters", [])
            intro = data.get("intro")
            stats["books"] += 1
            stats["chapters"] += len(chapters)
            stats["contents"] += sum(1 for c in chapters if c.content)
            if intro is not None and intro.intro:
                stats["intros"] += 1
            if pg_storage is not None:
                pg_storage.save_chapters(chapters)
                if intro is not None:
                    pg_storage.save_intros([intro])
                pg_storage.save_content_status(
                    platform=book.platform,
                    book_id=book.book_id,
                    target_chapters=self.max_chapters,
                    content_chapters=int(data.get("content_count", 0)),
                    title_chapters=int(data.get("title_count", 0)),
                    exhausted=bool(data.get("exhausted", False)),
                )
            print(
                f"      -> 章节 {len(chapters)}（含正文 {sum(1 for c in chapters if c.content)}）"
                f"，简介 {'有' if (intro and intro.intro) else '无'}",
                flush=True,
            )
        return stats

    def _result(
        self, chapters: List[NovelChapter], fetched: int, catalog_len: int, exhausted: Optional[bool] = None
    ) -> Dict[str, Any]:
        """组装单本抓取结果，并记录是否已抓完所有可用免费章节。"""
        if exhausted is None:
            # 只有目录有效且非空时，抓取数未达标才视为耗尽（免费章节少于目标）；
            # 目录获取失败（catalog_len==0）绝不标记为 exhausted，防止误杀导致不再重试。
            exhausted = (catalog_len > 0) and (fetched < self.max_chapters)
        return {
            "chapters": chapters[: max(self.max_titles, self.max_chapters)],
            "content_count": fetched,
            "title_count": catalog_len,
            "exhausted": bool(exhausted),
        }

    # ------------------------------------------------------------- 起点
    def _crawl_qidian(self, book: NovelBook) -> Dict[str, Any]:
        bid = (book.book_id or "").strip()
        chapters: List[NovelChapter] = []
        if not bid:
            return self._result(chapters, 0, 0, exhausted=False)

        catalog = self._qidian_catalog(bid)
        if not catalog:
            # 目录获取失败（可能因 WAF 挑战、网络波动或临时限流），绝不标记为 exhausted，留待下次重试
            return self._result(chapters, 0, 0, exhausted=False)

        for i, item in enumerate(catalog[: self.max_titles], 1):
            chapters.append(NovelChapter(
                platform=book.platform, book_id=bid, chapter_index=i,
                chapter_id=str(item.get("id", "")), title=item.get("cN", ""),
                is_free=True, word_count=int(item.get("cnt") or 0),
                url=f"https://m.qidian.com/chapter/{bid}/{item.get('id', '')}/",
            ))

        fetched = 0
        hit_vip_or_completed = False
        consecutive_errors = 0
        for i, item in enumerate(catalog, 1):
            if fetched >= self.max_chapters:
                hit_vip_or_completed = True
                break
            cid = str(item.get("id", ""))
            if not cid:
                continue
            data = self._qidian_chapter(bid, cid)
            if not data:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    print(f"[ContentCrawler] 起点《{book.title}》({bid}) 连续 3 次获取章节失败 (可能遭遇风控或限流)，保护性中断")
                    break
                continue
            consecutive_errors = 0
            content, name, words, free = data
            self._merge_chapter(chapters, book.platform, bid, i, cid, name, content, words, free)
            if not free:
                # 免费章节是有序前缀，遇到 VIP 即可停止，这是正常的 exhausted
                hit_vip_or_completed = True
                break
            if content:
                fetched += 1
            self._sleep()

        # 仅当命中 VIP 锁定或抓满额度，且无风控连错中断时才判定为 exhausted
        is_exhausted = hit_vip_or_completed or (consecutive_errors == 0 and fetched > 0)
        return self._result(chapters, fetched, len(catalog), exhausted=is_exhausted)

    def _qidian_catalog(self, bid: str) -> List[Dict[str, Any]]:
        try:
            html = self.qidian.fetch_url(
                f"https://m.qidian.com/book/{bid}/catalog/", is_mobile=True, encoding="utf-8"
            )
            if not html or self.qidian.is_waf_challenge(html):
                print(f"[ContentCrawler] [风控拦截] 起点目录命中 WAF 挑战或内容为空: bid={bid}")
                return []
            m = re.search(
                r'<script id="vite-plugin-ssr_pageContext" type="application/json">(.*?)</script>',
                html, re.S,
            )
            if not m:
                return []
            pd = json.loads(m.group(1))["pageContext"]["pageProps"]["pageData"]
            result: List[Dict[str, Any]] = []
            for vol in pd.get("vs", []) or []:
                for ch in vol.get("cs", []) or []:
                    if ch.get("cN") and self._is_story_chapter(ch.get("cN")):
                        result.append(ch)
            return result
        except Exception as e:
            print(f"[ContentCrawler] 起点目录失败 {bid}: {e}")
            return []

    def _qidian_chapter(self, bid: str, cid: str):
        try:
            html = self.qidian.fetch_url(
                f"https://m.qidian.com/chapter/{bid}/{cid}/", is_mobile=True, encoding="utf-8"
            )
            if not html or self.qidian.is_waf_challenge(html):
                print(f"[ContentCrawler] [风控拦截] 起点章节命中 WAF 挑战: bid={bid}, cid={cid}")
                return None
            m = re.search(
                r'<script id="vite-plugin-ssr_pageContext" type="application/json">(.*?)</script>',
                html, re.S,
            )
            if not m:
                return None
            pd = json.loads(m.group(1))["pageContext"]["pageProps"]["pageData"]
            info = pd.get("chapterInfo") or {}
            free = int(info.get("vipStatus") or 0) == 0
            content = _clean_text(info.get("content") or "") if free else ""
            return content, info.get("chapterName", ""), int(info.get("wordsCount") or 0), free
        except Exception as e:
            print(f"[ContentCrawler] 起点章节失败 {bid}/{cid}: {e}")
            return None

    # ------------------------------------------------------------- 纵横
    def _crawl_zongheng(self, book: NovelBook) -> Dict[str, Any]:
        bid = (book.book_id or "").strip()
        chapters: List[NovelChapter] = []
        if not bid:
            return self._result(chapters, 0, 0, exhausted=False)

        catalog = self._zongheng_catalog(bid)
        if not catalog:
            # 目录获取失败时绝不标记为 exhausted，防止阻断重试
            return self._result(chapters, 0, 0, exhausted=False)

        for i, item in enumerate(catalog[: self.max_titles], 1):
            chapters.append(NovelChapter(
                platform=book.platform, book_id=bid, chapter_index=i,
                chapter_id=str(item.get("chapterId", "")), title=item.get("chapterName", ""),
                is_free=(item.get("price") in (0, "0", None)),
                word_count=int(item.get("wordNums") or 0),
                url=f"https://read.zongheng.com/chapter/{bid}/{item.get('chapterId', '')}.html",
            ))

        fetched = 0
        hit_vip_or_completed = False
        consecutive_errors = 0
        for i, item in enumerate(catalog, 1):
            if fetched >= self.max_chapters:
                hit_vip_or_completed = True
                break
            if item.get("price") not in (0, "0", None):
                # 遇到 VIP 章节，停止继续抓取正文
                hit_vip_or_completed = True
                break
            cid = str(item.get("chapterId", ""))
            data = self._zongheng_chapter(bid, cid)
            if not data:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    print(f"[ContentCrawler] 纵横《{book.title}》({bid}) 连续 3 次获取正文失败，保护性中断")
                    break
                continue
            consecutive_errors = 0
            content, name = data
            self._merge_chapter(chapters, book.platform, bid, i, cid, name, content, 0, True)
            if content:
                fetched += 1
            self._sleep()

        is_exhausted = hit_vip_or_completed or (consecutive_errors == 0 and fetched > 0)
        return self._result(chapters, fetched, len(catalog), exhausted=is_exhausted)

    def _zongheng_catalog(self, bid: str) -> List[Dict[str, Any]]:
        # 1. 优先调用纵横官方目录直连接口（无风控探针、零前端渲染消耗、毫秒级响应）
        raw_list = self.zongheng.fetch_catalog(bid)
        if raw_list:
            return [
                it for it in raw_list
                if it.get("chapterId") and self._is_story_chapter(it.get("chapterName", ""))
            ]

        # 2. 备用兜底方案：Playwright 浏览器渲染（访问带 tabsName=catalogue 参数的详情页）
        if not self.use_playwright:
            print(f"[ContentCrawler] 纵横目录为空 (API未命中且Playwright未启用): {bid}")
            return []

        page = self._pw_goto(f"https://www.zongheng.com/detail/{bid}?tabsName=catalogue", wait_ms=4000)
        if page is None:
            return []
        script = """() => {
            const s = window.__NUXT__ && window.__NUXT__.state;
            const ch = s && s.detail && s.detail.detailChapter;
            if (!ch) return [];
            const vols = ch.chapterList || [];
            let out = [];
            for (const v of vols) out = out.concat(v.chapterViewList || []);
            return out;
        }"""
        for attempt in range(6):
            try:
                data = page.evaluate(script)
                if data:
                    return [
                        it for it in data
                        if it.get("chapterId") and self._is_story_chapter(it.get("chapterName", ""))
                    ]
            except Exception:
                pass
            page.wait_for_timeout(1000)
        print(f"[ContentCrawler] 纵横目录为空 {bid}")
        return []

    def _zongheng_chapter(self, bid: str, cid: str):
        if not cid:
            return None
        try:
            html = self.zongheng.fetch_url(
                f"https://read.zongheng.com/chapter/{bid}/{cid}.html",
                is_mobile=False, encoding="utf-8",
            )
            m = re.search(r'<div class="content"[^>]*>(.*?)</div>', html, re.S)
            block = m.group(1) if m else ""
            content = _clean_text(block)
            title = ""
            tm = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
            if tm:
                title = _compact(_clean_text(tm.group(1)))
            if len(content) < 300:
                return None
            return content, title
        except Exception:
            return None

    # ------------------------------------------------------------- 晋江
    def _crawl_jinjiang(self, book: NovelBook) -> Dict[str, Any]:
        bid = (book.book_id or "").strip()
        chapters: List[NovelChapter] = []
        if not bid:
            return self._result(chapters, 0, 0, exhausted=False)

        catalog = self._jinjiang_catalog(bid)
        if not catalog:
            # 目录获取失败时绝不标记为 exhausted，防止阻断重试
            return self._result(chapters, 0, 0, exhausted=False)

        for i, item in enumerate(catalog[: self.max_titles], 1):
            chapters.append(NovelChapter(
                platform=book.platform, book_id=bid, chapter_index=i,
                chapter_id=str(item.get("chapter_id", "")), title=item.get("title", ""),
                is_free=bool(item.get("is_free", True)),
                word_count=int(item.get("words") or 0),
                url=f"https://m.jjwxc.net/book2/{bid}/{item.get('chapter_id', '')}",
            ))

        fetched = 0
        hit_vip_or_completed = False
        consecutive_errors = 0
        for i, item in enumerate(catalog, 1):
            if fetched >= self.max_chapters:
                hit_vip_or_completed = True
                break
            if not item.get("is_free", True):
                # 遇到 VIP 章节，停止继续抓取正文
                hit_vip_or_completed = True
                break
            cid = str(item.get("chapter_id", ""))
            data = self._jinjiang_chapter(bid, cid)
            if not data:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    print(f"[ContentCrawler] 晋江《{book.title}》({bid}) 连续 3 次获取正文失败，保护性中断")
                    break
                continue
            consecutive_errors = 0
            content, name = data
            self._merge_chapter(chapters, book.platform, bid, i, cid, name, content, 0, True)
            if content:
                fetched += 1
            self._sleep()

        is_exhausted = hit_vip_or_completed or (consecutive_errors == 0 and fetched > 0)
        return self._result(chapters, fetched, len(catalog), exhausted=is_exhausted)

    def _jinjiang_catalog(self, bid: str) -> List[Dict[str, Any]]:
        try:
            html = self.jinjiang.fetch_url(
                f"https://www.jjwxc.net/onebook.php?novelid={bid}",
                is_mobile=False, encoding="gb18030",
            )
            if not html:
                return []
            if any(sig in html for sig in ("访问过于频繁", "请输入验证码", "滑动验证")) and "oneboolt" not in html:
                print(f"[ContentCrawler] [风控预警] 晋江目录触发频控或数美验证: bid={bid}")
                return []
            m = re.search(r'<table[^>]*id=["\']oneboolt["\'].*?</table>', html, re.S | re.I)
            if not m:
                return []
            result = []
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S):
                hm = re.search(r"chapterid=(\d+)", row)
                if not hm:
                    continue
                cid = hm.group(1)
                tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
                if len(tds) >= 4:
                    c_idx = _clean_text(tds[0])
                    c_title = _clean_text(tds[1]).replace("[VIP]", "").strip()
                    c_words = _clean_text(tds[3]).strip()
                    is_free = not ("[VIP]" in row or "vip" in row.lower())
                    title = f"第{c_idx}章 {c_title}" if c_title else f"第{c_idx}章"
                    words = int(c_words) if c_words.isdigit() else 0
                    result.append({
                        "chapter_id": cid,
                        "title": title,
                        "words": words,
                        "is_free": is_free,
                    })
            # 去重保序
            seen, out = set(), []
            for it in result:
                if it["chapter_id"] in seen:
                    continue
                seen.add(it["chapter_id"])
                out.append(it)
            return out
        except Exception as e:
            print(f"[ContentCrawler] 晋江目录失败 {bid}: {e}")
            return []

    def _jinjiang_chapter(self, bid: str, cid: str):
        if not cid:
            return None
        try:
            html = self.jinjiang.fetch_url(
                f"https://m.jjwxc.net/book2/{bid}/{cid}", is_mobile=True, encoding="gb18030"
            )
            if not html:
                return None
            if any(sig in html for sig in ("访问过于频繁", "请输入验证码", "滑动验证")):
                print(f"[ContentCrawler] [风控预警] 晋江章节触发频控或数美验证: bid={bid}, cid={cid}")
                return None
            # 目录页/被锁章节：正文通常很短，交给长度阈值过滤
            content = self._largest_text_from_html(html)
            tm = re.search(r"<h[12][^>]*>(.*?)</h[12]>", html, re.S)
            title = _compact(_clean_text(tm.group(1))) if tm else ""
            if len(content) < 300:
                return None
            return content, title
        except Exception:
            return None

    # ------------------------------------------------------------- 潇湘
    def _crawl_xiaoxiang(self, book: NovelBook) -> Dict[str, Any]:
        bid = (book.book_id or "").strip()
        chapters: List[NovelChapter] = []
        if not bid:
            return self._result(chapters, 0, 0, exhausted=False)

        catalog = self._xiaoxiang_catalog(bid)
        if not catalog:
            # 目录获取失败时绝不标记为 exhausted
            return self._result(chapters, 0, 0, exhausted=False)

        for i, item in enumerate(catalog[: self.max_titles], 1):
            is_free = item.get("price") in (0, "0", None)
            chapters.append(NovelChapter(
                platform=book.platform, book_id=bid, chapter_index=i,
                chapter_id=str(item.get("ccid", "")), title=item.get("title", ""),
                is_free=is_free, word_count=0,
                url=f"https://www.xxsy.net/chapter/{bid}/{item.get('ccid', '')}",
            ))

        fetched = 0
        hit_vip_or_completed = False
        consecutive_errors = 0
        for i, item in enumerate(catalog, 1):
            if fetched >= self.max_chapters:
                hit_vip_or_completed = True
                break
            if item.get("price") not in (0, "0", None):
                # 遇到 VIP 章节，停止继续抓取正文
                hit_vip_or_completed = True
                break
            cid = str(item.get("ccid", ""))
            data = self._xiaoxiang_chapter(bid, cid)
            if not data:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    print(f"[ContentCrawler] 潇湘《{book.title}》({bid}) 连续 3 次获取正文失败，保护性中断")
                    break
                continue
            consecutive_errors = 0
            content, name, words = data
            self._merge_chapter(chapters, book.platform, bid, i, cid, name, content, words, True)
            if content:
                fetched += 1
            self._sleep()

        is_exhausted = hit_vip_or_completed or (consecutive_errors == 0 and fetched > 0)
        return self._result(chapters, fetched, len(catalog), exhausted=is_exhausted)

    def _xiaoxiang_catalog(self, bid: str) -> List[Dict[str, Any]]:
        try:
            html = self.xiaoxiang.fetch_url(
                f"https://www.xxsy.net/chapterlist/{bid}", is_mobile=False, encoding="utf-8"
            )
            if not html or self.xiaoxiang.is_waf_challenge(html):
                print(f"[ContentCrawler] [风控拦截] 潇湘目录命中 WAF 挑战或内容为空: bid={bid}")
                return []
            m = re.search(
                r'<script id="vite-plugin-ssr_pageContext" type="application/json">(.*?)</script>',
                html, re.S,
            )
            if not m:
                return []
            pd = json.loads(m.group(1))["pageContext"]["pageProps"]["pageData"]
            result: List[Dict[str, Any]] = []
            for ch in pd.get("chapters", []) or []:
                ccid = str(ch.get("ccid") or "")
                title = ch.get("title", "")
                if ccid in ("", "0") or not self._is_story_chapter(title):
                    continue
                result.append({
                    "ccid": ccid,
                    "title": title,
                    "seq": ch.get("seq"),
                    "price": ch.get("price"),
                })
            return result
        except Exception as e:
            print(f"[ContentCrawler] 潇湘目录失败 {bid}: {e}")
            return []

    def _xiaoxiang_chapter(self, bid: str, cid: str):
        if not cid:
            return None
        try:
            html = self.xiaoxiang.fetch_url(
                f"https://www.xxsy.net/chapter/{bid}/{cid}", is_mobile=False, encoding="utf-8"
            )
            if not html or self.xiaoxiang.is_waf_challenge(html):
                print(f"[ContentCrawler] [风控拦截] 潇湘章节命中 WAF 挑战: bid={bid}, cid={cid}")
                return None
            m = re.search(
                r'<script id="vite-plugin-ssr_pageContext" type="application/json">(.*?)</script>',
                html, re.S,
            )
            if not m:
                return None
            pd = json.loads(m.group(1))["pageContext"]["pageProps"]["pageData"]
            cc = (pd.get("chapterContents") or [{}])[0]
            try:
                price = float(cc.get("chapterPrice") or 0)
            except (TypeError, ValueError):
                price = 1.0
            if price > 0:
                return None  # VIP/付费章节，跳过

            # 校验是否被阅文 Fock 动态字体加密 (font == True 或 encrypt == True)
            if cc.get("encrypt") is True or cc.get("font") is True:
                print(f"[ContentCrawler] [字体加密] 潇湘章节已被动态字体混淆，跳过: bid={bid}, cid={cid}")
                return None

            content = _clean_text(cc.get("chapterContent") or "")
            title = _compact(cc.get("chapterTitle", ""))
            try:
                words = int(cc.get("wordCount") or 0)
            except (TypeError, ValueError):
                words = 0
            if len(content) < 300:
                return None
            return content, title, words
        except Exception:
            return None

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _largest_text_from_html(html: str) -> str:
        blocks = re.findall(r"<div[^>]*>(.*?)</div>", html, re.S)
        if not blocks:
            return ""
        best = max(blocks, key=lambda b: len(_clean_text(b)))
        return _clean_text(best)

    @staticmethod
    def _merge_chapter(
        chapters: List[NovelChapter], platform: str, book_id: str, index: int,
        chapter_id: str, title: str, content: str, words: int, free: bool,
    ) -> None:
        """把抓到的正文合并进目录条目（按 chapter_id 或序号匹配）"""
        target = None
        for ch in chapters:
            if chapter_id and ch.chapter_id == chapter_id:
                target = ch
                break
        if target is None:
            target = NovelChapter(
                platform=platform, book_id=book_id, chapter_index=index,
                chapter_id=chapter_id, title=title, is_free=free,
                url="",
            )
            chapters.append(target)
        if title and not target.title:
            target.title = title
        if content:
            target.content = content
        if words and not target.word_count:
            target.word_count = words
        target.is_free = free
