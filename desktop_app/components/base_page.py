"""功能页基类：统一标题区、通知与内容布局。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    InfoBar,
    InfoBarPosition,
)


class _PageScrollArea(QScrollArea):
    """页面内容滚动容器。

    各功能页内容天然较高（约 950px）。若让页面如实汇报这个高度，
    QStackedWidget 会把主窗口的「最小高度」顶到 ~1000px，窗口就再也压不矮。
    这里用一个「矮」的滚动容器包裹内容：对外只汇报很小的尺寸提示，
    窗口可以很矮；窗口拉高时内容自适应铺满，窗口偏矮时内部滚动，不会裁切。
    """

    COMPACT_HEIGHT = 420
    COMPACT_MIN_HEIGHT = 120

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet("QScrollArea{background:transparent;border:none;}")

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(super().sizeHint().width(), self.COMPACT_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(0, self.COMPACT_MIN_HEIGHT)


class BasePage(QWidget):
    """所有功能页的公共外壳。

    - 顶部：标题 + 说明
    - 中部：内容区（可滚动，由子类填充）
    - 统一提供 notify() 用于右上角气泡提示
    """

    def __init__(self, object_name: str, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(12)

        # 导航栏已经显示了页面名称，这里不再重复渲染大标题（title 仅保留作参数兼容）
        if subtitle:
            label = BodyLabel(subtitle, self)
            label.setWordWrap(True)
            label.setTextColor("#5F5E5A", "#B4B2A9")
            outer.addWidget(label)

        host = QWidget(self)
        host.setObjectName(f"{object_name}Content")
        self.content = QVBoxLayout(host)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(12)

        self._scroll = _PageScrollArea(self)
        self._scroll.setWidget(host)
        outer.addWidget(self._scroll, 1)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self.content.addWidget(widget, stretch)

    def add_layout(self, layout, stretch: int = 0) -> None:
        self.content.addLayout(layout, stretch)

    def notify(self, title: str, content: str = "", level: str = "info") -> None:
        """右上角气泡提示。"""
        factory = {
            "info": InfoBar.info,
            "success": InfoBar.success,
            "warning": InfoBar.warning,
            "error": InfoBar.error,
        }.get(level, InfoBar.info)
        factory(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3200,
            parent=self.window(),
        )
