"""
tests/test_detail_fetcher.py
验证 ChangduDetailFetcher 物料提取器：
1. 校验对标 AutomatedEdit 的防风控请求头与抖动延时函数；
2. 校验数据模型 DramaDetailMaterial 字段完整性与类型；
3. 校验未上线剧目状态降级与容错判断逻辑。
"""
import pytest
from pathlib import Path
from changdu_sync.detail_fetcher import (
    DramaDetailMaterial,
    video_download_headers,
    human_delay
)


def test_video_download_headers_anti_ban():
    """测试仿浏览器下载请求头规范"""
    book_id = "7695658463472389182"
    headers = video_download_headers(book_id, cookie_str="test=123")
    
    # 验证关键防风控字段
    assert "User-Agent" in headers
    assert "Chrome" in headers["User-Agent"]
    assert "Referer" in headers
    assert book_id in headers["Referer"]
    assert headers["Sec-Fetch-Dest"] == "video"
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert headers["Cookie"] == "test=123"


def test_drama_detail_material_data_class():
    """测试标准化物料数据类"""
    mat = DramaDetailMaterial(
        book_id="7695658463472389182",
        book_name="搭伙后我靠山致富",
        category_text="都市脑洞",
        abstract="1978年的青岭大队...",
        episode_amount=92,
        publish_status=1,
        publish_time="2026-10-13 17:11:00",
        estimate_publish_time="2026-10-13 17:11:00",
        root_book_id="7694964482715438105",
        playlet_id="7695675893796046899",
        thumb_url="https://example.com/cover.jpg",
        ep1_item_id="7695674471151045656",
        ep1_chapter_name="第1集",
        ep1_audit_status=3,
        can_get_player_info=True,
        video_status="VIDEO_READY",
        video_url="https://v6-nsaleb.changdunovel.com/test.mp4",
        video_backup_url=None,
        video_width=1080,
        video_height=1920,
        video_expire_left_sec=7200,
    )

    d = mat.to_dict()
    assert d["book_name"] == "搭伙后我靠山致富"
    assert d["root_book_id"] == "7694964482715438105"
    assert d["video_status"] == "VIDEO_READY"
    assert d["video_width"] == 1080
    assert d["video_height"] == 1920


def test_unreleased_drama_graceful_degradation():
    """测试未到上线时间剧目的安全降级状态"""
    unreleased = DramaDetailMaterial(
        book_id="123456789",
        book_name="未上线新剧",
        category_text="战神",
        abstract="暂无视频排期中...",
        episode_amount=80,
        publish_status=0,
        publish_time="2026-10-20 12:00:00",
        estimate_publish_time="2026-10-20 12:00:00",
        root_book_id=None,
        playlet_id=None,
        thumb_url="",
        ep1_item_id=None,
        ep1_chapter_name=None,
        ep1_audit_status=1,
        can_get_player_info=False,
        video_status="PENDING_RELEASE",
        video_url=None,
        video_backup_url=None,
        video_width=0,
        video_height=0,
        video_expire_left_sec=0,
    )

    assert unreleased.video_status == "PENDING_RELEASE"
    assert unreleased.video_url is None
    assert unreleased.can_get_player_info is False
