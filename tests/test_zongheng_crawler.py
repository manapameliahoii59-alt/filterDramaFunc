"""
tests/test_zongheng_crawler.py
验证纵横中文网三大核心榜单（月票榜、新书榜、新书订阅榜）爬取逻辑
"""
import pytest
from novel_trends.crawlers.zongheng_crawler import ZonghengCrawler


def test_zongheng_crawler_boards_configured():
    crawler = ZonghengCrawler()
    board_names = [name for name, rtype in crawler.BOARDS]
    assert "月票榜" in board_names
    assert "新书榜" in board_names
    assert "新书订阅榜" in board_names
    assert len(crawler.BOARDS) == 3


def test_zongheng_crawler_fetch_board_live():
    crawler = ZonghengCrawler()
    # 抽测月票榜前 10 部
    yp_books = crawler.fetch_board("月票榜", 1, max_count=10)
    assert len(yp_books) >= 10
    first = yp_books[0]
    assert first.platform == "纵横中文网"
    assert first.rank_name == "月票榜"
    assert first.title
    assert first.author
    assert first.category

    # 抽测新书榜前 10 部
    nb_books = crawler.fetch_board("新书榜", 4, max_count=10)
    assert len(nb_books) >= 10
    assert nb_books[0].rank_name == "新书榜"

    # 抽测新书订阅榜 (当前真实上榜书籍约8部)
    sub_books = crawler.fetch_board("新书订阅榜", 9, max_count=50)
    assert len(sub_books) >= 1
    assert sub_books[0].rank_name == "新书订阅榜"


def test_zongheng_crawler_fetch_catalog_live():
    crawler = ZonghengCrawler()
    # 抽测真实作品《明君》(1610880) 的目录接口直连
    catalog = crawler.fetch_catalog("1610880")
    assert len(catalog) >= 10
    first = catalog[0]
    assert "chapterId" in first
    assert "chapterName" in first
    assert "price" in first

