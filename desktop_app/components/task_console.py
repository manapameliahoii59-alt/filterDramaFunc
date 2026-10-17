"""任务控制台：把「参数 → 运行 → 进度 → 日志」封装成一个可复用卡片。

页面只需提供一个命令构造器（返回命令行列表，或返回 None 表示参数校验未通过），
控制台负责线程调度、按钮状态、进度展示与日志回传。
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon as FIF,
    IndeterminateProgressBar,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
)

from desktop_app.components.log_view import LogView
from desktop_app.runtime.task_runner import EXIT_CANCELLED, TaskRunner

CommandProvider = Callable[[], Optional[list[str]]]

STATUS_READY = "就绪"
STATUS_RUNNING = "运行中…"
STATUS_DONE = "已完成"
STATUS_FAILED = "执行失败"
STATUS_CANCELLED = "已取消"


class TaskConsole(CardWidget):
    """带进度与实时日志的任务控制台。"""

    started = Signal()
    finished = Signal(int)
    line_received = Signal(str)

    def __init__(
        self,
        parent=None,
        *,
        title: str = "运行日志",
        run_text: str = "开始运行",
        min_log_height: int = 150,
    ):
        super().__init__(parent)
        self._runner: Optional[TaskRunner] = None
        self._provider: Optional[CommandProvider] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(StrongBodyLabel(title, self))

        self._status = BodyLabel(STATUS_READY, self)
        self._status.setTextColor("#5F5E5A", "#B4B2A9")
        head.addWidget(self._status)
        head.addStretch(1)

        self._stop_btn = PushButton(FIF.CANCEL, "停止", self)
        self._stop_btn.clicked.connect(lambda: self.stop())
        self._stop_btn.setEnabled(False)
        head.addWidget(self._stop_btn)

        self._run_btn = PrimaryPushButton(FIF.PLAY, run_text, self)
        self._run_btn.clicked.connect(lambda: self.run())
        head.addWidget(self._run_btn)
        layout.addLayout(head)

        self._busy = IndeterminateProgressBar(self)
        self._busy.hide()
        layout.addWidget(self._busy)

        self._bar = ProgressBar(self)
        self._bar.hide()
        layout.addWidget(self._bar)

        self._log = LogView(self, min_height=min_log_height)
        layout.addWidget(self._log)

    # ---- 配置 ----
    def set_command_provider(self, provider: CommandProvider) -> None:
        self._provider = provider

    def set_run_text(self, text: str) -> None:
        self._run_btn.setText(text)

    # ---- 状态 ----
    @property
    def is_running(self) -> bool:
        return self._runner is not None and self._runner.isRunning()

    def set_status(self, text: str, level: str = "info") -> None:
        palette = {
            "info": ("#5F5E5A", "#B4B2A9"),
            "ok": ("#0F6E56", "#5DCAA5"),
            "warn": ("#854F0B", "#EF9F27"),
            "error": ("#A32D2D", "#F09595"),
        }
        light, dark = palette.get(level, palette["info"])
        self._status.setText(text)
        self._status.setTextColor(light, dark)

    # ---- 日志 ----
    def log(self, text: str) -> None:
        self._log.append_line(text)

    def log_lines(self, lines: list[str]) -> None:
        self._log.append_lines(lines)

    def clear_log(self) -> None:
        self._log.clear()

    def log_text(self) -> str:
        return self._log.text()

    # ---- 进度 ----
    def set_progress(self, current: int, total: int) -> None:
        if total <= 0:
            return
        self._busy.stop()
        self._busy.hide()
        self._bar.show()
        self._bar.setRange(0, max(total, 1))
        self._bar.setValue(min(max(current, 0), total))

    # ---- 运行控制 ----
    def run(self, cmd: Optional[list[str]] = None) -> None:
        if self.is_running:
            return
        # 防御 Qt 信号自动传参（如 clicked 信号默认传递 bool 类型的 checked=False）
        if not isinstance(cmd, (list, tuple)):
            cmd = None
        if cmd is None:
            if self._provider is None:
                self.set_status("未配置命令", "error")
                return
            cmd = self._provider()
        if not cmd:
            return

        self._bar.hide()
        self._bar.setValue(0)
        self._busy.start()
        self._busy.show()

        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self.set_status(STATUS_RUNNING, "warn")
        self.log(f"$ {' '.join(cmd)}")

        runner = TaskRunner(cmd, parent=self)
        runner.line.connect(self._on_line)
        runner.failed.connect(self._on_failed)
        runner.done.connect(self._on_done)
        self._runner = runner
        self.started.emit()
        runner.start()

    def stop(self) -> None:
        if self._runner is None or not self._runner.isRunning():
            return
        self.set_status("正在停止…", "warn")
        self._runner.cancel()

    # ---- 内部回调 ----
    def _on_line(self, text: str) -> None:
        self._log.append_line(text)
        self.line_received.emit(text)

    def _on_failed(self, message: str) -> None:
        self._log.append_line(message)

    def _on_done(self, code: int) -> None:
        self._busy.stop()
        self._busy.hide()
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._runner = None

        if code == EXIT_CANCELLED:
            self.set_status(STATUS_CANCELLED, "warn")
            self._log.append_line("[已取消] 任务被手动中止")
        elif code == 0:
            self.set_status(STATUS_DONE, "ok")
            self._bar.show()
            self._bar.setRange(0, 1)
            self._bar.setValue(1)
        else:
            self.set_status(f"{STATUS_FAILED}（退出码 {code}）", "error")
            self._bar.hide()

        # 任务结束立刻把缓冲区刷出来，保证最后几行不延迟
        self._log.flush_now()
        self.finished.emit(code)
