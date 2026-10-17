"""iocpx 平台剧目列表抓取（含防风控处理）。"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from typing import Callable

import requests

from desktop_app.iocpx.auth import BASE_URL, ensure_auth

# 防风控：翻页之间的基础间隔（秒），每次在此基础上加随机抖动
PAGE_DELAY_BASE = 0.8
PAGE_DELAY_JITTER = 0.7  # 随机增加 0~0.7 秒
# 单页请求失败时的最大重试次数（不含首次）
MAX_PAGE_RETRIES = 3
# 指数退避：基础等待秒数
BACKOFF_BASE = 1.5


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _is_retryable(status_code: int, api_code: int | str | None) -> bool:
    """判断当前错误是否值得重试。"""
    # HTTP 层：429(限流) / 503(服务暂不可用) / 502(网关错误) / 504(网关超时)
    if status_code in (429, 502, 503, 504):
        return True
    # 业务层：部分平台用 100100000x 表示限流或繁忙
    if isinstance(api_code, int) and api_code in (1001000002, 1001000003):
        return True
    return False


def _page_delay() -> float:
    """计算本次翻页前的等待时间（随机抖动，避免固定节奏被识别）。"""
    return PAGE_DELAY_BASE + random.random() * PAGE_DELAY_JITTER


def _fetch_single_page(
    url: str,
    body: dict,
    headers: dict,
    page_no: int,
    log_callback: Callable[[str], None] | None,
) -> dict:
    """请求单页数据，带重试与风控退避。

    返回接口的 data 字段（含 list/total）。
    """
    last_err = None
    for attempt in range(1, MAX_PAGE_RETRIES + 2):  # 含首次
        try:
            r = requests.post(url, json=body, headers=headers, timeout=30)
            status = r.status_code

            # 非 2xx 时先判断是否需要重试
            if status != 200:
                if _is_retryable(status, None) and attempt <= MAX_PAGE_RETRIES:
                    wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.random()
                    msg = f"第 {page_no} 页 HTTP {status}，{attempt}/{MAX_PAGE_RETRIES + 1} 次重试，退避 {wait:.1f}s"
                    if log_callback:
                        log_callback(msg)
                    time.sleep(wait)
                    continue
                r.raise_for_status()

            data = r.json()
            api_code = data.get("code")
            if api_code not in (0, "0"):
                msg = data.get("msg", "未知错误")
                # 业务层限流码，重试
                if _is_retryable(status, api_code) and attempt <= MAX_PAGE_RETRIES:
                    wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.random()
                    if log_callback:
                        log_callback(
                            f"第 {page_no} 页接口返回 {api_code}（{msg}），"
                            f"{attempt}/{MAX_PAGE_RETRIES + 1} 次重试，退避 {wait:.1f}s"
                        )
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"接口返回错误: {api_code} {msg}")

            return data.get("data", {})

        except (requests.Timeout, requests.ConnectionError) as exc:
            last_err = exc
            if attempt <= MAX_PAGE_RETRIES:
                wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.random()
                if log_callback:
                    log_callback(
                        f"第 {page_no} 页网络异常（{type(exc).__name__}），"
                        f"{attempt}/{MAX_PAGE_RETRIES + 1} 次重试，退避 {wait:.1f}s"
                    )
                time.sleep(wait)
                continue
            raise RuntimeError(
                f"第 {page_no} 页请求失败，已重试 {MAX_PAGE_RETRIES} 次: {exc}"
            ) from exc

    # 理论上不会走到这里，兜底
    raise RuntimeError(f"第 {page_no} 页请求异常: {last_err}")


def fetch_schedule(
    email: str,
    password: str,
    *,
    min_chapter: int = 40,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page_size: int = 200,
    log_callback: Callable[[str], None] | None = None,
    no_delay: bool = False,
) -> list[dict]:
    """抓取 dramaDeliverySchedule/page 全量数据（自动翻页）。

    Args:
        email, password: iocpx 账号密码
        min_chapter: 最小集数（默认 40）
        start_time, end_time: 时间范围，默认最近 7 天
        page_size: 每页条数，最大 200
        log_callback: 每翻一页会回调一行日志文本
        no_delay: 调试开关，为 True 时跳过翻页延迟（不要用于生产）
    """
    headers = ensure_auth(email, password)

    if end_time is None:
        end_time = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    if start_time is None:
        start_time = (end_time - timedelta(days=6)).replace(hour=0, minute=0, second=0)

    payload_template = {
        "minChapterAmount": str(min_chapter),
        "bookNames": [],
        "sortingFields": [{"field": "bookPublishTime", "order": "desc"}],
        "editors": [],
        "editorDeptIds": [],
        "operatorDeptIds": [],
        "operators": [],
        "producers": [],
        "editStatus": "",
        "editAssignStatus": "",
        "cutTypes": [],
        "creationStatus": "",
        "hotDramaStatus": "",
        "pageNo": 1,
        "pageSize": min(page_size, 200),
    }

    all_items: list[dict] = []
    page_no = 1
    url = f"{BASE_URL}/adv-release-toutiao/dramaDeliverySchedule/page"

    while True:
        body = {
            **payload_template,
            "startTime": _fmt(start_time),
            "endTime": _fmt(end_time),
            "pageNo": page_no,
        }

        if log_callback:
            log_callback(f"正在请求第 {page_no} 页…")

        page_data = _fetch_single_page(url, body, headers, page_no, log_callback)
        items = page_data.get("list") or []
        total = page_data.get("total") or 0

        if log_callback:
            log_callback(f"第 {page_no} 页返回 {len(items)} 条（共 {total} 条）")

        all_items.extend(items)

        if len(items) == 0 or len(all_items) >= total:
            break

        page_no += 1

        # 防风控：翻页之间加随机延迟，避免被识别为爬虫
        if not no_delay:
            delay = _page_delay()
            if log_callback:
                log_callback(f"  等待 {delay:.1f}s 后继续…")
            time.sleep(delay)

    if log_callback:
        log_callback(f"抓取完成，累计 {len(all_items)} 条")

    return all_items
