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
    MessageBox,
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
    get_candidate_books_stats_from_db,
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
    ("数据质检/爽点", 130),
    ("赛道与细分流派", 170),
    ("破局底牌分类", 150),
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
                self.log_line.emit(f"  -> 《{title}》开篇未出底牌/无爽点：代码已自动丢弃不入库 (耗时 {res.get('elapsed')}s)")
            elif st == "LOW_CONFIDENCE_DISCARDED":
                slow_burn += 1
                self.log_line.emit(f"  -> 《{title}》提取把握度为低：代码已自动洗掉丢弃不入库 (耗时 {res.get('elapsed')}s)")
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
            "全自动批量提炼数据质检过滤、赛道世界观、主角人设与破局底牌、开局冲突与反杀闭环、第一集音画匹配锚点、二级爆款标签树 6 维结构化清单，"
            "在代码中自动洗掉无底牌爽点或低把握度废书，为短剧第一集原素材自动化比对提供高精度特征基底。",
            parent,
        )

        self._candidate_books: List[Dict[str, Any]] = []
        self._current_selected_dna: Optional[Dict[str, Any]] = None
        self._worker: Optional[BatchDnaWorker] = None
        self._batch_results: List[Dict[str, Any]] = []
        self._current_stats: Dict[str, Any] = {}
        self._actual_undone_count: int = 0

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
        self.m_processed = MetricCard("本批提取进度")
        self.m_qualified = MetricCard("放行入库 (有爽点)", accent="#1D9E75")
        self.m_slow = MetricCard("质检淘汰 (代码丢弃)", accent="#D32F2F")
        self.m_slow.setToolTip("因前6章未亮出底牌爽点或提取把握度为低，被代码自动洗掉丢弃的废书数量")
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
        self.spin_limit.setRange(1, 2000)
        self.spin_limit.setValue(20)
        self.spin_limit.setToolTip("每次批量提取的网文部数（默认20部，可自行调大）")
        r1.addWidget(self.spin_limit)

        self.chk_discard_slow = CheckBox("自动质检过滤（无底牌或低把握度不入库）", card)
        self.chk_discard_slow.setChecked(True)
        self.chk_discard_slow.setToolTip("严格执行代码质检：若前6章连外挂/底牌都没亮出来（false），或提取把握度为低，直接丢弃不入库")
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

        self.btn_view_error = PushButton(FIF.INFO, "查看失败原因", card)
        self.btn_view_error.setEnabled(False)
        self.btn_view_error.clicked.connect(self._show_selected_error_dialog)
        r2.addWidget(self.btn_view_error)

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
        self.table.cellDoubleClicked.connect(lambda row, col: self._show_selected_error_dialog())
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
        """刷新数据库中待处理的小说列表与统计。"""
        if self._worker and self._worker.isRunning():
            self.notify("正在执行批量任务", "请等待当前批次完成或终止后再刷新", "warning")
            return

        platform = self.combo_platform.currentText()
        only_undone = (self.combo_status.currentIndex() == 0)
        limit = self.spin_limit.value()

        self.btn_refresh.setEnabled(False)
        self.lbl_progress_text.setText("正在查询数据库待提取剧目与全量未处理统计...")

        def _fetch():
            books = fetch_candidate_books_from_db(limit=limit, platform=platform, only_undone=only_undone)
            stats = get_candidate_books_stats_from_db(platform=platform)
            return books, stats

        def _on_done(result: Tuple[List[Dict[str, Any]], Dict[str, Any]]):
            books, stats = result
            self.btn_refresh.setEnabled(True)
            self._candidate_books = books
            self._current_stats = stats

            # 当前筛选条件下的未处理总数与全平台未处理总数
            current_undone = stats.get("current_undone", 0)
            total_undone = stats.get("total_undone", 0)
            self._actual_undone_count = current_undone

            self._render_table_rows(books)

            # 1. 顶部指标卡：待处理总数展示数据库真实未处理总数
            if only_undone:
                self.m_total.set_value(str(current_undone))
                if platform != "全部平台":
                    self.m_total.set_subtext(f"全平台共 {total_undone} 部")
                    self.m_total.setToolTip(f"{platform} 待提取: {current_undone} 部\n全平台未提取: {total_undone} 部")
                else:
                    ready = stats.get("total_ready", 0)
                    done = stats.get("total_done", 0)
                    self.m_total.set_subtext(f"已入库 {done} / 就绪共 {ready}")
                    tip_lines = [f"{p}: {d['undone']} 部待处理" for p, d in stats.get("by_platform", {}).items()]
                    self.m_total.setToolTip("各平台待处理分布：\n" + "\n".join(tip_lines))
            else:
                current_total = stats.get("current_total", 0)
                self.m_total.set_value(str(current_total))
                self.m_total.set_subtext(f"其中未处理: {current_undone}")
                self.m_total.setToolTip(f"就绪书籍共: {current_total} 部\n其中未处理: {current_undone} 部")

            # 2. 本批提取进度
            self.m_processed.set_value(f"0 / {len(books)}")
            self.m_processed.set_subtext(f"批次上限: {limit}")
            self.m_qualified.set_value("0")
            self.m_slow.set_value("0")
            self.m_failed.set_value("0")

            # 3. 表格标题统计与进度文字
            if len(books) < current_undone:
                self.lbl_table_count.setText(f"本批已载入 {len(books)} 部 / 库中未处理共 {current_undone} 部")
            else:
                self.lbl_table_count.setText(f"共 {len(books)} 部候选剧目（已全量加载）")

            self.lbl_progress_text.setText(
                f"已就绪，当前批次已载入 {len(books)} 部候选剧目（数据库未处理共 {current_undone} 部）"
            )
            self.progress_bar.setValue(0)
            self.notify("书单已刷新", f"成功载入 {len(books)} 部候选剧目（实际未处理: {current_undone} 部）", "success")

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
                conf = str(dq.get("extraction_confidence", "高")).strip()
                is_low_conf = ("低" in conf or conf.lower() == "low")
                synopsis_valid = dq.get("is_synopsis_valid", True)

                track = dna.get("track_dna", {})
                prot = (
                    dna.get("protagonist_and_leverage_dna")
                    or dna.get("protagonist_and_cheat_dna", {})
                )
                conflict = dna.get("conflict_and_payoff_dna", {})

                # 5: 数据质检
                if not payoff:
                    q_text = "[淘汰] 无爽点底牌"
                    q_color = "#D32F2F"
                elif is_low_conf:
                    q_text = f"[淘汰] 把握度低({conf})"
                    q_color = "#E65100"
                else:
                    q_text = f"[合格] {conf}把握度"
                    q_color = "#2E7D32"

                item_q = QTableWidgetItem(q_text)
                item_q.setForeground(QColor(q_color))
                item_q.setToolTip(f"简介真实: {'是' if synopsis_valid else '否(已剔除假简介)'}\n开篇爽点: {'有' if payoff else '无(未亮出底牌)'}\n把握度: {conf}")
                self.table.setItem(row, 5, item_q)

                # 6: 赛道
                t_str = f"{track.get('gender_track', '')} · {track.get('core_sub_genre', '')}"
                item_track = QTableWidgetItem(t_str)
                item_track.setToolTip(f"世界观基底: {track.get('worldview_setting', '')}\n核心细分流派: {track.get('core_sub_genre', '')}")
                self.table.setItem(row, 6, item_track)

                # 7: 破局底牌分类
                lev_cat = prot.get("leverage_category") or prot.get("cheat_mechanism_type", "")
                lev_rule = prot.get("leverage_specific_mechanism") or prot.get("cheat_specific_rule", "")
                item_lev = QTableWidgetItem(lev_cat)
                item_lev.setToolTip(f"破局底牌分类: {lev_cat}\n底牌运作规则:\n{lev_rule}\n\n开局弱势: {prot.get('surface_status', '')}\n真实身份: {prot.get('hidden_or_awakened_identity', '')}")
                self.table.setItem(row, 7, item_lev)

                # 8: 反杀
                c_payoff = conflict.get("first_counterattack_payoff", "")
                item_payoff = QTableWidgetItem(c_payoff)
                item_payoff.setToolTip(
                    f"首个反杀爽点:\n{c_payoff}\n\n"
                    f"迫害者关系: {conflict.get('antagonist_relationship', '')}\n"
                    f"导火索事件: {conflict.get('inciting_incident', '')}\n"
                    f"反派拉仇恨恶行: {conflict.get('extreme_aggro_behavior', '')}"
                )
                self.table.setItem(row, 8, item_payoff)

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
        """表格行选中时更新当前选中的 DNA 结果指针或错误信息。"""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            self._current_selected_dna = None
            self.btn_copy_selected.setEnabled(False)
            self.btn_view_error.setEnabled(False)
            return
        row = selected_rows[0].row()
        if 0 <= row < len(self._candidate_books):
            book = self._candidate_books[row]
        else:
            book = None

        if not book:
            self._current_selected_dna = None
            self.btn_copy_selected.setEnabled(False)
            self.btn_view_error.setEnabled(False)
            return

        dna = book.get("dna_profile")
        err_msg = book.get("error_message")

        if dna:
            self._current_selected_dna = dna
            self.btn_copy_selected.setEnabled(True)
            self.btn_view_error.setEnabled(False)
            dq = dna.get("data_quality_check", {})
            payoff = dq.get("has_payoff_in_opening", True)
            conf = dq.get("extraction_confidence", "高")
            self.lbl_progress_text.setText(f"已选中小说：《{book.get('title')}》（DNA提取完成 | 开篇爽点: {'有' if payoff else '无'} | 把握度: {conf}）")
        elif err_msg:
            self._current_selected_dna = None
            self.btn_copy_selected.setEnabled(False)
            self.btn_view_error.setEnabled(True)
            self.lbl_progress_text.setText(f"《{book.get('title')}》提取失败: {err_msg[:70]}...（点击「查看失败原因」看排查方案）")
        else:
            self._current_selected_dna = None
            self.btn_copy_selected.setEnabled(False)
            self.btn_view_error.setEnabled(False)

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
            conf = str(dq.get("extraction_confidence", "高")).strip()
            is_low_conf = ("低" in conf or conf.lower() == "low")
            synopsis_valid = dq.get("is_synopsis_valid", True)

            track = dna.get("track_dna", {})
            prot = (
                dna.get("protagonist_and_leverage_dna")
                or dna.get("protagonist_and_cheat_dna", {})
            )
            conflict = dna.get("conflict_and_payoff_dna", {})

            # 5: 数据质检
            item_q = self.table.item(idx, 5)
            if item_q:
                if not payoff:
                    q_text = "[淘汰] 无爽点底牌"
                    q_color = "#D32F2F"
                elif is_low_conf:
                    q_text = f"[淘汰] 把握度低({conf})"
                    q_color = "#E65100"
                else:
                    q_text = f"[合格] {conf}把握度"
                    q_color = "#2E7D32"
                item_q.setText(q_text)
                item_q.setForeground(QColor(q_color))
                item_q.setToolTip(f"简介真实: {'是' if synopsis_valid else '否(已剔除假简介)'}\n开篇爽点: {'有' if payoff else '无(未亮出底牌)'}\n把握度: {conf}")

            # 6: 赛道
            t_str = f"{track.get('gender_track', '')} · {track.get('core_sub_genre', '')}"
            item_track = QTableWidgetItem(t_str)
            item_track.setToolTip(f"世界观基底: {track.get('worldview_setting', '')}\n核心细分流派: {track.get('core_sub_genre', '')}")
            self.table.setItem(idx, 6, item_track)

            # 7: 破局底牌分类
            lev_cat = prot.get("leverage_category") or prot.get("cheat_mechanism_type", "")
            lev_rule = prot.get("leverage_specific_mechanism") or prot.get("cheat_specific_rule", "")
            item_lev = QTableWidgetItem(lev_cat)
            item_lev.setToolTip(f"破局底牌分类: {lev_cat}\n底牌运作规则:\n{lev_rule}\n\n开局弱势: {prot.get('surface_status', '')}\n真实身份: {prot.get('hidden_or_awakened_identity', '')}")
            self.table.setItem(idx, 7, item_lev)

            # 8: 反杀
            c_payoff = conflict.get("first_counterattack_payoff", "")
            item_payoff = QTableWidgetItem(c_payoff)
            item_payoff.setToolTip(
                f"首个反杀爽点:\n{c_payoff}\n\n"
                f"迫害者关系: {conflict.get('antagonist_relationship', '')}\n"
                f"导火索事件: {conflict.get('inciting_incident', '')}\n"
                f"反派拉仇恨恶行: {conflict.get('extreme_aggro_behavior', '')}"
            )
            self.table.setItem(idx, 8, item_payoff)

            # 9: 入库状态
            item_st = self.table.item(idx, 9)
            if item_st:
                if st == "QUALIFIED":
                    item_st.setText("已入库 PG")
                    item_st.setForeground(QColor("#1D9E75"))
                elif st == "SLOW_BURN_DISCARDED":
                    item_st.setText("慢热丢弃")
                    item_st.setForeground(QColor("#D32F2F"))
                    item_st.setToolTip("开篇未亮出破局底牌/无爽点，代码自动洗掉丢弃不入库")
                elif st == "LOW_CONFIDENCE_DISCARDED":
                    item_st.setText("低把握度丢弃")
                    item_st.setForeground(QColor("#E65100"))
                    item_st.setToolTip("提取把握度为低，代码自动洗掉丢弃不入库")
                else:
                    item_st.setText("未入库")
                    item_st.setForeground(QColor("#888888"))
        else:
            err_msg = res.get("message", "未知错误")
            book["error_message"] = err_msg
            book["error_status"] = st

            # 5: 开篇质检
            item_q = self.table.item(idx, 5)
            if item_q:
                if st == "MISSING_CHAPTERS":
                    q_text = "[失败] 缺有效正文"
                elif st == "LLM_ERROR":
                    q_text = "[失败] 大模型异常"
                else:
                    q_text = "[失败] 分析异常"
                item_q.setText(q_text)
                item_q.setForeground(QColor("#D32F2F"))
                item_q.setToolTip(f"失败类型: {st}\n失败原因: {err_msg}\n（双击该行或点击上方「查看失败原因」查看具体排查指引）")

            # 8: 反杀/详情列展示简要错误原因
            item_conflict = self.table.item(idx, 8)
            if item_conflict:
                item_conflict.setText(f"原因: {err_msg[:24]}...")
                item_conflict.setForeground(QColor("#D32F2F"))
                item_conflict.setToolTip(f"失败原因:\n{err_msg}")

            # 9: 入库状态
            item_st = self.table.item(idx, 9)
            if item_st:
                item_st.setText("提取失败")
                item_st.setForeground(QColor("#D32F2F"))
                item_st.setToolTip(f"状态: {st}\n原因: {err_msg}")

        # 10: 耗时
        self.table.setItem(idx, 10, QTableWidgetItem(f"{elapsed}s"))

    def _show_selected_error_dialog(self):
        """弹出详细对话框展示选中行小说的提取失败具体原因与排查方案。"""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            self.notify("未选中剧目", "请先在列表中选中一行小说", "warning")
            return
        row = selected_rows[0].row()
        if 0 <= row < len(self._candidate_books):
            book = self._candidate_books[row]
        else:
            book = None

        if not book:
            return

        err_msg = book.get("error_message")
        st = book.get("error_status", "ERROR")
        title = book.get("title", "未知")
        plat = book.get("platform", "")
        bid = book.get("book_id", "")
        author = book.get("author", "")

        if not err_msg:
            if book.get("has_dna"):
                self.notify("提取成功", f"《{title}》已正常提取爆款 DNA，未发生错误。", "info")
            else:
                self.notify("尚未分析", f"《{title}》尚未执行提取分析。", "info")
            return

        advice = ""
        if st == "SLOW_BURN_DISCARDED":
            advice = (
                "\n\n💡 质检淘汰原因：\n"
                "该小说前 6 章未亮出破局底牌或无核心反击爽点（has_payoff_in_opening = False），"
                "不符合短剧第一集快节奏高密度叙事要求，已被代码自动过滤洗掉，不予入库。"
            )
        elif st == "LOW_CONFIDENCE_DISCARDED":
            advice = (
                "\n\n💡 质检淘汰原因：\n"
                "该小说大模型提取把握度为低（extraction_confidence = 低，正文短缺/模糊/异常），"
                "已按质检规则自动洗掉废书，不予入库。"
            )
        elif st == "MISSING_CHAPTERS" or "正文章节不足" in err_msg:
            advice = (
                "\n\n💡 解决方案建议：\n"
                "1. 该小说当前在数据库中缺少有效正文（章节正文为空或少于50字）；\n"
                "2. 请前往【小说风向标】重新抓取正文，或运行抓取脚本补齐前几章内容后再提取。"
            )
        elif "未配置 DASHSCOPE_API_KEY" in err_msg or "DEEPSEEK_API_KEY" in err_msg:
            advice = (
                "\n\n💡 解决方案建议：\n"
                "1. 请打开项目根目录下的 .env 配置文件；\n"
                "2. 填入有效的 DASHSCOPE_API_KEY 或 DEEPSEEK_API_KEY。"
            )
        elif "Insufficient Balance" in err_msg or "402" in err_msg:
            advice = (
                "\n\n💡 解决方案建议：\n"
                "大模型账户余额不足（HTTP 402），请登录阿里云百炼或 DeepSeek 开放平台充值。"
            )
        elif "timed out" in err_msg.lower() or "timeout" in err_msg.lower():
            advice = (
                "\n\n💡 解决方案建议：\n"
                "大模型请求网络超时，已对请求禁用慢速思考模式加速响应，可点击「开始批量提取」重新尝试。"
            )

        content = (
            f"【剧目书名】：《{title}》\n"
            f"【来源平台】：{plat}（作者：{author}，ID：{bid}）\n"
            f"【失败分类】：{st}\n\n"
            f"【具体错误日志】：\n{err_msg}"
            f"{advice}"
        )

        w = MessageBox(f"提取失败诊断详情 - 《{title}》", content, self.window())
        w.yesButton.setText("知道了")
        w.cancelButton.setVisible(False)
        w.exec()

    def _on_stats_updated(self, total: int, processed: int, qualified: int, slow_burn: int, failed: int):
        """指标卡片与进度条同步更新。"""
        # 实时根据已完成（合格入库 + 慢热淘汰）扣减实际剩余待处理总数
        remaining = max(0, self._actual_undone_count - (qualified + slow_burn))
        self.m_total.set_value(str(remaining))
        self.m_processed.set_value(f"{processed} / {total}")
        self.m_qualified.set_value(str(qualified))
        self.m_slow.set_value(str(slow_burn))
        self.m_failed.set_value(str(failed))

        pct = int(processed / total * 100) if total > 0 else 0
        self.progress_bar.setValue(pct)
        self.lbl_progress_text.setText(
            f"本批进度: {processed}/{total} ({pct}%) | 剩余待处理: {remaining} | 合格: {qualified} | 慢热丢弃: {slow_burn} | 异常: {failed}"
        )

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

        # 异步刷新最新的真实数据库统计
        platform = self.combo_platform.currentText()

        def _fetch_stats():
            return get_candidate_books_stats_from_db(platform=platform)

        def _on_stats_done(stats: Dict[str, Any]):
            self._current_stats = stats
            undone = stats.get("current_undone", 0)
            self._actual_undone_count = undone
            self.m_total.set_value(str(undone))
            msg = f"批量提取完成！本批处理 {total} 部，合格入库 {qualified} 部，慢热淘汰 {slow_burn} 部，失败 {failed} 部。库中剩余未处理: {undone} 部。"
            self.lbl_progress_text.setText(msg)
            self.lbl_table_count.setText(f"本批已完成 {total} 部 / 库中剩余未处理 {undone} 部")
            self.notify("批量任务完成", msg, "success")

        run_in_background(self, _fetch_stats, on_success=_on_stats_done)

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
