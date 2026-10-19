"""粗筛分级结果与统计弹框。

把原先平铺在粗筛工作台上的 8 个指标卡与分级结果表格移入独立的弹框中，
支持执行粗筛时或粗筛完成后随时唤起查看，并支持导出 CSV、打开目录与清空结果。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QMessageBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    PushButton,
    StrongBodyLabel,
    TableWidget,
)

from desktop_app.common.constants import PROJECT_ROOT, REPORT_JSON
from desktop_app.common.shell import open_path, reveal
from desktop_app.components.busy import guard_action
from desktop_app.components.metric_card import MetricCard

TIER_ACCENT: dict[str, object] = {
    "S": "#BA7517",
    "A": "#1D9E75",
    "B": ("#5F5E5A", "#B4B2A9"),
    "C": "#E24B4A",
}

# 列定义：(列名, 默认宽度, 字段备注说明)
COLUMN_DEFS = (
    ("剧名", 180, "短剧作品标准名称（已去除特殊前缀）"),
    (
        "分级",
        56,
        "综合业务评级：\n"
        "• S 级（≥85分）：爆款潜力推荐，题材张力与制作俱佳\n"
        "• A 级（70~84分）：保本优质推荐，符合主流商业标准\n"
        "• B 级（50~69分）：平庸待优化，有局部硬伤或节奏较平\n"
        "• C 级（<50分或触发违规）：劣质淘汰，不予立项/采购",
    ),
    (
        "综合分",
        80,
        "多维商业潜力总分（0~100 分）\n"
        "综合阶段一剧情初筛与阶段二视听精筛（点击表头可按分数排序）",
    ),
    ("题材", 130, "AI 与网文大盘风向标匹配出的核心赛道、母题与受众标签"),
    ("AI 点评", 260, "大模型对剧情情绪张力、叙事节奏、人设反差及吸睛点的量化点评"),
    (
        "诊断",
        220,
        "排查诊断结论：正常放行理由，或一票否决（如 PPT 漫、违规硬伤）的具体淘汰归因",
    ),
)
COLUMNS = tuple(c[0] for c in COLUMN_DEFS)
COLUMN_WIDTHS = tuple(c[1] for c in COLUMN_DEFS)

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


class ScreeningResultsDialog(QDialog):
    """粗筛分级结果与统计弹框。"""

    def __init__(self, parent=None, on_cleared: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self._on_cleared = on_cleared
        self._report_rows: list = []

        self.setWindowTitle("粗筛分级结果与统计")
        self.resize(1120, 720)
        self.setMinimumSize(900, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        # 头部操作栏
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(StrongBodyLabel("分级与指标统计", self))
        self.result_hint = CaptionLabel("尚未生成结果", self)
        self.result_hint.setTextColor("#5F5E5A", "#B4B2A9")
        head.addWidget(self.result_hint)
        head.addStretch(1)

        self.export_btn = PushButton(FIF.SAVE, "导出 CSV", self)
        self.export_btn.clicked.connect(guard_action(self.export_btn, self._export_csv))
        head.addWidget(self.export_btn)

        self.reveal_btn = PushButton(FIF.FOLDER, "打开产物目录", self)
        self.reveal_btn.clicked.connect(
            guard_action(self.reveal_btn, lambda: open_path(PROJECT_ROOT))
        )
        head.addWidget(self.reveal_btn)

        self.clear_btn = PushButton(FIF.DELETE, "清空结果", self)
        self.clear_btn.clicked.connect(guard_action(self.clear_btn, self._clear_results))
        head.addWidget(self.clear_btn)

        self.close_btn = PushButton(FIF.CLOSE, "关闭", self)
        self.close_btn.clicked.connect(self.close)
        head.addWidget(self.close_btn)
        layout.addLayout(head)

        # 指标卡网格
        layout.addWidget(self._build_metrics())

        # 结果表格
        layout.addWidget(self._build_table(), 1)

    # ------------------------------------------------------------- 布局构造
    def _build_metrics(self) -> QWidget:
        """汇总指标卡：2 行 × 4 列网格。"""
        host = CardWidget(self)
        grid = QGridLayout(host)
        grid.setContentsMargins(14, 12, 14, 14)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.m_total = MetricCard("待筛总数")
        self.m_total.setToolTip("本次任务输入的待处理剧单有效剧目总数")
        self.m_passed = MetricCard("合格放行", accent="#1D9E75")
        self.m_passed.setToolTip("综合评分达标且未触发一票否决，准予通过推荐的剧目数（S/A/B 级）")
        self.m_vetoed = MetricCard("劣质淘汰", accent="#E24B4A")
        self.m_vetoed.setToolTip("低于准入阈值或命中负向违规硬伤（如 PPT 漫、严重画质破音等）而被淘汰的剧目数（C 级）")
        self.m_s = MetricCard("S 级", accent=TIER_ACCENT["S"])
        self.m_s.setToolTip("综合得分 ≥85 分：具备极高市场爆款潜力的头部精品推荐")
        self.m_a = MetricCard("A 级", accent=TIER_ACCENT["A"])
        self.m_a.setToolTip("综合得分 70~84 分：剧情与制作扎实的保本优质推荐，符合主流商业标准")
        self.m_b = MetricCard("B 级", accent=TIER_ACCENT["B"])
        self.m_b.setToolTip("综合得分 50~69 分：平庸待优化，存在局部节奏或设定瑕疵但仍有可取之处")
        self.m_c = MetricCard("C 级", accent=TIER_ACCENT["C"])
        self.m_c.setToolTip("综合得分 <50 分或命中一票否决：劣质淘汰，不予立项采购")
        self.m_cost = MetricCard("总耗时")
        self.m_cost.setToolTip("全流程批处理筛选耗费的总执行时间（秒）")

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

    def _build_table(self) -> TableWidget:
        self.table = TableWidget(self)
        self.table.setColumnCount(len(COLUMN_DEFS))
        for idx, (col_name, width, tooltip) in enumerate(COLUMN_DEFS):
            header_item = QTableWidgetItem(col_name)
            header_item.setToolTip(tooltip)
            self.table.setHorizontalHeaderItem(idx, header_item)
            self.table.setColumnWidth(idx, width)
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setMinimumSectionSize(44)
        return self.table

    # ------------------------------------------------------------- 数据呈现
    def apply_report(self, data: Optional[dict], is_running: bool = False) -> None:
        """将报告数据（或筛选中状态）同步渲染到指标卡和表格。"""
        if not data:
            if is_running:
                self.result_hint.setText("正在执行粗筛…完成后将自动呈现最新结果")
            else:
                self.result_hint.setText("尚未生成结果（运行粗筛后自动呈现）")
            for m in (
                self.m_total,
                self.m_passed,
                self.m_vetoed,
                self.m_s,
                self.m_a,
                self.m_b,
                self.m_c,
            ):
                m.set_value(0)
            self.m_cost.set_value("—")
            self._report_rows = []
            self.table.setRowCount(0)
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

        if is_running:
            self.result_hint.setText(f"共 {len(rows)} 条历史记录（新任务筛选中…）")
        else:
            self.result_hint.setText(f"共 {len(rows)} 条记录")

    def _fill_table(self, rows: list) -> None:
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

    # ------------------------------------------------------------- 操作按钮
    def _export_csv(self) -> None:
        rows = self._report_rows
        if not rows:
            self._notify_parent("没有可导出的结果", "请先运行一次粗筛", "warning")
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
            self._notify_parent("导出失败", str(exc), "error")
            return
        self._notify_parent("导出成功", f"{target.name}（共 {len(rows)} 条）", "success")
        reveal(target)

    def _clear_results(self) -> None:
        if not self._report_rows and self.table.rowCount() == 0 and not REPORT_JSON.is_file():
            self._notify_parent("无需清空", "当前没有可清空的分级结果", "info")
            return

        answer = QMessageBox.question(
            self,
            "清空分级结果",
            "确定要清空当前分级结果吗？\n清空后将重置表格及指标统计，并删除本地粗筛报告文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            REPORT_JSON.unlink(missing_ok=True)
        except OSError as exc:
            self._notify_parent("清空失败", f"删除报告文件失败：{exc}", "error")
            return

        self._report_rows = []
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setSortingEnabled(True)

        for card in (
            self.m_total,
            self.m_passed,
            self.m_vetoed,
            self.m_s,
            self.m_a,
            self.m_b,
            self.m_c,
        ):
            card.set_value(0)
        self.m_cost.set_value("—")

        self.result_hint.setText("尚未生成结果")
        if callable(self._on_cleared):
            self._on_cleared()
        self._notify_parent("已清空", "分级结果已清空", "success")

    def _notify_parent(self, title: str, content: str = "", level: str = "info") -> None:
        parent = self.parent()
        if parent and hasattr(parent, "notify"):
            parent.notify(title, content, level)
