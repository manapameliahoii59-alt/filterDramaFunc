"""同步操作的忙碌反馈：防止重复点击，并让"正在处理"立刻可见。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


@contextmanager
def wait_cursor():
    """在耗时同步操作期间显示等待光标。"""
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def guard_action(button, action: Callable) -> Callable:
    """把按钮点击包成「禁用 → 执行 → 恢复」。

    作用：
    1. 防止快速连点造成重复执行（重复导出、重复刷新）；
    2. 立即刷新一次事件循环，让按钮的禁用态可见，
       避免出现"点了没反应"的错觉。
    """

    def _handler(*args, **kwargs):
        if not button.isEnabled():
            return None
        button.setEnabled(False)
        # 强制重绘一次，让禁用状态在耗时操作开始前就显示出来
        QApplication.processEvents()
        try:
            return action(*args, **kwargs)
        finally:
            button.setEnabled(True)

    return _handler
