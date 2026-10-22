"""小说风向标：对应「4_更新小说风向标与AI分析.bat」。"""

from __future__ import annotations

import json

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    PushButton,
    SpinBox,
    StrongBodyLabel,
)

from desktop_app.common.constants import (
    NOVEL_KB,
    PROJECT_ROOT,
    SCRIPT_CRAWL_MISSING,
    SCRIPT_TRENDS,
    build_cmd,
)
from novel_trends.config import DEFAULT_CHAPTER_COUNT, DEFAULT_TITLE_COUNT
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


def _read_missing_stats() -> dict:
    """后台线程：统计数据库中还没有完整章节记录（正文不足或标题未达标）的书籍数量。"""
    from novel_trends.pg_storage import PgNovelStorage

    pg = PgNovelStorage()
    pg.init_content_tables()
    return {
        "total": pg.count_missing_chapters(DEFAULT_CHAPTER_COUNT, DEFAULT_TITLE_COUNT),
        "by_platform": pg.count_missing_chapters_by_platform(DEFAULT_CHAPTER_COUNT, DEFAULT_TITLE_COUNT),
    }


class TrendsPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "trendsPage",
            "小说风向标",
            "直连起点 / 纵横 / 晋江 / 潇湘四大榜单，抓取前 50 章标题（连载不足有多少抓多少）、免费正文与简介入库"
            "（当前不调用 AI 提炼维度）。",
            parent,
        )
        self.add_widget(self._build_status())
        self.add_widget(self._build_options())

        self.console = TaskConsole(
            self, title="风向标同步日志", run_text="更新榜单并抓取内容", min_log_height=150
        )
        self.console.set_command_provider(self._build_command)
        self.console.finished.connect(lambda _code: self._refresh_status())
        self.console.finished.connect(lambda _code: self._refresh_missing())
        self.add_widget(self.console, 1)

        self._refresh_status()
        self._refresh_missing()

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
        self.m_missing = MetricCard("待补抓章节", accent="#EF9F27")
        for item in (self.m_books, self.m_size, self.m_updated, self.m_missing):
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

    def _build_options(self) -> CardWidget:
        card = CardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        layout.addWidget(BodyLabel("每个网站最多抓取", card))
        self.content_limit = SpinBox(card)
        self.content_limit.setRange(0, 5000)
        self.content_limit.setValue(20)
        self.content_limit.setSuffix(" 本")
        self.content_limit.setToolTip(
            "每个小说网站每次最多抓取多少本书；0 表示各站全部（耗时很长，谨慎使用）"
        )
        layout.addWidget(self.content_limit)

        layout.addWidget(BodyLabel("章节标题抓取", card))
        self.title_limit = SpinBox(card)
        self.title_limit.setRange(1, 200)
        self.title_limit.setValue(DEFAULT_TITLE_COUNT)
        self.title_limit.setSuffix(" 章")
        self.title_limit.setToolTip(
            "每本书抓取章节标题数量（默认 50 章；如果新书连载不满 50 章，有多少抓多少）"
        )
        layout.addWidget(self.title_limit)

        self.missing_btn = PushButton(FIF.SYNC, "补抓缺失章节", card)
        self.missing_btn.setToolTip("扫描数据库中缺失正文或标题的书，按上面的批量上限补抓")
        self.missing_btn.clicked.connect(guard_action(self.missing_btn, self._crawl_missing))
        layout.addWidget(self.missing_btn)

        self.titles_only_btn = PushButton(FIF.UPDATE, "仅补齐目录标题", card)
        self.titles_only_btn.setToolTip("快速扫描未满50章标题的书籍，只抓取目录标题（跳过正文，速度极快）")
        self.titles_only_btn.clicked.connect(guard_action(self.titles_only_btn, self._crawl_titles_only))
        layout.addWidget(self.titles_only_btn)
        layout.addStretch(1)

        tip = CaptionLabel("主按钮只更新榜单+抓内容（不调用 AI）；0 = 全部", card)
        tip.setTextColor("#5F5E5A", "#B4B2A9")
        layout.addWidget(tip)
        return card

    def _crawl_missing(self) -> None:
        """扫描数据库缺失章节的书并按批量上限补抓（复用当前日志控制台）。"""
        cmd = build_cmd(
            SCRIPT_CRAWL_MISSING,
            "--limit", str(self.content_limit.value()),
            "--titles", str(self.title_limit.value()),
        )
        self.console.run(cmd)

    def _crawl_titles_only(self) -> None:
        """快速补全数据库中章节标题不满 50 章的书籍目录（跳过正文下载）。"""
        cmd = build_cmd(
            SCRIPT_CRAWL_MISSING,
            "--limit", str(self.content_limit.value()),
            "--titles", str(self.title_limit.value()),
            "--titles-only",
        )
        self.console.run(cmd)

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

    def _refresh_missing(self) -> None:
        """后台统计「数据库中还没有章节记录」的书籍数量。"""
        self.m_missing.set_value("…")
        run_in_background(
            self,
            _read_missing_stats,
            on_success=self._apply_missing_stats,
            on_error=lambda _msg: self.m_missing.set_value("—"),
        )

    def _apply_missing_stats(self, stats: dict) -> None:
        total = stats.get("total")
        self.m_missing.set_value(f"{total} 本" if total is not None else "—")
        by = stats.get("by_platform") or {}
        tip = f"待补抓章节（正文不足 {DEFAULT_CHAPTER_COUNT} 章且未抓完）"
        if by:
            breakdown = "、".join(f"{p} {n}" for p, n in by.items())
            self.m_missing.setToolTip(f"{tip}\n{breakdown}")
        else:
            self.m_missing.setToolTip(tip)

    def _build_command(self) -> list[str] | None:
        # 只更新榜单 + 抓内容，不调用大模型（--skip-ai）
        return build_cmd(
            SCRIPT_TRENDS,
            "--with-content",
            "--skip-ai",
            "--content-limit", str(self.content_limit.value()),
            "--titles", str(self.title_limit.value()),
        )
