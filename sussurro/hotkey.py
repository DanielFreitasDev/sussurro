"""Captura global da tecla de atalho (press + release), com dois backends.

Diferente de um keylogger, apenas a tecla escolhida (e o Esc enquanto a
gravação está ativa) é interceptada — nenhum outro evento de teclado chega
até a aplicação. Isso vale para os dois caminhos:

- **X11**: `XGrabKey` numa conexão própria, em thread separada.
- **Wayland**: o KWin é o dono do teclado e não existe grab para aplicativos
  comuns. O registro é feito no KGlobalAccel (o mesmo serviço que atende os
  atalhos do próprio Plasma) por D-Bus, e o compositor devolve
  `globalShortcutPressed` / `globalShortcutReleased`.

`HotkeyListener` escolhe o backend e é a única classe que o resto do
aplicativo enxerga; os dois expõem exatamente os mesmos sinais e métodos.
"""

from __future__ import annotations

import queue
import select
import threading

from PySide6.QtCore import SLOT, QObject, Qt, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface
from Xlib import X, XK, display, error

from . import session

# Máscaras de modificadores "travados" que precisam ser incluídas no grab
# para que o atalho funcione com Num Lock / Caps Lock / Scroll Lock ativos.
_LOCK_MASKS = (0, X.LockMask, X.Mod2Mask, X.Mod3Mask, X.Mod5Mask)


def _mask_combinations() -> list[int]:
    combos = {0}
    for mask in _LOCK_MASKS[1:]:
        combos |= {c | mask for c in combos}
    return sorted(combos)


MASKS = _mask_combinations()


class _X11Listener(QObject):
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


# -- Wayland: KGlobalAccel ---------------------------------------------------
_KGA_SERVICE = "org.kde.kglobalaccel"
_KGA_PATH = "/kglobalaccel"
_KGA_IFACE = "org.kde.KGlobalAccel"
_KGA_COMPONENT = "org.kde.kglobalaccel.Component"
_COMPONENT = "sussurro"

# Flags de setShortcut. SetPresent é o que realmente instala o grab no
# compositor: sem ele o atalho é gravado no kglobalshortcutsrc, aparece como
# "tecla ocupada" e mesmo assim nunca dispara. NoAutoloading faz valer a tecla
# que passamos, em vez da que estiver no arquivo de configuração do KDE.
_SET_PRESENT, _NO_AUTOLOADING = 2, 4
_SET_FLAGS = _SET_PRESENT | _NO_AUTOLOADING

# O KGlobalAccel fala em teclas do Qt, não em keysyms do X11. Teclas
# modificadoras sozinhas (Control_R, Alt_R, Super_R) não são atalhos válidos
# para o KDE e por isso ficam de fora — `supports_key()` as esconde da
# janela de configurações quando a sessão é Wayland.
_QT_KEYS = {
    "Pause": Qt.Key.Key_Pause,
    "Scroll_Lock": Qt.Key.Key_ScrollLock,
    "Menu": Qt.Key.Key_Menu,
    "F13": Qt.Key.Key_F13,
    "F14": Qt.Key.Key_F14,
    "Insert": Qt.Key.Key_Insert,
}


def supports_key(keyname: str) -> bool:
    """A tecla pode ser registrada na sessão atual?"""
    return keyname in _QT_KEYS if session.is_wayland() else True


