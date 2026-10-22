# -*- coding: utf-8 -*-
"""
tests/test_pg_storage.py
验证 PostgreSQL 远程持久化存储、DDL、GIN 倒排索引检索与 Profile 加载
"""
import pytest
from novel_trends.pg_storage import PgNovelStorage
from novel_trends.models import NovelDnaProfile


def test_pg_storage_connection_and_count():
    storage = PgNovelStorage()
    count = storage.get_count()
    assert count >= 0, f"预期为有效非负整数，实际: {count}"


def test_pg_storage_gin_inverted_index_search():
    storage = PgNovelStorage()
    # 检索女频爆款母题（若刚清空数据库则跳过或验证空列表）
    results = storage.search_by_tropes(["#真假千金", "#重生复仇"], channel="女频")
    if storage.get_count() > 0:
        assert len(results) >= 1
        first = results[0]
        assert first["lifecycle_stage"] in ["SUPER_EXPLODING", "PEAK_HARVEST", "SATURATED", "OVERDUE", "DROPPED", "DEPRECATED"]
        assert first["heat_score"] >= 60.0
    else:
        assert isinstance(results, list)


def test_pg_storage_get_all_profiles():
    storage = PgNovelStorage()
    profiles = storage.get_all_profiles()
    if storage.get_count() > 0:
        assert len(profiles) >= 1
        for p in profiles:
            assert isinstance(p, NovelDnaProfile)
            assert p.title
            assert p.author
    else:
        assert isinstance(profiles, list)


def test_pg_storage_upsert_without_risk_level():
    storage = PgNovelStorage()
    test_prof = {
        "title": "测试去风险作品",
        "author": "测试作者",
        "track_and_audience": {"gender_track": "男频", "main_category": "都市", "sub_category": "高武", "target_audience": "青年"},
        "hierarchical_tropes": {"l1_macro_tropes": ["#都市高武"], "l2_micro_elements": ["#一拳开天"]},
        "dna_profile": {"test": 1}
    }
    ok = storage.upsert_novel_dna(test_prof, source_platform="起点中文网", rank_name="测试榜", rank_position=1)
    assert ok is True

    # 清理
    with storage.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM novel_dna_records WHERE book_key = %s;", ("测试去风险作品_测试作者",))
        conn.commit()
