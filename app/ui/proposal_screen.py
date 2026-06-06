from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont


class ProposalScreen(QWidget):
    back_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_placeholder())

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setObjectName("TopBar")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(16)

        back_btn = QPushButton("← BACK")
        back_btn.setObjectName("BackButton")
        back_btn.setFixedWidth(100)
        back_btn.clicked.connect(self.back_requested)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)

        title = QLabel("PROPOSAL DRAWING")
        title.setObjectName("ScreenTitle")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))

        layout.addWidget(back_btn)
        layout.addWidget(divider)
        layout.addWidget(title)
        layout.addStretch()

        return bar

    def _build_placeholder(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text = QLabel("PROPOSAL DRAWING")
        text.setObjectName("PlaceholderText")
        text.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("Under Development")
        sub.setObjectName("PlaceholderSubText")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(text)
        layout.addSpacing(8)
        layout.addWidget(sub)

        return panel
