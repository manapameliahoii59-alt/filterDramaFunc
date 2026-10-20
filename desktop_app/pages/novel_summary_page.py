"""小说爆款基因（DNA）大模型批量提取工作台。

支持对 PostgreSQL 中收录的网文进行全自动批量提取：
- 按章节分工将小说【前 5~6 章完整正文 + 前 20 章目录标题】一次性发给大模型（Qwen3.8-Flash / DeepSeek）；
- 执行 6 维爆款基因（DNA）结构化分析；
- 质检与慢热过滤：开篇无爽点/无金手指（has_payoff_in_opening = False）自动丢弃不入库；
- 合格数据自动写入 novel_dna_records；
- 实时表格流式更新提取进度与入库状态。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
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
    FluentIcon as FIF,
    FlowLayout,
    InfoBadge,
    InfoLevel,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    TableWidget,
    TransparentToolButton,
)

from desktop_app.common.constants import PROJECT_ROOT
from desktop_app.common.shell import reveal
from desktop_app.components.base_page import BasePage
from desktop_app.components.metric_card import MetricCard
from desktop_app.runtime.function_runner import run_in_background
from novel_trends.novel_summary_engine import (
    build_user_prompt,
    call_llm_extract_dna,
    extract_dna_for_book,
    fetch_candidate_books_from_db,
    load_book_context_from_db,
    save_dna_to_postgres,
)

# 表格列配置
BATCH_COLS = (
    ("序号", 50),
    ("平台", 90),
    ("书名", 160),
    ("作者", 100),
    ("章节数", 65),
    ("开篇质检/爽点", 130),
    ("赛道与细分流派", 170),
    ("外挂破局机制", 150),
    ("首个反杀爽点", 220),
    ("入库状态", 95),
    ("耗时", 60),
)


class BatchDnaWorker(QThread):
    """后台线程：批量调用大模型提取小说 6 维 DNA。"""

    item_started = Signal(int, int, dict)          # index, total, book
    item_finished = Signal(int, int, dict, dict)     # index, total, book, result
    stats_updated = Signal(int, int, int, int, int) # total, processed, qualified, slow_burn, failed
    log_line = Signal(str)
    all_finished = Signal(dict)

    def __init__(self, books: List[Dict[str, Any]], discard_slow_burn: bool = True, parent=None):
        super().__init__(parent)
        self.books = books
        self.discard_slow_burn = discard_slow_burn
        self._is_stopped = False
        self._is_paused = False

    def pause(self):
        self._is_paused = True

    def resume(self):
        self._is_paused = False

    def is_paused(self) -> bool:
        return self._is_paused

    def stop(self):
        self._is_stopped = True
        self._is_paused = False

    def run(self):
        total = len(self.books)
        processed = 0
        qualified = 0
        slow_burn = 0
        failed = 0

        self.stats_updated.emit(total, processed, qualified, slow_burn, failed)

        for idx, book in enumerate(self.books):
            if self._is_stopped:
                self.log_line.emit("[批量任务] 用户手动终止了批量提取任务。")
                break

            while self._is_paused:
                if self._is_stopped:
                    break
                self.msleep(300)

            if self._is_stopped:
                break

            platform = book.get("platform", "")
            book_id = book.get("book_id", "")
            title = book.get("title", "")
            author = book.get("author", "")

            self.item_started.emit(idx, total, book)
            self.log_line.emit(f"[{idx+1}/{total}] 正在调用大模型分析《{title}》({platform} - {author})...")

            try:
                res = extract_dna_for_book(
                    platform=platform,
                    book_id=book_id,
                    discard_slow_burn=self.discard_slow_burn,
                )
            except Exception as e:
                res = {
                    "success": False,
                    "status": "ERROR",
                    "message": str(e),
                    "elapsed": 0.0,
                    "dna": None,
                }

            processed += 1
            st = res.get("status", "ERROR")
            if st == "QUALIFIED":
                qualified += 1
                self.log_line.emit(f"  -> 《{title}》提取成功：开篇有爽点，已入库 PG (耗时 {res.get('elapsed')}s)")
            elif st == "SLOW_BURN_DISCARDED":
                slow_burn += 1
                self.log_line.emit(f"  -> 《{title}》极度慢热：前5~6章未出核心爽点，已丢弃不入库 (耗时 {res.get('elapsed')}s)")
            else:
                failed += 1
                self.log_line.emit(f"  -> 《{title}》提取异常: {res.get('message')}")

            self.item_finished.emit(idx, total, book, res)
            self.stats_updated.emit(total, processed, qualified, slow_burn, failed)

            # 微小休眠防止 API 速率受限
            self.msleep(400)

        self.all_finished.emit({
            "total": total,
            "processed": processed,
            "qualified": qualified,
            "slow_burn": slow_burn,
            "failed": failed,
        })


class NovelSummaryPage(BasePage):
    """小说爆款 DNA 特征提取批量工作台。"""

    def __init__(self, parent=None):
        super().__init__(
            "novelSummaryPage",
            "小说DNA提取",
            "基于小说【前 5~6 章完整正文 + 前 20 章目录标题】一次性调用工业级大模型（DashScope Qwen3.8-Flash / DeepSeek），"
            "全自动批量提取数据质检、赛道世界观、主角外挂、矛盾反杀、第一集音画锚点与爆款标签树 6 维结构化特征。",
            parent,
        )

        self._candidate_books: List[Dict[str, Any]] = []
        self._current_selected_dna: Optional[Dict[str, Any]] = None
        self._worker: Optional[BatchDnaWorker] = None
        self._batch_results: List[Dict[str, Any]] = []

        # 构建各模块
        self.add_widget(self._build_metric_cards())
        self.add_widget(self._build_batch_control_card())
        self.add_widget(self._build_batch_table_card())
        self.add_widget(self._build_manual_card())

        # 启动时刷新数据库待处理书籍列表
        self._refresh_batch_candidates()

    # =========================================================================
    # 1. 顶部指标统计卡片
    # =========================================================================
    def _build_metric_cards(self) -> CardWidget:
        card = CardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        self.m_total = MetricCard("待处理总数")
        self.m_processed = MetricCard("已分析进度")
        self.m_qualified = MetricCard("放行入库 (有爽点)", accent="#1D9E75")
        self.m_slow = MetricCard("慢热淘汰 (代码丢弃)", accent="#D32F2F")
        self.m_failed = MetricCard("异常失败", accent="#EF9F27")

        for m in (self.m_total, self.m_processed, self.m_qualified, self.m_slow, self.m_failed):
            layout.addWidget(m, 1)

        return card

    # =========================================================================
    # 2. 批量任务控制与配置面板
    # =========================================================================
    def _build_batch_control_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        # 第一行：筛选与过滤条件
        r1 = QHBoxLayout()
        r1.setSpacing(12)

        r1.addWidget(BodyLabel("来源平台:", card))
        self.combo_platform = ComboBox(card)
        self.combo_platform.addItems(["全部平台", "起点中文网", "纵横中文网", "晋江文学城", "潇湘书院"])
        self.combo_platform.currentIndexChanged.connect(lambda _: self._refresh_batch_candidates())
        r1.addWidget(self.combo_platform)

        r1.addWidget(BodyLabel("处理范围:", card))
        self.combo_status = ComboBox(card)
        self.combo_status.addItems(["仅未提取 DNA 的小说", "全部书籍（含重新分析）"])
        self.combo_status.currentIndexChanged.connect(lambda _: self._refresh_batch_candidates())
        r1.addWidget(self.combo_status)

        r1.addWidget(BodyLabel("提取批次限制:", card))
        self.spin_limit = SpinBox(card)
        self.spin_limit.setRange(1, 500)
        self.spin_limit.setValue(20)
        r1.addWidget(self.spin_limit)

        self.chk_discard_slow = CheckBox("慢热自动丢弃（无爽点不入库）", card)
        self.chk_discard_slow.setChecked(True)
        r1.addWidget(self.chk_discard_slow)

        r1.addStretch(1)

        self.btn_refresh = PushButton(FIF.SYNC, "刷新待处理列表", card)
        self.btn_refresh.clicked.connect(self._refresh_batch_candidates)
        r1.addWidget(self.btn_refresh)
        layout.addLayout(r1)

        # 第二行：操作按钮栏与通道提示
        r2 = QHBoxLayout()
        r2.setSpacing(12)

        self.btn_start_batch = PrimaryPushButton(FIF.PLAY, "开始批量提取", card)
        self.btn_start_batch.clicked.connect(self._toggle_start_batch)
        r2.addWidget(self.btn_start_batch)

        self.btn_pause = PushButton(FIF.PAUSE, "暂停", card)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause_batch)
        r2.addWidget(self.btn_pause)

        self.btn_stop = PushButton(FIF.CANCEL, "终止", card)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_batch)
        r2.addWidget(self.btn_stop)

        self.btn_copy_selected = PushButton(FIF.COPY, "复制选中小说 JSON", card)
        self.btn_copy_selected.setEnabled(False)
        self.btn_copy_selected.clicked.connect(self._copy_result_json)
        r2.addWidget(self.btn_copy_selected)

        self.btn_export = PushButton(FIF.DOCUMENT, "导出批次结果 (JSON)", card)
        self.btn_export.clicked.connect(self._export_batch_json)
        r2.addWidget(self.btn_export)

        r2.addStretch(1)

        self.lbl_channel_status = CaptionLabel(
            "通道: DashScope Qwen3.8-Flash (备用容灾: DeepSeek-Chat)", card
        )
        self.lbl_channel_status.setTextColor("#5F5E5A", "#B4B2A9")
        r2.addWidget(self.lbl_channel_status)

        self.btn_toggle_manual = TransparentToolButton(FIF.EDIT, card)
        self.btn_toggle_manual.setText("单本手动调试/粘贴测试")
        self.btn_toggle_manual.clicked.connect(self._toggle_manual_panel)
        r2.addWidget(self.btn_toggle_manual)

        layout.addLayout(r2)

        # 第三行：进度条与状态
        r3 = QHBoxLayout()
        self.progress_bar = ProgressBar(card)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)
        r3.addWidget(self.progress_bar, 1)

        self.lbl_progress_text = CaptionLabel("就绪，等待开始批量提取", card)
        self.lbl_progress_text.setTextColor("#1D9E75", "#26A69A")
        r3.addWidget(self.lbl_progress_text)
        layout.addLayout(r3)

        return card

    # =========================================================================
    # 3. 批量任务列表表格
    # =========================================================================
    def _build_batch_table_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(StrongBodyLabel("批量提取剧目列表", card))
        tip = CaptionLabel("实时监控全网小说 6 维爆款 DNA 提取进度与入库状态", card)
        tip.setTextColor("#1D9E75", "#26A69A")
        head.addWidget(tip)
        head.addStretch(1)

        self.lbl_table_count = CaptionLabel("共 0 部待处理剧目", card)
        self.lbl_table_count.setTextColor("#5F5E5A", "#B4B2A9")
        head.addWidget(self.lbl_table_count)
        layout.addLayout(head)

        self.table = TableWidget(card)
        self.table.setColumnCount(len(BATCH_COLS))
        self.table.setHorizontalHeaderLabels([col[0] for col in BATCH_COLS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(520)

        header = self.table.horizontalHeader()
        for i, (_name, width) in enumerate(BATCH_COLS):
            self.table.setColumnWidth(i, width)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)

        self.table.itemSelectionChanged.connect(self._on_table_row_selected)
        layout.addWidget(self.table)

        return card

    # =========================================================================
    # 4. 单本调试/自由输入折叠卡片（备用）
    # =========================================================================
    def _build_manual_card(self) -> CardWidget:
        self.manual_card = CardWidget(self)
        layout = QVBoxLayout(self.manual_card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(StrongBodyLabel("单本手动调试与自由录入（可选）", self.manual_card))
        head.addStretch(1)
        layout.addLayout(head)

        meta_grid = QGridLayout()
        meta_grid.setHorizontalSpacing(14)
        meta_grid.setVerticalSpacing(8)

        meta_grid.addWidget(BodyLabel("书名:", self.manual_card), 0, 0)
        self.txt_manual_title = LineEdit(self.manual_card)
        self.txt_manual_title.setPlaceholderText("输入测试书名")
        meta_grid.addWidget(self.txt_manual_title, 0, 1)

        meta_grid.addWidget(BodyLabel("作者:", self.manual_card), 0, 2)
        self.txt_manual_author = LineEdit(self.manual_card)
        self.txt_manual_author.setPlaceholderText("作者")
        meta_grid.addWidget(self.txt_manual_author, 0, 3)

        meta_grid.addWidget(BodyLabel("标签/流派:", self.manual_card), 0, 4)
        self.txt_manual_category = LineEdit(self.manual_card)
        self.txt_manual_category.setPlaceholderText("都市 / 末世等")
        meta_grid.addWidget(self.txt_manual_category, 0, 5)

        layout.addLayout(meta_grid)

        layout.addWidget(BodyLabel("原始小说简介：", self.manual_card))
        self.txt_manual_intro = PlainTextEdit(self.manual_card)
        self.txt_manual_intro.setPlaceholderText("粘贴小说原始简介...")
        self.txt_manual_intro.setFixedHeight(50)
        layout.addWidget(self.txt_manual_intro)

        ch_layout = QHBoxLayout()
        ch_left = QVBoxLayout()
        ch_left.addWidget(BodyLabel("前 20 章目录标题（一行一章）：", self.manual_card))
        self.txt_manual_titles = PlainTextEdit(self.manual_card)
        self.txt_manual_titles.setFixedHeight(90)
        ch_left.addWidget(self.txt_manual_titles)
        ch_layout.addLayout(ch_left, 1)

        ch_right = QVBoxLayout()
        ch_right.addWidget(BodyLabel("前 5~6 章完整正文：", self.manual_card))
        self.txt_manual_contents = PlainTextEdit(self.manual_card)
        self.txt_manual_contents.setFixedHeight(90)
        ch_right.addWidget(self.txt_manual_contents)
        ch_layout.addLayout(ch_right, 2)
        layout.addLayout(ch_layout)

        btn_row = QHBoxLayout()
        self.btn_run_single_manual = PrimaryPushButton(FIF.ROBOT, "测试单本提取", self.manual_card)
        self.btn_run_single_manual.clicked.connect(self._run_single_manual_test)
        btn_row.addWidget(self.btn_run_single_manual)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.manual_card.setVisible(False)
        return self.manual_card

    # =========================================================================
    # 逻辑与槽函数
    # =========================================================================
    def _toggle_manual_panel(self):
        visible = not self.manual_card.isVisible()
        self.manual_card.setVisible(visible)

    def _refresh_batch_candidates(self):
        """刷新数据库中待处理的小说列表。"""
        if self._worker and self._worker.isRunning():
            self.notify("正在执行批量任务", "请等待当前批次完成或终止后再刷新", "warning")
            return

        platform = self.combo_platform.currentText()
        only_undone = (self.combo_status.currentIndex() == 0)
        limit = self.spin_limit.value()

        self.btn_refresh.setEnabled(False)
        self.lbl_progress_text.setText("正在查询数据库待提取剧目...")

        def _fetch():
            return fetch_candidate_books_from_db(limit=limit, platform=platform, only_undone=only_undone)

        def _on_done(books: List[Dict[str, Any]]):
            self.btn_refresh.setEnabled(True)
            self._candidate_books = books
            self._render_table_rows(books)
            self.m_total.set_value(str(len(books)))
            self.m_processed.set_value("0")
            self.lbl_table_count.setText(f"共 {len(books)} 部待处理剧目")
            self.lbl_progress_text.setText(f"已就绪，已加载 {len(books)} 部候选剧目")
            self.progress_bar.setValue(0)
            self.notify("书单已刷新", f"成功载入 {len(books)} 部候选剧目", "success")

        def _on_err(err: str):
            self.btn_refresh.setEnabled(True)
            self.lbl_progress_text.setText(f"查询失败: {err}")
            self.notify("查询异常", err, "error")

        run_in_background(self, _fetch, on_success=_on_done, on_error=_on_err)

    def _render_table_rows(self, books: List[Dict[str, Any]]):
        """渲染表格所有候选行。"""
        self.table.setRowCount(len(books))
        for row, b in enumerate(books):
            # 0: 序号
            item_idx = QTableWidgetItem(str(row + 1))
            item_idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item_idx)

            # 1: 平台
            item_plat = QTableWidgetItem(b.get("platform", ""))
            self.table.setItem(row, 1, item_plat)

            # 2: 书名
            item_title = QTableWidgetItem(f"《{b.get('title', '')}》")
            self.table.setItem(row, 2, item_title)

            # 3: 作者
            item_author = QTableWidgetItem(b.get("author", ""))
            self.table.setItem(row, 3, item_author)

            # 4: 章节数
            item_ch = QTableWidgetItem(str(b.get("chapter_count", 0)))
            item_ch.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, item_ch)

            # 判断是否已存在 DNA
            has_dna = b.get("has_dna", False)
            dna = b.get("dna_profile")

            if has_dna and dna:
                dq = dna.get("data_quality_check", {})
                payoff = dq.get("has_payoff_in_opening", True)
                track = dna.get("track_dna", {})
                prot = dna.get("protagonist_and_cheat_dna", {})
                conflict = dna.get("conflict_and_payoff_dna", {})

                # 5: 开篇质检
                q_text = "[合格] 有爽点" if payoff else "[淘汰] 极度慢热"
                item_q = QTableWidgetItem(q_text)
                item_q.setForeground(QColor("#2E7D32" if payoff else "#D32F2F"))
                self.table.setItem(row, 5, item_q)

                # 6: 赛道
                t_str = f"{track.get('gender_track', '')} · {track.get('core_sub_genre', '')}"
                self.table.setItem(row, 6, QTableWidgetItem(t_str))

                # 7: 外挂
                self.table.setItem(row, 7, QTableWidgetItem(prot.get("cheat_mechanism_type", "")))

                # 8: 反杀
                self.table.setItem(row, 8, QTableWidgetItem(conflict.get("first_counterattack_payoff", "")))

                # 9: 入库状态
                item_st = QTableWidgetItem("已入库 PG")
                item_st.setForeground(QColor("#1D9E75"))
                self.table.setItem(row, 9, item_st)

                # 10: 耗时
                self.table.setItem(row, 10, QTableWidgetItem("已提取"))
            else:
                # 待处理占位
                item_q = QTableWidgetItem("等待分析")
                item_q.setForeground(QColor("#888888"))
                self.table.setItem(row, 5, item_q)
                self.table.setItem(row, 6, QTableWidgetItem("-"))
                self.table.setItem(row, 7, QTableWidgetItem("-"))
                self.table.setItem(row, 8, QTableWidgetItem("-"))
                item_st = QTableWidgetItem("等待中")
                item_st.setForeground(QColor("#888888"))
                self.table.setItem(row, 9, item_st)
                self.table.setItem(row, 10, QTableWidgetItem("-"))

            # 存储原始数据指针
            item_idx.setData(Qt.ItemDataRole.UserRole, b)

    def _on_table_row_selected(self):
        """表格行选中时更新当前选中的 DNA 结果指针。"""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            self._current_selected_dna = None
            self.btn_copy_selected.setEnabled(False)
            return
        row = selected_rows[0].row()
        item = self.table.item(row, 0)
        if not item:
            self._current_selected_dna = None
            self.btn_copy_selected.setEnabled(False)
            return

        book = item.data(Qt.ItemDataRole.UserRole)
        if not book:
            self._current_selected_dna = None
            self.btn_copy_selected.setEnabled(False)
            return

        dna = book.get("dna_profile")
        if dna:
            self._current_selected_dna = dna
            self.btn_copy_selected.setEnabled(True)
        else:
            self._current_selected_dna = None
            self.btn_copy_selected.setEnabled(False)

    def _toggle_start_batch(self):
        """启动批量提取任务。"""
        if self._worker and self._worker.isRunning():
            return

        if not self._candidate_books:
            self.notify("暂无待处理书籍", "请先点击「刷新待处理列表」加载候选网文", "warning")
            return

        discard_slow = self.chk_discard_slow.isChecked()
        self._batch_results.clear()

        # UI 按钮状态切换
        self.btn_start_batch.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暂停")
        self.btn_pause.setIcon(FIF.PAUSE)
        self.btn_stop.setEnabled(True)
        self.btn_refresh.setEnabled(False)

        # 启动后台 Worker 线程
        self._worker = BatchDnaWorker(self._candidate_books, discard_slow_burn=discard_slow, parent=self)
        self._worker.item_started.connect(self._on_item_started)
        self._worker.item_finished.connect(self._on_item_finished)
        self._worker.stats_updated.connect(self._on_stats_updated)
        self._worker.log_line.connect(self._on_worker_log)
        self._worker.all_finished.connect(self._on_all_finished)
        self._worker.start()

        self.notify("批量提取已启动", f"正在开始批量分析 {len(self._candidate_books)} 部小说...", "info")

    def _toggle_pause_batch(self):
        """暂停或恢复批量提取任务。"""
        if not self._worker or not self._worker.isRunning():
            return

        if self._worker.is_paused():
            self._worker.resume()
            self.btn_pause.setText("暂停")
            self.btn_pause.setIcon(FIF.PAUSE)
            self.lbl_progress_text.setText("已恢复批量提取...")
            self.notify("已恢复", "批量提取任务继续执行", "info")
        else:
            self._worker.pause()
            self.btn_pause.setText("继续")
            self.btn_pause.setIcon(FIF.PLAY)
            self.lbl_progress_text.setText("任务已暂停")
            self.notify("已暂停", "批量提取任务已暂停，点击继续可恢复", "warning")

    def _stop_batch(self):
        """终止批量提取任务。"""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self.btn_stop.setEnabled(False)
            self.lbl_progress_text.setText("正在终止批量任务...")
            self.notify("正在终止", "已发送停止信号，将在当前小说处理完毕后停止", "warning")

    def _on_item_started(self, idx: int, total: int, book: Dict[str, Any]):
        """单本小说开始提取。"""
        title = book.get("title", "")
        self.lbl_progress_text.setText(f"[{idx+1}/{total}] 正在调用大模型分析《{title}》...")

        item_q = self.table.item(idx, 5)
        if item_q:
            item_q.setText("正在分析...")
            item_q.setForeground(QColor("#0078D4"))

        item_st = self.table.item(idx, 9)
        if item_st:
            item_st.setText("提取中")
            item_st.setForeground(QColor("#0078D4"))

        # 滚动并将该行选中高亮
        self.table.selectRow(idx)

    def _on_item_finished(self, idx: int, total: int, book: Dict[str, Any], res: Dict[str, Any]):
        """单本小说提取完成。"""
        st = res.get("status", "ERROR")
        dna = res.get("dna")
        elapsed = res.get("elapsed", 0.0)

        # 挂载提取结果
        if dna:
            book["dna_profile"] = dna
            book["has_dna"] = True
            self._batch_results.append(dna)
            self._current_selected_dna = dna
            self.btn_copy_selected.setEnabled(True)

        # 更新表格行单元格
        if dna:
            dq = dna.get("data_quality_check", {})
            payoff = dq.get("has_payoff_in_opening", True)
            track = dna.get("track_dna", {})
            prot = dna.get("protagonist_and_cheat_dna", {})
            conflict = dna.get("conflict_and_payoff_dna", {})

            # 5: 开篇质检
            item_q = self.table.item(idx, 5)
            if item_q:
                q_text = "[合格] 有爽点" if payoff else "[淘汰] 极度慢热"
                item_q.setText(q_text)
                item_q.setForeground(QColor("#2E7D32" if payoff else "#D32F2F"))

            # 6: 赛道
            t_str = f"{track.get('gender_track', '')} · {track.get('core_sub_genre', '')}"
            self.table.setItem(idx, 6, QTableWidgetItem(t_str))

            # 7: 外挂
            self.table.setItem(idx, 7, QTableWidgetItem(prot.get("cheat_mechanism_type", "")))

            # 8: 反杀
            self.table.setItem(idx, 8, QTableWidgetItem(conflict.get("first_counterattack_payoff", "")))

            # 9: 入库状态
            item_st = self.table.item(idx, 9)
            if item_st:
                if st == "QUALIFIED":
                    item_st.setText("已入库 PG")
                    item_st.setForeground(QColor("#1D9E75"))
                else:
                    item_st.setText("慢热丢弃")
                    item_st.setForeground(QColor("#D32F2F"))
        else:
            item_q = self.table.item(idx, 5)
            if item_q:
                item_q.setText("分析失败")
                item_q.setForeground(QColor("#D32F2F"))
            item_st = self.table.item(idx, 9)
            if item_st:
                item_st.setText("提取失败")
                item_st.setForeground(QColor("#D32F2F"))

        # 10: 耗时
        self.table.setItem(idx, 10, QTableWidgetItem(f"{elapsed}s"))

    def _on_stats_updated(self, total: int, processed: int, qualified: int, slow_burn: int, failed: int):
        """指标卡片与进度条同步更新。"""
        self.m_total.set_value(str(total))
        self.m_processed.set_value(f"{processed} / {total}")
        self.m_qualified.set_value(str(qualified))
        self.m_slow.set_value(str(slow_burn))
        self.m_failed.set_value(str(failed))

        pct = int(processed / total * 100) if total > 0 else 0
        self.progress_bar.setValue(pct)
        self.lbl_progress_text.setText(f"进度: {processed}/{total} ({pct}%) | 合格: {qualified} | 慢热丢弃: {slow_burn} | 异常: {failed}")

    def _on_worker_log(self, text: str):
        # 可选控制台输出
        print(f"[DNA提取] {text}")

    def _on_all_finished(self, summary: Dict[str, Any]):
        """批量任务全部结束。"""
        self.btn_start_batch.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_refresh.setEnabled(True)
        self._worker = None

        total = summary.get("total", 0)
        qualified = summary.get("qualified", 0)
        slow_burn = summary.get("slow_burn", 0)
        failed = summary.get("failed", 0)

        msg = f"批量提取完成！共处理 {total} 部，合格入库 {qualified} 部，慢热淘汰 {slow_burn} 部，失败 {failed} 部。"
        self.lbl_progress_text.setText(msg)
        self.notify("批量任务完成", msg, "success")

    def _copy_result_json(self):
        """复制结构化 JSON 到剪贴板。"""
        if not self._current_selected_dna:
            self.notify("未选中或未提取", "请先在列表中选中已提取 DNA 的小说行", "warning")
            return
        text = json.dumps(self._current_selected_dna, ensure_ascii=False, indent=2)
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.notify("复制成功", "结构化爆款 DNA JSON 已复制到系统剪贴板！", "success")

    def _export_batch_json(self):
        """导出当前批次已成功提取的 DNA 数据为 JSON 文件。"""
        if not self._batch_results:
            self.notify("无提取数据可导出", "请在批量提取完成后再导出", "warning")
            return

        out_dir = PROJECT_ROOT / "data" / "dna_reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = out_dir / f"batch_novel_dna_{ts}.json"

        try:
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(self._batch_results, f, ensure_ascii=False, indent=2)
            self.notify("导出成功", f"已保存 {len(self._batch_results)} 份 DNA 报告至：{out_file.name}", "success")
            reveal(out_file)
        except Exception as exc:
            self.notify("导出失败", str(exc), "error")

    # =========================================================================
    # 单本测试支持
    # =========================================================================
    def _run_single_manual_test(self):
        """单本手动测试。"""
        title = self.txt_manual_title.text().strip()
        author = self.txt_manual_author.text().strip()
        category = self.txt_manual_category.text().strip()
        intro = self.txt_manual_intro.toPlainText().strip()
        titles = [line.strip() for line in self.txt_manual_titles.toPlainText().splitlines() if line.strip()]
        content_text = self.txt_manual_contents.toPlainText().strip()

        if not title:
            self.notify("请输入书名", "书名不能为空", "warning")
            return
        if not content_text:
            self.notify("缺少正文", "请至少填入前几章正文", "warning")
            return

        chapters = [("测试章节", content_text)]
        prompt = build_user_prompt(title, author, category, intro, titles, chapters)

        self.btn_run_single_manual.setEnabled(False)
        self.lbl_progress_text.setText(f"正在分析单本测试《{title}》...")

        def _do_call():
            return call_llm_extract_dna(prompt)

        def _on_done(result: Tuple[bool, Any]):
            self.btn_run_single_manual.setEnabled(True)
            ok, payload = result
            if ok:
                self._current_selected_dna = payload
                self.btn_copy_selected.setEnabled(True)
                self.notify("单本提取成功", f"《{title}》DNA 提炼完成！", "success")
            else:
                self.notify("提取失败", str(payload), "error")

        def _on_err(err: str):
            self.btn_run_single_manual.setEnabled(True)
            self.notify("异常", err, "error")

        run_in_background(self, _do_call, on_success=_on_done, on_error=_on_err)
