"""
fetch_from_changdu.py
一键安全同步常读大盘剧目至本地「待筛剧目.txt」
复用工业级防风控 Stealth 补丁与 secsdk 签名就绪机制
"""
import os
import sys
import shutil
import argparse
from pathlib import Path

# 终端中文编码兼容
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from changdu_sync import (
    ChangduSyncClient,
    run_changdu_login,
    export_dramas_to_txt
)

DEFAULT_AUTH_FILE = Path(__file__).parent / "auth.json"
DEFAULT_OUTPUT_TXT = Path(__file__).parent / "待筛剧目.txt"
AUTOMATED_EDIT_AUTH = Path(r"C:\Users\Administrator\Desktop\project\AutomatedEdit\changdu_data\auth.json")


def ensure_auth_file(auth_path: Path) -> Path:
    """确保登录态文件存在；若本地不存在，尝试从已有的 AutomatedEdit 中只读复制"""
    if auth_path.is_file():
        return auth_path

    # 智能复用：如果 AutomatedEdit 已登录，直接单向只读复制到本地，无需重新打开浏览器登录
    if AUTOMATED_EDIT_AUTH.is_file():
        print(f"[提示] 检测到已存在常读登录凭据，正在单向复制至本地: {auth_path.name}")
        shutil.copy2(AUTOMATED_EDIT_AUTH, auth_path)
        return auth_path

    # 否则引导用户弹窗登录
    print("[提示] 本地未检测到常读登录态，准备启动浏览器引导登录...")
    run_changdu_login(auth_path)
    return auth_path


def main():
    parser = argparse.ArgumentParser(description="一键安全拉取常读短剧大盘剧目到待筛文本")
    parser.add_argument("--login", action="store_true", help="强制重新启动浏览器登录常读平台")
    parser.add_argument("--limit", type=int, default=20, help="拉取剧目数量 (默认 20 部)")
    parser.add_argument("--days", type=int, default=30, help="拉取近多少天内上架的剧目 (默认 30 天)")
    parser.add_argument("--append", action="store_true", help="追加写入而非覆盖已有的待筛剧目.txt")
    parser.add_argument("--output", "-o", type=str, default=str(DEFAULT_OUTPUT_TXT), help="导出 txt 文件路径")
    parser.add_argument("--headless", action="store_true", default=True, help="是否无头后台运行浏览器")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="显示浏览器窗口运行")
    parser.add_argument("--auth-file", type=str, default=None,
                        help="登录态保存路径（默认 auth.json；多账号建议放 auth_pool/auth_xxx.json）")
    args = parser.parse_args()

    auth_file = Path(args.auth_file).resolve() if args.auth_file else DEFAULT_AUTH_FILE

    # 1. 显式要求重新登录
    if args.login:
        run_changdu_login(auth_file)
        print("登录完成！现在可以开始拉取剧目。")
        return

    # 2. 检查或准备凭证
    try:
        ensure_auth_file(auth_file)
    except Exception as e:
        print(f"[错误] 登录初始化失败: {e}")
        sys.exit(1)

    print("=" * 65)
    print("  常读平台大盘剧目安全同步工具")
    print(f"  拉取参数: 近 {args.days} 天 | 目标数量: {args.limit} 部")
    print("=" * 65)

    # 3. 启动安全客户端拉取
    try:
        print("正在启动拟真环境连接常读平台（等待 secsdk 签名就绪）...")
        with ChangduSyncClient(auth_file=auth_file, headless=args.headless) as client:
            print(f"安全通道已建立，正在从常读大盘拉取前 {args.limit} 部最新剧目...")
            dramas = client.fetch_dramas(limit=args.limit, days=args.days)
            print(f"成功获取常读大盘剧目: {len(dramas)} 部！")

            # 4. 格式化写入 txt 文件
            out_path = Path(args.output).resolve()
            count = export_dramas_to_txt(dramas, out_path, append=args.append)

            print("\n" + "=" * 65)
            print("  同步完成！")
            print("=" * 65)
            print(f"  - 成功写入文件: {out_path.name} ({count} 部剧目，换行符分隔)")
            print(f"  - 完整存储路径: {out_path}")
            print("-" * 65)
            print("  [下一步提示]：")
            print("  现在你可以直接运行以下命令进行极速负向粗筛一票否决：")
            print("  >>> python main.py")
            print("=" * 65)

    except Exception as e:
        print(f"\n[同步失败] {e}")
        print("提示：若提示登录态失效，可运行 'python fetch_from_changdu.py --login' 重新登录。")
        sys.exit(1)


if __name__ == "__main__":
    main()
