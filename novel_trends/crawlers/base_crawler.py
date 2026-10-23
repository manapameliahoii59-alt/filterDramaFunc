"""
novel_trends/crawlers/base_crawler.py
小说榜单爬虫抽象基类
提供完善的企业级防风控与反爬能力：
1. 真实浏览器指纹与动态 User-Agent 池轮询
2. 拟人化随机请求延时 (Random Jitter)，消除机械规律
3. 指数退避智能重试机制 (Exponential Backoff with Retry)
4. 移动端/轻量 SSR 避开高防前端与图形滑块
5. Gzip/Deflate 自动解压与 SSL 容错
6. 透明代理支持 (HTTP_PROXY / HTTPS_PROXY)
"""
import os
import ssl
import time
import gzip
import random
import urllib.request
from urllib.error import HTTPError, URLError
from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from ..models import NovelBook


# 真实自洽的移动端设备标头配置（杜绝 iOS 携带 Chromium 标头等矛盾特征）
MOBILE_PROFILES = [
    {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        # WebKit / iOS Safari 原生不发送 Sec-Ch-Ua
    },
    {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    },
    {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S9180 Build/UP1A.231005.007) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Sec-Ch-Ua": '"Chromium";v="124", "Android WebView";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?1",
        "Sec-Ch-Ua-Platform": '"Android"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    },
]

# 真实自洽的桌面端设备标头配置
DESKTOP_PROFILES = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Sec-Ch-Ua": '"Microsoft Edge";v="123", "Chromium";v="123", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
        "Accept-Encoding": "gzip, deflate",
        "Upgrade-Insecure-Requests": "1",
        # Firefox 原生不发送 Sec-Ch-Ua
    },
]

# 兼容保留
DESKTOP_UA_POOL = [p["User-Agent"] for p in DESKTOP_PROFILES]
MOBILE_UA_POOL = [p["User-Agent"] for p in MOBILE_PROFILES]


class BaseNovelCrawler(ABC):
    """小说平台排行榜爬虫基类"""
    platform_name: str = "未知平台"
    channel: str = "男频"

    def __init__(self, timeout: int = 12, min_delay: float = 0.3, max_delay: float = 1.0, max_retries: int = 3):
        self.timeout = timeout
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries

        # SSL 证书容错
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

        # 检查是否配置了代理环境变量
        self.opener = self._build_opener()

    def _build_opener(self) -> urllib.request.OpenerDirector:
        """构建支持透明代理与 SSL 的 Opener"""
        handlers = [urllib.request.HTTPSHandler(context=self.ssl_ctx)]
        http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if http_proxy or https_proxy:
            proxy_dict = {}
            if http_proxy:
                proxy_dict["http"] = http_proxy
            if https_proxy:
                proxy_dict["https"] = https_proxy
            handlers.append(urllib.request.ProxyHandler(proxy_dict))
        return urllib.request.build_opener(*handlers)

    def _apply_random_jitter(self):
        """请求间隙拟人化随机延时，打乱机械周期规律"""
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)

    @staticmethod
    def is_waf_challenge(html: str) -> bool:
        """检测页面是否为腾讯防水墙/Chaos VM 探针挑战页"""
        if not html:
            return False
        # 常见探针与验证码特征
        if "/C2WF946J0/probe.js" in html or "probev3.js" in html:
            return True
        if "TCaptcha.js" in html or "tcaptcha-frame" in html or "/WafCaptcha" in html:
            return True
        return False

    def fetch_url(
        self,
        url: str,
        is_mobile: bool = False,
        encoding: str = "utf-8",
        referer: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None
    ) -> str:
        """
        具备多重风控防御的统一安全请求引擎
        支持：自洽设备指纹头、自动解压、拟人延时、指数退避智能重试、WAF挑战检测
        """
        # 拟人化微延时
        self._apply_random_jitter()

        profile = random.choice(MOBILE_PROFILES if is_mobile else DESKTOP_PROFILES)
        headers = dict(profile)
        if referer:
            headers["Referer"] = referer
            if "Sec-Fetch-Site" in headers:
                headers["Sec-Fetch-Site"] = "same-origin"
        if extra_headers:
            headers.update(extra_headers)

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers)
                with self.opener.open(req, timeout=self.timeout) as resp:
                    raw_bytes = resp.read()
                    
                    # 自动处理 Gzip 压缩流
                    if resp.headers.get("Content-Encoding") == "gzip":
                        try:
                            raw_bytes = gzip.decompress(raw_bytes)
                        except Exception:
                            pass
                    
                    decoded = raw_bytes.decode(encoding, errors="ignore")
                    if self.is_waf_challenge(decoded):
                        print(f"[{self.platform_name}] [风控告警] 响应命中 WAF 挑战页 (包含探针或验证码)，退避重试 (第{attempt}次)... URL: {url}")
                        backoff = (2 ** attempt) + random.uniform(1.0, 3.0)
                        time.sleep(backoff)
                        last_error = RuntimeError(f"命中 WAF 挑战页 ({url})")
                        continue
                    return decoded

            except HTTPError as he:
                last_error = he
                # 针对 429 (频率限制) 或 503 (服务端限流) 或 403 (临时拦截) 触发指数退避
                if he.code in (429, 503, 502, 403):
                    backoff = (2 ** attempt) + random.uniform(1.0, 2.5)
                    time.sleep(backoff)
                else:
                    # 404 或其他非限流错误不重复重试
                    break

            except (URLError, TimeoutError) as ue:
                last_error = ue
                backoff = 1.0 + attempt * 0.5
                time.sleep(backoff)

            except Exception as ex:
                last_error = ex
                break

        # 所有重试均告失败时抛出异常或记录告警
        raise RuntimeError(f"[{self.platform_name}] 请求失败 (已重试 {self.max_retries} 次): {url} -> {last_error}")

    @abstractmethod
    def fetch_top_books(self, max_count: int = 20) -> List[NovelBook]:
        """抓取当前平台头部畅销/月票榜作品"""
        pass
