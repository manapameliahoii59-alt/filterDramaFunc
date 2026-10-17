"""粗筛工作台：对应「2_执行粗筛主程序.bat」。

读取项目根目录下的待筛 txt → 调用 main.py → 展示 S/A/B/C 分级结果。
"""

from __future__ import annotations

import csv
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    FluentIcon as FIF,
    PushButton,
    StrongBodyLabel,
    TableWidget,
    TransparentToolButton,
)

from desktop_app.common.constants import (
    PROJECT_ROOT,
    REPORT_JSON,
    SCRIPT_MAIN,
    build_cmd,
    count_non_empty_lines,
    list_candidate_txt_files,
)
from desktop_app.common.shell import open_path, read_json_safe, reveal
from desktop_app.components.base_page import BasePage
from desktop_app.components.busy import guard_action
from desktop_app.components.metric_card import MetricCard
from desktop_app.components.task_console import TaskConsole
from desktop_app.runtime.function_runner import run_in_background

TIER_ACCENT: dict[str, object] = {
    "S": "#BA7517",
    "A": "#1D9E75",
    "B": ("#5F5E5A", "#B4B2A9"),
    "C": "#E24B4A",
}

_START_RE = re.compile(r"正在开始筛选剧目[:：]\s*《(.+?)》")

COLUMNS = ("剧名", "分级", "综合分", "题材", "AI 点评", "诊断")
# ⚠️ qfluentwidgets 的 TableItemDelegate 渲染时会给单元格额外留约 36px 水平空间
#    （原生 QTableWidget 没有这个开销），因此列宽必须按「文本宽 + 36」估算，
#    否则内容会被静默省略成 "6..."。下面的宽度已按此规律核算：
#    分级 "S"=8px、综合分 "63.8"=27px，均能完整显示；长文本列超出部分走 tooltip。
COLUMN_WIDTHS = (148, 52, 80, 100, 150, 115)

# CSV 导出用完整字段（表格为窄窗口做了取舍，导出不丢信息）
CSV_COLUMNS = (
    ("剧名", "title"),
    ("分级", "tier"),
    ("综合分", "final_score"),
    ("阶段一", "stage1_score"),
    ("题材", "genre_summary"),
    ("AI 点评", "ai_review"),
    ("诊断", "diagnostic_message"),
    ("是否PPT漫", "is_ppt"),
    ("PPT刚体率", "ppt_ratio"),
    ("命中缓存", "from_cache"),
    ("常读ID", "book_id"),
    ("集数", "episode_amount"),
)


def _read_report_payload() -> dict | None:
    """在后台线程读取并解析粗筛报告。

    单独提成模块级函数，确保后台线程里不触碰任何界面对象。
    """
    return read_json_safe(REPORT_JSON)


class _NumberItem(QTableWidgetItem):
    """按数值排序的表格单元格。"""

    def __init__(self, value: float, text: str | None = None):
        super().__init__(text if text is not None else f"{value:.1f}")
        self.setData(Qt.ItemDataRole.UserRole, float(value))
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def __lt__(self, other):  # noqa: D105
        try:
            return float(self.data(Qt.ItemDataRole.UserRole)) < float(
                other.data(Qt.ItemDataRole.UserRole)
            )
        except (TypeError, ValueError):
            return super().__lt__(other)


class ScreeningPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "screeningPage",
            "粗筛工作台",
            "读取 txt 剧单 → 负向一票否决粗筛 → 输出 S/A/B/C 分级。"
            "等价于双击「2_执行粗筛主程序.bat」。",
            parent,
        )
        self._total = 0
        self._current = 0
        self._report_rows: list = []

        self.add_widget(self._build_params())
        self.add_widget(self._build_metrics())

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.setChildrenCollapsible(False)

        self.console = TaskConsole(
            splitter, title="粗筛运行日志", run_text="开始粗筛", min_log_height=120
        )
        self.console.set_command_provider(self._build_command)
        self.console.line_received.connect(self._on_line)
        self.console.finished.connect(self._on_finished)
        splitter.addWidget(self.console)

        splitter.addWidget(self._build_results())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        self.add_widget(splitter, 1)

        self._reload_files()
        self._load_report()

    # ------------------------------------------------------------------ 参数区
    def _build_params(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(BodyLabel("待筛剧单", card))
        self.file_combo = ComboBox(card)
        self.file_combo.setMinimumWidth(320)
        self.file_combo.currentIndexChanged.connect(self._on_file_changed)
        row1.addWidget(self.file_combo)
        refresh_btn = TransparentToolButton(FIF.SYNC, card)
        refresh_btn.setToolTip("刷新文件列表")
        refresh_btn.clicked.connect(guard_action(refresh_btn, self._reload_files))
        row1.addWidget(refresh_btn)
        self.file_hint = CaptionLabel("", card)
        self.file_hint.setTextColor("#5F5E5A", "#B4B2A9")
        row1.addWidget(self.file_hint)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(BodyLabel("精筛阈值", card))
        self.threshold = DoubleSpinBox(card)
        self.threshold.setRange(0.0, 100.0)
        self.threshold.setSingleStep(1.0)
        self.threshold.setDecimals(1)
        self.threshold.setValue(55.0)
        self.threshold.setMinimumWidth(140)
        row2.addWidget(self.threshold)
        hint = CaptionLabel("阶段一得分低于此值的剧直接淘汰，不再拉取视频（默认 55）", card)
        hint.setTextColor("#5F5E5A", "#B4B2A9")
        row2.addWidget(hint)
        row2.addStretch(1)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(16)
        self.skip_cache = CheckBox("强制穿透缓存", card)
        self.skip_cache.setToolTip("忽略 14 天本地缓存，强制重新请求常读与 AI")
        self.no_video = CheckBox("纯文本模式", card)
        self.no_video.setToolTip("跳过第 1 集视频采样与光流检测，速度更快但漏检 PPT 漫")
        row3.addWidget(self.skip_cache)
        row3.addWidget(self.no_video)
        row3.addStretch(1)
        layout.addLayout(row3)

        return card

    def _build_metrics(self) -> QWidget:
        """指标卡按 4 列网格排布。

        窗口收窄后单行放不下 8 张卡，用 2 行 × 4 列可以保证每张卡的
        数字都完整显示，不会被挤压省略。
        """
        host = QWidget(self)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.m_total = MetricCard("待筛总数")
        self.m_passed = MetricCard("合格放行", accent="#1D9E75")
        self.m_vetoed = MetricCard("劣质淘汰", accent="#E24B4A")
        self.m_s = MetricCard("S 级", accent=TIER_ACCENT["S"])
        self.m_a = MetricCard("A 级", accent=TIER_ACCENT["A"])
        self.m_b = MetricCard("B 级", accent=TIER_ACCENT["B"])
        self.m_c = MetricCard("C 级", accent=TIER_ACCENT["C"])
        self.m_cost = MetricCard("总耗时")

        cards = (
            self.m_total,
            self.m_passed,
            self.m_vetoed,
            self.m_s,
            self.m_a,
            self.m_b,
            self.m_c,
            self.m_cost,
        )
        for idx, card in enumerate(cards):
            grid.addWidget(card, idx // 4, idx % 4)
        return host

    def _build_results(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(StrongBodyLabel("分级结果", card))
        self.result_hint = CaptionLabel("尚未生成结果", card)
        self.result_hint.setTextColor("#5F5E5A", "#B4B2A9")
        head.addWidget(self.result_hint)
        head.addStretch(1)

        self.export_btn = PushButton(FIF.SAVE, "导出 CSV", card)
        self.export_btn.clicked.connect(guard_action(self.export_btn, self._export_csv))
        head.addWidget(self.export_btn)

        self.reveal_btn = PushButton(FIF.FOLDER, "打开产物目录", card)
        self.reveal_btn.clicked.connect(
            guard_action(self.reveal_btn, lambda: open_path(PROJECT_ROOT))
        )
        head.addWidget(self.reveal_btn)
        layout.addLayout(head)

        self.table = TableWidget(card)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for idx, width in enumerate(COLUMN_WIDTHS):
            self.table.setColumnWidth(idx, width)
        # 窗口拉宽时由最后一列吸收多余宽度，不留下空白
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setMinimumSectionSize(44)
        layout.addWidget(self.table)
        return card

    # ------------------------------------------------------------------ 文件列表
    def _reload_files(self) -> None:
        current = self.file_combo.currentText()
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        for path in list_candidate_txt_files():
            self.file_combo.addItem(path.name, userData=str(path))
        self.file_combo.blockSignals(False)

        if self.file_combo.count() == 0:
            self.file_combo.addItem("（未找到待筛 txt，请放到项目根目录）", userData="")
        else:
            index = self.file_combo.findText(current)
            self.file_combo.setCurrentIndex(index if index >= 0 else 0)
        self._on_file_changed()

    def _selected_file(self):
        data = self.file_combo.currentData()
        if not data:
            return None
        from pathlib import Path

        path = Path(str(data))
        return path if path.is_file() else None

    def _on_file_changed(self, *_args) -> None:
        path = self._selected_file()
        if path is None:
            self.file_hint.setText("未选择有效文件")
            return
        count = count_non_empty_lines(path)
        self.file_hint.setText(f"共 {count} 部待筛")

    # ------------------------------------------------------------------ 运行
    def _build_command(self) -> list[str] | None:
        path = self._selected_file()
        if path is None:
            self.notify("未选择剧单", "请把待筛 txt 放到项目根目录后点刷新", "warning")
            return None

        total = count_non_empty_lines(path)
        if total == 0:
            self.notify("剧单为空", f"{path.name} 中没有有效剧名", "warning")
            return None

        self._total = total
        self._current = 0
        args: list[object] = [
            "--file",
            str(path),
            "--threshold",
            f"{self.threshold.value():.1f}",
        ]
        if self.skip_cache.isChecked():
            args.append("--skip-cache")
        if self.no_video.isChecked():
            args.append("--no-video")
        self.result_hint.setText(f"正在筛选 {total} 部…")
        return build_cmd(SCRIPT_MAIN, *args)

    def _on_line(self, text: str) -> None:
        if _START_RE.search(text):
            self._current += 1
            self.console.set_progress(self._current, self._total)

    def _on_finished(self, code: int) -> None:
        if code == 0:
            self._load_report()
        elif code > 0:
            self.result_hint.setText("任务异常结束，未刷新结果")

    # ------------------------------------------------------------------ 结果
    def _load_report(self) -> None:
        """后台读取报告，回到主线程再刷新界面，避免大文件阻塞 UI。"""
        self.result_hint.setText("正在加载结果…")
        run_in_background(
            self,
            _read_report_payload,
            on_success=self._apply_report,
            on_error=lambda msg: self.result_hint.setText(f"读取结果失败：{msg}"),
        )

    def _apply_report(self, data) -> None:
        if not data:
            self.result_hint.setText("尚未生成结果（运行一次粗筛后自动加载）")
            return

        summary = data.get("summary") or {}
        grades = summary.get("grade_distribution") or {}
        total = int(summary.get("total_count") or 0)
        cost = float(summary.get("total_cost_sec") or 0.0)

        self.m_total.set_value(total)
        self.m_passed.set_value(summary.get("passed_count", 0))
        self.m_vetoed.set_value(summary.get("vetoed_count", 0))
        self.m_s.set_value(grades.get("S", 0))
        self.m_a.set_value(grades.get("A", 0))
        self.m_b.set_value(grades.get("B", 0))
        self.m_c.set_value(grades.get("C", 0))
        self.m_cost.set_value(f"{cost:.1f}s" if cost else "—")

        rows = data.get("results") or []
        self._report_rows = rows
        self._fill_table(rows)
        self.result_hint.setText(f"共 {len(rows)} 条记录")

    def _fill_table(self, rows: list) -> None:
        """批量写入表格：期间关闭重绘与排序，避免逐格重排造成卡顿。"""
        table = self.table
        table.setSortingEnabled(False)
        table.setUpdatesEnabled(False)
        try:
            self._fill_table_rows(rows)
        finally:
            table.setUpdatesEnabled(True)
            table.setSortingEnabled(True)

    def _fill_table_rows(self, rows: list) -> None:
        table = self.table
        table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            if not isinstance(item, dict):
                continue
            tier = str(item.get("tier") or "-")
            title = str(item.get("title") or "")
            stage1 = float(item.get("stage1_score") or 0.0)
            final = float(item.get("final_score") or 0.0)

            title_item = QTableWidgetItem(title)
            title_item.setToolTip(title)
            table.setItem(r, 0, title_item)

            tier_item = QTableWidgetItem(tier)
            tier_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            color = TIER_ACCENT.get(tier)
            if isinstance(color, tuple):
                color = color[0]
            if color:
                tier_item.setForeground(QColor(color))
            table.setItem(r, 1, tier_item)

            score_item = _NumberItem(final)
            # 阶段一得分收进提示，避免窄窗口下列数过多
            score_item.setToolTip(f"综合分 {final:.1f}　｜　阶段一 {stage1:.1f}")
            table.setItem(r, 2, score_item)

            for col, key in (
                (3, "genre_summary"),
                (4, "ai_review"),
                (5, "diagnostic_message"),
            ):
                text = str(item.get(key) or "")
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                table.setItem(r, col, cell)

    def _export_csv(self) -> None:
        """导出完整字段（表格为窄窗口做了取舍，导出不丢信息）。"""
        rows = self._report_rows
        if not rows:
            self.notify("没有可导出的结果", "请先运行一次粗筛", "warning")
            return
        target = PROJECT_ROOT / "粗筛结果导出.csv"
        try:
            with target.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow([header for header, _ in CSV_COLUMNS])
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    writer.writerow([item.get(key, "") for _, key in CSV_COLUMNS])
        except OSError as exc:
            self.notify("导出失败", str(exc), "error")
            return
        self.notify("导出成功", f"{target.name}（共 {len(rows)} 条）", "success")
        reveal(target)
