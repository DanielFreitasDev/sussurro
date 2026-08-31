"""Entrega do texto transcrito na janela ativa.

No X11 a colagem é um XTEST direto. No Wayland quem sintetiza a tecla é o
compositor, através do portal (veja `portal.py`); o efeito para o usuário é o
mesmo, mas a primeira colagem pede autorização uma única vez.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time

from Xlib import X, XK, display
from Xlib.ext import xtest

from . import portal, session
from .config import TERMINAL_CLASSES

# Combinações suportadas: (modificadores, tecla)
_COMBOS = {
    "ctrl_v": (("Control_L",), "v"),
    "ctrl_shift_v": (("Control_L", "Shift_L"), "v"),
    "shift_insert": (("Shift_L",), "Insert"),
}

# As mesmas combinações em códigos evdev, que é o que o portal entende.
_EVDEV_COMBOS = {
    "ctrl_v": ([portal.KEY_LEFTCTRL], portal.KEY_V),
    "ctrl_shift_v": ([portal.KEY_LEFTCTRL, portal.KEY_LEFTSHIFT], portal.KEY_V),
    "shift_insert": ([portal.KEY_LEFTSHIFT], portal.KEY_INSERT),
}


def active_window_class() -> str:
    """WM_CLASS da janela em foco, em minúsculas ("" se indisponível).

    No Wayland não há resposta: o compositor não conta a ninguém qual janela
    está em foco, e o XWayland só enxerga clientes X — que hoje quase não
    existem. O modo "automático" cai então no Ctrl+V, e quem usa terminal
    precisa escolher Ctrl+Shift+V nas configurações.
    """
    if session.is_wayland():
        return ""
    try:
        dsp = display.Display()
    except Exception:
        return ""
    try:
        window = dsp.get_input_focus().focus
        for _ in range(8):
            if not window or isinstance(window, int):
                return ""
            try:
                wm_class = window.get_wm_class()
            except Exception:
                wm_class = None
            if wm_class:
                return (wm_class[-1] or wm_class[0] or "").lower()
            parent = window.query_tree().parent
            if not parent or parent == window:
                return ""
            window = parent
        return ""
    except Exception:
        return ""
    finally:
        try:
            dsp.close()
        except Exception:
            pass


def resolve_mode(mode: str) -> str:
    """Converte o modo "auto" na combinação adequada à janela em foco."""
    if mode != "auto":
        return mode
    return "ctrl_shift_v" if active_window_class() in TERMINAL_CLASSES else "ctrl_v"


def deliver(text: str, mode: str, on_done=None) -> None:
    """Cola (ou digita) o texto sem bloquear a interface."""
    resolved = resolve_mode(mode)
    if resolved == "none":
        if on_done:
            on_done(True, "")
        return

    def job() -> None:
        if resolved == "type":
            ok, err = (portal.keyboard.type_text(text) if session.is_wayland()
                       else _type_text(text))
        else:
            ok, err = (_send_combo_wayland(resolved) if session.is_wayland()
                       else _send_combo(resolved))
        if on_done:
            on_done(ok, err)

    threading.Thread(target=job, name="sussurro-paste", daemon=True).start()


# -- implementação -----------------------------------------------------------
def _send_combo_wayland(mode: str) -> tuple[bool, str]:
    modifiers, key = _EVDEV_COMBOS.get(mode, _EVDEV_COMBOS["ctrl_v"])
    return portal.keyboard.send_combo(modifiers, key)


def _send_combo(mode: str) -> tuple[bool, str]:
    mods, key = _COMBOS.get(mode, _COMBOS["ctrl_v"])
    try:
        dsp = display.Display()
    except Exception as exc:
        return False, f"Sem acesso ao servidor X: {exc}"
    try:
        codes = [dsp.keysym_to_keycode(XK.string_to_keysym(m)) for m in mods]
        key_code = dsp.keysym_to_keycode(XK.string_to_keysym(key))
        if not key_code or not all(codes):
            return False, "Não foi possível mapear as teclas de colagem."

        for code in codes:
            xtest.fake_input(dsp, X.KeyPress, code)
        dsp.sync()
        time.sleep(0.012)
        xtest.fake_input(dsp, X.KeyPress, key_code)
        dsp.sync()
        time.sleep(0.02)
        xtest.fake_input(dsp, X.KeyRelease, key_code)
        for code in reversed(codes):
            xtest.fake_input(dsp, X.KeyRelease, code)
        dsp.sync()
        return True, ""
    except Exception as exc:
        return False, f"Falha ao enviar a colagem: {exc}"
    finally:
        try:
            dsp.close()
        except Exception:
            pass


def _type_text(text: str) -> tuple[bool, str]:
    if not shutil.which("ydotool"):
        return False, "ydotool não está instalado — usando apenas a cópia."
    try:
        proc = subprocess.run(
            ["ydotool", "type", "--key-delay", "6", "--file", "-"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=max(10, len(text) * 0.05),
        )
    except Exception as exc:
        return False, f"Falha ao digitar: {exc}"
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return False, detail[-1] if detail else "ydotoold não está em execução."
    return True, ""
