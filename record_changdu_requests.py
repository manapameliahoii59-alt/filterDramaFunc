"""
record_changdu_requests.py
常读短剧网络接口与数据源嗅探工具

功能：
1. 启动 Chromium 拟真浏览器并载入现有的 auth.json 登录凭证；
2. 注入反反爬防风控 Stealth 补丁；
3. 全局监听网络请求与响应（Fetch/XHR/媒体流），自动过滤并解析 changdupingtai.com 相关接口；
4. 引导用户自由浏览目标剧目详情页（包括第1集、多集列表、无视频剧目等）；
5. 自动捕获所有请求 URL、Method、Headers、Request Payload、Response JSON 及媒体流地址；
6. 结束时输出格式化汇总并安全导出至 scratch/changdu_network_dump.json 供后续精准对接。
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List

# 终端中文编码兼容
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.sync_api import sync_playwright, Response, Request
from changdu_sync.stealth import (
    LAUNCH_ARGS,
    STEALTH_INIT_SCRIPT,
    browser_context_kwargs,
    launch_playwright_browser,
)

DEFAULT_AUTH_PATH = Path(__file__).parent / "auth.json"
DEFAULT_DUMP_PATH = Path(__file__).parent / "scratch" / "changdu_network_dump.json"
DEFAULT_DETAIL_URL = "https://www.changdupingtai.com/sale/short-play/list/detail?id=7695658463472389182&contentGenre=2"


class ChangduNetworkSniffer:
    def __init__(self, auth_path: Path, output_path: Path):
        self.auth_path = auth_path
        self.output_path = output_path
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.captured_records: List[Dict[str, Any]] = []
        self.captured_urls_set = set()

    def _should_capture(self, url: str) -> bool:
        """过滤关心的 API 或媒体流域名"""
        lower = url.lower()
        # 忽略普通静态样式、字体、微前端脚本
        if any(lower.endswith(ext) for ext in [".css", ".js", ".woff", ".woff2", ".ttf", ".svg", ".png", ".jpg", ".jpeg", ".ico"]):
            return False
        # 抓取包含 API、常读业务域名、或者音视频流的请求
        keywords = [
            "changdupingtai.com",
            "/novelsale/",
            "/node/api/",
            "/api/",
            "player",
            "episode",
            "series",
            "playlet",
            ".m3u8",
            ".mp4",
            "volces.com",
            "bytevcloud",
            "tos-cn",
        ]
        return any(k in lower for k in keywords)

    def on_response(self, response: Response):
        url = response.url
        if not self._should_capture(url):
            return

        parsed = urlparse(url)
        path = parsed.path
        status = response.status
        content_type = response.headers.get("content-type", "")

        is_json = "json" in content_type or "javascript" in content_type
        is_media = any(ext in url.lower() for ext in [".m3u8", ".mp4", ".ts"]) or "video" in content_type or "audio" in content_type

        # 仅关注 JSON 接口或媒体流
        if not (is_json or is_media or "/api/" in path or "/novelsale/" in path):
            return

        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "url": url,
            "path": path,
            "method": response.request.method,
            "status": status,
            "content_type": content_type,
            "query_params": {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()},
            "request_post_data": None,
            "response_data": None,
            "is_media_stream": is_media,
        }

        # 尝试捕获请求 body
        try:
            post_data = response.request.post_data
            if post_data:
                try:
                    record["request_post_data"] = json.loads(post_data)
                except Exception:
                    record["request_post_data"] = post_data
        except Exception:
            pass

        # 尝试捕获响应 JSON
        if is_json or "text" in content_type:
            try:
                body_text = response.text()
                try:
                    record["response_data"] = json.loads(body_text)
                except Exception:
                    record["response_data"] = body_text[:2000]
            except Exception as e:
                record["response_data"] = f"[无法提取响应文本: {e}]"
        elif is_media:
            record["response_data"] = f"[媒体流地址: {url}]"

        self.captured_records.append(record)

        # 实时打印到终端
        tag = "[媒体流]" if is_media else "[接口响应]"
        print(f"\n{tag} HTTP {status} | {response.request.method} {path}")
        print(f"   完整URL: {url[:110]}{'...' if len(url) > 110 else ''}")
        if record["query_params"]:
            print(f"   Query参数: {json.dumps(record['query_params'], ensure_ascii=False)[:120]}")
        if is_json and isinstance(record["response_data"], dict):
            keys = list(record["response_data"].keys())
            code = record["response_data"].get("code", record["response_data"].get("status_code", "N/A"))
            msg = record["response_data"].get("msg", record["response_data"].get("message", ""))
            print(f"   返回结果: code={code}, msg='{msg}', 键名={keys[:8]}")

    def save_dump(self):
        """保存捕获的数据到本地 JSON"""
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump({
                "meta": {
                    "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "total_records": len(self.captured_records),
                },
                "records": self.captured_records
            }, f, ensure_ascii=False, indent=2)
        print(f"\n[已成功保存] 抓取到 {len(self.captured_records)} 条网络请求/响应！")
        print(f"保存位置: {self.output_path}")

    def print_summary(self):
        """控制台打印结构化摘要"""
        if not self.captured_records:
            print("\n[提示] 本次浏览未捕获到常读平台 API 请求。")
            return

        api_records = [r for r in self.captured_records if not r.get("is_media_stream")]
        media_records = [r for r in self.captured_records if r.get("is_media_stream")]

        print("\n" + "=" * 75)
        print("                   常读嗅探捕获报告摘要")
        print("=" * 75)
        print(f"1. 捕获核心 API 数量: {len(api_records)} 个")
        print(f"2. 捕获媒体流数量: {len(media_records)} 个")
        print("-" * 75)
        print("【发现的核心接口列表】：")

        seen_paths = set()
        for r in api_records:
            p = r["path"]
            if p in seen_paths:
                continue
            seen_paths.add(p)
            print(f"  • [{r['method']}] {p}")
            if r["query_params"]:
                print(f"     参数特征: {list(r['query_params'].keys())}")
            if isinstance(r.get("response_data"), dict):
                sample_data = r["response_data"].get("data")
                if isinstance(sample_data, dict):
                    print(f"     返回 data 字段: {list(sample_data.keys())[:10]}")
                elif isinstance(sample_data, list) and sample_data:
                    first = sample_data[0]
                    if isinstance(first, dict):
                        print(f"     返回 data 列表项字段: {list(first.keys())[:10]}")

        if media_records:
            print("-" * 75)
            print("【发现的播放媒体流】：")
            for m in media_records[:3]:
                print(f"  • {m['url'][:100]}...")
        print("=" * 75)


def run(target_url: str, auth_file: Path, output_file: Path):
    if not auth_file.is_file():
        print(f"[错误] 未在当前目录下找到登录凭证: {auth_file}")
        print("请先执行 '1_常读平台登录.bat' 登录常读平台。")
        sys.exit(1)

    sniffer = ChangduNetworkSniffer(auth_path=auth_file, output_path=output_file)

    print("=" * 75)
    print("           常读短剧网络接口与数据源嗅探录制工具")
    print("=" * 75)
    print(f"目标详情页: {target_url}")
    print(f"登录凭证: {auth_file.name}")
    print(f"抓取结果导出: {output_file.name}")
    print("=" * 75)
    print("正在启动 Chrome 浏览器（反风控高拟真模式）...")

    with sync_playwright() as pw:
        browser = launch_playwright_browser(pw, headless=False)
        context = browser.new_context(
            **browser_context_kwargs(storage_state=str(auth_file))
        )
        page = context.new_page()
        page.add_init_script(STEALTH_INIT_SCRIPT)

        # 挂载网络响应监听器
        page.on("response", sniffer.on_response)

        print(f"正在导航至目标页面: {target_url}")
        try:
            page.goto(target_url, timeout=60_000, wait_until="load")
        except Exception as e:
            print(f"[提示] 页面加载事件响应中（不影响交互）: {e}")

        print("\n" + "#" * 75)
        print("【浏览器已就绪！】")
        print("操作建议：")
        print("  1. 在弹出的浏览器窗口中查看该剧的各项内容（简介、第1集播放、选集列表）。")
        print("  2. 点击播放第1集视频（如果已有视频），观察下方控制台输出的媒体流与播放接口。")
        print("  3. 若该剧未到上线时间（无视频），可以观察界面提示以及是否有未发布/即将上架的状态字段。")
        print("  4. 你也可以在浏览器地址栏切换其他短剧详情页进行对比。")
        print("  5. 操作完成后：")
        print("     - 可以直接【关闭浏览器窗口】；")
        print("     - 或在当前控制台按【回车键 (Enter)】完成抓取。")
        print("#" * 75 + "\n")

        # 循环检测用户是回车退出，还是关闭了窗口
        try:
            while True:
                # 检查页面是否仍然存活
                if page.is_closed() or len(context.pages) == 0:
                    print("\n[检测到浏览器窗口已关闭]")
                    break
                page.wait_for_timeout(1000)
        except KeyboardInterrupt:
            print("\n[用户中断录制]")
        except Exception:
            pass

        try:
            browser.close()
        except Exception:
            pass

    # 保存与汇总输出
    sniffer.save_dump()
    sniffer.print_summary()


def main():
    parser = argparse.ArgumentParser(description="常读详情页网络接口与数据源嗅探工具")
    parser.add_argument("--url", type=str, default=DEFAULT_DETAIL_URL, help="目标常读详情页 URL")
    parser.add_argument("--auth", type=str, default=str(DEFAULT_AUTH_PATH), help="auth.json 路径")
    parser.add_argument("--out", type=str, default=str(DEFAULT_DUMP_PATH), help="输出 dump.json 路径")
    args = parser.parse_args()

    run(
        target_url=args.url,
        auth_file=Path(args.auth).resolve(),
        output_file=Path(args.out).resolve()
    )


if __name__ == "__main__":
    main()
