"""
tests/test_jinjiang_crawler.py
验证晋江文学城三大核心榜单（月度排行榜、新晋作者榜、完结金榜）爬取逻辑
"""
import pytest
from novel_trends.crawlers.jinjiang_crawler import JinjiangCrawler


def test_jinjiang_crawler_boards_configured():
    crawler = JinjiangCrawler()
    board_names = [name for name, path, cat in crawler.BOARDS]
    assert "月度排行榜" in board_names
    assert "新晋作者榜" in board_names
    assert "完结金榜" in board_names
    assert len(crawler.BOARDS) == 3


def test_jinjiang_crawler_fetch_board_live():
    crawler = JinjiangCrawler()
    # 抽测月度排行榜前 10 部
    month_books = crawler.fetch_board("月度排行榜", "/rank/naturalmore/5", "女频言情", max_count=10)
    assert len(month_books) >= 10
    first = month_books[0]
    assert first.platform == "晋江文学城"
    assert first.rank_name == "月度排行榜"
    assert first.title
    assert first.author
    assert first.category

    # 抽测新晋作者榜前 10 部
    new_books = crawler.fetch_board("新晋作者榜", "/rank/naturalmore/29", "女频新晋", max_count=10)
    assert len(new_books) >= 10
    assert new_books[0].rank_name == "新晋作者榜"
