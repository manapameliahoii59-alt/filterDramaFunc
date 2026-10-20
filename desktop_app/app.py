"""桌面端入口：FluentWindow 主窗口 + 导航。

启动方式：
    python -m desktop_app.app
或在项目根目录双击「启动桌面端.bat」。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证项目根目录在 sys.path 最前（与直接 python main.py 的行为保持一致）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qfluentwidgets import (  # noqa: E402
    FluentIcon as FIF,
    FluentWindow,
    Theme,
    setFontFamilies,
    setTheme,
    setThemeColor,
)

from desktop_app.common.constants import APP_NAME, APP_SUBTITLE, APP_VERSION  # noqa: E402
from desktop_app.pages.account_page import AccountPage  # noqa: E402
from desktop_app.pages.daily_fetch_page import DailyFetchPage  # noqa: E402
from desktop_app.pages.novel_summary_page import NovelSummaryPage  # noqa: E402
from desktop_app.pages.screening_page import ScreeningPage  # noqa: E402
from desktop_app.pages.trends_page import TrendsPage  # noqa: E402

THEME_COLOR = "#1D9E75"

# 显式字体族链：保证中文在任何机器上都有可用字体，避免出现方块
FONT_FAMILIES = [
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI",
    "SimSun",
    "sans-serif",
]

# 窗口尺寸：偏宽扁（横屏友好），并按实际屏幕自动收敛，保证不会被裁掉
PREFERRED_WIDTH = 1560
PREFERRED_HEIGHT = 780
MIN_WIDTH = 1100
MIN_HEIGHT = 600
NAV_EXPAND_WIDTH = 176


class MainWindow(FluentWindow):
    """主窗口：左侧导航 + 功能页。"""

    def __init__(self):
        super().__init__()
        self._nav_expanded_once = False
        self.setWindowTitle(f"{APP_NAME} · {APP_SUBTITLE} v{APP_VERSION}")
        self._apply_initial_size()

        self.screening_page = ScreeningPage(self)
        self.novel_summary_page = NovelSummaryPage(self)
        self.trends_page = TrendsPage(self)
        self.daily_fetch_page = DailyFetchPage(self)
        self.account_page = AccountPage(self)

        self.addSubInterface(self.screening_page, FIF.FILTER, "粗筛工作台")
        self.addSubInterface(self.novel_summary_page, FIF.ROBOT, "小说DNA提取")
        self.addSubInterface(self.trends_page, FIF.MARKET, "小说风向标")
        self.addSubInterface(self.daily_fetch_page, FIF.CALENDAR, "每日筛选")
        self.addSubInterface(self.account_page, FIF.CONNECT, "常读账号")

        self.navigationInterface.setExpandWidth(NAV_EXPAND_WIDTH)
        self.switchTo(self.screening_page)

    def _apply_initial_size(self) -> None:
        """按屏幕可用区域决定初始尺寸：偏宽偏扁，但不超出屏幕。

        页面内容被包在可滚动的矮容器里（见 BasePage），因此窗口的自然高度
        只有 ~620，普通 resize 即可生效，无需再压制框架的「撑高」行为。
        """
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        screen = QApplication.primaryScreen()
        if screen is None:
            width, height = PREFERRED_WIDTH, PREFERRED_HEIGHT
        else:
            avail = screen.availableGeometry()
            # 预留窗口边框（标题栏）空间，避免底部被任务栏遮住
            width = min(PREFERRED_WIDTH, max(MIN_WIDTH, avail.width() - 80))
            height = min(PREFERRED_HEIGHT, max(MIN_HEIGHT, avail.height() - 52))

        self.resize(width, height)

    def showEvent(self, event):  # noqa: N802 - Qt 命名
        """首次显示后做一次收尾：展开导航 + 贴屏校正。

        FluentWindow 在首次 show 期间会把导航收成 COMPACT，且在 showEvent 内
        直接 expand() 会被随后的布局覆盖；因此延后到事件循环下一拍再处理。
        """
        super().showEvent(event)
        if not self._nav_expanded_once:
            self._nav_expanded_once = True
            QTimer.singleShot(0, self._after_first_show)

    def _after_first_show(self) -> None:
        self.navigationInterface.expand(useAni=False)
        self._fit_on_screen()

    def _fit_on_screen(self) -> None:
        """确保窗口完整落在屏幕可用区域内（不被任务栏遮挡），并居中。"""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()

        frame = self.frameGeometry()
        if frame.height() > avail.height():
            overflow = frame.height() - avail.height()
            self.resize(self.width(), max(MIN_HEIGHT, self.height() - overflow))

        frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        self.move(frame.topLeft())


def create_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSize(9)
    app.setFont(font)
    try:
        setFontFamilies(FONT_FAMILIES)
    except Exception:
        pass

    setTheme(Theme.AUTO)
    setThemeColor(THEME_COLOR)
    return app


def main() -> int:
    app = create_app()
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