class _KGlobalAccelListener(QObject):
    """Registra a tecla no serviço de atalhos do Plasma e escuta o D-Bus.

    Não há thread aqui: os sinais do KGlobalAccel chegam pelo laço de eventos
    do Qt, já na thread principal.
    """

    pressed = Signal()
    released = Signal()
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, keyname: str = "Pause", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keyname = keyname
        self._enabled = True
        self._registered = False
        self._escape = False
        self._bus = QDBusConnection.sessionBus()
        self._kga = QDBusInterface(_KGA_SERVICE, _KGA_PATH, _KGA_IFACE, self._bus)
        self._connected = False

    # -- API pública ---------------------------------------------------------
    def start(self) -> None:
        if not self._kga.isValid():
            self.failed.emit("O serviço de atalhos do Plasma (KGlobalAccel) não respondeu.")
            return
        # `_bind` registra a ação e só então liga os sinais: o componente não
        # existe no serviço antes do primeiro `doRegister`.
        self._bind(self._keyname)

    def stop(self) -> None:
        # Sem isso o atalho continuaria listado nas configurações do sistema
        # como uma ação de um programa que não está mais rodando.
        self._unbind()
        self._call("unRegister", self._action("cancel"))

    def set_hotkey(self, keyname: str) -> None:
        self._unbind()
        self._keyname = keyname
        if self._enabled:
            self._bind(keyname)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled and not self._registered:
            self._bind(self._keyname)
        elif not enabled and self._registered:
            self._unbind()

    def grab_escape(self, grab: bool) -> None:
        """Registra o Esc só enquanto a gravação está no ar.

        Se o KDE recusar (o Esc puro é um atalho incomum), a gravação
        simplesmente não poderá ser cancelada pelo teclado — o resto continua
        funcionando, então a falha é silenciosa.
        """
        if grab == self._escape:
            return
        action = self._action("cancel", "Cancelar a gravação")
        if grab:
            self._call("doRegister", action)
            keys = self._call("setShortcut", action, [int(Qt.Key.Key_Escape.value)], _SET_FLAGS)
            self._escape = bool(keys) and int(Qt.Key.Key_Escape.value) in keys
        else:
            self._call("unRegister", action)
            self._escape = False

    # -- interno -------------------------------------------------------------
    def _action(self, unique: str, friendly: str = "") -> list[str]:
        # Ordem exigida pelo KGlobalAccel: componente, ação, nome amigável do
        # componente, nome amigável da ação.
        return [_COMPONENT, unique, "Sussurro", friendly or unique]

    def _call(self, method: str, *args):
        """Chama pelo metaobject, não por `QDBusInterface.call()`.

        A introspecção é o que converte os argumentos para os tipos exatos que
        o KGlobalAccel exige (`as`, `ai`, `u`). Com `call()` o PySide6 manda
        `av` e `i`, e o serviço recusa a mensagem inteira por assinatura
        errada — silenciosamente, do ponto de vista de quem chamou.
        """
        try:
            return getattr(self._kga, method)(*args)
        except Exception:
            return None

    def _listen(self) -> None:
        if self._connected:
            return
        path = self._call("getComponent", _COMPONENT)
        if path is None:
            return
        path = path.path() if hasattr(path, "path") else str(path)
        for signal, slot in (("globalShortcutPressed", "_on_pressed"),
                             ("globalShortcutReleased", "_on_released")):
            self._bus.connect(_KGA_SERVICE, path, _KGA_COMPONENT, signal, self,
                              SLOT(f"{slot}(QString,QString,qlonglong)"))
        self._connected = True

    def _bind(self, keyname: str) -> None:
        key = _QT_KEYS.get(keyname)
        if key is None:
            self.failed.emit(
                f"A tecla “{keyname}” não pode ser usada como atalho no Wayland. "
                "Escolha outra nas configurações."
            )
            return
        action = self._action("dictate", "Segurar para ditar")
        self._call("doRegister", action)
        keys = self._call("setShortcut", action, [int(key.value)], _SET_FLAGS)
        self._registered = bool(keys) and int(key.value) in keys
        if not self._registered:
            self.failed.emit(
                f"A tecla “{keyname}” já está reservada por outro programa. "
                "Escolha outro atalho nas configurações."
            )
            return
        self._listen()

    def _unbind(self) -> None:
        self._call("unRegister", self._action("dictate"))
        self._registered = False

    # Os slots precisam existir no metaobject: o QDBusConnection.connect casa
    # pela assinatura, não por um callable Python.
    @Slot(str, str, "qlonglong")
    def _on_pressed(self, component: str, action: str, timestamp: int) -> None:
        if self._enabled and action == "dictate":
            self.pressed.emit()
        elif action == "cancel":
            self.cancelled.emit()

    @Slot(str, str, "qlonglong")
    def _on_released(self, component: str, action: str, timestamp: int) -> None:
        # `globalShortcutRepeated` (auto-repeat) é um sinal à parte e não é
        # escutado: no Wayland o compositor já separa repetição de soltar, o
        # que dispensa o malabarismo de desligar o auto-repeat do X11.
        if self._enabled and action == "dictate":
            self.released.emit()


class HotkeyListener(QObject):
    """Fachada: escolhe o backend conforme a sessão e repassa os sinais."""

    pressed = Signal()
    released = Signal()
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, keyname: str = "Pause", parent: QObject | None = None) -> None:
        super().__init__(parent)
        backend = _KGlobalAccelListener if session.is_wayland() else _X11Listener
        self._backend = backend(keyname, self)
        self._backend.pressed.connect(self.pressed)
        self._backend.released.connect(self.released)
        self._backend.cancelled.connect(self.cancelled)
        self._backend.failed.connect(self.failed)

    def start(self) -> None:
        self._backend.start()

    def stop(self) -> None:
        self._backend.stop()

    def set_hotkey(self, keyname: str) -> None:
        self._backend.set_hotkey(keyname)

    def set_enabled(self, enabled: bool) -> None:
        self._backend.set_enabled(enabled)

    def grab_escape(self, grab: bool) -> None:
        self._backend.grab_escape(grab)
