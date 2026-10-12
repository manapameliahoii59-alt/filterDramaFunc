"""
tests/test_jinjiang_crawler.py
验证晋江文学城三大高频快节奏榜单（新晋作者榜、月度排行榜、读者栽培榜）爬取逻辑（已剔除完结榜）
"""
import pytest
from novel_trends.crawlers.jinjiang_crawler import JinjiangCrawler


def test_jinjiang_crawler_boards_configured():
    crawler = JinjiangCrawler()
    board_names = [name for name, path, cat in crawler.BOARDS]
    assert "新晋作者榜" in board_names
    assert "月度排行榜" in board_names
    assert "读者栽培榜" in board_names
    assert "完结金榜" not in board_names
    assert len(crawler.BOARDS) == 3


def test_jinjiang_crawler_fetch_board_live():
    crawler = JinjiangCrawler()
    # 抽测新晋作者榜前 10 部
    new_books = crawler.fetch_board("新晋作者榜", "/rank/naturalmore/29", "女频新晋", max_count=10)
    assert len(new_books) >= 5
    assert new_books[0].platform == "晋江文学城"
    assert new_books[0].rank_name == "新晋作者榜"
    assert new_books[0].title
    assert new_books[0].author
    assert new_books[0].category

    # 抽测读者栽培榜前 10 部
    cultivate_books = crawler.fetch_board("读者栽培榜", "/rank/naturalmore/44", "女频付费追更", max_count=10)
    assert len(cultivate_books) >= 5
    assert cultivate_books[0].rank_name == "读者栽培榜"
    assert cultivate_books[0].title
