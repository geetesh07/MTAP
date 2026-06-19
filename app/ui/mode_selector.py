from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QCursor


class ModeCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, mode_key: str, title: str, description: str, active: bool = True):
        super().__init__()
        self._mode_key = mode_key
        self._active = active
        self.setObjectName("ModeCard")
        self.setFixedSize(280, 230)
        if active:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._build_ui(title, description, active)

    def _build_ui(self, title: str, description: str, active: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(0)

        # Status badge
        badge = QLabel("ACTIVE" if active else "COMING SOON")
        badge.setObjectName("CardBadge" if active else "CardBadgeDim")
        badge.setFixedWidth(90)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(badge)

        layout.addSpacing(18)

        # Title
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle" if active else "CardTitleDim")
        title_label.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        layout.addSpacing(10)

        # Description
        desc_label = QLabel(description)
        desc_label.setObjectName("CardDesc" if active else "CardDescDim")
        desc_label.setWordWrap(True)
        desc_label.setFont(QFont("Segoe UI", 10))
        layout.addWidget(desc_label)

        layout.addStretch()

        # Click indicator
        arrow = QLabel("OPEN  →" if active else "——")
        arrow.setObjectName("CardArrow" if active else "CardArrowDim")
        arrow.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        layout.addWidget(arrow)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._active:
            self.clicked.emit(self._mode_key)


class ModeSelectorScreen(QWidget):
    mode_selected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(64, 56, 64, 40)
        layout.setSpacing(0)

        # Section header
        header = QLabel("SELECT DRAWING MODE")
        header.setObjectName("SectionHeader")
        layout.addWidget(header)

        layout.addSpacing(10)

        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        layout.addSpacing(52)

        # Cards row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(24)
        cards_row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        cards_data = [
            (
                "blank",
                "BLANK\nDRAWING",
                "Raw ground stock before fluting. Shank, neck, point, and chamfer geometry.",
                True,
            ),
            (
                "proposal",
                "PROPOSAL\nDRAWING",
                "Full to-scale tool drawing with helix projection. Customer approval document.",
                True,
            ),
            (
                "production",
                "PRODUCTION\nDRAWING",
                "Shop-floor manufacturing drawing. GD&T, surface finish, inspection dimensions.",
                False,
            ),
        ]

        for mode_key, title, desc, active in cards_data:
            card = ModeCard(mode_key, title, desc, active)
            card.clicked.connect(self.mode_selected)
            cards_row.addWidget(card)

        layout.addLayout(cards_row)
        layout.addStretch()

        # Footer
        footer = QLabel(
            "MTAP by NTS  v0.1.0   ·   Blank Drawing active   ·   "
            "Proposal Drawing active   ·   Production in development   ·   MADE BY NTS"
        )
        footer.setObjectName("FooterNote")
        layout.addWidget(footer)
