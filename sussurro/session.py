"""Que tipo de sessão gráfica está no ar.

O Sussurro nasceu no X11, onde ele mesmo dava conta de tudo: `XGrabKey` para o
atalho, `XTEST` para a colagem. No Wayland nada disso é permitido a um
aplicativo comum — o compositor é o dono do teclado —, e cada uma dessas duas
funções passa a depender de um serviço do sistema. Este módulo é o único lugar
que decide qual caminho seguir.
"""

from __future__ import annotations

import os


def is_wayland() -> bool:
    """True quando o compositor é Wayland, mesmo que o Qt rode sobre XWayland.

    A checagem não pode ser `QGuiApplication.platformName()`: o aplicativo força
    o backend `xcb` (veja `__main__.py`), então o Qt diria "xcb" numa sessão
    Wayland e o atalho seria registrado no lugar errado.
    """
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))
