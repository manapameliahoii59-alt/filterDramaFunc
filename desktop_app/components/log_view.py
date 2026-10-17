"""实时日志面板：批量刷新 + 分级着色 + 自动滚动。

性能要点（避免界面假死）
- 子进程输出是突发式的：管道缓冲会让成百上千行在同一瞬间到达。
  如果每行都立刻重绘，UI 线程会被连续占用数百毫秒。
- 因此这里把日志先攒进缓冲区，用定时器按固定节奏（默认 80ms）合并刷新，
  缓冲区过大时立刻刷新，保证既不卡顿也不延迟。
"""

from __future__ import annotations

import html
import time
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    PlainTextEdit,
    TransparentToolButton,
)

# 分级着色（中间调，亮/暗主题下都清晰）
COLOR_ERROR = "#E24B4A"
COLOR_WARN = "#EF9F27"
COLOR_OK = "#1D9E75"

_ERROR_MARKS = ("错误", "失败", "异常", "traceback", "error", "[err]", "否决", "已拦截")
_WARN_MARKS = ("⚠", "警告", "降级", "波动", "未检测到", "跳过", "重试")
_OK_MARKS = ("✓", "成功", "完成", "🏆", "已通过", "已写入", "就绪")


def classify(text: str) -> str | None:
    """按关键字判定日志级别，返回着色；返回 None 表示使用默认文字色。"""
    low = text.lower()
    if any(m in low for m in _ERROR_MARKS):
        return COLOR_ERROR
    if any(m in low for m in _WARN_MARKS):
        return COLOR_WARN
    if any(m in low for m in _OK_MARKS):
        return COLOR_OK
    return None


