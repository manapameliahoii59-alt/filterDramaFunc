# -*- coding: utf-8 -*-
"""
batch_ingest_dna.py
基于已抓取的四大平台真实榜单 (crawled_ranking_books)，
调用阿里云百炼 Qwen3.8-Flash 大模型，批量提取 6 维结构化爆款 DNA，
并实时入库至 PostgreSQL 数据表 novel_dna_records。
"""
import os
import sys
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List

# 确保导入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from novel_trends.pg_storage import PgNovelStorage
from novel_trends.novel_dna_extractor import NovelDnaExtractor, MODEL_NAME


def sync_existing_cache_to_pg(storage: PgNovelStorage, extractor: NovelDnaExtractor) -> int:
    """将本地 novel_dna_kb.json 中已有的分析结果先行入库 PostgreSQL"""
    synced = 0
    for key, profile_dict in extractor.dna_cache.items():
        title = profile_dict.get("title", "")
        author = profile_dict.get("author", "")
        if not title:
            continue
        ok = storage.upsert_novel_dna(
            profile=profile_dict,
            source_platform="大盘精选",
            rank_name="月票榜",
            rank_position=1,
            board_type="MONTHLY_HOT"
        )
        if ok:
            synced += 1
    return synced


def get_pending_books(
    storage: PgNovelStorage,
    top_n_per_board: int = 5,
    fetch_all: bool = False,
    limit: int = 0
) -> List[Dict[str, Any]]:
    """
    从 PostgreSQL crawled_ranking_books 获取待分析入库的书籍：
    每个榜单优先提取 TOP N 部作品，或全量提取，且排除 novel_dna_records 中已存在的作品
    """
    if fetch_all:
        sql = """
        SELECT DISTINCT ON (c.title, c.author) 
            c.title, c.author, c.platform, c.rank_name, c.rank_position, c.channel
        FROM crawled_ranking_books c
        LEFT JOIN novel_dna_records d ON d.book_key = (c.title || '_' || c.author)
        WHERE c.author != '未知' AND c.author != '' AND d.id IS NULL
        ORDER BY c.title, c.author, c.rank_position ASC
        """
        if limit > 0:
            sql += f" LIMIT {limit};"
        else:
            sql += ";"
    else:
        sql = f"""
        WITH ranked_books AS (
            SELECT 
                c.title, c.author, c.platform, c.rank_name, c.rank_position, c.channel,
                ROW_NUMBER() OVER (PARTITION BY c.platform, c.rank_name ORDER BY c.rank_position ASC) as rn
            FROM crawled_ranking_books c
            WHERE c.author != '未知' AND c.author != ''
        )
        SELECT r.title, r.author, r.platform, r.rank_name, r.rank_position, r.channel
        FROM ranked_books r
        LEFT JOIN novel_dna_records d ON d.book_key = (r.title || '_' || r.author)
        WHERE r.rn <= {top_n_per_board} AND d.id IS NULL
        ORDER BY r.platform, r.rank_name, r.rank_position
        """
        if limit > 0:
            sql += f" LIMIT {limit};"
        else:
            sql += ";"

    pending = []
    with storage.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            for row in rows:
                pending.append({
                    "title": row[0],
                    "author": row[1],
                    "platform": row[2],
                    "rank_name": row[3],
                    "rank_position": row[4],
                    "channel": row[5]
                })
    return pending


