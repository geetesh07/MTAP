"""Reusable custom widgets for the MTAP UI."""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QButtonGroup
from PyQt6.QtCore import Qt, pyqtSignal


class YesNoToggle(QWidget):
    """A compact segmented YES / NO control (replaces a checkbox)."""

    changed = pyqtSignal(bool)

    def __init__(self, value: bool = False):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._yes = QPushButton("YES")
        self._no = QPushButton("NO")
        for b in (self._yes, self._no):
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(28)
            b.setMinimumWidth(54)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self._yes)
        self._group.addButton(self._no)

        layout.addWidget(self._yes)
        layout.addWidget(self._no)
        layout.addStretch()

        self._yes.clicked.connect(lambda: self._set(True, emit=True))
        self._no.clicked.connect(lambda: self._set(False, emit=True))
        self._set(value, emit=False)

    def _set(self, value: bool, emit: bool) -> None:
        self._value = value
        self._yes.setChecked(value)
        self._no.setChecked(not value)
        if emit:
            self.changed.emit(value)

    def setValue(self, value: bool) -> None:
        self._set(bool(value), emit=False)

    def value(self) -> bool:
        return self._value
