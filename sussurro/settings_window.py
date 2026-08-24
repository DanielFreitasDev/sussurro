"""Janela de configurações — preto e branco, sem moldura do sistema."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import (Property, QEasingCurve, QObject, QPoint,
                            QPropertyAnimation, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QCursor, QFontMetrics, QGuiApplication,
                           QIcon, QPainter, QPen)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame,
                               QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QScrollBar, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import __version__, history, theme
from .config import (HOTKEYS, LANGUAGES, MODELS, PASTE_MODES, RESULT_FEEDBACK,
                     THEMES, Config)
from .recorder import list_sources
from .transcriber import check_key

ASSETS = Path(__file__).parent / "assets"
ICON = ASSETS / "icon.svg"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "sussurro"

SHADOW = 26          # respiro ao redor da janela para a sombra


def _glyphs(pal: theme.Palette) -> tuple[str, str, str]:
    """Gera chevron, check e o mic do cabeçalho na cor do tema atual."""
    CACHE.mkdir(parents=True, exist_ok=True)
    suffix = "dark" if pal.dark else "light"
    stroke = pal.text.name()          # texto do tema
    inverse = pal.on_accent.name()    # sobre o preenchimento sólido

    chevron = CACHE / f"chevron-{suffix}.svg"
    chevron.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
        f'<path d="M4 6.5 L8 10.5 L12 6.5" fill="none" stroke="{stroke}" stroke-opacity="0.55"'
        ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        encoding="utf-8")

    check = CACHE / f"check-{suffix}.svg"
    check.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
        f'<path d="M3.5 8.4 L6.4 11.3 L12.5 5.2" fill="none" stroke="{inverse}"'
        ' stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        encoding="utf-8")

    mic = CACHE / f"mic-{suffix}.svg"
    mic.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        f'<rect x="12" y="4" width="8" height="15" rx="4" fill="{stroke}"/>'
        f'<path d="M7 15a9 9 0 0 0 18 0" fill="none" stroke="{stroke}" stroke-width="2.4"'
        ' stroke-linecap="round"/>'
        f'<path d="M16 24v4" stroke="{stroke}" stroke-width="2.4" stroke-linecap="round"/>'
        '</svg>',
        encoding="utf-8")
    return chevron.as_posix(), check.as_posix(), mic.as_posix()


def _stylesheet(pal: theme.Palette, chevron: str, check: str) -> str:
    c = theme.css
    return f"""
    QWidget#root {{ background: transparent; }}
    QFrame#window {{
        background: {c(pal.bg)};
        border: 1px solid {c(pal.border)};
        border-radius: 22px;
    }}
    QWidget#header, QWidget#page {{ background: transparent; }}
    QWidget#footer {{ background: transparent; border-top: 1px solid {c(pal.border)}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QFrame#card {{
        background: {c(pal.surface)};
        border: 1px solid {c(pal.border)};
        border-radius: 16px;
    }}
    QLabel {{ color: {c(pal.text)}; background: transparent; }}
    QLabel#title {{ font-size: 19px; font-weight: 600; }}
    QLabel#subtitle, QLabel#hint {{ color: {c(pal.muted)}; font-size: 12px; }}
    QLabel#section {{
        color: {c(pal.faint)}; font-size: 11px; font-weight: 600;
        letter-spacing: 1.2px; text-transform: uppercase;
    }}
    QLabel#field {{ color: {c(pal.muted)}; font-size: 12px; }}
    QLineEdit, QComboBox, QPlainTextEdit {{
        background: {c(pal.surface_hi)};
        border: 1px solid {c(pal.border)};
        border-radius: 11px; padding: 9px 12px;
        color: {c(pal.text)}; font-size: 13px;
        selection-background-color: {c(pal.text, 0.22)};
        selection-color: {c(pal.text)};
    }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {c(pal.border_hi)};
    }}
    QComboBox::drop-down {{ border: none; width: 28px; }}
    QComboBox::down-arrow {{ image: url({chevron}); width: 14px; height: 14px; }}
    QComboBox QAbstractItemView {{
        background: {c(pal.bg)};
        border: 1px solid {c(pal.border)};
        border-radius: 11px;
        color: {c(pal.text)};
        selection-background-color: {c(pal.text, 0.12)};
        selection-color: {c(pal.text)};
        padding: 5px; outline: none;
    }}
    QCheckBox {{ color: {c(pal.text)}; font-size: 13px; spacing: 9px; background: transparent; }}
    QCheckBox::indicator {{
        width: 17px; height: 17px; border-radius: 6px;
        border: 1px solid {c(pal.border_hi)};
        background: transparent;
    }}
    QCheckBox::indicator:checked {{
        background: {c(pal.text)};
        border: 1px solid {c(pal.text)};
        image: url({check});
    }}
    """


class _Header(QWidget):
    """Cabeçalho arrastável — a janela não tem decoração do sistema."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("header")
        self._drag: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            origin = self.window().frameGeometry().topLeft()
            self._drag = event.globalPosition().toPoint() - origin
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None


