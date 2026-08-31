"""Emulação de teclado no Wayland pelo portal XDG RemoteDesktop.

No X11 o `paste.py` injeta a colagem com XTEST direto. No Wayland só o
compositor pode sintetizar entrada: o Xwayland do Plasma 6 roda com
`-enable-ei-portal`, então todo XTEST vindo de um cliente X é desviado para
este mesmo portal e descartado em silêncio se não houver permissão. Falar com
o portal é, portanto, o caminho sancionado — e o único que não exige `sudo`.

A sessão é criada uma vez e reaproveitada: a primeira vez abre o diálogo de
autorização do KDE, e o `restore_token` gravado em disco faz as próximas
sessões subirem caladas.

Aqui é a única parte do aplicativo que usa `jeepney` em vez do QtDBus: o
portal exige `uint` de verdade (`u`) dentro de `a{sv}`, e o QtDBus do PySide6
só sabe produzir `i`, `b`, `d`, `x` ou `v` — a chamada é recusada com
"Expected type 'u' for option 'types'". Veja o LEARNING.md.
"""

from __future__ import annotations

import secrets
import threading

from jeepney import DBusAddress, MessageType, new_method_call
from jeepney.bus_messages import MatchRule, message_bus
from jeepney.io.blocking import open_dbus_connection

from .config import DATA_DIR

_PORTAL = DBusAddress(
    "/org/freedesktop/portal/desktop",
    bus_name="org.freedesktop.portal.Desktop",
    interface="org.freedesktop.portal.RemoteDesktop",
)
_TOKEN_FILE = DATA_DIR / "remote-desktop.token"

_DEVICE_KEYBOARD = 1
_PERSIST_UNTIL_REVOKED = 2
_TIMEOUT_DIALOG = 120.0      # o usuário precisa clicar em "permitir"
_TIMEOUT_CALL = 15.0

# Códigos evdev (linux/input-event-codes.h), não keysyms do X11.
KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V, KEY_INSERT = 29, 42, 47, 110
RELEASED, PRESSED = 0, 1


class RemoteDesktop:
    """Sessão de emulação de entrada, criada sob demanda e reaproveitada."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn = None
        self._handle: str | None = None

    # -- API pública ---------------------------------------------------------
    def send_combo(self, modifiers: list[int], key: int) -> tuple[bool, str]:
        """Envia uma combinação (ex.: Ctrl + V) como pressiona/solta."""
        with self._lock:
            ok, erro = self._ensure()
            if not ok:
                return False, erro
            try:
                for code in modifiers:
                    self._notify_key(code, PRESSED)
                self._notify_key(key, PRESSED)
                self._notify_key(key, RELEASED)
                for code in reversed(modifiers):
                    self._notify_key(code, RELEASED)
            except Exception as exc:
                self._drop()
                return False, f"Falha ao enviar a colagem: {exc}"
        return True, ""

    def type_text(self, text: str) -> tuple[bool, str]:
        """Digita o texto caractere a caractere, por keysym."""
        with self._lock:
            ok, erro = self._ensure()
            if not ok:
                return False, erro
            try:
                for char in text:
                    keysym = _keysym(char)
                    self._call("NotifyKeyboardKeysym", "oa{sv}iu",
                               (self._handle, {}, keysym, PRESSED))
                    self._call("NotifyKeyboardKeysym", "oa{sv}iu",
                               (self._handle, {}, keysym, RELEASED))
            except Exception as exc:
                self._drop()
                return False, f"Falha ao digitar: {exc}"
        return True, ""

    def prepare(self) -> None:
        """Sobe a sessão antes da primeira colagem, em segundo plano.

        A autorização abre um diálogo do KDE, que rouba o foco. Pedir isso no
        meio de um ditado faria o Ctrl+V chegar na janela errada — pedir na
        partida deixa o caminho livre. Da segunda vez em diante o
        `restore_token` já responde por nós e nada aparece na tela.
        """
        def job() -> None:
            with self._lock:
                self._ensure()
        threading.Thread(target=job, name="sussurro-portal", daemon=True).start()

    def close(self) -> None:
        with self._lock:
            self._drop()

    # -- sessão --------------------------------------------------------------
    def _ensure(self) -> tuple[bool, str]:
        if self._handle:
            return True, ""
        try:
            return self._open()
        except Exception as exc:
            self._drop()
            return False, f"Não foi possível falar com o portal do sistema: {exc}"

    def _open(self) -> tuple[bool, str]:
        self._conn = open_dbus_connection(bus="SESSION")
        sender = self._conn.unique_name[1:].replace(".", "_")

        code, results = self._request(sender, "CreateSession", "a{sv}", lambda t: ({
            "handle_token": ("s", t), "session_handle_token": ("s", t)},))
        if code:
            return False, "A criação da sessão de entrada foi recusada."
        self._handle = results["session_handle"][1]

        options = {"types": ("u", _DEVICE_KEYBOARD),
                   "persist_mode": ("u", _PERSIST_UNTIL_REVOKED)}
        token = _load_token()
        if token:
            options["restore_token"] = ("s", token)
        code, _ = self._request(sender, "SelectDevices", "oa{sv}",
                                lambda t: (self._handle, {**options, "handle_token": ("s", t)}))
        if code:
            return False, "O portal recusou o pedido de acesso ao teclado."

        code, results = self._request(sender, "Start", "osa{sv}",
                                      lambda t: (self._handle, "", {"handle_token": ("s", t)}),
                                      timeout=_TIMEOUT_DIALOG)
        if code:
            self._drop()
            return False, "Permissão de controle do teclado negada."
        if "restore_token" in results:
            _save_token(results["restore_token"][1])
        if not results.get("devices", (None, 0))[1] & _DEVICE_KEYBOARD:
            self._drop()
            return False, "O portal não concedeu acesso ao teclado."
        return True, ""

    def _request(self, sender: str, method: str, signature: str, build,
                 timeout: float = _TIMEOUT_CALL):
        """Chama um método que responde por sinal e espera a resposta.

        A regra de match entra antes da chamada: o portal pode responder antes
        de a chamada retornar, e o sinal se perderia.
        """
        token = "sussurro" + secrets.token_hex(8)
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request",
                         member="Response", path=path)
        self._conn.send_and_get_reply(message_bus.AddMatch(rule))
        with self._conn.filter(rule) as queue:
            self._call(method, signature, build(token))
            return self._conn.recv_until_filtered(queue, timeout=timeout).body

    def _call(self, method: str, signature: str, body):
        reply = self._conn.send_and_get_reply(
            new_method_call(_PORTAL, method, signature, body))
        if reply.header.message_type is MessageType.error:
            raise RuntimeError(f"{reply.header.fields.get(4)}: {reply.body}")
        return reply.body

    def _notify_key(self, code: int, state: int) -> None:
        self._call("NotifyKeyboardKeycode", "oa{sv}iu", (self._handle, {}, code, state))

    def _drop(self) -> None:
        self._handle = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def _keysym(char: str) -> int:
    """Keysym X11 do caractere: ASCII é ele mesmo, o resto usa a faixa Unicode."""
    code = ord(char)
    return code if 0x20 <= code <= 0x7E else 0x01000000 + code


def _load_token() -> str:
    try:
        return _TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def _save_token(token: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(token)
        _TOKEN_FILE.chmod(0o600)
    except OSError:
        pass          # sem o token o próximo início só pede autorização de novo


keyboard = RemoteDesktop()          # sessão única, compartilhada pelas colagens
