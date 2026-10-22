# -*- coding: utf-8 -*-
"""
crawl_missing_chapters.py
扫描 PostgreSQL 中「还没有章节记录」的书籍，按批次补抓章节标题、免费正文与简介。

用法：
    python crawl_missing_chapters.py --limit 20
    python crawl_missing_chapters.py --limit 0 --chapters 5 --delay 1.2

说明：
- 「缺失」判定：crawled_ranking_books 里存在，但 novel_chapters 里没有任何记录。
- 抓过但正文为空（例如 VIP 锁定）的书也会写入标题行，因此不会每次重复抓取。
- 每批数量由 --limit 控制，0 表示本次处理全部缺失书籍（谨慎使用，容易被限流）。
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from novel_trends.models import NovelBook
from novel_trends.pg_storage import PgNovelStorage
from novel_trends.content_crawler import ContentCrawler
from novel_trends.config import DEFAULT_CHAPTER_COUNT, DEFAULT_TITLE_COUNT


def count_missing(pg: PgNovelStorage, target: int = DEFAULT_CHAPTER_COUNT) -> int:
    return pg.count_missing_chapters(target)


def count_missing_by_platform(pg: PgNovelStorage, target: int = DEFAULT_CHAPTER_COUNT) -> dict[str, int]:
    return pg.count_missing_chapters_by_platform(target)


def fetch_missing_books(pg: PgNovelStorage, limit: int, target: int = DEFAULT_CHAPTER_COUNT) -> list[NovelBook]:
    sql = """
    WITH books AS (
        SELECT DISTINCT ON (c.platform, c.book_id)
            c.platform, c.book_id, c.title, c.author, c.channel,
            c.category, c.intro, c.book_url, c.rank_position
        FROM crawled_ranking_books c
        WHERE COALESCE(c.book_id, '') <> ''
        ORDER BY c.platform, c.book_id, c.rank_position ASC
    ),
    agg AS (
        SELECT platform, book_id,
               COUNT(*) FILTER (WHERE content <> '') AS content_chapters
        FROM novel_chapters
        GROUP BY platform, book_id
    ),
    need AS (
        SELECT b.*
        FROM books b
        LEFT JOIN agg a ON a.platform = b.platform AND a.book_id = b.book_id
        LEFT JOIN novel_content_status s ON s.platform = b.platform AND s.book_id = b.book_id
        WHERE COALESCE(a.content_chapters, 0) < %s
          AND COALESCE(s.exhausted, FALSE) = FALSE
    )
    SELECT platform, book_id, title, author, channel,
           category, intro, book_url, rank_position
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY platform ORDER BY rank_position ASC) AS rn
        FROM need
    ) t
    WHERE %s = 0 OR rn <= %s
    ORDER BY platform, rank_position ASC;
    """

    books: list[NovelBook] = []
    try:
        with pg.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (int(target), int(limit), int(limit)))
                for row in cur.fetchall():
                    books.append(NovelBook(
                        platform=row[0],
                        book_id=str(row[1]),
                        title=row[2] or "",
                        author=row[3] or "",
                        channel=row[4] or "男频",
                        rank_name="补抓",
                        rank_position=int(row[8] or 1),
                        category=row[5] or "",
                        intro=row[6] or "",
                        book_url=row[7] or "",
                    ))
    except Exception as e:
        print(f"[补抓] 查询待补抓书籍失败: {e}")
    return books


def main() -> int:
    parser = argparse.ArgumentParser(description="补抓数据库中缺失章节内容的书籍")
    parser.add_argument("--limit", type=int, default=20,
                        help="每个网站本批最多抓取多少本书 (默认 20；0 表示全部)")
    parser.add_argument("--titles", type=int, default=DEFAULT_TITLE_COUNT,
                        help=f"每本抓取的章节标题数量 (默认 {DEFAULT_TITLE_COUNT}，连载不足有多少抓多少)")
    parser.add_argument("--chapters", type=int, default=DEFAULT_CHAPTER_COUNT,
                        help=f"每本抓取的免费正文章节数量 (默认 {DEFAULT_CHAPTER_COUNT})")
    parser.add_argument("--delay", type=float, default=0.8,
                        help="请求间基础间隔秒数（叠加随机抖动，默认 0.8）")
    parser.add_argument("--no-playwright", action="store_true", help="禁用 Playwright")
    parser.add_argument("--dry-run", action="store_true", help="只列出待补抓数量，不实际抓取")
    args = parser.parse_args()

    pg = PgNovelStorage()
    print("=" * 68)
    print("  缺失章节内容补抓")
    print("=" * 68)

    pg.init_content_tables()
    total = count_missing(pg, args.chapters)
    by_platform = count_missing_by_platform(pg, args.chapters)
    breakdown = "、".join(f"{p} {n} 本" for p, n in by_platform.items()) or "无"
    print(f"当前缺失章节内容的书籍总数: {total}（{breakdown}）")

    if total == 0:
        print("没有需要补抓的书籍。")
        return 0

    batch_desc = "每站全部" if not args.limit or args.limit <= 0 else f"每站 {args.limit} 本"
    print(f"本批计划抓取: {batch_desc}（标题 {args.titles} 章 / 正文 {args.chapters} 章）")

    books = fetch_missing_books(pg, args.limit, args.chapters)
    if not books:
        print("未查询到待抓书籍。")
        return 0

    if args.dry_run:
        for i, b in enumerate(books, 1):
            print(f"  {i}. {b.platform} 《{b.title}》 id={b.book_id}")
        print(f"[dry-run] 共 {len(books)} 本，未实际抓取。")
        return 0

    crawler = ContentCrawler(
        max_titles=args.titles,
        max_chapters=args.chapters,
        use_playwright=not args.no_playwright,
        request_delay=args.delay,
    )
    try:
        stats = crawler.crawl_and_save(books, pg)
    finally:
        crawler.close()

    print("\n[补抓完成] "
          f"书籍 {stats['books']} 本 | 章节 {stats['chapters']} 条 | "
          f"含正文 {stats['contents']} 条 | 简介 {stats['intros']} 条")
    print(f"[PostgreSQL] novel_chapters 当前 {pg.get_chapter_count()} 条 | "
          f"novel_intros 当前 {pg.get_intro_count()} 条")
    print(f"剩余需要补抓: {count_missing(pg, args.chapters)} 本（可再次运行继续补抓）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
