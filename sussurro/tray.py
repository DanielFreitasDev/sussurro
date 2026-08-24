"""Ícone de bandeja e menu do aplicativo."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import (QAction, QGuiApplication, QIcon, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import history, theme


def _draw_icon(size: int = 64) -> QIcon:
    """Microfone monocromático na cor do tema — combina com o painel."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    color = theme.p().text
    unit = size / 64.0

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(25 * unit, 9 * unit, 14 * unit, 27 * unit),
                            7 * unit, 7 * unit)

    pen = QPen(color, 5 * unit)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(16 * unit, 14 * unit, 32 * unit, 32 * unit), 180 * 16, 180 * 16)
    painter.drawLine(int(32 * unit), int(46 * unit), int(32 * unit), int(55 * unit))
    painter.end()
    return QIcon(pixmap)


class Tray(QObject):
    settings_requested = Signal()
    toggle_requested = Signal()
    quit_requested = Signal()
    enabled_changed = Signal(bool)

    def __init__(self, hotkey_label: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tray = QSystemTrayIcon(_draw_icon(), self)
        self.menu = QMenu()

        self.record_action = QAction("Gravar agora", self.menu)
        self.record_action.triggered.connect(self.toggle_requested)
        self.menu.addAction(self.record_action)

        self.history_menu = QMenu("Histórico", self.menu)
        self.menu.addMenu(self.history_menu)
        self.menu.addSeparator()

        self.enabled_action = QAction("Atalho ativo", self.menu)
        self.enabled_action.setCheckable(True)
        self.enabled_action.setChecked(True)
        self.enabled_action.toggled.connect(self.enabled_changed)
        self.menu.addAction(self.enabled_action)

        settings_action = QAction("Configurações…", self.menu)
        settings_action.triggered.connect(self.settings_requested)
        self.menu.addAction(settings_action)
        self.menu.addSeparator()

        quit_action = QAction("Sair", self.menu)
        quit_action.triggered.connect(self.quit_requested)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.set_idle(hotkey_label)
        self.refresh_history()
        self.tray.show()

    # -- estado --------------------------------------------------------------
    def refresh_icon(self) -> None:
        self.tray.setIcon(_draw_icon())

    def set_idle(self, hotkey_label: str) -> None:
        self.tray.setToolTip(f"Sussurro — segure {hotkey_label} para ditar")
        self.record_action.setText("Gravar agora")

    def set_recording(self) -> None:
        self.tray.setToolTip("Sussurro — gravando…")
        self.record_action.setText("Parar e transcrever")

    def set_working(self) -> None:
        self.tray.setToolTip("Sussurro — transcrevendo…")
        self.record_action.setText("Transcrevendo…")

    def refresh_history(self) -> None:
        self.history_menu.clear()
        entries = history.recent(6)
        if not entries:
            empty = QAction("Nada ainda", self.history_menu)
            empty.setEnabled(False)
            self.history_menu.addAction(empty)
            return
        for entry in entries:
            text = " ".join((entry.get("text") or "").split())
            label = text[:60] + ("…" if len(text) > 60 else "")
            action = QAction(label, self.history_menu)
            action.setToolTip("Copiar")
            action.triggered.connect(
                lambda _=False, t=text: QGuiApplication.clipboard().setText(t))
            self.history_menu.addAction(action)

    def notify(self, title: str, message: str) -> None:
        self.tray.showMessage(title, message, _draw_icon(), 3000)

    # -- interno -------------------------------------------------------------
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.settings_requested.emit()
