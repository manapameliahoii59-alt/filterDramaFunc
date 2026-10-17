"""iocpx 平台登录与鉴权（含防风控处理）。

参考漫剧神器项目的 ensureAuth 逻辑：
1. login1 用账号密码换取 session_id（从 set-cookie 提取）
2. 从返回的 modules 列表中挑选短剧模块
3. login2 选中模块，完成鉴权
4. token 缓存在本地，6 天内复用
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

import requests

# 项目根目录（auth.py 的 ../../ ）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = PROJECT_ROOT / ".iocpx_session.json"

BASE_URL = "https://api.iocpx.com"
DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://console.iocpx.com",
    "Referer": "https://console.iocpx.com/",
}

TOKEN_TTL_MS = 6 * 24 * 60 * 60 * 1000  # 6 天

# 登录请求重试配置
MAX_LOGIN_RETRIES = 3
BACKOFF_BASE = 1.5


@dataclass
class Session:
    token: str
    module_id: int
    time: int

    def is_fresh(self) -> bool:
        return (time.time() * 1000 - self.time) < TOKEN_TTL_MS

    def to_dict(self) -> dict:
        return {"token": self.token, "module_id": self.module_id, "time": self.time}

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            token=d.get("token", ""),
            module_id=d.get("module_id", 0),
            time=d.get("time", 0),
        )


def _load_cache() -> Session | None:
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        sess = Session.from_dict(data)
        return sess if sess.token else None
    except Exception:
        return None


def _save_cache(sess: Session) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(sess.to_dict(), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _pick_drama_module(modules: list[dict]) -> int:
    """从模块列表里挑短剧模块，没有则挑第一个启用的。"""
    if not modules:
        return 0
    # 优先找名称含"短剧"且未关闭的
    for m in modules:
        if "短剧" in str(m.get("moduleName", "")) and m.get("isOpen") is not False:
            return int(m["moduleId"])
    # 再找任意未关闭的
    for m in modules:
        if m.get("isOpen") is not False and m.get("moduleId") is not None:
            return int(m["moduleId"])
    # 兜底第一个
    return int(modules[0].get("moduleId", 0))


def _make_auth_headers(token: str) -> dict[str, str]:
    return {
        "authorization": token,
        "cookie": f"ocpx_session_id={token}",
        **DEFAULT_HEADERS,
    }


def _sleep_backoff(attempt: int) -> None:
    """指数退避 + 随机抖动。"""
    wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.random()
    time.sleep(wait)


class AuthError(Exception):
    pass


def _post_with_retry(url: str, json_body: dict, headers: dict | None = None, timeout: int = 15) -> requests.Response:
    """POST 请求带重试，处理网络异常和限流。"""
    last_exc = None
    for attempt in range(1, MAX_LOGIN_RETRIES + 2):
        try:
            r = requests.post(url, json=json_body, headers=headers or DEFAULT_HEADERS, timeout=timeout)
            # 429/503 等限流码，先重试
            if r.status_code in (429, 502, 503, 504) and attempt <= MAX_LOGIN_RETRIES:
                _sleep_backoff(attempt)
                continue
            return r
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt <= MAX_LOGIN_RETRIES:
                _sleep_backoff(attempt)
                continue
            raise AuthError(
                f"请求失败（已重试 {MAX_LOGIN_RETRIES} 次）: {exc}"
            ) from exc
    raise AuthError(f"请求异常: {last_exc}")


def login(email: str, password: str) -> tuple[Session, dict[str, str]]:
    """账号密码登录，返回 Session 和后续请求用的 headers。"""

    # --- step 1: login1 ---
    r1 = _post_with_retry(
        f"{BASE_URL}/merchant/auth/login1",
        {"email": email, "password": password, "rememberMe": True},
    )
    r1.raise_for_status()
    payload = r1.json()
    code = payload.get("code")
    if code not in (0, "0"):
        raise AuthError(f"login1 失败: {code} {payload.get('msg', '未知错误')}")

    # 从 set-cookie 提取 ocpx_session_id
    set_cookie = r1.headers.get("set-cookie") or ""
    # set-cookie 可能是逗号分隔的多条，或单条
    session_id = ""
    for part in set_cookie.split(","):
        part = part.strip()
        if part.startswith("ocpx_session_id="):
            session_id = part.split(";")[0].split("=", 1)[1]
            break
    if not session_id:
        # 有些环境 set-cookie 会展开成列表
        cookies = r1.cookies.get_dict()
        session_id = cookies.get("ocpx_session_id", "")
    if not session_id:
        raise AuthError("未获取到 session_id")

    # --- step 2: 选模块 ---
    raw_data = payload.get("data", {})
    if isinstance(raw_data, list):
        modules = raw_data
    elif isinstance(raw_data, dict):
        modules = raw_data.get("modules", [])
    else:
        modules = []
    module_id = _pick_drama_module(modules)
    if not module_id:
        raise AuthError("未找到可用模块")

    auth_h = _make_auth_headers(session_id)
    r2 = _post_with_retry(
        f"{BASE_URL}/merchant/auth/login2",
        {"moduleId": module_id},
        headers=auth_h,
    )
    r2.raise_for_status()
    payload2 = r2.json()
    code2 = payload2.get("code")
    if code2 not in (0, "0"):
        raise AuthError(f"login2 失败: {code2} {payload2.get('msg', '未知错误')}")

    sess = Session(token=session_id, module_id=module_id, time=int(time.time() * 1000))
    _save_cache(sess)
    return sess, auth_h


def ensure_auth(email: str, password: str) -> dict[str, str]:
    """确保拿到可用的鉴权 headers（优先缓存，过期自动重登）。"""
    cached = _load_cache()
    if cached and cached.is_fresh():
        # 试探性验证
        auth_h = _make_auth_headers(cached.token)
        try:
            r = requests.get(
                f"{BASE_URL}/merchant/auth/info",
                headers=auth_h,
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                code = data.get("code")
                # 1001000001 是过期码
                if code != 1001000001:
                    # 复用成功，再次 login2 确保模块已选中
                    _ = _post_with_retry(
                        f"{BASE_URL}/merchant/auth/login2",
                        {"moduleId": cached.module_id},
                        headers=auth_h,
                    )
                    return auth_h
        except Exception:
            pass

    # 缓存无效或过期，重新登录
    _, headers = login(email, password)
    return headers


def clear_cache() -> None:
    """清除本地 token 缓存（退出登录/切换账号时使用）。"""
    try:
        CACHE_PATH.unlink(missing_ok=True)
    except OSError:
        pass
