"""粗筛工作台：对应「2_执行粗筛主程序.bat」。

支持导入任意 txt 待筛剧单 → 调用 main.py 执行粗筛 →
通过弹框查看「待筛总数/放行/淘汰/各级分布」与「分级结果详情」。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    DoubleSpinBox,
    FluentIcon as FIF,
    LineEdit,
    PushButton,
)

from desktop_app.common.constants import (
    REPORT_JSON,
    SCRIPT_MAIN,
    build_cmd,
    count_non_empty_lines,
    list_candidate_txt_files,
)
from desktop_app.common.shell import read_json_safe
from desktop_app.components.base_page import BasePage
from desktop_app.components.busy import guard_action
from desktop_app.components.task_console import TaskConsole
from desktop_app.pages.screening_results_dialog import ScreeningResultsDialog
from desktop_app.runtime.function_runner import run_in_background

_START_RE = re.compile(r"正在开始筛选剧目[:：]\s*《(.+?)》")


def _read_report_payload() -> dict | None:
    """在后台线程读取并解析粗筛报告。"""
    return read_json_safe(REPORT_JSON)


class FileDropLineEdit(LineEdit):
    """支持拖拽 .txt 文件自动填入路径的输入框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.setText(path)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class ScreeningPage(BasePage):
    """粗筛工作台主页面。"""

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
        self._report_data: Optional[dict] = None
        self._results_dialog: Optional[ScreeningResultsDialog] = None

        self.add_widget(self._build_params())

        self.console = TaskConsole(
            self, title="粗筛运行日志", run_text="开始粗筛", min_log_height=200
        )
        self.console.set_command_provider(self._build_command)
        self.console.line_received.connect(self._on_line)
        self.console.finished.connect(self._on_finished)

        # 头部查看分级结果按钮：筛选中或存在结果时显示，点击弹窗查看
        self.view_results_btn = PushButton(FIF.VIEW, "查看分级结果", self.console)
        self.view_results_btn.setMinimumWidth(165)
        self.view_results_btn.clicked.connect(self._show_results_dialog)
        self.console.add_action_widget(self.view_results_btn)
        self.view_results_btn.hide()

        self.add_widget(self.console, 1)

        # 初始化默认候选文件（若项目根目录下存在 待筛剧目.txt 等则默认填入）
        default_files = list_candidate_txt_files()
        if default_files:
            self.file_edit.setText(str(default_files[0]))
        else:
            self._on_file_changed()

        self._load_report()

    # ------------------------------------------------------------------ 参数区
    def _build_params(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        # 待筛剧单：文件输入框 + 导入文件按钮
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(BodyLabel("待筛剧单", card))

        self.file_edit = FileDropLineEdit(card)
        self.file_edit.setPlaceholderText("请点击右侧「导入文件」或拖拽 .txt 文件到此处…")
        self.file_edit.setClearButtonEnabled(True)
        self.file_edit.setMinimumWidth(380)
        self.file_edit.textChanged.connect(self._on_file_changed)
        row1.addWidget(self.file_edit)

        self.import_btn = PushButton(FIF.FOLDER, "导入文件", card)
        self.import_btn.clicked.connect(guard_action(self.import_btn, self._import_file))
        row1.addWidget(self.import_btn)

        self.file_hint = CaptionLabel("", card)
        self.file_hint.setTextColor("#5F5E5A", "#B4B2A9")
        row1.addWidget(self.file_hint)
        row1.addStretch(1)
        layout.addLayout(row1)

        # 精筛阈值
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

        # 运行选项
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

    # ------------------------------------------------------------------ 文件导入
    def _import_file(self) -> None:
        """唤起系统文件选择对话框导入 txt 剧单。"""
        start_dir = str(PROJECT_ROOT)
        current = self._selected_file()
        if current and current.parent.is_dir():
            start_dir = str(current.parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择待筛剧单文件",
            start_dir,
            "文本文件 (*.txt);;所有文件 (*.*)",
        )
        if path:
            self.file_edit.setText(path)

    def _selected_file(self) -> Optional[Path]:
        raw = self.file_edit.text().strip()
        if not raw:
            return None
        p = Path(raw)
        return p if p.is_file() else None

    def _on_file_changed(self, *_args) -> None:
        raw = self.file_edit.text().strip()
        if not raw:
            self.file_hint.setText("未选择文件")
            return
        p = Path(raw)
        if not p.is_file():
            self.file_hint.setText("文件不存在")
            return
        count = count_non_empty_lines(p)
        self.file_hint.setText(f"共 {count} 部待筛")

    # ------------------------------------------------------------------ 运行与进度
    def _build_command(self) -> list[str] | None:
        path = self._selected_file()
        if path is None:
            self.notify("未选择有效剧单", "请点击「导入文件」选择待筛 txt 文件", "warning")
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

        # 启动时立即使「查看分级结果」按钮可见
        self.view_results_btn.show()
        self.view_results_btn.setText("查看分级结果 (筛选中…)")
        if self._results_dialog is not None:
            self._results_dialog.apply_report(self._report_data, is_running=True)

        return build_cmd(SCRIPT_MAIN, *args)

    def _on_line(self, text: str) -> None:
        if _START_RE.search(text):
            self._current += 1
            self.console.set_progress(self._current, self._total)

    def _on_finished(self, code: int) -> None:
        if code == 0:
            self._load_report()
            self.notify("粗筛完成", "可点击「查看分级结果」查看分级详情与汇总指标", "success")
        elif code > 0:
            if self._report_data:
                rows = self._report_data.get("results") or []
                self.view_results_btn.setText(f"查看分级结果 ({len(rows)}条)")
            else:
                self.view_results_btn.setText("查看分级结果")
            self.notify("粗筛异常中断", "请检查控制台运行日志", "error")

    # ------------------------------------------------------------------ 弹框交互与报告
    def _load_report(self) -> None:
        """后台异步读取报告，避免大文件阻塞界面。"""
        run_in_background(
            self,
            _read_report_payload,
            on_success=self._apply_report,
            on_error=lambda _msg: None,
        )

    def _apply_report(self, data: Optional[dict]) -> None:
        self._report_data = data
        rows = (data.get("results") or []) if data else []
        if rows or (REPORT_JSON.is_file() and data):
            self.view_results_btn.show()
            self.view_results_btn.setText(f"查看分级结果 ({len(rows)}条)")
        else:
            if not self.console.is_running:
                self.view_results_btn.hide()

        if self._results_dialog is not None:
            self._results_dialog.apply_report(data, is_running=self.console.is_running)

    def _on_dialog_cleared(self) -> None:
        """弹框内点击「清空结果」后的联动回调。"""
        self._report_data = None
        if not self.console.is_running:
            self.view_results_btn.hide()
        self.view_results_btn.setText("查看分级结果")

    def _show_results_dialog(self) -> None:
        """弹出分级结果与指标统计对话框。"""
        if self._results_dialog is None:
            self._results_dialog = ScreeningResultsDialog(
                self, on_cleared=self._on_dialog_cleared
            )
        self._results_dialog.apply_report(
            self._report_data, is_running=self.console.is_running
        )
        self._results_dialog.show()
        self._results_dialog.raise_()
        self._results_dialog.activateWindow()
