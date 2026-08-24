"""Ponto de entrada: instância única + bandeja."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import APP_ID, APP_NAME
from .app import Sussurro

SOCKET = f"sussurro-{os.getuid()}"


def _already_running() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(SOCKET)
    if socket.waitForConnected(300):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True
    return False


def main() -> int:
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get("DISPLAY"):
        print("Sussurro precisa de uma sessão X11 (ou XWayland) para capturar o atalho.",
              file=sys.stderr)
        return 1

    # WM_CLASS previsível: o KDE agrupa a janela com o atalho .desktop.
    os.environ.setdefault("RESOURCE_NAME", APP_ID)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(False)
    for efeito in (Qt.UIEffect.UI_AnimateCombo, Qt.UIEffect.UI_AnimateMenu,
                   Qt.UIEffect.UI_FadeMenu, Qt.UIEffect.UI_AnimateTooltip,
                   Qt.UIEffect.UI_FadeTooltip):
        app.setEffectEnabled(efeito, True)
    icon = Path(__file__).parent / "assets" / "icon.svg"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    if _already_running():
        print("Sussurro já está em execução — abrindo as configurações.")
        return 0
    QLocalServer.removeServer(SOCKET)
    server = QLocalServer()
    server.listen(SOCKET)

    sussurro = Sussurro(app)
    sussurro.server = server
    server.newConnection.connect(
        lambda: (server.nextPendingConnection(), sussurro.open_settings()))

    if "--settings" in sys.argv:
        sussurro.open_settings()

    signal.signal(signal.SIGINT, lambda *_: sussurro.quit())
    signal.signal(signal.SIGTERM, lambda *_: sussurro.quit())
    heartbeat = QTimer()          # devolve o controle ao Python p/ tratar sinais
    heartbeat.start(400)
    heartbeat.timeout.connect(lambda: None)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
