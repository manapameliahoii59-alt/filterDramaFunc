"""接口录制（高级工具）：对应「5_录制常读页面接口.bat」。"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    LineEdit,
    PushButton,
    StrongBodyLabel,
)

from desktop_app.common.constants import REQUEST_DUMP, SCRIPT_RECORD, build_cmd
from desktop_app.common.shell import file_size_text, mtime_text, reveal
from desktop_app.components.base_page import BasePage
from desktop_app.components.busy import guard_action
from desktop_app.components.metric_card import MetricCard
from desktop_app.components.task_console import TaskConsole


class RecorderPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "recorderPage",
            "接口录制",
            "低频开发工具：录制常读详情页的真实网络请求，用于分析接口与分页参数。"
            "等价于双击「5_录制常读页面接口.bat」。",
            parent,
        )
        self.add_widget(self._build_params())
        self.add_layout(self._build_metrics())

        self.console = TaskConsole(
            self, title="录制运行日志", run_text="开始录制", min_log_height=150
        )
        self.console.set_command_provider(self._build_command)
        self.console.finished.connect(lambda _code: self._refresh_metrics())
        self.add_widget(self.console, 1)

        self._refresh_metrics()

    def _build_params(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(BodyLabel("目标详情页 URL", card))
        self.url = LineEdit(card)
        self.url.setPlaceholderText("留空则使用脚本内置的默认详情页地址")
        self.url.setClearButtonEnabled(True)
        row.addWidget(self.url, 1)
        layout.addLayout(row)

        tip = CaptionLabel(
            "录制需要有效的常读登录态；未登录请先到「常读账号」页登录。"
            "输出为结构化 JSON，可直接用于分析未混淆的响应结构。",
            card,
        )
        tip.setWordWrap(True)
        tip.setTextColor("#5F5E5A", "#B4B2A9")
        layout.addWidget(tip)
        return card

    def _build_metrics(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self.m_size = MetricCard("dump 大小")
        self.m_updated = MetricCard("最后录制")
        row.addWidget(self.m_size, 1)
        row.addWidget(self.m_updated, 1)

        self.open_btn = PushButton(FIF.VIEW, "定位 dump 文件", self)
        self.open_btn.clicked.connect(
            guard_action(self.open_btn, lambda: reveal(REQUEST_DUMP))
        )
        row.addWidget(self.open_btn)
        return row

    def _refresh_metrics(self) -> None:
        self.m_size.set_value(file_size_text(REQUEST_DUMP))
        self.m_updated.set_value(mtime_text(REQUEST_DUMP))

    def _build_command(self) -> list[str] | None:
        url = self.url.text().strip()
        if url:
            return build_cmd(SCRIPT_RECORD, "--url", url)
        return build_cmd(SCRIPT_RECORD)
