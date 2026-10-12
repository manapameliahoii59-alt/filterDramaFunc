"""
changdu_sync/detail_fetcher.py
常读短剧详情页与第1集物料安全提取器

核心特性（严格对齐 AutomatedEdit 工业级防风控规范）：
1. 真实浏览器上下文内执行 Fetch，抹除自动化特征并复用 secsdk 签名环境；
2. 拟人化动态自适应抖动延时（Human Jitter），阻断固定频率风控拦截；
3. 严格浏览器伪装 Headers（sec-ch-ua、Origin、Referer、Sec-Fetch-*）；
4. 原画视频流流式错峰下载与 Range 切片采样（支持前30秒采样，避免全量下载大文件）；
5. 针对未上线/未到时间剧目的「分级降级容错机制」（Graceful Degradation）。
"""

import os
import sys
import json
import time
import random
import requests
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable

# 终端中文编码兼容
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .client import ChangduSyncClient
from .stealth import (
    HOME_ORIGIN,
    CHROME_USER_AGENT,
    SEC_CH_UA,
    SEC_CH_UA_MOBILE,
    SEC_CH_UA_PLATFORM,
    ACCEPT_LANGUAGE
)

SERIES_DETAIL_PATH = "/novelsale/distributor/content/series/detail/v1/"
EPISODE_INFO_PATH = "/novelsale/distributor/content/episode/info/v1"
PLAYER_INFO_PATH = "/novelsale/distributor/content/player/info/v2/"


@dataclass
class DramaDetailMaterial:
    """标准化短剧物料数据包"""
    book_id: str
    book_name: str
    category_text: str
    abstract: str
    episode_amount: int
    publish_status: int
    publish_time: str
    estimate_publish_time: str
    root_book_id: Optional[str]
    playlet_id: Optional[str]
    thumb_url: str
    # 第 1 集物料
    ep1_item_id: Optional[str]
    ep1_chapter_name: Optional[str]
    ep1_audit_status: int
    can_get_player_info: bool
    # 视频播放流
    video_status: str               # "VIDEO_READY" | "PENDING_RELEASE" | "ERROR"
    video_url: Optional[str]        # 原画视频直链（有效期2小时）
    video_backup_url: Optional[str]
    video_width: int
    video_height: int
    video_expire_left_sec: int
    # 本地缓存片段（若执行了切片采样）
    preview_video_path: Optional[str] = None
    preview_audio_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def human_delay(base_sec: float = 1.2, jitter_min: float = 0.5, jitter_max: float = 2.0):
    """
    自适应拟人抖动延时（对标 AutomatedEdit _human_delay）
    在基准间隔上叠加均匀随机抖动，彻底打破机械节奏
    """
    delay = base_sec + random.uniform(jitter_min, jitter_max)
    time.sleep(max(0.2, delay))


