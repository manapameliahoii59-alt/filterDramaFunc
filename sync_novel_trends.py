"""
sync_novel_trends.py
一键抓取四大网络文学平台（起点、纵横、晋江、潇湘）真实榜单，并增量执行 AI 题材风向标分析
支持专项深度抓取：
  - 起点中文网：月票榜、潜力榜、签约新书榜、未签约新书榜 (各 TOP 50)
  - 纵横中文网：月票榜、新书榜、新书订阅榜 (各 TOP 50)
  - 晋江文学城：月度排行榜、新晋作者榜、完结金榜 (各 TOP 50)
  - 潇湘书院：潇湘票榜、畅销榜、完结榜、阅读榜、收藏榜 (各 TOP 20)
"""
import argparse
import sys
import os

# 确保导入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 修复 Windows 控制台 GBK 编码输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from novel_trends import NovelCacheManager, NovelSyncService, NovelAIAnalyzer, PgNovelStorage
from novel_trends.config import DEFAULT_CHAPTER_COUNT, DEFAULT_TITLE_COUNT


def main():
    parser = argparse.ArgumentParser(description="小说榜单同步 + 章节正文采集 (+ 可选 AI 风向标分析)")
    parser.add_argument("--with-content", action="store_true",
                        help="同步榜单后抓取前 N 章标题与免费正文并入库")
    parser.add_argument("--skip-ai", action="store_true",
                        help="不调用大模型做 DNA/风向标分析，只同步榜单与抓内容")
    parser.add_argument("--titles", type=int, default=DEFAULT_TITLE_COUNT,
                        help=f"每本抓取的章节标题数量 (默认 {DEFAULT_TITLE_COUNT})")
    parser.add_argument("--chapters", type=int, default=DEFAULT_CHAPTER_COUNT,
                        help=f"每本抓取的免费正文章节数量 (默认 {DEFAULT_CHAPTER_COUNT})")
    parser.add_argument("--content-limit", type=int, default=0,
                        help="本次最多抓取多少本书的章节/正文 (0 表示全部上榜书)")
    parser.add_argument("--no-playwright", action="store_true",
                        help="禁用 Playwright（仅用静态请求，部分站点目录可能抓不到）")
    parser.add_argument("--delay", type=float, default=0.6,
                        help="内容采集请求间基础间隔秒数（会叠加随机抖动，默认 0.6）")
    args = parser.parse_args()

    print("=" * 68)
    print("  短剧/漫剧大盘题材风向标同步引擎 (真实小说平台直连)")
    print("  覆盖平台: 起点中文网 (月票榜/潜力榜/签约新书榜/未签约新书榜 各 TOP 50)")
    print("            纵横中文网 (月票榜/新书榜/新书订阅榜 各 TOP 50)")
    print("            晋江文学城 (新晋作者榜/月度排行榜/读者栽培榜 各 TOP 50)")
    print("            潇湘书院   (近7天阅读榜/30天畅销榜/潇湘票榜/收藏榜 各 TOP 20)")
    print("=" * 68)

    cache = NovelCacheManager()
    sync_service = NovelSyncService(cache)
    pg_storage = PgNovelStorage()
    ai_analyzer = NovelAIAnalyzer(cache, pg_storage=pg_storage)

    # 1. 抓取最新榜单作品 (四大主流平台 14 大核心快节奏榜单)
    total_fetched, new_count, new_books = sync_service.sync_all_platforms(
        limit_per_platform=15,
        qidian_top_per_board=50,
        zongheng_top_per_board=50,
        jinjiang_top_per_board=50,
        xiaoxiang_top_per_board=20
    )

    # 2. 将抓取到的全量真实榜单数据同步存入 PostgreSQL 远程数据库
    all_crawled_books = sync_service.all_crawled_books if sync_service.all_crawled_books else list(cache.kb.books.values())
    saved_pg = pg_storage.save_crawled_books(all_crawled_books)
    print(f"\n[PostgreSQL 实时入库] 已将 {saved_pg} 部四大平台榜单作品同步入库至远程 PostgreSQL (crawled_ranking_books)！")

    # 3. 增量 AI 分析（可用 --skip-ai 跳过，只抓内容时不做）
    if args.skip_ai:
        print("\n[跳过 AI] 已指定 --skip-ai，不调用大模型做 DNA/风向标分析。")
    else:
        analyzed_count = ai_analyzer.analyze_books(new_books)

    # 4. 可选：抓取章节标题/免费正文并入库
    if args.with_content:
        from novel_trends.content_crawler import ContentCrawler

        # 同一本书可能同时出现在多个榜单，按 (平台, book_id) 去重，避免重复抓取
        seen_keys = set()
        unique_books = []
        for b in (all_crawled_books or list(cache.kb.books.values())):
            key = (b.platform, b.book_id or b.title)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_books.append(b)

        books_for_content = unique_books
        if args.content_limit and args.content_limit > 0:
            # 每个平台各取 content_limit 本，而不是从总列表里截前 N 本
            by_platform = {}
            for b in unique_books:
                by_platform.setdefault(b.platform, []).append(b)
            books_for_content = []
            for platform_books in by_platform.values():
                books_for_content.extend(platform_books[: args.content_limit])

        if books_for_content:
            counts = {}
            for b in books_for_content:
                counts[b.platform] = counts.get(b.platform, 0) + 1
            breakdown = "、".join(f"{p} {n} 本" for p, n in counts.items())
        else:
            breakdown = "无"

        print("\n" + "=" * 68)
        print(f"  [内容采集] 准备抓取 {len(books_for_content)} 本书（{breakdown}）："
              f"标题 {args.titles} 章 / 正文 {args.chapters} 章 / 简介")
        print(f"  仅抓取免费章节；Playwright: {'关闭' if args.no_playwright else '开启'}")
        print("=" * 68)

        pg_storage.init_content_tables()
        content_crawler = ContentCrawler(
            max_titles=args.titles,
            max_chapters=args.chapters,
            use_playwright=not args.no_playwright,
            request_delay=args.delay,
        )
        try:
            stats = content_crawler.crawl_and_save(books_for_content, pg_storage)
        finally:
            content_crawler.close()

        print("\n[内容采集完成] "
              f"书籍 {stats['books']} 本 | 章节 {stats['chapters']} 条 | "
              f"含正文 {stats['contents']} 条 | 简介 {stats['intros']} 条")
        print(f"[PostgreSQL] novel_chapters 当前 {pg_storage.get_chapter_count()} 条 | "
              f"novel_intros 当前 {pg_storage.get_intro_count()} 条")

    # 5. 输出大盘风向看板
    kb = cache.kb
    print("\n" + "=" * 68)
    print("  当前全网大盘近期畅销爆款题材母题图谱 (AI 提炼总结)")
    print("=" * 68)

    if kb.male_summary and kb.male_summary.hot_patterns:
        print("\n【男频核心风向标 (起点 / 纵横)】:")
        for p in kb.male_summary.hot_patterns[:5]:
            ref = f"代表作: 《{'》, 《'.join(p.representative_books[:3])}》" if p.representative_books else ""
            print(f"  [热门题材] [{p.pattern_name}] (加权分: +{p.market_bonus_weight}分)")
            print(f"     爽点机制: {p.description}")
            if ref:
                print(f"     {ref}")

    if kb.female_summary and kb.female_summary.hot_patterns:
        print("\n【女频核心风向标 (晋江 / 潇湘)】:")
        for p in kb.female_summary.hot_patterns[:5]:
            ref = f"代表作: 《{'》, 《'.join(p.representative_books[:3])}》" if p.representative_books else ""
            print(f"  [热门题材] [{p.pattern_name}] (加权分: +{p.market_bonus_weight}分)")
            print(f"     爽点机制: {p.description}")
            if ref:
                print(f"     {ref}")

    print("\n" + "-" * 68)
    print(f"  知识库已安全持久化: novel_trends/data/novel_knowledge_base.json")
    print(f"  已入库真实书籍总数: {len(kb.books)} 部 (下次同步新书将自动增量识别)")
    print("=" * 68)


if __name__ == "__main__":
    main()
