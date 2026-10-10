"""
login.py
常读平台交互式登录与登录态保存工具
打开带 Stealth 补丁的浏览器供用户登录，登录成功后自动将 Cookie/LocalStorage 保存至本地 auth.json
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright
from .stealth import (
    HOME_URL,
    LAUNCH_ARGS,
    STEALTH_INIT_SCRIPT,
    browser_context_kwargs
)

SALE_URL_PATTERN = "**/sale/**"


def run_changdu_login(auth_file: Path | str) -> str:
    """
    打开真实浏览器供用户登录常读平台，登录成功后将持久化状态保存到 auth_file
    """
    target_path = Path(auth_file).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("正在启动 Chrome 浏览器，请在弹出的常读平台窗口中完成登录...")
    print("（登录成功跳转至商家后台后，窗口将自动保存登录态并关闭）")
    print("=" * 60)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=False,
                args=list(LAUNCH_ARGS),
            )
            context = browser.new_context(**browser_context_kwargs())
            page = context.new_page()
            page.add_init_script(STEALTH_INIT_SCRIPT)
            
            page.goto(HOME_URL, timeout=60_000)

            # 尝试自动触发“登录/注册”弹窗
            try:
                trigger = page.get_by_text(re.compile(r"登录\s*/\s*注册"))
                if trigger.count() > 0:
                    trigger.first.click(timeout=5_000)
            except Exception:
                pass

            # 等待用户完成登录并跳转进入后台
            print("等待用户登录跳转中...")
            page.wait_for_url(SALE_URL_PATTERN, timeout=0)
            
            # 保存登录态
            context.storage_state(path=str(target_path))
            browser.close()

            print(f"\n[成功] 常读平台登录态已持久化保存至: {target_path.name}")
            return str(target_path)

    except Exception as exc:
        msg = str(exc)
        if "TargetClosed" in msg or "BrowserClosed" in msg or "closed" in msg.lower():
            raise RuntimeError("浏览器窗口被提前关闭，登录未完成。") from exc
        raise
