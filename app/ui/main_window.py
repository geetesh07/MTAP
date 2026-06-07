from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QStackedWidget, QFrame, QPushButton,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from app.ui import theme

from app.ui.mode_selector import ModeSelectorScreen
from app.ui.blank_drawing_screen import BlankDrawingScreen
from app.ui.proposal_screen import ProposalScreen
from app.ui.production_screen import ProductionScreen
from app.ui.logo import make_logo_pixmap
from app.utils.config import APP_NAME, APP_BRAND, APP_FULL_NAME, APP_VERSION


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_BRAND} — {APP_FULL_NAME}")
        self.setMinimumSize(1200, 760)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_top_bar())

        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        self._mode_selector = ModeSelectorScreen()
        self._blank_screen = BlankDrawingScreen()
        self._proposal_screen = ProposalScreen()
        self._production_screen = ProductionScreen()

        self.stack.addWidget(self._mode_selector)      # 0
        self.stack.addWidget(self._blank_screen)        # 1
        self.stack.addWidget(self._proposal_screen)     # 2
        self.stack.addWidget(self._production_screen)   # 3

        self._mode_selector.mode_selected.connect(self._on_mode_selected)
        self._blank_screen.back_requested.connect(self._go_home)
        self._proposal_screen.back_requested.connect(self._go_home)
        self._production_screen.back_requested.connect(self._go_home)

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(60)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(22, 0, 24, 0)
        layout.setSpacing(14)

        # Logo mark
        logo_mark = QLabel()
        logo_mark.setObjectName("LogoMark")
        logo_mark.setPixmap(make_logo_pixmap(34))
        logo_mark.setFixedWidth(38)

        # Wordmark + tagline stacked
        word_box = QVBoxLayout()
        word_box.setContentsMargins(0, 0, 0, 0)
        word_box.setSpacing(0)

        wordmark = QLabel(APP_BRAND)
        wordmark.setObjectName("AppLogo")
        wordmark.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))

        tagline = QLabel(APP_FULL_NAME.upper())
        tagline.setObjectName("AppSubtitle")
        tagline.setFont(QFont("Segoe UI", 8))

        word_box.addWidget(wordmark)
        word_box.addWidget(tagline)

        # Right side: version + attribution stacked
        right_box = QVBoxLayout()
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(1)
        right_box.setAlignment(Qt.AlignmentFlag.AlignRight)

        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("AppVersion")
        version.setAlignment(Qt.AlignmentFlag.AlignRight)

        made_by = QLabel("MADE BY NTS")
        made_by.setObjectName("MadeBy")
        made_by.setAlignment(Qt.AlignmentFlag.AlignRight)

        right_box.addWidget(version)
        right_box.addWidget(made_by)

        # Theme toggle
        self._theme_btn = QPushButton()
        self._theme_btn.setObjectName("ThemeToggle")
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.clicked.connect(self._toggle_theme)
        self._update_theme_button()

        layout.addWidget(logo_mark)
        layout.addLayout(word_box)
        layout.addStretch()
        layout.addWidget(self._theme_btn)
        layout.addSpacing(16)
        layout.addLayout(right_box)

        return bar

    def _update_theme_button(self) -> None:
        # Label shows the theme you'll switch TO.
        self._theme_btn.setText("☀ LIGHT" if theme.current_theme() == "dark" else "☾ DARK")

    def _toggle_theme(self) -> None:
        theme.toggle()
        self._update_theme_button()

    def _on_mode_selected(self, mode: str) -> None:
        mapping = {"blank": 1, "proposal": 2, "production": 3}
        self.stack.setCurrentIndex(mapping.get(mode, 0))

    def _go_home(self) -> None:
        self.stack.setCurrentIndex(0)
