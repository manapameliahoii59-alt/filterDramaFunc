"""指标卡：展示单个汇总数字。"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget

Accent = str | tuple[str, str] | None


class MetricCard(CardWidget):
    def __init__(self, label: str, value: str = "—", accent: Accent = None, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(96)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(2)

        self._caption = CaptionLabel(label, self)
        self._caption.setTextColor("#5F5E5A", "#B4B2A9")
        layout.addWidget(self._caption)

        self._value = BodyLabel(value, self)
        font = self._value.font()
        font.setPointSize(font.pointSize() + 7)
        font.setBold(True)
        self._value.setFont(font)
        layout.addWidget(self._value)

        if accent is not None:
            self.set_accent(accent)

        self._sub_caption = CaptionLabel("", self)
        self._sub_caption.setTextColor("#888888", "#9E9E9E")
        self._sub_caption.setVisible(False)
        layout.addWidget(self._sub_caption)

    def set_value(self, value: object) -> None:
        self._value.setText(str(value))

    def set_subtext(self, text: str) -> None:
        if text:
            self._sub_caption.setText(str(text))
            self._sub_caption.setVisible(True)
        else:
            self._sub_caption.setText("")
            self._sub_caption.setVisible(False)

    def set_accent(self, accent: Accent) -> None:
        if isinstance(accent, tuple):
            self._value.setTextColor(*accent)
        elif accent:
            self._value.setTextColor(accent)
