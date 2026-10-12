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
    assert count >= 5, f"预期至少 5 部标杆网文，实际: {count}"


def test_pg_storage_gin_inverted_index_search():
    storage = PgNovelStorage()
    # 检索女频爆款母题
    results = storage.search_by_tropes(["#真假千金", "#全能马甲"], channel="女频")
    assert len(results) >= 1
    hit_titles = [r["title"] for r in results]
    assert "真千金她是全能大佬" in hit_titles
    first = results[0]
    assert first["lifecycle_stage"] == "EXPLODING"
    assert first["heat_score"] >= 80.0


def test_pg_storage_get_all_profiles():
    storage = PgNovelStorage()
    profiles = storage.get_all_profiles()
    assert len(profiles) >= 5
    for p in profiles:
        assert isinstance(p, NovelDnaProfile)
        assert p.title
        assert p.author
        assert "gender_track" in p.track_and_audience
        assert "l1_macro_tropes" in p.hierarchical_tropes
