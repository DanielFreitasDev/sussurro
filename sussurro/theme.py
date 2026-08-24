"""Paleta monocromática que acompanha o tema do sistema.

Escuro: preto com texto branco. Claro: branco com texto preto. Sem cores de
acento — a hierarquia vem de opacidade, peso e espaçamento.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: QColor            # fundo da janela
    surface: QColor       # cartões
    surface_hi: QColor    # campos, botões secundários
    border: QColor
    border_hi: QColor
    text: QColor
    muted: QColor
    faint: QColor
    capsule: QColor       # fundo da cápsula flutuante
    on_accent: QColor     # texto sobre o botão primário
    shadow: QColor


DARK = Palette(
    dark=True,
    bg=QColor(0, 0, 0),
    surface=QColor(255, 255, 255, 10),
    surface_hi=QColor(255, 255, 255, 20),
    border=QColor(255, 255, 255, 33),
    border_hi=QColor(255, 255, 255, 66),
    text=QColor(255, 255, 255),
    muted=QColor(255, 255, 255, 150),
    faint=QColor(255, 255, 255, 97),
    capsule=QColor(0, 0, 0, 242),
    on_accent=QColor(0, 0, 0),
    shadow=QColor(0, 0, 0, 220),
)

LIGHT = Palette(
    dark=False,
    bg=QColor(255, 255, 255),
    surface=QColor(0, 0, 0, 8),
    surface_hi=QColor(0, 0, 0, 15),
    border=QColor(0, 0, 0, 33),
    border_hi=QColor(0, 0, 0, 71),
    text=QColor(0, 0, 0),
    muted=QColor(0, 0, 0, 145),
    faint=QColor(0, 0, 0, 102),
    capsule=QColor(255, 255, 255, 245),
    on_accent=QColor(255, 255, 255),
    shadow=QColor(0, 0, 0, 90),
)

_mode = "auto"          # auto | dark | light
_active = DARK


# -- detecção ----------------------------------------------------------------
def _system_scheme() -> str:
    hints = QGuiApplication.styleHints()
    if hints is not None:
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    return _portal_scheme()


@lru_cache(maxsize=1)
def _portal_scheme() -> str:
    """Reserva: pergunta ao xdg-desktop-portal (1 = escuro, 2 = claro)."""
    try:
        out = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.freedesktop.portal.Desktop",
             "--object-path", "/org/freedesktop/portal/desktop",
             "--method", "org.freedesktop.portal.Settings.Read",
             "org.freedesktop.appearance", "color-scheme"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:
        return "dark"
    return "light" if "uint32 2" in out else "dark"


def set_mode(mode: str) -> bool:
    """Define auto/dark/light. Devolve True se a paleta ativa mudou."""
    global _mode
    _mode = mode if mode in ("auto", "dark", "light") else "auto"
    return refresh()


def refresh() -> bool:
    """Reavalia a paleta ativa. Devolve True se ela mudou."""
    global _active
    scheme = _mode if _mode in ("dark", "light") else _system_scheme()
    palette = DARK if scheme == "dark" else LIGHT
    changed = palette is not _active
    _active = palette
    return changed


def p() -> Palette:
    return _active


# -- helpers -----------------------------------------------------------------
def css(color: QColor, alpha: float | None = None) -> str:
    a = color.alphaF() if alpha is None else alpha
    return f"rgba({color.red()},{color.green()},{color.blue()},{a:.3f})"


def mix(color: QColor, alpha: float) -> QColor:
    out = QColor(color)
    out.setAlphaF(max(0.0, min(1.0, alpha)))
    return out


@lru_cache(maxsize=1)
def family() -> str:
    available = set(QFontDatabase.families())
    for name in ("Inter", "Manrope", "Ubuntu", "Noto Sans", "DejaVu Sans"):
        if name in available:
            return name
    return "sans-serif"


@lru_cache(maxsize=32)
def font(size: int, weight: QFont.Weight = QFont.Weight.Medium,
         spacing: float = 0.0) -> QFont:
    f = QFont(family(), size)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f
