"""
sync_novel_trends.py
一键抓取四大网络文学平台（起点、纵横、晋江、潇湘）真实榜单，并增量执行 AI 题材风向标分析
支持专项深度抓取：
  - 起点中文网：月票榜、潜力榜、签约新书榜、未签约新书榜 (各 TOP 50)
  - 纵横中文网：月票榜、新书榜、新书订阅榜 (各 TOP 50)
  - 晋江文学城：月度排行榜、新晋作者榜、完结金榜 (各 TOP 50)
  - 潇湘书院：潇湘票榜、畅销榜、完结榜、阅读榜、收藏榜 (各 TOP 20)
"""
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


def main():
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

    # 3. 增量 AI 分析：已存在情节的书籍严格跳过，零重复消耗
    analyzed_count = ai_analyzer.analyze_books(new_books)

    # 3. 输出大盘风向看板
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