class _Combo(QComboBox):
    """Seletor que ignora a roda do mouse: só muda por clique ou teclado."""

    def __init__(self) -> None:
        super().__init__()
        # Sem isto o item mais largo vira a largura mínima da janela inteira.
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def wheelEvent(self, event) -> None:
        event.ignore()


class _Prompt(QPlainTextEdit):
    """Campo de texto que devolve a rolagem à página quando não tem o que rolar."""

    def wheelEvent(self, event) -> None:
        if self.verticalScrollBar().maximum() == 0:
            event.ignore()
            return
        super().wheelEvent(event)



class _Button(QPushButton):
    """Botão pintado à mão: hover e clique com transição suave (o QSS não anima)."""

    VARIANTS = ("default", "primary", "ghost", "link")

    def __init__(self, text: str = "", variant: str = "default") -> None:
        super().__init__(text)
        self._variant = variant if variant in self.VARIANTS else "default"
        self._hover = 0.0
        self._press = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme.font(12 if self._variant == "link" else 13))
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        self._hover_anim = QPropertyAnimation(self, b"hoverAmount", self)
        self._hover_anim.setDuration(190)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._press_anim = QPropertyAnimation(self, b"pressAmount", self)
        self._press_anim.setDuration(130)
        self._press_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # -- propriedades animáveis ---------------------------------------------
    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hoverAmount = Property(float, _get_hover, _set_hover)

    def _get_press(self) -> float:
        return self._press

    def _set_press(self, value: float) -> None:
        self._press = value
        self.update()

    pressAmount = Property(float, _get_press, _set_press)

    def _animate(self, anim: QPropertyAnimation, target: float) -> None:
        anim.stop()
        anim.setStartValue(anim.targetObject().property(anim.propertyName().data().decode()))
        anim.setEndValue(target)
        anim.start()

    # -- eventos -------------------------------------------------------------
    def enterEvent(self, event) -> None:
        self._animate(self._hover_anim, 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate(self._hover_anim, 0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._animate(self._press_anim, 1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._animate(self._press_anim, 0.0)
        super().mouseReleaseEvent(event)

    def sizeHint(self):
        hint = super().sizeHint()
        metrics = QFontMetrics(self.font())
        if self._variant == "ghost":
            return hint
        padding = 20 if self._variant == "link" else 34
        height = 28 if self._variant == "link" else 36
        hint.setWidth(metrics.horizontalAdvance(self.text()) + padding)
        hint.setHeight(height)
        return hint

    # -- pintura -------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        pal = theme.p()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        hover = self._hover
        press = self._press
        enabled = self.isEnabled()
        dim = 1.0 if enabled else 0.42
        radius = 11.0
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        if self._variant == "primary":
            if hover > 0.01:      # halo suave ao redor, em vez de piscar a cor
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(theme.mix(pal.text, 0.16 * hover)))
                halo = rect.adjusted(-3 * hover, -3 * hover, 3 * hover, 3 * hover)
                painter.drawRoundedRect(halo, radius + 3, radius + 3)
            fill = theme.mix(pal.text, (1.0 - 0.05 * hover - 0.08 * press) * dim)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(QPen(theme.mix(pal.on_accent, dim)))
        elif self._variant == "default":
            base = 0.055 + 0.065 * hover + 0.03 * press
            painter.setPen(QPen(theme.mix(pal.text, (0.13 + 0.17 * hover) * dim), 1.0))
            painter.setBrush(QBrush(theme.mix(pal.text, base * dim)))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(QPen(theme.mix(pal.text, dim)))
        elif self._variant == "ghost":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(theme.mix(pal.text, (0.07 * hover + 0.04 * press))))
            painter.drawRoundedRect(rect, 10.0, 10.0)
            painter.setPen(QPen(theme.mix(pal.text, (0.5 + 0.5 * hover) * dim)))
        else:  # link
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(theme.mix(pal.text, 0.05 * hover)))
            painter.drawRoundedRect(rect, 8.0, 8.0)
            painter.setPen(QPen(theme.mix(pal.text, (0.52 + 0.48 * hover) * dim)))

        if self.isCheckable() and self.isChecked() and self._variant == "default":
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(theme.mix(pal.text, 0.07)))
            painter.drawRoundedRect(rect, radius, radius)
            painter.restore()
            painter.setPen(QPen(theme.mix(pal.text, dim)))

        painter.setFont(self.font())
        align = (Qt.AlignmentFlag.AlignLeft if self._variant == "link"
                 else Qt.AlignmentFlag.AlignHCenter) | Qt.AlignmentFlag.AlignVCenter
        text_rect = self.rect().adjusted(9 if self._variant == "link" else 0, 0, -2, 0)
        painter.drawText(text_rect, align, self.text())
        painter.end()


