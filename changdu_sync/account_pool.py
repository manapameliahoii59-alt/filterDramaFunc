"""
changdu_sync/account_pool.py
常读多账号轮换调度池与防风控执行引擎
特性：
1. 支持单账号 (auth.json) 与多账号池 (auth_pool/auth_*.json) 自动发现；
2. 轮询负载均衡 (Round-Robin) 与熔断冷却 (Failover & Backoff)；
3. 拟人自适应抖动延时 (Gaussian Jitter 1.8s ~ 3.2s) 与微休息机制；
4. 剧目精确/模糊搜索与最佳匹配；
5. 第 1 集物料提取与流式采样下载。
"""
import os
import sys
import json
import time
import random
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from .client import ChangduSyncClient, SERIES_LIST_PATH
from .account_limits import LIMITS_FILENAME, get_limit
from .detail_fetcher import (
    ChangduDetailFetcher,
    DramaDetailMaterial,
    video_download_headers,
    EPISODE_INFO_PATH,
    PLAYER_INFO_PATH,
    SERIES_DETAIL_PATH
)

DEFAULT_AUTH_FILE = Path(__file__).parent.parent / "auth.json"
DEFAULT_AUTH_POOL_DIR = Path(__file__).parent.parent / "auth_pool"


class AccountSession:
    """单个常读账号的运行态管理"""
    def __init__(self, auth_file: Path, headless: bool = True):
        self.auth_file = auth_file
        self.headless = headless
        self.client: Optional[ChangduSyncClient] = None
        self.fetcher: Optional[ChangduDetailFetcher] = None
        self.cooling_until: float = 0.0
        self.request_count: int = 0
        self.account_id: str = auth_file.stem
        # 单账号最多获取多少部筛选目标剧目（0 = 不限制）
        self.max_dramas: int = get_limit(auth_file.name)
        self.dramas_fetched: int = 0

    def ensure_connected(self):
        if self.client is None or self.client.page is None:
            self.client = ChangduSyncClient(self.auth_file, headless=self.headless)
            self.client.connect()
            self.fetcher = ChangduDetailFetcher(self.client)

    def is_quota_exhausted(self) -> bool:
        return self.max_dramas > 0 and self.dramas_fetched >= self.max_dramas

    def is_available(self) -> bool:
        return time.time() >= self.cooling_until and not self.is_quota_exhausted()

    def mark_cooldown(self, seconds: float = 60.0):
        self.cooling_until = time.time() + seconds
        print(f"  [调度器] 账号 {self.account_id} 触发限频退避，冷却 {seconds} 秒")

    def close(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
            self.fetcher = None


class ChangduAccountPool:
    """常读多账号调度池"""

    def __init__(self, auth_dir: Optional[Path | str] = None, headless: bool = True):
        self.headless = headless
        self.sessions: List[AccountSession] = []
        self._current_idx = 0
        self._total_requests = 0

        # 发现账号文件：auth_pool/*.json + 单账号 auth.json 一起纳入池
        auth_dir_path = Path(auth_dir or DEFAULT_AUTH_POOL_DIR).resolve()
        auth_files = []
        if auth_dir_path.exists() and auth_dir_path.is_dir():
            auth_files = sorted(
                p for p in auth_dir_path.glob("*.json")
                if p.name != LIMITS_FILENAME
            )

        single_auth = Path(DEFAULT_AUTH_FILE).resolve()
        resolved = {p.resolve() for p in auth_files}
        if single_auth.is_file() and single_auth not in resolved:
            auth_files.insert(0, single_auth)

        if not auth_files:
            raise FileNotFoundError(f"未找到任何常读登录凭证文件 (已检查 {DEFAULT_AUTH_FILE} 与 {auth_dir_path})")

        print(f"[账号池] 成功初始化 {len(auth_files)} 个账号凭据: {[f.stem for f in auth_files]}")
        for af in auth_files:
            self.sessions.append(AccountSession(af, headless=self.headless))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        for s in self.sessions:
            s.close()

    def _get_active_session(self, ignore_quota: bool = False) -> AccountSession:
        """获取下一个可用账号（轮询 + 容灾冷却 + 单账号抓取上限）。"""

        def usable(sess: AccountSession) -> bool:
            if time.time() < sess.cooling_until:
                return False
            return ignore_quota or not sess.is_quota_exhausted()

        n = len(self.sessions)
        for _ in range(n):
            sess = self.sessions[self._current_idx % n]
            self._current_idx += 1
            if usable(sess):
                sess.ensure_connected()
                return sess

        # 没有可用账号：区分「全部达到单账号上限」与「都在冷却」
        if not ignore_quota and all(s.is_quota_exhausted() for s in self.sessions):
            raise RuntimeError("所有账号均已达到单账号抓取上限")

        eligible = [s for s in self.sessions if ignore_quota or not s.is_quota_exhausted()]
        if not eligible:
            raise RuntimeError("所有账号均不可用")

        min_cool = min(s.cooling_until for s in eligible)
        wait_sec = max(1.0, min_cool - time.time())
        print(f"[账号池] 所有可用账号均处于冷却期，自动平滑休眠等待 {wait_sec:.1f} 秒...")
        time.sleep(wait_sec)
        for sess in eligible:
            if usable(sess):
                sess.ensure_connected()
                return sess
        sess = eligible[0]
        sess.ensure_connected()
        return sess

    def _apply_human_pacing(self, session: AccountSession):
        """执行拟人自适应抖动与微休息机制"""
        session.request_count += 1
        self._total_requests += 1

        # 批次微休息：每 15 次请求模拟人类停顿 12~18 秒
        if self._total_requests % 15 == 0:
            pause_time = random.uniform(12.0, 18.0)
            print(f"  ☕ [拟人节奏] 累计完成 15 组请求，模拟运营人员休息停顿 {pause_time:.1f} 秒...")
            time.sleep(pause_time)
            return

        # 正常抖动：高斯分布 2.0s ~ 3.5s
        delay = max(1.5, min(4.0, random.gauss(mu=2.5, sigma=0.6)))
        time.sleep(delay)

    def search_drama(self, name: str) -> Optional[Dict[str, Any]]:
        """
        按剧名在常读平台检索短剧基本信息
        返回规范数据包:
        {
            "book_id": str,
            "book_name": str,
            "abstract": str,
            "total_episode_count": int,
            "category_text": str,
            "original_thumb_url": str,
            "root_book_id": Optional[str],
            "raw_item": dict
        }
        """
        name_clean = name.strip()
        if not name_clean:
            return None

        try:
            session = self._get_active_session()
        except RuntimeError as exc:
            print(f"  ⚠ {exc}，跳过常读检索，使用本地输入。")
            return None

        session.dramas_fetched += 1
        if session.max_dramas > 0 and session.dramas_fetched == session.max_dramas:
            print(f"  [账号池] 账号 {session.account_id} 已达到上限 {session.max_dramas} 部，后续停用。")
        self._apply_human_pacing(session)

        end = datetime.now()
        start = end - timedelta(days=29)

        # 针对剧名中的标点切分子关键词
        keywords = [name_clean]
        for sep in ("：", ":", "，", ",", " ", "！", "!"):
            if sep in name_clean:
                parts = [p.strip() for p in name_clean.split(sep) if p.strip()]
                for p in parts:
                    if p not in keywords and len(p) >= 2:
                        keywords.append(p)

        best_item = None

        for kw in keywords:
            params = {
                "search_type": "2",
                "query": kw,
                "sort_type": "1",
                "start_time": start.strftime("%Y-%m-%d"),
                "end_time": end.strftime("%Y-%m-%d"),
                "sort_field": "8",
                "aweme_user_new_version": "true",
                "page_index": "0",
                "page_size": "10",
            }

            try:
                raw_res = session.client.page.evaluate(
                    """async ({ apiPath, params, app, adUserId }) => {
                        const qs = new URLSearchParams(params).toString();
                        const headers = {
                            accept: 'application/json, text/plain, */*',
                            appid: String(app.app_id),
                            apptype: String(app.app_type),
                            distributorid: String(app.distributor_id),
                            aduserid: adUserId,
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
                        "apiPath": SERIES_LIST_PATH,
                        "params": params,
                        "app": session.client.app_info,
                        "adUserId": session.client.ad_user_id,
                    }
                )
            except Exception as e:
                print(f"  ⚠ 账号 {session.account_id} 请求异常: {e}")
                session.mark_cooldown(45.0)
                continue

            data = raw_res.get("data") or raw_res
            series_list = data.get("series_list") or data.get("book_list") or (data.get("data") if isinstance(data.get("data"), list) else [])

            if not series_list:
                continue

            # 匹配最佳剧目
            norm_query = re.sub(r"\s+", "", name_clean)
            for item in series_list:
                sname = item.get("book_name") or item.get("series_name") or ""
                norm_sname = re.sub(r"\s+", "", sname)
                if norm_sname == norm_query:
                    best_item = item
                    break

            if not best_item:
                for item in series_list:
                    sname = item.get("book_name") or item.get("series_name") or ""
                    norm_sname = re.sub(r"\s+", "", sname)
                    if norm_query in norm_sname or norm_sname in norm_query:
                        best_item = item
                        break

            if not best_item and series_list:
                best_item = series_list[0]

            if best_item:
                break

        if not best_item:
            return None

        book_id = str(best_item.get("book_id") or best_item.get("series_id") or "")
        book_name = best_item.get("book_name") or best_item.get("series_name") or name_clean
        abstract = (best_item.get("abstract") or best_item.get("intro") or "").strip()
        ep_count = int(best_item.get("total_episode_count") or best_item.get("episode_count") or 0)
        tag_list = best_item.get("tag_list") or []
        category_text = ",".join(tag_list) if isinstance(tag_list, list) else str(tag_list)
        thumb = best_item.get("original_thumb_url") or best_item.get("thumb_url") or ""

        return {
            "book_id": book_id,
            "book_name": book_name,
            "abstract": abstract,
            "total_episode_count": ep_count,
            "category_text": category_text,
            "original_thumb_url": thumb,
            "raw_item": best_item
        }

    def fetch_first_episode_media(
        self,
        book_id: str,
        dest_dir: Optional[Path | str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        阶段二调用：抓取第 1 集视频流并下载用于视听检测
        """
        try:
            session = self._get_active_session(ignore_quota=True)
        except RuntimeError as exc:
            print(f"  ⚠ {exc}")
            return None
        self._apply_human_pacing(session)

        save_dir = Path(dest_dir or (Path(__file__).parent.parent / "scratch" / "previews")).resolve()
        save_dir.mkdir(parents=True, exist_ok=True)

        try:
            material = session.fetcher.fetch_material(
                book_id=book_id,
                fetch_video=True,
                download_preview=True,
                preview_max_bytes=16 * 1024 * 1024,
                dest_dir=save_dir
            )
            return material.to_dict()
        except Exception as e:
            print(f"  ⚠ 抓取第1集物料失败: {e}")
            return None

    def fetch_first_episode_media_browser(
        self,
        book_id: str,
        dest_dir: Optional[Path | str] = None,
        duration_sec: float = 6.0
    ) -> Dict[str, Any]:
        """
        方案 B：通过 Playwright 浏览器渲染解密播放器，捕获原生解密音频和高清画面帧
        """
        try:
            session = self._get_active_session(ignore_quota=True)
        except RuntimeError as exc:
            print(f"  ⚠ {exc}")
            return {"success": False, "error": str(exc)}
        self._apply_human_pacing(session)
        session.ensure_connected()
        return session.fetcher.capture_ep1_browser_media(
            book_id=book_id,
            dest_dir=dest_dir,
            duration_sec=duration_sec
        )

