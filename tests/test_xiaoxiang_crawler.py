"""
tests/test_xiaoxiang_crawler.py
验证潇湘书院四大高频快节奏榜单（阅读榜、畅销榜、潇湘票榜、收藏榜）爬取逻辑（已剔除完结榜）
"""
import pytest
from novel_trends.crawlers.xiaoxiang_crawler import XiaoxiangCrawler


def test_xiaoxiang_crawler_boards_configured():
    crawler = XiaoxiangCrawler()
    board_names = [name for name, idx in crawler.BOARDS]
    assert "阅读榜" in board_names
    assert "畅销榜" in board_names
    assert "潇湘票榜" in board_names
    assert "收藏榜" in board_names
    assert "完结榜" not in board_names
    assert len(crawler.BOARDS) == 4


def test_xiaoxiang_crawler_fetch_boards_live():
    crawler = XiaoxiangCrawler()
    
    # 抽测【潇湘票榜】
    yp_books = crawler.fetch_board("潇湘票榜", section_idx=0, max_count=10)
    assert len(yp_books) >= 5
    first = yp_books[0]
    assert first.platform == "潇湘书院"
    assert first.channel == "女频"
    assert first.rank_name == "潇湘票榜"
    assert first.title
    assert first.author
    assert first.category
    assert first.heat_score >= 80.0

    # 抽测【畅销榜】
    sales_books = crawler.fetch_board("畅销榜", section_idx=2, max_count=10)
    assert len(sales_books) >= 5
    assert sales_books[0].rank_name == "畅销榜"
    assert sales_books[0].platform == "潇湘书院"


def test_xiaoxiang_crawler_fetch_all_and_top_books():
    crawler = XiaoxiangCrawler()
    # 抽测全榜单小批量聚合
    all_books = crawler.fetch_all_boards(max_per_board=5)
    assert len(all_books) >= 20

    # 抽测兼容接口
    top_books = crawler.fetch_top_books(max_count=15)
    assert len(top_books) == 15
