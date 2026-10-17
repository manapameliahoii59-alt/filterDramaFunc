"""常读账号管理：对应「1_常读平台登录.bat」。"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    PushButton,
    PrimaryPushButton,
    StrongBodyLabel,
)

from desktop_app.common.constants import (
    AUTH_FILE,
    AUTH_POOL_DIR,
    PROJECT_ROOT,
    SCRIPT_FETCH,
    build_cmd,
)
from desktop_app.common.shell import mtime_text, open_path
from desktop_app.components.base_page import BasePage
from desktop_app.components.busy import guard_action
from desktop_app.components.metric_card import MetricCard
from desktop_app.components.task_console import TaskConsole


class AccountPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "accountPage",
            "常读账号",
            "维护常读平台登录态。登录凭证保存在项目根目录的 auth.json，"
            "多账号可放入 auth_pool 目录。等价于双击「1_常读平台登录.bat」。",
            parent,
        )
        self.add_widget(self._build_status())

        self.console = TaskConsole(
            self, title="登录 / 检测日志", run_text="打开浏览器登录", min_log_height=150
        )
        self.console.set_command_provider(self._build_command)
        self.console.finished.connect(lambda _code: self._refresh_status())
        self.add_widget(self.console, 1)

        self._refresh_status()

    def _build_status(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(StrongBodyLabel("登录状态", card))
        self.hint = CaptionLabel("", card)
        self.hint.setTextColor("#5F5E5A", "#B4B2A9")
        head.addWidget(self.hint)
        head.addStretch(1)

        refresh_btn = PushButton(FIF.SYNC, "刷新状态", card)
        refresh_btn.clicked.connect(guard_action(refresh_btn, self._refresh_status))
        head.addWidget(refresh_btn)

        folder_btn = PushButton(FIF.FOLDER, "打开项目目录", card)
        folder_btn.clicked.connect(
            guard_action(folder_btn, lambda: open_path(PROJECT_ROOT))
        )
        head.addWidget(folder_btn)
        layout.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.m_state = MetricCard("登录态")
        self.m_updated = MetricCard("凭证更新时间")
        self.m_pool = MetricCard("账号池")
        self.m_size = MetricCard("凭证大小")
        for item in (self.m_state, self.m_updated, self.m_pool, self.m_size):
            row.addWidget(item, 1)
        layout.addLayout(row)

        tip = CaptionLabel(
            "提示：首次使用或登录过期时点「打开浏览器登录」，扫码完成后凭证会自动写入 auth.json。"
            "若 AutomatedEdit 已登录，同步脚本会自动复用其凭证，无需重复扫码。",
            card,
        )
        tip.setWordWrap(True)
        tip.setTextColor("#5F5E5A", "#B4B2A9")
        layout.addWidget(tip)
        return card

    def _refresh_status(self) -> None:
        if AUTH_FILE.is_file():
            size_kb = AUTH_FILE.stat().st_size / 1024.0
            self.m_state.set_value("已登录")
            self.m_state.set_accent("#1D9E75")
            self.m_updated.set_value(mtime_text(AUTH_FILE))
            self.m_size.set_value(f"{size_kb:.1f} KB")
        else:
            self.m_state.set_value("未登录")
            self.m_state.set_accent("#E24B4A")
            self.m_updated.set_value("—")
            self.m_size.set_value("—")

        pool_count = 0
        if AUTH_POOL_DIR.is_dir():
            pool_count = len(list(AUTH_POOL_DIR.glob("*.json")))
        self.m_pool.set_value(f"{pool_count} 个")

        if AUTH_FILE.is_file():
            self.hint.setText("可直接开始同步与粗筛")
        else:
            self.hint.setText("需要先登录，否则只能走纯文本离线模式")

    def _build_command(self) -> list[str] | None:
        return build_cmd(SCRIPT_FETCH, "--login")