def video_download_headers(book_id: str, cookie_str: Optional[str] = None) -> Dict[str, str]:
    """
    流式拉取视频直链使用的仿浏览器请求头（对标 AutomatedEdit zip_download_headers）
    """
    referer = f"{HOME_ORIGIN}/sale/short-play/list/detail?id={book_id}&contentGenre=2"
    headers = {
        "User-Agent": CHROME_USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": ACCEPT_LANGUAGE,
        "Accept-Encoding": "identity",
        "Referer": referer,
        "Origin": HOME_ORIGIN,
        "Connection": "keep-alive",
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": SEC_CH_UA_MOBILE,
        "sec-ch-ua-platform": SEC_CH_UA_PLATFORM,
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    return headers


class ChangduDetailFetcher:
    """常读平台短剧详情与第1集物料安全提取器"""

    def __init__(self, client: ChangduSyncClient):
        self.client = client
        self.page = client.page
        self.app_info = client.app_info
        self.ad_user_id = client.ad_user_id
        self.root_ad_user_id = client.root_ad_user_id

    def fetch_material(
        self,
        book_id: str,
        *,
        fetch_video: bool = True,
        download_preview: bool = False,
        preview_max_bytes: int = 8 * 1024 * 1024,  # 默认采样前8MB（约前30~45秒高码率视频）
        dest_dir: Optional[Path | str] = None,
    ) -> DramaDetailMaterial:
        """
        全流程安全抓取单部短剧的完整详情物料
        
        执行步骤：
        1. 拟人抖动后请求详情 API (元数据、排期、原作小说ID)
        2. 拟人抖动后请求分集 API (第1集 item_id 与就绪状态)
        3. 状态分支判定：若未就绪或未到时间，优雅降级为 PENDING_RELEASE
        4. 若视频已就绪且 fetch_video=True，拟人抖动后请求播放流 API
        5. 若 download_preview=True，流式拉取前 N 字节切片并持久化
        """
        book_id = str(book_id).strip()
        print(f"\n[物料抓取] 正在通过防风控安全通道提取短剧 ID: {book_id}...")

        # ---------------------------------------------------------
        # Step 1: 抓取详情元数据
        # ---------------------------------------------------------
        human_delay(base_sec=1.0, jitter_min=0.3, jitter_max=1.2)
        raw_detail = self.page.evaluate(
            """async ({ apiPath, params, app, adUserId, rootAdUserId }) => {
                const qs = new URLSearchParams(params).toString();
                const headers = {
                    accept: 'application/json, text/plain, */*',
                    appid: String(app.app_id),
                    apptype: String(app.app_type),
                    distributorid: String(app.distributor_id),
                    aduserid: adUserId,
                    rootaduserid: rootAdUserId,
                    'agw-js-conv': 'str',
                    'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"',
                    'sec-fetch-dest': 'empty',
                    'sec-fetch-mode': 'cors',
                    'sec-fetch-site': 'same-origin',
                };
                const res = await fetch(`${apiPath}?${qs}`, {
                    method: 'GET',
                    credentials: 'include',
                    headers,
                });
                return res.json();
            }""",
            {
                "apiPath": SERIES_DETAIL_PATH,
                "params": {"book_id": book_id, "aweme_user_new_version": "true"},
                "app": self.app_info,
                "adUserId": self.ad_user_id,
                "rootAdUserId": self.root_ad_user_id,
            }
        )

        detail_data = raw_detail.get("data") or {}
        if not detail_data:
            raise RuntimeError(f"获取剧集详情为空或失败: {raw_detail.get('message', '未知错误')}")

        book_name = detail_data.get("book_name") or f"未知剧目_{book_id}"
        category_text = detail_data.get("category_text") or "都市"
        abstract = (detail_data.get("abstract") or "").strip()
        episode_amount = int(detail_data.get("episode_amount") or 0)
        thumb_url = detail_data.get("original_thumb_url") or detail_data.get("thumb_url") or ""

        aweme_data = detail_data.get("aweme_version_book_data") or {}
        publish_status = int(aweme_data.get("publish_status") or 0)
        publish_time = aweme_data.get("publish_time") or ""
        estimate_publish_time = aweme_data.get("estimate_publish_time") or publish_time
        root_book_id = str(aweme_data.get("root_book_id") or "") if aweme_data.get("root_book_id") else None
        playlet_id = str(aweme_data.get("playlet_id") or "") if aweme_data.get("playlet_id") else None

        print(f"  ✓ 详情解析成功: 《{book_name}》[共{episode_amount}集] | 题材: {category_text}")
        if root_book_id:
            print(f"  ✓ 关联原作网文 ID: {root_book_id}")

        # ---------------------------------------------------------
        # Step 2: 抓取分集列表与第 1 集标志
        # ---------------------------------------------------------
        human_delay(base_sec=0.8, jitter_min=0.3, jitter_max=1.0)
        raw_episodes = self.page.evaluate(
            """async ({ apiPath, params, app, adUserId, rootAdUserId }) => {
                const qs = new URLSearchParams(params).toString();
                const headers = {
                    accept: 'application/json, text/plain, */*',
                    appid: String(app.app_id),
                    apptype: String(app.app_type),
                    distributorid: String(app.distributor_id),
                    aduserid: adUserId,
                    rootaduserid: rootAdUserId,
                    'agw-js-conv': 'str',
                };
                const res = await fetch(`${apiPath}?${qs}`, {
                    method: 'GET',
                    credentials: 'include',
                    headers,
                });
                return res.json();
            }""",
            {
                "apiPath": EPISODE_INFO_PATH,
                "params": {
                    "book_id": book_id,
                    "page_index": "0",
                    "page_size": "10",
                    "aweme_user_new_version": "true",
                },
                "app": self.app_info,
                "adUserId": self.ad_user_id,
                "rootAdUserId": self.root_ad_user_id,
            }
        )

        ep_list = (raw_episodes.get("data") or {}).get("data") or []
        ep1_item_id = None
        ep1_chapter_name = None
        ep1_audit_status = 0
        can_get_player_info = False

        if ep_list:
            ep1 = ep_list[0]
            ep1_item_id = str(ep1.get("item_id") or "")
            ep1_chapter_name = ep1.get("chapter_name") or "第1集"
            ep1_audit_status = int(ep1.get("audit_status") or 0)
            can_get_player_info = bool(ep1.get("can_get_player_info"))

        # ---------------------------------------------------------
        # Step 3: 分级容错与播放流决策
        # ---------------------------------------------------------
        video_status = "PENDING_RELEASE"
        video_url = None
        video_backup_url = None
        video_width = 0
        video_height = 0
        video_expire_left_sec = 0

        if not can_get_player_info or not ep1_item_id:
            print(f"  ⚠ [防风控与降级] 该剧视频未上线或未就绪 (上线时间: {estimate_publish_time or '未知'})")
            print("    -> 自动标记为 PENDING_RELEASE，走纯文本元数据初筛通道。")
        elif not fetch_video:
            video_status = "VIDEO_READY"
            print(f"  ✓ 第1集可播放 (item_id: {ep1_item_id})，跳过拉取直链。")
        else:
            # ---------------------------------------------------------
            # Step 4: 抓取第 1 集原画播放流直链
            # ---------------------------------------------------------
            human_delay(base_sec=1.0, jitter_min=0.4, jitter_max=1.5)
            raw_player = self.page.evaluate(
                """async ({ apiPath, params, app, adUserId, rootAdUserId }) => {
                    const qs = new URLSearchParams(params).toString();
                    const headers = {
                        accept: 'application/json, text/plain, */*',
                        appid: String(app.app_id),
                        apptype: String(app.app_type),
                        distributorid: String(app.distributor_id),
                        aduserid: adUserId,
                        rootaduserid: rootAdUserId,
                        'agw-js-conv': 'str',
                    };
                    const res = await fetch(`${apiPath}?${qs}`, {
                        method: 'GET',
                        credentials: 'include',
                        headers,
                    });
                    return res.json();
                }""",
                {
                    "apiPath": PLAYER_INFO_PATH,
                    "params": {"book_id": book_id, "item_id": ep1_item_id},
                    "app": self.app_info,
                    "adUserId": self.ad_user_id,
                    "rootAdUserId": self.root_ad_user_id,
                }
            )

            player_data = raw_player.get("data") or {}
            video_url = player_data.get("main_url")
            video_backup_url = player_data.get("backup_url")
            video_width = int(player_data.get("VideoWidth") or 0)
            video_height = int(player_data.get("VideoHeight") or 0)
            video_expire_left_sec = int(player_data.get("url_expire_left_sec") or 0)

            if video_url:
                video_status = "VIDEO_READY"
                print(f"  ✓ 原画播放流获取成功: {video_width}x{video_height} | Token有效: {video_expire_left_sec}s")
            else:
                video_status = "PENDING_RELEASE"
                print(f"  ⚠ 播放器接口未下发播放直链，安全降级。")

        # ---------------------------------------------------------
        # Step 5: 可选：流式防封下载视频前序切片
        # ---------------------------------------------------------
        preview_video_path = None
        if download_preview and video_url and video_status == "VIDEO_READY":
            save_dir = Path(dest_dir or (Path(__file__).parent.parent / "scratch" / "previews")).resolve()
            save_dir.mkdir(parents=True, exist_ok=True)
            out_file = save_dir / f"{book_id}_ep1_sample.mp4"

            # 错峰防封下载切片
            preview_video_path = self._download_preview_chunk(
                video_url=video_url,
                book_id=book_id,
                dest_file=out_file,
                max_bytes=preview_max_bytes
            )

        material = DramaDetailMaterial(
            book_id=book_id,
            book_name=book_name,
            category_text=category_text,
            abstract=abstract,
            episode_amount=episode_amount,
            publish_status=publish_status,
            publish_time=publish_time,
            estimate_publish_time=estimate_publish_time,
            root_book_id=root_book_id,
            playlet_id=playlet_id,
            thumb_url=thumb_url,
            ep1_item_id=ep1_item_id,
            ep1_chapter_name=ep1_chapter_name,
            ep1_audit_status=ep1_audit_status,
            can_get_player_info=can_get_player_info,
            video_status=video_status,
            video_url=video_url,
            video_backup_url=video_backup_url,
            video_width=video_width,
            video_height=video_height,
            video_expire_left_sec=video_expire_left_sec,
            preview_video_path=str(preview_video_path) if preview_video_path else None,
        )

        return material

    def _download_preview_chunk(
        self,
        video_url: str,
        book_id: str,
        dest_file: Path,
        max_bytes: int = 8 * 1024 * 1024,
        retries: int = 2,
    ) -> Optional[Path]:
        """
        防封流式拉取视频前序字节（对标 AutomatedEdit _download_prepared_with_retry）
        使用 Range 头只取头部，避免全量下载触发 CDN 频控
        """
        headers = video_download_headers(book_id)
        # 请求前 N 字节切片，刚好包含 moov 头与前几十秒音视频
        headers["Range"] = f"bytes=0-{max_bytes - 1}"

        for attempt in range(1, retries + 1):
            try:
                # 错峰抖动，避免瞬时高频打出
                human_delay(0.8, 0.2, 1.0)
                print(f"  -> 正在流式拉取前 {max_bytes // 1024 // 1024}MB 视频切片进行视听检测...")

                resp = requests.get(video_url, headers=headers, stream=True, timeout=(10, 30))
                if resp.status_code in (200, 206):
                    with open(dest_file, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=64 * 1024):
                            if chunk:
                                f.write(chunk)
                    print(f"  ✓ 视频切片下载成功: {dest_file.name} ({dest_file.stat().st_size // 1024} KB)")
                    return dest_file
                else:
                    print(f"  ⚠ 视频流响应异常 HTTP {resp.status_code}，重试中...")
            except Exception as e:
                print(f"  ⚠ 切片下载波动: {e}")
                if attempt < retries:
                    time.sleep(2.0 + random.uniform(0.5, 1.5))

        return None
