"""Cápsula flutuante que dá o retorno visual do ditado.

Desenhada à mão em preto e branco: acompanha o tema do sistema e mantém a
mesma aparência sobre qualquer janela.
"""

from __future__ import annotations

import math
import time
from functools import lru_cache

from PySide6.QtCore import (Property, QEasingCurve, QPoint, QPropertyAnimation,
                            QRect, QRectF, Qt, QTimer)
from PySide6.QtGui import (QBrush, QColor, QCursor, QFontMetrics, QGuiApplication,
                           QLinearGradient, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QWidget

from . import theme

SHADOW = 22          # respiro ao redor da cápsula para a sombra
HEIGHT = 56
RADIUS = HEIGHT / 2
BARS = 34
BAR_W = 3.0
BAR_GAP = 3.4
BAR_MAX = 22.0

ARMED, RECORDING, WORKING, SUCCESS, ERROR, INFO = "armed", "rec", "work", "ok", "err", "info"


@lru_cache(maxsize=32)
def _metrics(size: int) -> QFontMetrics:
    return QFontMetrics(theme.font(size))


class Overlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._state = INFO
        self._message = ""
        self._fade = 0.0
        self._phase = 0.0
        self._started_at = 0.0
        self._live = False
        self._bars = [0.0] * BARS
        self._targets = [0.0] * BARS
        self._position = "bottom"

        self._frames = QTimer(self)
        self._frames.setInterval(16)
        self._frames.timeout.connect(self._tick)

        self._autohide = QTimer(self)
        self._autohide.setSingleShot(True)
        self._autohide.timeout.connect(self.dismiss)

        self._fade_anim = QPropertyAnimation(self, b"fade", self)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.finished.connect(self._on_fade_finished)
        self._fading_out = False

        self._geom_anim = QPropertyAnimation(self, b"geometry", self)
        self._geom_anim.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._geom_anim.setDuration(340)

        self.resize(360 + SHADOW * 2, HEIGHT + SHADOW * 2)

    # -- propriedade animável ------------------------------------------------
    def _get_fade(self) -> float:
        return self._fade

    def _set_fade(self, value: float) -> None:
        self._fade = value
        self.update()

    fade = Property(float, _get_fade, _set_fade)

    # -- API -----------------------------------------------------------------
    def set_position(self, position: str) -> None:
        self._position = position

    def begin(self) -> None:
        """Atalho pressionado: a cápsula entra em modo de escuta."""
        self._state = ARMED
        self._live = False
        self._message = ""
        self._started_at = time.monotonic()
        self._bars = [0.0] * BARS
        self._targets = [0.0] * BARS
        self._autohide.stop()
        self._appear(330)

    def set_live(self) -> None:
        if self._state in (ARMED, RECORDING):
            self._state = RECORDING
            self._live = True
            self._started_at = time.monotonic()

    def push_level(self, level: float) -> None:
        if self._state not in (ARMED, RECORDING):
            return
        self._targets = self._targets[1:] + [max(0.04, min(1.0, level))]

    def show_working(self) -> None:
        self._state = WORKING
        self._message = "Transcrevendo…"
        self._started_at = time.monotonic()
        self._autohide.stop()
        self._appear(self._width_for_text(self._message, 108))

    def show_success(self, text: str = "") -> None:
        """Com texto, mostra a transcrição; sem texto, só o selo de concluído."""
        self._state = SUCCESS
        self._message = " ".join(text.split())
        if self._message:
            self._appear(self._width_for_text(self._message, 96))
            self._autohide.start(1700)
        else:
            self._appear(74)
            self._autohide.start(900)

    def show_error(self, message: str) -> None:
        self._state = ERROR
        self._message = message
        self._appear(self._width_for_text(message, 96))
        self._autohide.start(4200)

    def show_info(self, message: str) -> None:
        self._state = INFO
        self._message = message
        self._appear(self._width_for_text(message, 96))
        self._autohide.start(2200)

    def dismiss(self) -> None:
        if not self.isVisible():
            return
        self._autohide.stop()
        self._fade_anim.stop()
        self._fading_out = True
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fade_anim.setDuration(320)
        self._fade_anim.setStartValue(self._fade)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    # -- interno -------------------------------------------------------------
    def _on_fade_finished(self) -> None:
        if self._fading_out and self._fade <= 0.01:
            self._fading_out = False
            self._frames.stop()
            self.hide()

    def _appear(self, capsule_width: int) -> None:
        rect = self._target_geometry(capsule_width)
        if self.isVisible():
            self._geom_anim.stop()
            self._geom_anim.setStartValue(self.geometry())
            self._geom_anim.setEndValue(rect)
            self._geom_anim.start()
        else:
            self.setGeometry(rect)
            self.show()
            self.raise_()

        self._fade_anim.stop()
        self._fading_out = False
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.setDuration(230)
        self._fade_anim.setStartValue(self._fade)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        if not self._frames.isActive():
            self._frames.start()

    def _target_geometry(self, capsule_width: int) -> QRect:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        width = capsule_width + SHADOW * 2
        height = HEIGHT + SHADOW * 2
        x = area.x() + (area.width() - width) // 2
        if self._position == "top":
            y = area.y() + 28
        else:
            y = area.y() + area.height() - height - 66
        return QRect(x, y, width, height)

    def _width_for_text(self, text: str, chrome: int) -> int:
        return max(272, min(620, _metrics(12).horizontalAdvance(text) + chrome))

    def _tick(self) -> None:
        self._phase += 0.016
        if self._state in (ARMED, RECORDING):
            if not self._live:
                # respiração suave enquanto o microfone não entregou áudio
                for i in range(BARS):
                    self._targets[i] = 0.10 + 0.06 * math.sin(self._phase * 3 + i * 0.35)
            for i in range(BARS):
                self._bars[i] += (self._targets[i] - self._bars[i]) * 0.30
        elif self._state == WORKING:
            for i in range(BARS):
                wave = math.sin(self._phase * 4.2 - i * 0.42)
                self._targets[i] = 0.10 + 0.34 * max(0.0, wave) ** 2
                self._bars[i] += (self._targets[i] - self._bars[i]) * 0.22
        self.update()

    # -- pintura -------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        if self._fade <= 0.005:
            return
        pal = theme.p()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.translate(0, (1.0 - self._fade) * 12)

        capsule = QRectF(SHADOW, SHADOW, self.width() - SHADOW * 2, HEIGHT)
        self._paint_shadow(painter, capsule, pal)
        self._paint_capsule(painter, capsule, pal)

        left = capsule.left() + 20
        right = capsule.right() - 20
        center_y = capsule.center().y()

        if self._state in (ARMED, RECORDING):
            self._paint_mic(painter, left + 8, center_y, pal)
            elapsed = self._elapsed_text()
            timer_w = _metrics(12).horizontalAdvance(elapsed)
            self._paint_wave(painter, left + 26, right - timer_w - 14, center_y, pal)
            self._paint_text(painter, elapsed, right - timer_w, center_y,
                             theme.font(12), pal.muted)
        elif self._state == WORKING:
            self._paint_wave(painter, left, left + 42, center_y, pal, subtle=True)
            self._paint_text(painter, self._message, left + 56, center_y,
                             theme.font(12), pal.text)
        elif not self._message:
            self._paint_badge(painter, capsule.center().x(), center_y, pal)
        else:
            self._paint_badge(painter, left + 8, center_y, pal)
            font = theme.font(12)
            available = int(right - (left + 28))
            text = _metrics(12).elidedText(
                self._message, Qt.TextElideMode.ElideRight, available)
            self._paint_text(painter, text, left + 28, center_y, font, pal.text)
        painter.end()

    def _alpha(self, color: QColor, scale: float = 1.0) -> QColor:
        out = QColor(color)
        out.setAlphaF(max(0.0, min(1.0, color.alphaF() * scale * self._fade)))
        return out

    def _paint_shadow(self, painter: QPainter, capsule: QRectF, pal) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        layers = 9
        strength = 0.055 if pal.dark else 0.030
        for i in range(layers, 0, -1):
            spread = i * 2.2
            alpha = strength * (1 - i / (layers + 1)) ** 1.6
            painter.setBrush(QBrush(self._alpha(QColor(0, 0, 0, 255), alpha)))
            rect = capsule.adjusted(-spread, -spread * 0.55, spread, spread * 1.15)
            painter.drawRoundedRect(rect, RADIUS + spread, RADIUS + spread)

    def _paint_capsule(self, painter: QPainter, capsule: QRectF, pal) -> None:
        path = QPainterPath()
        path.addRoundedRect(capsule, RADIUS, RADIUS)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._alpha(pal.capsule)))
        painter.drawPath(path)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._alpha(pal.border), 1.0))
        painter.drawRoundedRect(capsule.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)

    def _paint_mic(self, painter: QPainter, x: float, y: float, pal) -> None:
        pulse = 0.5 + 0.5 * math.sin(self._phase * 4.0)
        level = self._bars[-1] if self._bars else 0.0
        radius = 4.0 + level * 2.2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._alpha(pal.text, 0.10 + 0.14 * pulse)))
        painter.drawEllipse(QPoint(int(x), int(y)), int(radius + 6), int(radius + 6))
        painter.setBrush(QBrush(self._alpha(pal.text)))
        painter.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))

    def _paint_badge(self, painter: QPainter, x: float, y: float, pal) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._alpha(pal.text, 0.12)))
        painter.drawEllipse(QRectF(x - 10, y - 10, 20, 20))
        pen = QPen(self._alpha(pal.text), 1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._state == SUCCESS:
            path = QPainterPath()
            path.moveTo(x - 4.6, y + 0.3)
            path.lineTo(x - 1.2, y + 3.8)
            path.lineTo(x + 5.0, y - 3.8)
            painter.drawPath(path)
        elif self._state == ERROR:
            painter.drawLine(QPoint(int(x), int(y - 4)), QPoint(int(x), int(y + 1)))
            painter.drawPoint(QPoint(int(x), int(y + 4)))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._alpha(pal.text)))
            painter.drawEllipse(QRectF(x - 2.0, y - 2.0, 4.0, 4.0))

    def _paint_wave(self, painter: QPainter, x0: float, x1: float, cy: float, pal,
                    subtle: bool = False) -> None:
        span = max(1.0, x1 - x0)
        count = BARS if not subtle else 8
        step = span / count
        width = min(BAR_W, max(2.0, step - BAR_GAP)) if not subtle else 2.6

        # As barras mais antigas (à esquerda) desbotam: dá direção ao movimento.
        gradient = QLinearGradient(x0, 0, x1, 0)
        gradient.setColorAt(0.0, self._alpha(pal.text, 0.32))
        gradient.setColorAt(1.0, self._alpha(pal.text, 1.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))

        values = self._bars[-count:]
        breathe = 0.0 if subtle else 0.035
        for i, value in enumerate(values):
            floor = 0.055 + breathe * math.sin(self._phase * 3.1 - i * 0.38)
            height = max(3.0, max(value, floor) * BAR_MAX * 2)
            rect = QRectF(x0 + i * step, cy - height / 2, width, height)
            painter.drawRoundedRect(rect, width / 2, width / 2)

    def _paint_text(self, painter: QPainter, text: str, x: float, cy: float,
                    font, color: QColor) -> None:
        painter.setFont(font)
        painter.setPen(QPen(self._alpha(color)))
        metrics = _metrics(font.pointSize())
        baseline = cy + (metrics.ascent() - metrics.descent()) / 2
        painter.drawText(QPoint(int(x), int(baseline)), text)

    def _elapsed_text(self) -> str:
        seconds = int(time.monotonic() - self._started_at) if self._started_at else 0
        return f"{seconds // 60:d}:{seconds % 60:02d}"
