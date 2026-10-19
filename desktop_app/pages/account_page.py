"""常读账号管理：对应「1_常读平台登录.bat」。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
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
    SpinBox,
    StrongBodyLabel,
    TableWidget,
    TransparentToolButton,
)

from changdu_sync.account_limits import (
    DEFAULT_LIMIT,
    LIMITS_FILENAME,
    load_limits,
    save_limits,
)

from desktop_app.common.constants import (
    AUTH_FILE,
    AUTH_POOL_DIR,
    PROJECT_ROOT,
    SCRIPT_FETCH,
    build_cmd,
)
from desktop_app.common.shell import file_size_text, mtime_text, open_path
from desktop_app.components.base_page import BasePage
from desktop_app.components.busy import guard_action
from desktop_app.components.metric_card import MetricCard
from desktop_app.components.task_console import TaskConsole

ACCOUNT_COLS = (
    ("账号文件", "登录态凭证文件名（auth.json 为当前主账号，其余为账号池轮换账号）"),
    ("抓取上限", "该账号单次任务最多从常读获取的目标剧目数；0 = 不设上限"),
    ("更新时间", "该凭证文件的最近修改/更新时间戳"),
    ("大小", "凭证 JSON 文件的磁盘存储大小"),
    ("操作", "操作栏：删除不需要或已失效的账号凭证"),
)


class AccountPage(BasePage):
    def __init__(self, parent=None):
        super().__init__(
            "accountPage",
            "常读账号",
            "维护常读平台登录态。可添加多个账号，登录态保存在 auth_pool/，"
            "粗筛会自动轮换使用这些账号。",
            parent,
        )
        self.add_widget(self._build_status())
        self.add_widget(self._build_accounts())

        self.console = TaskConsole(
            self, title="登录 / 检测日志", run_text="添加账号并登录", min_log_height=150
        )
        self.console.set_command_provider(self._build_command)
        self.console.finished.connect(lambda _code: self._refresh_all())
        self.add_widget(self.console, 1)

        self._refresh_all()

    # ------------------------------------------------------------- 状态卡片
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
        refresh_btn.clicked.connect(guard_action(refresh_btn, self._refresh_all))
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
        self.m_updated = MetricCard("最近更新")
        self.m_pool = MetricCard("账号数量")
        self.m_size = MetricCard("凭证总大小")
        for item in (self.m_state, self.m_updated, self.m_pool, self.m_size):
            row.addWidget(item, 1)
        layout.addLayout(row)

        tip = CaptionLabel(
            "提示：点「添加账号并登录」会打开浏览器，扫码完成后自动保存为 auth_pool/auth_时间戳.json。"
            "粗筛阶段会按轮询方式自动切换可用账号。",
            card,
        )
        tip.setWordWrap(True)
        tip.setTextColor("#5F5E5A", "#B4B2A9")
        layout.addWidget(tip)
        return card

    # ------------------------------------------------------------- 账号列表
    def _build_accounts(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(StrongBodyLabel("账号列表", card))
        self.accounts_hint = CaptionLabel("", card)
        self.accounts_hint.setTextColor("#5F5E5A", "#B4B2A9")
        head.addWidget(self.accounts_hint)
        head.addStretch(1)
        layout.addLayout(head)

        self.table = TableWidget(card)
        self.table.setColumnCount(len(ACCOUNT_COLS))
        for idx, (col_name, tooltip) in enumerate(ACCOUNT_COLS):
            header_item = QTableWidgetItem(col_name)
            header_item.setToolTip(tooltip)
            self.table.setHorizontalHeaderItem(idx, header_item)
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 70)
        self.table.setMaximumHeight(240)
        layout.addWidget(self.table)
        return card

    # ------------------------------------------------------------- 刷新
    def _account_files(self) -> list[Path]:
        files: list[Path] = []
        seen: set[Path] = set()

        # 主账号 auth.json
        if AUTH_FILE.is_file():
            resolved = AUTH_FILE.resolve()
            files.append(resolved)
            seen.add(resolved)

        # 账号池（排除账号上限配置文件）
        if AUTH_POOL_DIR.is_dir():
            for p in sorted(AUTH_POOL_DIR.glob("*.json")):
                if p.name == LIMITS_FILENAME:
                    continue
                resolved = p.resolve()
                if resolved not in seen:
                    files.append(resolved)
                    seen.add(resolved)
        return files

    def _refresh_all(self) -> None:
        self._refresh_status()
        self._populate_accounts()

    def _refresh_status(self) -> None:
        files = self._account_files()
        total = len(files)

        if total:
            self.m_state.set_value("已登录")
            self.m_state.set_accent("#1D9E75")
            newest = max(files, key=lambda p: p.stat().st_mtime)
            self.m_updated.set_value(mtime_text(newest))
            self.m_pool.set_value(f"{total} 个")
            total_kb = sum(p.stat().st_size for p in files) / 1024.0
            self.m_size.set_value(f"{total_kb:.1f} KB")
            self.hint.setText("粗筛会自动轮换使用这些账号")
        else:
            self.m_state.set_value("未登录")
            self.m_state.set_accent("#E24B4A")
            self.m_updated.set_value("—")
            self.m_pool.set_value("0 个")
            self.m_size.set_value("—")
            self.hint.setText("需要先登录，否则只能走纯文本离线模式")

    def _populate_accounts(self) -> None:
        files = self._account_files()
        limits = load_limits()
        self.accounts_hint.setText(f"共 {len(files)} 个登录态（默认 {DEFAULT_LIMIT} 部，0 = 不限制）")
        self.table.setRowCount(len(files))

        for row, path in enumerate(files):
            is_main = path == AUTH_FILE.resolve()
            name = "auth.json（主账号）" if is_main else path.stem
            account_key = path.name
            try:
                size = file_size_text(path)
                updated = mtime_text(path)
            except OSError:
                size = updated = "—"

            self.table.setItem(row, 0, QTableWidgetItem(name))

            spin = SpinBox(self.table)
            spin.setRange(0, 9999)
            spin.setSuffix(" 部")
            spin.setValue(int(limits.get(account_key, DEFAULT_LIMIT)))
            spin.setToolTip("该账号最多从常读获取多少部筛选目标剧目；0 = 不限制")
            spin.valueChanged.connect(
                lambda value, key=account_key: self._save_limit(key, value)
            )
            spin_holder = QWidget(self.table)
            hs = QHBoxLayout(spin_holder)
            hs.setContentsMargins(4, 0, 4, 0)
            hs.addWidget(spin)
            self.table.setCellWidget(row, 1, spin_holder)

            self.table.setItem(row, 2, QTableWidgetItem(updated))
            self.table.setItem(row, 3, QTableWidgetItem(size))

            btn = TransparentToolButton(FIF.DELETE, self.table)
            btn.setToolTip("删除该登录态")
            btn.clicked.connect(
                lambda _checked=False, p=path: self._delete_account(p)
            )
            holder = QWidget(self.table)
            h = QHBoxLayout(holder)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(btn)
            self.table.setCellWidget(row, 4, holder)

    def _save_limit(self, account_key: str, value: int) -> None:
        limits = load_limits()
        limits[account_key] = int(value)
        save_limits(limits)

    def _delete_account(self, path: Path) -> None:
        name = path.name
        answer = QMessageBox.question(
            self,
            "删除登录态",
            f"确定删除登录态「{name}」吗？\n删除后该账号将不再参与粗筛轮换。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self.notify("删除失败", str(exc), "error")
            return
        limits = load_limits()
        limits.pop(path.name, None)
        save_limits(limits)
        self.notify("已删除", name, "success")
        self._refresh_all()

    # ------------------------------------------------------------- 登录
    def _build_command(self) -> list[str] | None:
        AUTH_POOL_DIR.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("auth_%Y%m%d_%H%M%S")
        target = AUTH_POOL_DIR / f"{name}.json"
        return build_cmd(SCRIPT_FETCH, "--login", "--auth-file", str(target))