def process_single_book(book: Dict[str, Any], extractor: NovelDnaExtractor, storage: PgNovelStorage) -> bool:
    """提取单本书籍 DNA 并存入 PostgreSQL"""
    title = book["title"]
    author = book["author"]
    platform = book["platform"]
    rank_name = book["rank_name"]
    rank_pos = book["rank_position"]
    b_type = "NEW_TREND" if any(k in rank_name for k in ["新", "潜力"]) else "MONTHLY_HOT"

    t0 = time.time()
    profile = extractor.get_novel_dna(title=title, author=author, force_refresh=False)
    cost = time.time() - t0

    if not profile:
        print(f"  ❌ 《{title}》（{author}）AI 提取失败，耗时 {cost:.2f}s")
        return False

    prof_dict = profile if isinstance(profile, dict) else (profile.model_dump() if hasattr(profile, "model_dump") else profile.dict())
    success = storage.upsert_novel_dna(
        profile=prof_dict,
        source_platform=platform,
        rank_name=rank_name,
        rank_position=rank_pos,
        board_type=b_type
    )
    if success:
        track = prof_dict.get("track_dna") or prof_dict.get("track_and_audience", {})
        sub = track.get("core_sub_genre") or track.get("sub_category", "")
        l1 = prof_dict.get("hierarchical_tropes", {}).get("l1_macro_tropes", [])[:3]
        print(f"  ✅ [入库成功] 《{title}》（{author}）| 平台: {platform} - {rank_name} 第{rank_pos}名 | 耗时: {cost:.1f}s | 赛道: {sub} | 母题: {l1}")
        return True
    else:
        print(f"  ❌ 《{title}》写入 PostgreSQL 失败")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Qwen3.8-Flash 网文 DNA 批量提取与 PostgreSQL 实时入库引擎")
    parser.add_argument("--top-n", type=int, default=3, help="各榜单提取前 N 名头部书籍 (默认 3)")
    parser.add_argument("--all", action="store_true", help="全量处理所有尚未分析的榜单书籍 (忽略 top-n)")
    parser.add_argument("--limit", type=int, default=0, help="限制本次最大提取总数 (0 为不限制)")
    parser.add_argument("--workers", type=int, default=3, help="并发处理线程数 (默认 3)")
    args = parser.parse_args()

    print("=" * 72)
    print("  Qwen3.8-Flash 大盘网文 DNA 批量提取与 PostgreSQL 实时入库引擎")
    print(f"  AI 驱动模型: {MODEL_NAME}")
    print(f"  提取模式: {'全量剩余作品' if args.all else f'各榜单 TOP {args.top_n}'} | 并发线程: {args.workers}")
    print("=" * 72)

    storage = PgNovelStorage()
    extractor = NovelDnaExtractor()

    # 1. 确保表结构与字段注释就绪
    storage.init_database()

    # 2. 将之前测试已提取的缓存记录同步入库
    cached_synced = sync_existing_cache_to_pg(storage, extractor)
    if cached_synced > 0:
        print(f"\n[本地缓存对齐] 成功将本地 {cached_synced} 部已有基因数据同步入库 PostgreSQL (novel_dna_records)！")

    # 3. 查询待入库的核心榜单作品
    pending = get_pending_books(
        storage,
        top_n_per_board=args.top_n,
        fetch_all=args.all,
        limit=args.limit
    )
    print(f"\n[待分析队列] 筛选出 {len(pending)} 部待提取书籍执行 AI 基因提取...")

    if not pending:
        print("[提示] 所有目标榜单头部作品已全部入库！")
        count = storage.get_count()
        print(f"PostgreSQL novel_dna_records 当前总计: {count} 部作品。")
        return

    # 4. 多线程并发调度提取与入库
    success_count = 0
    fail_count = 0
    start_time = time.time()

    print(f"\n[启动流水线] 正在通过 Qwen3.8-Flash 进行批量提取并实时写入 PostgreSQL...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_map = {
            pool.submit(process_single_book, b, extractor, storage): b
            for b in pending
        }
        for idx, future in enumerate(as_completed(future_map), 1):
            b = future_map[future]
            try:
                ok = future.result()
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
                if idx % 10 == 0 or idx == len(pending):
                    pct = idx * 100 // len(pending)
                    print(f"--> [全量进度] 已处理 {idx}/{len(pending)} 部 ({pct}%) | 成功: {success_count} | 失败: {fail_count}")
            except Exception as e:
                print(f"  ❌ 处理《{b['title']}》发生异常: {e}")
                fail_count += 1

    total_time = time.time() - start_time
    total_in_db = storage.get_count()

    print("\n" + "=" * 72)
    print(f"  批量入库执行完毕！")
    print(f"  本次成功入库: {success_count} 部 | 失败: {fail_count} 部 | 总耗时: {total_time:.2f}s")
    print(f"  PostgreSQL (novel_dna_records) 当前累计入库总数: {total_in_db} 部")
    print("=" * 72)


if __name__ == "__main__":
    main()
