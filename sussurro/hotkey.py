"""Captura global da tecla de atalho no X11 (press + release) via XGrabKey.

Diferente de um keylogger, apenas a tecla escolhida (e o Esc enquanto a
gravação está ativa) é interceptada — nenhum outro evento de teclado chega
até a aplicação.
"""

from __future__ import annotations

import queue
import select
import threading

from PySide6.QtCore import QObject, Signal
from Xlib import X, XK, display, error

# Máscaras de modificadores "travados" que precisam ser incluídas no grab
# para que o atalho funcione com Num Lock / Caps Lock / Scroll Lock ativos.
_LOCK_MASKS = (0, X.LockMask, X.Mod2Mask, X.Mod3Mask, X.Mod5Mask)


def _mask_combinations() -> list[int]:
    combos = {0}
    for mask in _LOCK_MASKS[1:]:
        combos |= {c | mask for c in combos}
    return sorted(combos)


MASKS = _mask_combinations()


class HotkeyListener(QObject):
    """Escuta a tecla de atalho em uma conexão X11 própria."""

    pressed = Signal()
    released = Signal()
    cancelled = Signal()          # Esc pressionado durante a gravação
    failed = Signal(str)

    def __init__(self, keyname: str = "Pause", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keyname = keyname
        self._cmds: queue.Queue[tuple[str, object]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = True

    # -- API pública ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sussurro-hotkey", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def set_hotkey(self, keyname: str) -> None:
        self._keyname = keyname
        self._cmds.put(("hotkey", keyname))

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._cmds.put(("enabled", enabled))

    def grab_escape(self, grab: bool) -> None:
        self._cmds.put(("escape", grab))

    # -- thread --------------------------------------------------------------
    def _run(self) -> None:
        try:
            dsp = display.Display()
        except Exception as exc:  # servidor X indisponível
            self.failed.emit(f"Não foi possível conectar ao servidor X: {exc}")
            return

        root = dsp.screen().root
        state = {"keycode": 0, "escape": 0, "esc_grabbed": False, "grabbed": False}

        def keycode_for(name: str) -> int:
            keysym = XK.string_to_keysym(name)
            return dsp.keysym_to_keycode(keysym) if keysym else 0

        def set_repeat(keycode: int, enabled: bool) -> None:
            """O auto-repeat do X11 gera pares press/release falsos enquanto a
            tecla está segurada; desligá-lo só para essa tecla torna a detecção
            de "soltou" exata."""
            if not keycode:
                return
            mode = X.AutoRepeatModeDefault if enabled else X.AutoRepeatModeOff
            try:
                dsp.change_keyboard_control(key=keycode, auto_repeat_mode=mode)
                dsp.sync()
            except Exception:
                pass

        def grab(keycode: int) -> bool:
            if not keycode:
                return False
            catch = error.CatchError(error.BadAccess)
            for mask in MASKS:
                root.grab_key(keycode, mask, True, X.GrabModeAsync, X.GrabModeAsync,
                              onerror=catch)
            dsp.sync()
            if catch.get_error():
                return False
            return True

        def ungrab(keycode: int, restore_repeat: bool = True) -> None:
            if not keycode:
                return
            for mask in MASKS:
                root.ungrab_key(keycode, mask)
            dsp.sync()
            # Só a tecla do atalho teve o auto-repeat desligado; mexer no do
            # Esc seria alterar o teclado do usuário sem motivo.
            if restore_repeat:
                set_repeat(keycode, True)

        def bind(name: str) -> None:
            if state["grabbed"]:
                ungrab(state["keycode"])
                state["grabbed"] = False
            keycode = keycode_for(name)
            state["keycode"] = keycode
            if not keycode:
                self.failed.emit(f"A tecla “{name}” não existe neste teclado.")
                return
            if grab(keycode):
                state["grabbed"] = True
                set_repeat(keycode, False)
            else:
                self.failed.emit(
                    f"A tecla “{name}” já está reservada por outro programa. "
                    "Escolha outro atalho nas configurações."
                )

        state["escape"] = keycode_for("Escape")
        bind(self._keyname)

        while not self._stop.is_set():
            # comandos vindos da thread principal
            try:
                while True:
                    cmd, value = self._cmds.get_nowait()
                    if cmd == "hotkey":
                        bind(str(value))
                    elif cmd == "enabled":
                        if value and not state["grabbed"]:
                            bind(self._keyname)
                        elif not value and state["grabbed"]:
                            ungrab(state["keycode"])
                            state["grabbed"] = False
                    elif cmd == "escape":
                        want = bool(value)
                        if want and not state["esc_grabbed"]:
                            state["esc_grabbed"] = grab(state["escape"])
                        elif not want and state["esc_grabbed"]:
                            ungrab(state["escape"], restore_repeat=False)
                            state["esc_grabbed"] = False
            except queue.Empty:
                pass

            try:
                select.select([dsp.fileno()], [], [], 0.08)
            except Exception:      # conexão X encerrada (logout, servidor caiu)
                break

            # O select só acusa o que ainda está no socket, e todo sync() —
            # os grabs fazem um a cada gravação — lê o socket inteiro para
            # achar sua resposta, levando junto os eventos que chegaram no
            # meio. Eles ficam na fila interna do python-xlib, invisíveis para
            # o select: sem drenar a fila a cada volta, uma tecla apertada
            # nesse instante só é entregue quando a próxima chegar.
            try:
                while dsp.pending_events():
                    self._handle(dsp.next_event(), state, bind)
            except Exception:
                continue

        if state["grabbed"]:
            ungrab(state["keycode"])
        if state["esc_grabbed"]:
            ungrab(state["escape"], restore_repeat=False)
        try:
            dsp.close()
        except Exception:
            pass

    def _handle(self, event, state: dict, bind) -> None:
        if event.type == X.MappingNotify:
            # O layout de teclado mudou: refaz o grab com o novo keycode.
            bind(self._keyname)
            return
        if event.type not in (X.KeyPress, X.KeyRelease):
            return
        if state["esc_grabbed"] and event.detail == state["escape"]:
            if event.type == X.KeyPress:
                self.cancelled.emit()
            return
        if event.detail != state["keycode"] or not self._enabled:
            return
        if event.type == X.KeyPress:
            self.pressed.emit()
        else:
            self.released.emit()
