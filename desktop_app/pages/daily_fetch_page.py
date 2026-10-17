"""每日筛选剧目：从 iocpx 平台抓取 dramaDeliverySchedule 列表。"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QAbstractItemView,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
    TransparentToolButton,
)

from desktop_app.common.constants import PROJECT_ROOT
from desktop_app.common.shell import open_path, reveal
from desktop_app.components.base_page import BasePage
from desktop_app.components.busy import guard_action
from desktop_app.components.log_view import LogView
from desktop_app.components.metric_card import MetricCard
from desktop_app.iocpx.fetcher import fetch_schedule

# 默认账号（用户提供的固定账号）
DEFAULT_EMAIL = "zlp@zz.com"
DEFAULT_PASSWORD = "qq19842086097"

# 结果保存目录
FETCH_OUT_DIR = PROJECT_ROOT / "data" / "daily_fetch"
FETCH_OUT_DIR.mkdir(parents=True, exist_ok=True)

# 表格列
TABLE_COLS = ("剧名", "集数", "发布时间", "分类", "公司", "编辑")
TABLE_COL_WIDTHS = (180, 52, 120, 130, 150, 80)


def _today_str(fmt: str = "%Y-%m-%d") -> str:
    return datetime.now().strftime(fmt)


def _parse_date(text: str) -> datetime:
    """把 YYYY-MM-DD 转成 datetime（当天 00:00:00）。"""
    return datetime.strptime(text.strip(), "%Y-%m-%d")


def _format_ts(ts_ms: int | None) -> str:
    if not ts_ms:
        return "—"
    try:
        return datetime.fromtimestamp(ts_ms / 1000).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts_ms)


class FetchWorker(QThread):
    """后台线程：执行 iocpx 抓取，通过信号实时回传日志。"""

    log_line = Signal(str)
    finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        email: str,
        password: str,
        min_chapter: int,
        start_time: datetime,
        end_time: datetime,
        parent=None,
    ):
        super().__init__(parent)
        self.email = email
        self.password = password
        self.min_chapter = min_chapter
        self.start_time = start_time
        self.end_time = end_time

    def run(self) -> None:
        try:
            items = fetch_schedule(
                self.email,
                self.password,
                min_chapter=self.min_chapter,
                start_time=self.start_time,
                end_time=self.end_time,
                log_callback=lambda msg: self.log_line.emit(msg),
            )
            self.finished.emit(items)
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")


class DailyFetchPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "dailyFetchPage",
            "每日筛选剧目",
            "从 iocpx 平台抓取 dramaDeliverySchedule 每日剧目列表，"
            "支持按最小集数、日期范围筛选，结果自动保存为 JSON / CSV。",
            parent,
        )
        self._worker: FetchWorker | None = None

        self.add_widget(self._build_params())
        self.add_layout(self._build_metrics())
        self.add_widget(self._build_log(), 1)
        self.add_widget(self._build_table())

        self._refresh_path_hint()

    # ------------------------------------------------------------------ 参数
    def _build_params(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(StrongBodyLabel("抓取参数", card))
        self.status_label = BodyLabel("未登录", card)
        self.status_label.setTextColor("#E24B4A", "#E24B4A")
        head.addWidget(self.status_label)
        head.addStretch(1)

        # 刷新登录状态
        refresh_btn = TransparentToolButton(FIF.SYNC, card)
        refresh_btn.setToolTip("刷新登录状态")
        refresh_btn.clicked.connect(self._check_login)
        head.addWidget(refresh_btn)
        layout.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(10)

        # 最小集数
        row.addWidget(BodyLabel("最小集数 ≥", card))
        self.min_spinner = QSpinBox(card)
        self.min_spinner.setRange(1, 999)
        self.min_spinner.setValue(40)
        self.min_spinner.setFixedWidth(90)
        row.addWidget(self.min_spinner)

        row.addSpacing(14)
        row.addWidget(BodyLabel("开始", card))
        self.start_edit = LineEdit(card)
        self.start_edit.setText((datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d"))
        self.start_edit.setFixedWidth(110)
        row.addWidget(self.start_edit)

        row.addSpacing(8)
        row.addWidget(BodyLabel("结束", card))
        self.end_edit = LineEdit(card)
        self.end_edit.setText(datetime.now().strftime("%Y-%m-%d"))
        self.end_edit.setFixedWidth(110)
        row.addWidget(self.end_edit)

        row.addStretch(1)
        layout.addLayout(row)

        # 第二行：账号 + 操作按钮
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(CaptionLabel("账号", card))
        self.email_edit = LineEdit(card)
        self.email_edit.setText(DEFAULT_EMAIL)
        self.email_edit.setFixedWidth(160)
        row2.addWidget(self.email_edit)
        row2.addWidget(CaptionLabel("密码", card))
        self.pwd_edit = LineEdit(card)
        self.pwd_edit.setText(DEFAULT_PASSWORD)
        self.pwd_edit.setEchoMode(LineEdit.EchoMode.Password)
        self.pwd_edit.setFixedWidth(160)
        row2.addWidget(self.pwd_edit)
        row2.addStretch(1)

        self.fetch_btn = PrimaryPushButton(FIF.DOWNLOAD, "开始抓取", card)
        self.fetch_btn.setMinimumWidth(100)
        self.fetch_btn.clicked.connect(self._start_fetch)
        row2.addWidget(self.fetch_btn)

        self.export_btn = PushButton(FIF.SAVE, "导出 CSV", card)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(
            guard_action(self.export_btn, self._export_csv)
        )
        row2.addWidget(self.export_btn)

        open_btn = PushButton(FIF.FOLDER, "打开目录", card)
        open_btn.clicked.connect(
            guard_action(open_btn, lambda: open_path(FETCH_OUT_DIR))
        )
        row2.addWidget(open_btn)

        layout.addLayout(row2)

        return card

    # ------------------------------------------------------------------ 指标
    def _build_metrics(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self.m_count = MetricCard("抓取条数")
        self.m_unique = MetricCard("去重后", accent="#1D9E75")
        self.m_path = MetricCard("保存路径")
        for card in (self.m_count, self.m_unique, self.m_path):
            row.addWidget(card, 1)
        return row

    # ------------------------------------------------------------------ 日志
    def _build_log(self) -> LogView:
        self.log_view = LogView(self, min_height=140)
        return self.log_view

    # ------------------------------------------------------------------ 表格
    def _build_table(self) -> TableWidget:
        self.table = TableWidget(self)
        self.table.setColumnCount(len(TABLE_COLS))
        self.table.setHorizontalHeaderLabels(list(TABLE_COLS))
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        for idx, width in enumerate(TABLE_COL_WIDTHS):
            self.table.setColumnWidth(idx, width)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(220)
        return self.table

    # ------------------------------------------------------------------ 逻辑
    def _check_login(self) -> None:
        """尝试复用缓存 token，或触发一次静默登录验证状态。"""
        from desktop_app.iocpx.auth import ensure_auth

        self.status_label.setText("正在检查登录…")
        try:
            ensure_auth(self.email_edit.text().strip(), self.pwd_edit.text().strip())
            self.status_label.setText("已登录")
            self.status_label.setTextColor("#1D9E75", "#1D9E75")
            self.notify("登录状态正常", "token 有效", "success")
        except Exception as exc:
            self.status_label.setText("登录失败")
            self.status_label.setTextColor("#E24B4A", "#E24B4A")
            self.notify("登录失败", str(exc), "error")

    def _start_fetch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.notify("正在抓取中", "请等待当前任务完成", "warning")
            return

        # 解析日期
        try:
            start_dt = _parse_date(self.start_edit.text())
            end_dt = _parse_date(self.end_edit.text())
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
        except ValueError:
            self.notify("日期格式错误", "请使用 YYYY-MM-DD 格式", "error")
            return

        if start_dt > end_dt:
            self.notify("日期范围错误", "开始日期不能晚于结束日期", "error")
            return

        email = self.email_edit.text().strip()
        pwd = self.pwd_edit.text().strip()
        min_chapter = self.min_spinner.value()

        self.log_view.clear()
        self.log_view.append_line("=" * 40)
        self.log_view.append_line(f"开始抓取：{start_dt.date()} ~ {end_dt.date()}")
        self.log_view.append_line(f"最小集数 ≥ {min_chapter}")
        self.log_view.append_line("=" * 40)

        self.fetch_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.table.setRowCount(0)

        self._worker = FetchWorker(email, pwd, min_chapter, start_dt, end_dt, parent=self)
        self._worker.log_line.connect(self.log_view.append_line)
        self._worker.finished.connect(self._on_fetch_done)
        self._worker.error.connect(self._on_fetch_error)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.error.connect(self._cleanup_worker)
        self._worker.start()

    def _cleanup_worker(self) -> None:
        self._worker = None
        self.fetch_btn.setEnabled(True)

    def _on_fetch_done(self, items: list[dict]) -> None:
        self.log_view.append_line(f"✓ 抓取完成，共 {len(items)} 条")
        self.m_count.set_value(str(len(items)))

        # 去重统计（按 bookId）
        seen = set()
        unique = []
        for it in items:
            bid = it.get("bookId")
            if bid and bid not in seen:
                seen.add(bid)
                unique.append(it)
        self.m_unique.set_value(str(len(unique)))

        # 保存 JSON
        today = _today_str()
        json_path = FETCH_OUT_DIR / f"筛选剧目_{today}.json"
        try:
            json_path.write_text(
                json.dumps(items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.log_view.append_line(f"✓ JSON 已保存：{json_path.name}")
        except OSError as exc:
            self.log_view.append_line(f"✗ JSON 保存失败：{exc}")

        # 保存 CSV
        csv_path = FETCH_OUT_DIR / f"筛选剧目_{today}.csv"
        try:
            with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow([
                    "bookId", "bookName", "chapterAmount", "bookPublishTime",
                    "categoryText", "company", "editorName", "douyinId",
                    "nickName", "linkUrl", "hotDramaStatus", "createTime",
                ])
                for it in items:
                    writer.writerow([
                        it.get("bookId", ""),
                        it.get("bookName", ""),
                        it.get("chapterAmount", ""),
                        it.get("bookPublishTime", ""),
                        it.get("categoryText", ""),
                        it.get("company", ""),
                        it.get("editorName", ""),
                        it.get("douyinId", ""),
                        it.get("nickName", ""),
                        it.get("linkUrl", ""),
                        it.get("hotDramaStatus", ""),
                        it.get("createTime", ""),
                    ])
            self.log_view.append_line(f"✓ CSV 已保存：{csv_path.name}")
        except OSError as exc:
            self.log_view.append_line(f"✗ CSV 保存失败：{exc}")

        self._last_json_path = json_path
        self._last_csv_path = csv_path
        self._refresh_path_hint()
        self.export_btn.setEnabled(True)

        # 填充表格（只显示前 100 条，避免表格过卡）
        display_items = items[:100]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(display_items))
        for r, it in enumerate(display_items):
            self.table.setItem(r, 0, QTableWidgetItem(str(it.get("bookName") or "")))
            self.table.setItem(r, 1, QTableWidgetItem(str(it.get("chapterAmount") or "")))
            self.table.setItem(r, 2, QTableWidgetItem(_format_ts(it.get("bookPublishTime"))))
            self.table.setItem(r, 3, QTableWidgetItem(str(it.get("categoryText") or "")))
            self.table.setItem(r, 4, QTableWidgetItem(str(it.get("company") or "")))
            self.table.setItem(r, 5, QTableWidgetItem(str(it.get("editorName") or "")))
        self.table.setSortingEnabled(True)

        self.notify("抓取完成", f"共 {len(items)} 条，已保存", "success")

    def _on_fetch_error(self, msg: str) -> None:
        self.log_view.append_line(f"✗ 抓取失败：{msg}")
        self.notify("抓取失败", msg, "error")
        self.fetch_btn.setEnabled(True)

    def _refresh_path_hint(self) -> None:
        rel = FETCH_OUT_DIR.relative_to(PROJECT_ROOT)
        self.m_path.set_value(str(rel))

    def _export_csv(self) -> None:
        if hasattr(self, "_last_csv_path") and self._last_csv_path.exists():
            reveal(self._last_csv_path)
        else:
            self.notify("没有可定位的文件", "请先完成一次抓取", "warning")
