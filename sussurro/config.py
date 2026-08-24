"""Configuração persistente do Sussurro (~/.config/sussurro/config.json)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sussurro"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "sussurro"
AUTOSTART_FILE = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "autostart"
    / "sussurro.desktop"
)

MODELS = [
    ("whisper-large-v3-turbo", "Turbo — rápido, ótimo custo-benefício"),
    ("whisper-large-v3", "Large v3 — máxima precisão"),
]

LANGUAGES = [
    ("", "Detectar automaticamente"),
    ("pt", "Português"),
    ("en", "Inglês"),
    ("es", "Espanhol"),
    ("fr", "Francês"),
    ("de", "Alemão"),
    ("it", "Italiano"),
    ("ja", "Japonês"),
    ("zh", "Chinês"),
]

HOTKEYS = [
    ("Pause", "Pause / Break"),
    ("Scroll_Lock", "Scroll Lock"),
    ("Menu", "Menu (tecla de contexto)"),
    ("Super_R", "Super direito"),
    ("Control_R", "Ctrl direito"),
    ("Alt_R", "Alt direito"),
    ("F13", "F13"),
    ("F14", "F14"),
    ("Insert", "Insert"),
]

RESULT_FEEDBACK = [
    ("text", "Mostrar o texto transcrito"),
    ("check", "Apenas um ✓ discreto"),
    ("none", "Nada — colar direto"),
]

THEMES = [
    ("auto", "Acompanhar o sistema"),
    ("dark", "Escuro"),
    ("light", "Claro"),
]

PASTE_MODES = [
    ("auto", "Automático (detecta terminais)"),
    ("ctrl_v", "Ctrl + V"),
    ("ctrl_shift_v", "Ctrl + Shift + V"),
    ("shift_insert", "Shift + Insert"),
    ("type", "Digitar caractere a caractere"),
    ("none", "Apenas copiar (não colar)"),
]

# Aplicativos que usam Ctrl+Shift+V para colar.
TERMINAL_CLASSES = {
    "konsole", "gnome-terminal", "gnome-terminal-server", "xterm", "uxterm",
    "alacritty", "kitty", "wezterm", "terminator", "tilix", "yakuake",
    "xfce4-terminal", "mate-terminal", "lxterminal", "st", "urxvt",
    "rxvt", "foot", "contour", "ghostty", "guake", "deepin-terminal",
    "qterminal", "termite", "sakura", "hyper", "warp", "blackbox",
}


@dataclass
class Config:
    api_key: str = ""
    model: str = "whisper-large-v3-turbo"
    language: str = "pt"
    prompt: str = ""
    temperature: float = 0.0
    hotkey: str = "Pause"
    input_device: str = ""            # "" = fonte padrão do sistema
    paste_mode: str = "auto"
    restore_clipboard: bool = False
    min_duration: float = 0.45        # segundos
    max_duration: int = 300           # segundos
    keep_history: bool = True
    theme: str = "auto"
    result_feedback: str = "text"
    overlay_position: str = "bottom"  # bottom | top
    trailing_punctuation: bool = False
    _path: Path = field(default=CONFIG_FILE, repr=False, compare=False)

    # -- persistência --------------------------------------------------------
    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> "Config":
        cfg = cls()
        cfg._path = path
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            known = {f.name for f in fields(cls) if not f.name.startswith("_")}
            for key, value in raw.items():
                if key in known:
                    setattr(cfg, key, value)
        return cfg

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self._path)

    # -- derivados -----------------------------------------------------------
    def resolved_key(self) -> str:
        """A chave do ambiente tem prioridade sobre a salva em disco."""
        return (os.environ.get("GROQ_API_KEY") or self.api_key or "").strip()

    @property
    def autostart_enabled(self) -> bool:
        return AUTOSTART_FILE.exists()

    def set_autostart(self, enabled: bool, exec_path: str) -> None:
        if enabled:
            AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART_FILE.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Sussurro\n"
                "Comment=Ditado por voz global\n"
                f"Exec={exec_path}\n"
                "Icon=sussurro\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
        elif AUTOSTART_FILE.exists():
            AUTOSTART_FILE.unlink()