class LogView(QWidget):
    """可复用的日志显示控件（带缓冲合并刷新）。"""

    MAX_BLOCKS = 4000
    # 合并刷新间隔：越大越省 CPU，但日志到达观感越"顿"
    FLUSH_INTERVAL_MS = 40
    # 单次刷新允许占用 UI 线程的时间上限（毫秒）。
    # 这是防止"突发几千行把界面卡死"的关键：每次只写一小批，
    # 写完把剩余的交回事件循环，下一拍继续，界面始终保持可响应。
    FLUSH_BUDGET_MS = 20.0
    # 单次刷新写入行数硬上限（兜底，防止极短行刷太多）
    MAX_LINES_PER_FLUSH = 600
    # 缓冲超过该行数立刻安排一次刷新（不再无限等定时器）
    MAX_BUFFER = 800

    def __init__(self, parent=None, min_height: int = 150):
        super().__init__(parent)
        self._auto_scroll = True
        self._pending: deque[str] = deque()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        self._hint = BodyLabel("任务输出会实时显示在这里", self)
        self._hint.setTextColor("#888780", "#B4B2A9")
        bar.addWidget(self._hint)
        bar.addStretch(1)

        self._scroll_btn = TransparentToolButton(FIF.SCROLL, self)
        self._scroll_btn.setToolTip("自动滚动：开")
        self._scroll_btn.clicked.connect(self._toggle_auto_scroll)

        self._copy_btn = TransparentToolButton(FIF.COPY, self)
        self._copy_btn.setToolTip("复制全部日志")
        self._copy_btn.clicked.connect(self.copy_all)

        self._save_btn = TransparentToolButton(FIF.SAVE, self)
        self._save_btn.setToolTip("导出日志到文件")
        self._save_btn.clicked.connect(self.save_to_file)

        self._clear_btn = TransparentToolButton(FIF.DELETE, self)
        self._clear_btn.setToolTip("清空日志")
        self._clear_btn.clicked.connect(self.clear)

        for btn in (self._scroll_btn, self._copy_btn, self._save_btn, self._clear_btn):
            bar.addWidget(btn)
        layout.addLayout(bar)

        self._edit = PlainTextEdit(self)
        self._edit.setReadOnly(True)
        self._edit.setMinimumHeight(min_height)
        self._edit.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self._edit.document().setMaximumBlockCount(self.MAX_BLOCKS)
        self._edit.setPlaceholderText("（暂无输出）")
        layout.addWidget(self._edit)

        # 合并刷新定时器
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(self.FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._on_flush_tick)

    # ---- 对外接口 ----
    def append_line(self, text: str) -> None:
        self._pending.append(text)
        self._schedule_flush()

    def append_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        self._pending.extend(lines)
        self._schedule_flush()

    def flush_now(self, max_batches: int = 1) -> None:
        """尽量把积压刷出来，但限制批数，避免长时间占用 UI 线程。

        剩余积压会继续由定时器消化，因此这里不会造成可感知的卡顿。
        """
        self._flush_timer.stop()
        for _ in range(max(1, int(max_batches))):
            if not self._flush():
                break
        if self._pending and not self._flush_timer.isActive():
            self._flush_timer.start()

    def drain_all(self) -> None:
        """把积压全部写完。

        这是**阻塞调用**，仅在用户明确要求完整文本时使用（复制 / 导出日志），
        不要用在任务回调里。
        """
        self._flush_timer.stop()
        while self._flush():
            pass

    def clear(self) -> None:
        self._flush_timer.stop()
        self._pending.clear()
        self._edit.clear()
        self._hint.setText("任务输出会实时显示在这里")

    def text(self) -> str:
        self.drain_all()
        return self._edit.toPlainText()

    def line_count(self) -> int:
        return self._edit.blockCount() + len(self._pending)

    def copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self.text())

    def save_to_file(self) -> None:
        from qfluentwidgets import MessageBox

        from desktop_app.common.constants import PROJECT_ROOT

        target = PROJECT_ROOT / "logs" / "desktop_task.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(self.text(), encoding="utf-8")
        except OSError as exc:
            MessageBox("导出失败", f"写入日志失败：{exc}", self.window()).exec()
            return
        MessageBox("导出成功", f"日志已保存到：\n{target}", self.window()).exec()

    # ---- 内部 ----
    def _schedule_flush(self) -> None:
        if len(self._pending) >= self.MAX_BUFFER:
            # 积压过多：立刻安排一次（受时间预算限制，不会长时间卡住）
            self._flush_timer.stop()
            self._on_flush_tick()
        elif not self._flush_timer.isActive():
            self._flush_timer.start()

    def _on_flush_tick(self) -> None:
        if self._flush() and not self._flush_timer.isActive():
            # 还有积压，下一拍继续
            self._flush_timer.start()

    def _flush(self) -> bool:
        """写入一批日志，返回是否仍有积压。

        关键：单次写入受 FLUSH_BUDGET_MS 时间预算限制。
        子进程一次涌来几千行时，每拍只写一小批就把控制权交回事件循环，
        界面始终保持可响应，不会出现"点击后假死"。
        """
        if not self._pending:
            return False

        budget = self.FLUSH_BUDGET_MS / 1000.0
        started = time.perf_counter()
        default_color = self._default_color()
        written = 0

        # 批量写入时关闭重绘，避免每行触发一次布局与重绘
        self._edit.setUpdatesEnabled(False)
        try:
            append = self._edit.appendHtml
            render = self._render
            pending = self._pending
            while pending and written < self.MAX_LINES_PER_FLUSH:
                append(render(pending.popleft(), default_color))
                written += 1
                if (time.perf_counter() - started) >= budget:
                    break
        finally:
            self._edit.setUpdatesEnabled(True)

        self._hint.setText(f"共 {self._edit.blockCount()} 行输出")
        if self._auto_scroll:
            self._scroll_to_end()
        return bool(self._pending)

    def _render(self, text: str, default_color: str | None = None) -> str:
        color = classify(text) or default_color or self._default_color()
        safe = html.escape(text) if text else "&nbsp;"
        return f'<span style="color:{color};white-space:pre;">{safe}</span>'

    def _default_color(self) -> str:
        return self._edit.palette().color(QPalette.ColorRole.Text).name()

    def _scroll_to_end(self) -> None:
        bar = self._edit.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _toggle_auto_scroll(self) -> None:
        self._auto_scroll = not self._auto_scroll
        self._scroll_btn.setToolTip(f"自动滚动：{'开' if self._auto_scroll else '关'}")
        if self._auto_scroll:
            self._scroll_to_end()

    def wheelEvent(self, event):  # noqa: N802 - Qt 命名
        # 用户手动上滚时自动暂停跟随
        if event.angleDelta().y() > 0:
            self._auto_scroll = False
            self._scroll_btn.setToolTip("自动滚动：关")
        super().wheelEvent(event)
