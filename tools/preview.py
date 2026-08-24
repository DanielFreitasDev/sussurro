"""Pré-visualiza os estados do overlay sem precisar da API.

Uso: python tools/preview.py [rec|work|ok|check|err|info] [segundos]
"""

import math
import os
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from sussurro import theme
from sussurro.overlay import Overlay

state = sys.argv[1] if len(sys.argv) > 1 else "rec"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

app = QApplication(sys.argv)
theme.set_mode(os.environ.get("SUSSURRO_TEMA", "auto"))
overlay = Overlay()

if state == "rec":
    overlay.begin()
    overlay.set_live()
    start = time.monotonic()

    def feed():
        t = time.monotonic() - start
        # envelope de fala sintética: sílabas + pausas
        syllable = max(0.0, math.sin(t * 7.5)) ** 0.6
        phrase = 0.55 + 0.45 * math.sin(t * 1.1)
        overlay.push_level(min(1.0, 0.12 + syllable * phrase * 0.85))

    timer = QTimer()
    timer.timeout.connect(feed)
    timer.start(64)
elif state == "work":
    overlay.show_working()
elif state == "ok":
    overlay.show_success(
        "Preciso revisar o contrato antes da reunião de quinta-feira com o time "
        "de produto.")
    overlay._autohide.stop()
elif state == "check":
    overlay.show_success()
    overlay._autohide.stop()
elif state == "err":
    overlay.show_error("Limite de uso da conta atingido. Tente em 42s.")
    overlay._autohide.stop()
else:
    overlay.show_info("Muito curto — segure a tecla enquanto fala.")
    overlay._autohide.stop()

QTimer.singleShot(int(seconds * 1000), app.quit)
sys.exit(app.exec())
