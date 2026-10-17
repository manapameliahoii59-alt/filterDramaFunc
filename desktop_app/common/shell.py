"""与操作系统交互的小工具（打开目录、安全读取文件）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def open_path(path: Path) -> None:
    """用系统默认方式打开文件或目录。"""
    target = Path(path)
    if not target.exists():
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # noqa: S606 - Windows 专用
        elif sys.platform == "darwin":
            subprocess.run(["open", str(target)], check=False)
        else:
            subprocess.run(["xdg-open", str(target)], check=False)
    except OSError:
        pass


def reveal(path: Path) -> None:
    """在资源管理器中定位并选中该文件。"""
    target = Path(path)
    if not target.exists():
        open_path(target.parent)
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["explorer", "/select,", str(target)], check=False)
        else:
            open_path(target.parent)
    except OSError:
        open_path(target.parent)


def read_json_safe(path: Path) -> dict[str, Any] | None:
    """读取 JSON，失败返回 None（不抛异常，避免界面崩溃）。"""
    target = Path(path)
    if not target.is_file():
        return None
    try:
        with target.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_text_safe(path: Path, max_chars: int = 20000) -> str:
    """读取文本，失败返回空串。"""
    target = Path(path)
    if not target.is_file():
        return ""
    try:
        text = target.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return text[:max_chars]


def file_size_text(path: Path) -> str:
    """人类可读的文件大小。"""
    target = Path(path)
    if not target.is_file():
        return "—"
    size = target.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def mtime_text(path: Path) -> str:
    """文件最后修改时间。"""
    target = Path(path)
    if not target.is_file():
        return "—"
    from datetime import datetime

    return datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