class _HistoryItem(_Button):
    """Linha do histórico: encurta o texto em vez de esticar a janela."""

    def __init__(self, text: str) -> None:
        super().__init__(text, variant="link")
        self._full = text
        self.setToolTip("Clique para copiar")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(28)

    def resizeEvent(self, event) -> None:
        available = max(60, self.width() - 18)
        self.setText(QFontMetrics(self.font()).elidedText(
            self._full, Qt.TextElideMode.ElideRight, available))
        super().resizeEvent(event)


class _ScrollBar(QScrollBar):
    """Barra de rolagem em pílula, que engorda suavemente sob o cursor."""

    TRACK = 14
    MARGIN = 6

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Vertical)
        self.setFixedWidth(self.TRACK)
        self._hover = 0.0
        self._drag = None
        self._anim = QPropertyAnimation(self, b"hoverAmount", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hoverAmount = Property(float, _get_hover, _set_hover)

    def _animate_hover(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def _handle(self) -> QRectF:
        span = self.height() - self.MARGIN * 2
        total = self.maximum() - self.minimum() + self.pageStep()
        if total <= 0 or span <= 0:
            return QRectF()
        height = max(42.0, span * self.pageStep() / total)
        travel = span - height
        progress = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        width = 5.0 + 3.0 * self._hover
        return QRectF((self.TRACK - width) / 2, self.MARGIN + travel * progress,
                      width, height)

    def paintEvent(self, _event) -> None:
        if self.maximum() == self.minimum():
            return
        pal = theme.p()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        track = QRectF((self.TRACK - 3) / 2, self.MARGIN, 3.0,
                       self.height() - self.MARGIN * 2)
        painter.setBrush(QBrush(theme.mix(pal.text, 0.05 * self._hover)))
        painter.drawRoundedRect(track, 1.5, 1.5)

        handle = self._handle()
        painter.setBrush(QBrush(theme.mix(pal.text, 0.20 + 0.20 * self._hover)))
        painter.drawRoundedRect(handle, handle.width() / 2, handle.width() / 2)
        painter.end()

    def enterEvent(self, event) -> None:
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._drag is None:
            self._animate_hover(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        handle = self._handle()
        pos = event.position()
        if handle.contains(pos):
            self._drag = pos.y() - handle.y()
        else:
            span = self.height() - self.MARGIN * 2 - handle.height()
            if span > 0:
                ratio = min(1.0, max(0.0, (pos.y() - self.MARGIN - handle.height() / 2) / span))
                self.setValue(int(self.minimum() + ratio * (self.maximum() - self.minimum())))
            self._drag = handle.height() / 2
        self._animate_hover(1.0)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        handle = self._handle()
        span = self.height() - self.MARGIN * 2 - handle.height()
        if span <= 0:
            return
        ratio = min(1.0, max(0.0, (event.position().y() - self._drag - self.MARGIN) / span))
        self.setValue(int(self.minimum() + ratio * (self.maximum() - self.minimum())))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        if not self.rect().contains(event.position().toPoint()):
            self._animate_hover(0.0)
        event.accept()

    def wheelEvent(self, event) -> None:
        event.ignore()      # quem rola é a área, com animação


class _Scroll(QScrollArea):
    """Área de rolagem com inércia suave na roda do mouse."""

    def __init__(self) -> None:
        super().__init__()
        self.setVerticalScrollBar(_ScrollBar())
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._target = 0

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        steps = event.angleDelta().y()
        if bar.maximum() <= 0 or steps == 0:
            super().wheelEvent(event)
            return
        if self._anim.state() != QPropertyAnimation.State.Running:
            self._target = bar.value()
        step = max(52, int(bar.pageStep() * 0.34))
        self._target = max(0, min(bar.maximum(), self._target - int(steps / 120 * step)))
        self._anim.stop()
        self._anim.setStartValue(bar.value())
        self._anim.setEndValue(self._target)
        self._anim.start()
        event.accept()


class _KeyCheck(QObject):
    done = Signal(bool, str)


class SettingsWindow(QWidget):
    applied = Signal()

    def __init__(self, cfg: Config) -> None:
        super().__init__(None)
        self.cfg = cfg
        self.setObjectName("root")
        self.setWindowTitle("Sussurro — Configurações")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
        self.setMinimumSize(540, 480)
        self._placed = False
        self._closing = False

        self._fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_in.setDuration(220)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_out.setDuration(170)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out.finished.connect(self._finish_close)

        self._checker = _KeyCheck()
        self._checker.done.connect(self._on_key_checked)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(SHADOW, SHADOW - 6, SHADOW, SHADOW + 6)
        shell.setSpacing(0)

        self.frame = QFrame()
        self.frame.setObjectName("window")
        self.glow = QGraphicsDropShadowEffect(self)
        self.glow.setBlurRadius(48)
        self.glow.setOffset(0, 12)
        self.frame.setGraphicsEffect(self.glow)
        shell.addWidget(self.frame)

        outer = QVBoxLayout(self.frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = _Header()
        head = QHBoxLayout(header)
        head.setContentsMargins(24, 20, 16, 14)
        head.setSpacing(13)
        self.badge = QLabel()
        self.badge.setFixedSize(30, 30)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Sussurro")
        title.setObjectName("title")
        subtitle = QLabel(f"Ditado por voz com Groq Whisper · v{__version__}")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        head.addLayout(titles, 1)
        close_x = _Button("✕", "ghost")
        close_x.setFixedSize(30, 30)
        close_x.setCursor(Qt.CursorShape.PointingHandCursor)
        close_x.setToolTip("Fechar (Esc)")
        close_x.clicked.connect(self.close)
        head.addWidget(close_x, 0, Qt.AlignmentFlag.AlignTop)
        outer.addWidget(header)

        scroll = _Scroll()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        page = QWidget()
        page.setObjectName("page")
        self.body = QVBoxLayout(page)
        self.body.setContentsMargins(24, 4, 20, 22)
        self.body.setSpacing(16)
        scroll.setWidget(page)
        self.scroll = scroll
        outer.addWidget(scroll, 1)

        self._build_connection()
        self._build_transcription()
        self._build_input()
        self._build_system()
        self._build_history()
        self.body.addStretch(1)

        footer = QWidget()
        footer.setObjectName("footer")
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(24, 14, 24, 18)
        foot.setSpacing(10)
        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        close_btn = _Button("Fechar")
        close_btn.clicked.connect(self.close)
        save_btn = _Button("Salvar", "primary")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        foot.addWidget(close_btn)
        foot.addWidget(save_btn)
        outer.addWidget(footer)

        self.restyle()
        self._load_values()

    # -- aparência -----------------------------------------------------------
    def restyle(self) -> None:
        """Reaplica a paleta (troca de tema do sistema ou da preferência)."""
        pal = theme.p()
        chevron, check, mic = _glyphs(pal)
        self.setStyleSheet(_stylesheet(pal, chevron, check))
        self.badge.setPixmap(QIcon(mic).pixmap(30, 30))
        self.glow.setColor(QColor(0, 0, 0, 190 if pal.dark else 70))
        self._key_status_color = None
        self.key_status.setStyleSheet(f"color: {theme.css(pal.muted)}; font-size: 12px;")

    # -- construção ----------------------------------------------------------
    def _card(self, section: str) -> QVBoxLayout:
        label = QLabel(section)
        label.setObjectName("section")
        self.body.addWidget(label)
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        self.body.addWidget(card)
        return layout

    def _row(self, layout: QVBoxLayout, label: str, widget: QWidget,
             hint: str = "") -> None:
        caption = QLabel(label)
        caption.setObjectName("field")
        layout.addWidget(caption)
        layout.addWidget(widget)
        if hint:
            note = QLabel(hint)
            note.setObjectName("hint")
            note.setWordWrap(True)
            layout.addWidget(note)

    def _build_connection(self) -> None:
        card = self._card("Conexão")
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("gsk_…")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.key_edit, 1)
        self.reveal_btn = _Button("Mostrar")
        self.reveal_btn.setCheckable(True)
        self.reveal_btn.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        self.test_btn = _Button("Testar")
        self.test_btn.clicked.connect(self._test_key)
        row.addWidget(self.reveal_btn)
        row.addWidget(self.test_btn)
        holder = QWidget()
        holder.setLayout(row)
        self._row(card, "Chave da API Groq", holder,
                  "Crie em console.groq.com/keys · a variável de ambiente "
                  "GROQ_API_KEY, se existir, tem prioridade.")
        self.key_status = QLabel("")
        self.key_status.setObjectName("hint")
        self.key_status.setWordWrap(True)
        card.addWidget(self.key_status)

        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:3128")
        self._row(card, "Proxy (opcional)", self.proxy_edit,
                  "Vazio detecta sozinho: variáveis de ambiente, depois a configuração "
                  "de proxy do sistema.")

    def _build_transcription(self) -> None:
        card = self._card("Transcrição")
        self.model_combo = _Combo()
        for value, label in MODELS:
            self.model_combo.addItem(label, value)
        self._row(card, "Modelo", self.model_combo)

        self.lang_combo = _Combo()
        for value, label in LANGUAGES:
            self.lang_combo.addItem(label, value)
        self._row(card, "Idioma", self.lang_combo,
                  "Fixar o idioma acelera e evita traduções indesejadas.")

        self.prompt_edit = _Prompt()
        self.prompt_edit.setFixedHeight(74)
        self.prompt_edit.setPlaceholderText(
            "Nomes próprios, siglas e jargões que o modelo costuma errar…")
        self._row(card, "Vocabulário / contexto", self.prompt_edit,
                  "Enviado como prompt ao Whisper (limite de 224 tokens).")

    def _build_input(self) -> None:
        card = self._card("Atalho e áudio")
        self.hotkey_combo = _Combo()
        for value, label in HOTKEYS:
            self.hotkey_combo.addItem(label, value)
        self._row(card, "Tecla de ditado (segure para falar)", self.hotkey_combo)

        mic_row = QHBoxLayout()
        mic_row.setContentsMargins(0, 0, 0, 0)
        mic_row.setSpacing(8)
        self.mic_combo = _Combo()
        refresh = _Button("Atualizar")
        refresh.clicked.connect(self._reload_mics)
        mic_row.addWidget(self.mic_combo, 1)
        mic_row.addWidget(refresh)
        mic_holder = QWidget()
        mic_holder.setLayout(mic_row)
        self._row(card, "Microfone", mic_holder)

        self.paste_combo = _Combo()
        for value, label in PASTE_MODES:
            self.paste_combo.addItem(label, value)
        self._row(card, "Como entregar o texto", self.paste_combo,
                  "No modo automático, terminais recebem Ctrl+Shift+V.")

        self.restore_check = QCheckBox(
            "Restaurar a área de transferência anterior após colar")
        card.addWidget(self.restore_check)

    def _build_system(self) -> None:
        card = self._card("Sistema")
        self.theme_combo = _Combo()
        for value, label in THEMES:
            self.theme_combo.addItem(label, value)
        self._row(card, "Tema", self.theme_combo)
        self.position_combo = _Combo()
        self.position_combo.addItem("Rodapé da tela", "bottom")
        self.position_combo.addItem("Topo da tela", "top")
        self._row(card, "Posição do indicador", self.position_combo)
        self.feedback_combo = _Combo()
        for value, label in RESULT_FEEDBACK:
            self.feedback_combo.addItem(label, value)
        self._row(card, "Ao concluir a transcrição", self.feedback_combo,
                  "O texto é colado do mesmo jeito — isto controla só o indicador.")
        self.autostart_check = QCheckBox("Iniciar junto com a sessão")
        card.addWidget(self.autostart_check)
        self.history_check = QCheckBox("Guardar histórico local das transcrições")
        card.addWidget(self.history_check)

    def _build_history(self) -> None:
        card = self._card("Histórico recente")
        self.history_box = QVBoxLayout()
        self.history_box.setSpacing(8)
        card.addLayout(self.history_box)
        clear = _Button("Limpar histórico")
        clear.clicked.connect(self._clear_history)
        card.addWidget(clear, 0, Qt.AlignmentFlag.AlignLeft)
        self.refresh_history()

    # -- dados ---------------------------------------------------------------
    def _load_values(self) -> None:
        self.key_edit.setText(self.cfg.api_key)
        self.proxy_edit.setText(self.cfg.proxy)
        self._select(self.model_combo, self.cfg.model)
        self._select(self.lang_combo, self.cfg.language)
        self.prompt_edit.setPlainText(self.cfg.prompt)
        self._select(self.hotkey_combo, self.cfg.hotkey)
        self._reload_mics()
        self._select(self.paste_combo, self.cfg.paste_mode)
        self.restore_check.setChecked(self.cfg.restore_clipboard)
        self._select(self.theme_combo, self.cfg.theme)
        self.autostart_check.setChecked(self.cfg.autostart_enabled)
        self.history_check.setChecked(self.cfg.keep_history)
        self._select(self.position_combo, self.cfg.overlay_position)
        self._select(self.feedback_combo, self.cfg.result_feedback)

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _reload_mics(self) -> None:
        current = self.mic_combo.currentData() or self.cfg.input_device
        self.mic_combo.clear()
        self.mic_combo.addItem("Padrão do sistema", "")
        for name, description in list_sources():
            self.mic_combo.addItem(description, name)
        self._select(self.mic_combo, current)

    def refresh_history(self) -> None:
        while self.history_box.count():
            item = self.history_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        entries = history.recent(6)
        if not entries:
            empty = QLabel("Nada por aqui ainda.")
            empty.setObjectName("hint")
            self.history_box.addWidget(empty)
            return
        for entry in entries:
            text = " ".join((entry.get("text") or "").split())
            stamp = time.strftime("%d/%m %H:%M", time.localtime(entry.get("ts", 0)))
            button = _HistoryItem(f"{stamp}  ·  {text}")
            button.clicked.connect(lambda _=False, t=text: self._copy(t))
            self.history_box.addWidget(button)

    def _copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self._flash("Copiado para a área de transferência.")

    def _clear_history(self) -> None:
        history.clear()
        self.refresh_history()
        self._flash("Histórico apagado.")

    # -- ações ---------------------------------------------------------------
    def _test_key(self) -> None:
        key = self.key_edit.text().strip() or self.cfg.resolved_key()
        proxy = self.proxy_edit.text().strip()
        self.test_btn.setEnabled(False)
        self.key_status.setText("Verificando…")

        def job() -> None:
            ok, message = check_key(key, proxy)
            self._checker.done.emit(ok, message)

        threading.Thread(target=job, daemon=True).start()

    def _on_key_checked(self, ok: bool, message: str) -> None:
        pal = theme.p()
        self.test_btn.setEnabled(True)
        self.key_status.setText(("✓ " if ok else "✕ ") + message)
        tone = pal.text if ok else pal.muted
        self.key_status.setStyleSheet(f"color: {theme.css(tone)}; font-size: 12px;")

    def _save(self) -> None:
        self.cfg.api_key = self.key_edit.text().strip()
        self.cfg.proxy = self.proxy_edit.text().strip()
        self.cfg.model = self.model_combo.currentData()
        self.cfg.language = self.lang_combo.currentData()
        self.cfg.prompt = self.prompt_edit.toPlainText().strip()
        self.cfg.hotkey = self.hotkey_combo.currentData()
        self.cfg.input_device = self.mic_combo.currentData() or ""
        self.cfg.paste_mode = self.paste_combo.currentData()
        self.cfg.restore_clipboard = self.restore_check.isChecked()
        self.cfg.theme = self.theme_combo.currentData()
        self.cfg.keep_history = self.history_check.isChecked()
        self.cfg.overlay_position = self.position_combo.currentData()
        self.cfg.result_feedback = self.feedback_combo.currentData()
        self.cfg.save()
        self.cfg.set_autostart(self.autostart_check.isChecked(), _launcher_path())
        self.applied.emit()
        self._flash("Configurações salvas.")

    def _flash(self, message: str) -> None:
        self.status.setText(message)
        QTimer.singleShot(2600, lambda: self.status.setText(""))

    # -- janela --------------------------------------------------------------
    def showEvent(self, event) -> None:
        self.refresh_history()
        if not self._placed:
            self._placed = True
            self._fit_to_screen()
        self.scroll.verticalScrollBar().setValue(0)   # sempre abre no topo
        self._closing = False
        self._fade_out.stop()
        self._fade_in.stop()
        self.setWindowOpacity(0.0)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.start()
        super().showEvent(event)

    def prepare_shutdown(self) -> None:
        """Encerramento do aplicativo: fecha sem animação.

        No Qt 6, quit() fecha todas as janelas antes de sair — se o closeEvent
        ignorar o evento para animar, o encerramento inteiro é cancelado.
        """
        self._closing = True
        self._fade_in.stop()
        self._fade_out.stop()
        self.hide()

    def closeEvent(self, event) -> None:
        app = QApplication.instance()
        if self._closing or (app is not None and app.property("sussurro_quitting")):
            self._closing = True
            super().closeEvent(event)
            return
        event.ignore()
        self._closing = True
        self._fade_in.stop()
        self._fade_out.stop()
        self._fade_out.setStartValue(self.windowOpacity())
        self._fade_out.setEndValue(0.0)
        self._fade_out.start()

    def _finish_close(self) -> None:
        if self._closing:
            self.hide()
            self.setWindowOpacity(1.0)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def _fit_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        width = min(620, area.width() - 80)
        height = min(840, area.height() - 110)
        self.resize(width, height)
        self.move(area.x() + (area.width() - width) // 2,
                  area.y() + (area.height() - height) // 2)


def _launcher_path() -> str:
    launcher = Path.home() / ".local/bin/sussurro"
    if launcher.exists():
        return str(launcher)
    return f"{Path(__file__).resolve().parents[1]}/bin/sussurro"
