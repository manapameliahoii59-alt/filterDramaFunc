"""
client.py
常读平台 API 安全客户端
基于真实 Playwright 上下文与 secsdk 签名就绪机制，安全拉取常读短剧大盘列表
"""
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from .stealth import (
    HOME_URL,
    SHORT_PLAY_LIST_URL,
    LAUNCH_ARGS,
    STEALTH_INIT_SCRIPT,
    browser_context_kwargs
)

SERIES_LIST_PATH = "/novelsale/distributor/content/series/list/v1/"
AVAILABLE_PACKAGES_PATH = "/novelsale/distributor/get_available_packages/v2/?"
SHORT_PLAY_APP_TYPE = 21  # 短剧应用类型


class ChangduSyncClient:
    """常读平台安全同步客户端"""

    def __init__(self, auth_file: Path | str, headless: bool = True):
        self.auth_file = Path(auth_file).resolve()
        self.headless = headless
        self._pw = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.ad_user_id: Optional[str] = None
        self.root_ad_user_id: Optional[str] = None
        self.app_info: Optional[Dict[str, Any]] = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self):
        """启动浏览器并恢复登录态，等待安全签名环境就绪"""
        if not self.auth_file.is_file():
            raise FileNotFoundError(f"未找到登录凭证文件: {self.auth_file}，请先执行登录")

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=self.headless,
            args=list(LAUNCH_ARGS),
        )
        self.context = self.browser.new_context(
            **browser_context_kwargs(storage_state=str(self.auth_file))
        )
        self.page = self.context.new_page()
        self.page.add_init_script(STEALTH_INIT_SCRIPT)

        # 校验登录 Cookie
        cookies = self.context.cookies()
        self.ad_user_id = next((c["value"] for c in cookies if c["name"] == "adUserId"), None)
        self.root_ad_user_id = next((c["value"] for c in cookies if c["name"] == "rootAdUserId"), None)

        if not self.ad_user_id or not self.root_ad_user_id:
            self.close()
            raise RuntimeError("凭证中缺少 adUserId/rootAdUserId，登录态已过期，请重新登录")

        # 访问首页并等待字节 secsdk 签名劫持就绪
        self.page.goto(HOME_URL, timeout=60_000, wait_until="load")
        self._wait_secsdk_ready()

        # 解析商户应用包配置
        self.app_info = self._resolve_app_info()

        # 预热短剧列表页
        self._warmup_short_play_list()

    def _wait_secsdk_ready(self):
        """等待前端反爬 SDK 签名函数初始化"""
        try:
            self.page.wait_for_function("() => typeof window.use === 'function'", timeout=20_000)
            self.page.wait_for_timeout(2500)
        except Exception:
            self.page.wait_for_timeout(4000)

    def _resolve_app_info(self) -> Dict[str, Any]:
        """通过内置上下文请求商户短剧 app 信息"""
        result = self.page.evaluate(
            """async (uid) => {
                const res = await fetch('/novelsale/distributor/get_available_packages/v2/?', {
                    credentials: 'include',
                    headers: {
                        accept: 'application/json, text/plain, */*',
                        appid: '0',
                        apptype: '1',
                        distributorid: '0',
                        aduserid: uid,
                        'agw-js-conv': 'str',
                        'x-secsdk-csrf-token': 'DOWNGRADE',
                    },
                });
                return res.json();
            }""",
            self.ad_user_id,
        )
        packages = (result.get("available_packages") or {}).get(str(SHORT_PLAY_APP_TYPE), [None])
        app = packages[0]
        if not app:
            raise RuntimeError(f"未找到短剧类型权限配置(app_type={SHORT_PLAY_APP_TYPE})，请确认账号权限")
        return {
            "app_id": app["app_id"],
            "app_type": app["app_type"],
            "distributor_id": app["distributor_id"],
        }

    def _warmup_short_play_list(self):
        """预热并加载列表页，就绪当前会话的上下文"""
        end = datetime.now()
        start = end - timedelta(days=29)
        query = {
            "sort_type": "1",
            "start_time": start.strftime("%Y-%m-%d"),
            "end_time": end.strftime("%Y-%m-%d"),
            "sort_field": "8",
            "aweme_user_new_version": "true",
            "page_index": "0",
            "page_size": "10",
        }
        self.page.goto(
            f"{SHORT_PLAY_LIST_URL}?{urlencode(query)}",
            timeout=60_000,
            wait_until="load"
        )
        self.page.wait_for_timeout(2000)

    def fetch_dramas(self, limit: int = 50, days: int = 30) -> List[Dict[str, Any]]:
        """
        批量安全拉取常读大盘近期的短剧列表
        """
        end = datetime.now()
        start = end - timedelta(days=max(days - 1, 0))
        date_range = {
            "start_time": start.strftime("%Y-%m-%d"),
            "end_time": end.strftime("%Y-%m-%d"),
        }

        collected_dramas = []
        page_index = 0
        page_size = min(limit, 20)

        while len(collected_dramas) < limit:
            params = {
                "sort_type": "1",
                **date_range,
                "sort_field": "8",
                "aweme_user_new_version": "true",
                "page_index": str(page_index),
                "page_size": str(page_size),
            }

            # 浏览器上下文内执行 fetch，携带真实浏览器指纹与签名
            raw_res = self.page.evaluate(
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
                    const text = await res.text();
                    try { return { status: res.status, json: JSON.parse(text) }; }
                    catch(e) { return { status: res.status, error: text.slice(0, 300) }; }
                }""",
                {
                    "apiPath": SERIES_LIST_PATH,
                    "params": params,
                    "app": self.app_info,
                    "adUserId": self.ad_user_id,
                }
            )

            if raw_res.get("status") != 200:
                raise RuntimeError(f"请求常读列表接口异常: {raw_res}")

            data = raw_res.get("json", {})
            series_list = data.get("series_list") or data.get("book_list") or []
            if not series_list:
                break

            for item in series_list:
                # 提取核心字段
                book_id = item.get("book_id") or item.get("series_id") or ""
                book_name = item.get("book_name") or item.get("series_name") or ""
                abstract = (item.get("abstract") or item.get("intro") or "").strip()
                tag_list = item.get("tag_list") or []
                total_ep = item.get("total_episode_count") or item.get("episode_count") or 0

                if book_name:
                    collected_dramas.append({
                        "book_id": str(book_id),
                        "book_name": book_name,
                        "abstract": abstract,
                        "total_episode_count": total_ep,
                        "tag_list": tag_list,
                    })

                if len(collected_dramas) >= limit:
                    break

            page_index += 1
            # 拟人化抖动延时，避免突发高频请求
            time.sleep(1.2)

        return collected_dramas

    def close(self):
        """安全关闭"""
        if self.page:
            try:
                self.page.close()
            except Exception:
                pass
            self.page = None
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None


def export_dramas_to_txt(
    dramas: List[Dict[str, Any]],
    output_txt_path: Path | str,
    append: bool = False
) -> int:
    """
    将抓取的常读大盘剧目格式化输出至 txt 文件（换行符分隔）
    格式: 《剧名》：剧情梗概...
    """
    target = Path(output_txt_path).resolve()
    lines = []
    for d in dramas:
        name = d["book_name"]
        intro = d["abstract"]
        tags_str = "/".join(d["tag_list"]) if d["tag_list"] else "都市/爽剧"
        total_ep = d["total_episode_count"]

        # 组装格式（包含集数体量标签）
        if intro:
            # 去除梗概内部的多余换行符，确保一部剧严格对应一行
            clean_intro = " ".join(intro.split())
            line = f"《{name}》[共{total_ep}集]：{clean_intro}"
        else:
            line = f"《{name}》[共{total_ep}集]：【题材: {tags_str}】暂无详细梗概"
        lines.append(line)

    mode = "a" if append else "w"
    with open(target, mode, encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

    return len(lines)
