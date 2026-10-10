"""
tests/test_qidian_crawler.py
验证起点中文网四大榜单（月票榜、潜力榜、签约新书榜、未签约新书榜）爬取逻辑
"""
import pytest
from novel_trends.crawlers.qidian_crawler import QidianCrawler


def test_qidian_crawler_boards_configured():
    crawler = QidianCrawler()
    board_names = [name for name, slug in crawler.BOARDS]
    assert "月票榜" in board_names
    assert "潜力榜" in board_names
    assert "签约新书榜" in board_names
    assert "未签约新书榜" in board_names
    assert len(crawler.BOARDS) == 4


def test_qidian_crawler_parse_records():
    crawler = QidianCrawler()
    sample_html = """
    <html>
    <script id="vite-plugin-ssr_pageContext">
    {"pageContext": {"pageProps": {"pageData": {"records": [
        {"bid": "1001", "bName": "测试修仙传", "bAuth": "修真作者", "cat": "仙侠", "desc": "主角穿越修真界"},
        {"bid": "1002", "bName": "测试都市神豪", "bAuth": "都市作者", "cat": "都市", "desc": "开局绑定万亿神豪系统"}
    ]}}}}
    </script>
    </html>
    """
    records = crawler._parse_page_context_records(sample_html)
    assert len(records) == 2
    assert records[0]["title"] == "测试修仙传"
    assert records[0]["author"] == "修真作者"
    assert records[0]["category"] == "仙侠"
    assert records[1]["title"] == "测试都市神豪"


def test_qidian_crawler_fetch_board_live():
    crawler = QidianCrawler()
    # 抽测月票榜前 5 部，验证真实网络请求与数据清洗
    books = crawler.fetch_board("月票榜", "yuepiao", max_count=5)
    assert len(books) >= 5
    first = books[0]
    assert first.platform == "起点中文网"
    assert first.rank_name == "月票榜"
    assert first.rank_position == 1
    assert first.title
    assert first.author
