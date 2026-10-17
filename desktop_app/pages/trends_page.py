"""小说风向标：对应「4_更新小说风向标与AI分析.bat」。"""

from __future__ import annotations

import json

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    PushButton,
    StrongBodyLabel,
)

from desktop_app.common.constants import (
    NOVEL_KB,
    PROJECT_ROOT,
    SCRIPT_TRENDS,
    build_cmd,
)
from desktop_app.common.shell import file_size_text, mtime_text, open_path, read_text_safe
from desktop_app.components.base_page import BasePage
from desktop_app.components.busy import guard_action
from desktop_app.components.metric_card import MetricCard
from desktop_app.components.task_console import TaskConsole
from desktop_app.runtime.function_runner import run_in_background


def _count_kb_books() -> int | None:
    """统计知识库条目数（兼容 dict / list 两种结构）。"""
    raw = read_text_safe(NOVEL_KB, max_chars=8_000_000)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        books = data.get("books")
        return len(books) if isinstance(books, (list, dict)) else len(data)
    if isinstance(data, list):
        return len(data)
    return None


def _read_kb_stats() -> dict:
    """后台线程：读取知识库统计信息（不触碰任何界面对象）。

    知识库会随榜单同步不断变大（当前 1MB+），放在 UI 线程读取会在
    数据量增长后造成可感知的卡顿，因此统一走后台线程。
    """
    return {
        "size": file_size_text(NOVEL_KB),
        "updated": mtime_text(NOVEL_KB),
        "count": _count_kb_books(),
        "exists": NOVEL_KB.exists(),
    }


class TrendsPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "trendsPage",
            "小说风向标",
            "直连起点 / 纵横 / 晋江 / 潇湘四大榜单，增量调用 AI 提炼畅销母题。"
            "已分析过的书籍自动复用缓存，不重复消耗 Token。"
            "等价于双击「4_更新小说风向标与AI分析.bat」。",
            parent,
        )
        self.add_widget(self._build_status())

        self.console = TaskConsole(
            self, title="风向标同步日志", run_text="开始更新与分析", min_log_height=150
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
        head.addWidget(StrongBodyLabel("本地知识库", card))
        self.hint = CaptionLabel("", card)
        self.hint.setTextColor("#5F5E5A", "#B4B2A9")
        head.addWidget(self.hint)
        head.addStretch(1)

        folder_btn = PushButton(FIF.FOLDER, "打开数据目录", card)
        folder_btn.clicked.connect(
            guard_action(folder_btn, lambda: open_path(NOVEL_KB.parent))
        )
        head.addWidget(folder_btn)
        layout.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.m_books = MetricCard("已收录书籍", accent="#1D9E75")
        self.m_size = MetricCard("知识库大小")
        self.m_updated = MetricCard("最后更新")
        for item in (self.m_books, self.m_size, self.m_updated):
            row.addWidget(item, 1)
        layout.addLayout(row)

        tip = CaptionLabel(
            "榜单结果会写入 novel_trends/data/，粗筛阶段会自动叠加「命中顶流母题」的趋势加分。",
            card,
        )
        tip.setWordWrap(True)
        tip.setTextColor("#5F5E5A", "#B4B2A9")
        layout.addWidget(tip)
        return card

    def _refresh_status(self) -> None:
        """后台读取知识库，回到主线程刷新指标卡。"""
        self.hint.setText("正在读取知识库…")
        run_in_background(
            self,
            _read_kb_stats,
            on_success=self._apply_status,
            on_error=lambda msg: self.hint.setText(f"读取失败：{msg}"),
        )

    def _apply_status(self, stats: dict) -> None:
        self.m_size.set_value(stats.get("size") or "—")
        self.m_updated.set_value(stats.get("updated") or "—")
        count = stats.get("count")
        self.m_books.set_value(f"{count} 本" if count is not None else "—")
        if stats.get("exists"):
            self.hint.setText(f"位于 {NOVEL_KB.relative_to(PROJECT_ROOT)}")
        else:
            self.hint.setText("尚未生成，运行一次后自动创建")

    def _build_command(self) -> list[str] | None:
        return build_cmd(SCRIPT_TRENDS)
