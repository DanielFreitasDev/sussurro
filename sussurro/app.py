"""Máquina de estados do Sussurro: atalho → gravação → transcrição → colagem."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from . import history, paste, portal, session, theme
from .config import HOTKEYS, Config
from .hotkey import HotkeyListener
from .overlay import Overlay
from .recorder import BYTES_PER_SECOND, Recorder, to_wav
from .settings_window import SettingsWindow
from .transcriber import TranscribeWorker
from .tray import Tray

IDLE, RECORDING, WORKING = "idle", "recording", "working"
RELEASE_DEBOUNCE_MS = 60     # rede de segurança extra contra auto-repeat
SILENCE_THRESHOLD = 0.02
MIN_AUDIO_SECONDS = 0.12     # abaixo disso não há o que transcrever


class Sussurro(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self.app = app
        self.cfg = Config.load()
        theme.set_mode(self.cfg.theme)
        self.state = IDLE
        self._clipboard_backup: str | None = None
        self._duration = 0.0
        self._held_since = 0.0

        self.overlay = Overlay()
        self.overlay.set_position(self.cfg.overlay_position)

        self.recorder = Recorder(self)
        self.recorder.level.connect(self.overlay.push_level)
        self.recorder.started.connect(self.overlay.set_live)
        self.recorder.failed.connect(self._on_record_failed)

        self.worker = TranscribeWorker()
        self.worker.finished.connect(self._on_text)
        self.worker.failed.connect(self._on_api_error)

        self.hotkey = HotkeyListener(self.cfg.hotkey)
        self.hotkey.pressed.connect(self._on_press)
        self.hotkey.released.connect(self._on_release)
        self.hotkey.cancelled.connect(self.cancel)
        self.hotkey.failed.connect(self._on_hotkey_failed)

        self.tray = Tray(self._hotkey_label(), self)
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.toggle_requested.connect(self._toggle)
        self.tray.quit_requested.connect(self.quit)
        self.tray.enabled_changed.connect(self.hotkey.set_enabled)

        self.settings: SettingsWindow | None = None
        self.server = None          # QLocalServer da instância única

        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.timeout.connect(self._finish_recording)

        self._max_timer = QTimer(self)
        self._max_timer.setSingleShot(True)
        self._max_timer.timeout.connect(self._finish_recording)

        self._paste_bridge = _PasteBridge()
        self._paste_bridge.done.connect(self._on_paste_done)

        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self._on_system_theme)

        self.hotkey.start()

        if not self.cfg.resolved_key():
            QTimer.singleShot(400, self._first_run)

        if session.is_wayland() and self.cfg.paste_mode != "none":
            QTimer.singleShot(1500, portal.keyboard.prepare)

    # -- atalho --------------------------------------------------------------
    def _on_press(self) -> None:
        if self._release_timer.isActive():
            # Auto-repeat: a tecla nunca foi realmente solta.
            self._release_timer.stop()
            return
        if self.state == RECORDING:
            return
        if self.state == WORKING:
            self.overlay.show_info("Aguardando a transcrição anterior…")
            return
        self._start_recording()

    def _on_release(self) -> None:
        if self.state != RECORDING:
            return
        self._release_timer.start(RELEASE_DEBOUNCE_MS)

    def _toggle(self) -> None:
        if self.state == RECORDING:
            self._release_timer.stop()
            self._finish_recording()
        elif self.state == IDLE:
            self._start_recording()

    # -- ciclo ---------------------------------------------------------------
    def _start_recording(self) -> None:
        if not self.cfg.resolved_key():
            self.overlay.show_error("Configure sua chave de API.")
            self.open_settings()
            return
        self.overlay.begin()
        if not self.recorder.start(self.cfg.input_device):
            self.overlay.show_error("Não foi possível acessar o microfone.")
            return
        self._held_since = time.monotonic()
        self.state = RECORDING
        self.tray.set_recording()
        self.hotkey.grab_escape(True)
        self._max_timer.start(self.cfg.max_duration * 1000)

    def _finish_recording(self) -> None:
        if self.state != RECORDING:
            return
        self._max_timer.stop()
        self.hotkey.grab_escape(False)
        held = time.monotonic() - self._held_since
        peak = self.recorder.peak
        pcm = self.recorder.stop()
        self._duration = len(pcm) / BYTES_PER_SECOND

        # O veredito é sobre quanto tempo a tecla ficou segurada, não sobre o
        # tamanho do áudio: o `pw-record` leva ~150 ms até o primeiro byte, e
        # cobrar isso do usuário descartava ditados em que ele segurou o
        # suficiente. Áudio vazio com tecla segurada é falha da captura.
        if held < self.cfg.min_duration:
            self.state = IDLE
            self.tray.set_idle(self._hotkey_label())
            self.overlay.show_info("Muito curto — segure a tecla enquanto fala.")
            return
        if self._duration < MIN_AUDIO_SECONDS:
            self.state = IDLE
            self.tray.set_idle(self._hotkey_label())
            self.overlay.show_error("O microfone não entregou áudio a tempo.")
            return
        if peak < SILENCE_THRESHOLD:
            self.state = IDLE
            self.tray.set_idle(self._hotkey_label())
            self.overlay.show_error("Nenhum som captado — verifique o microfone.")
            return

        self.state = WORKING
        self.tray.set_working()
        self.overlay.show_working()
        self.worker.run(
            to_wav(pcm),
            api_key=self.cfg.resolved_key(),
            model=self.cfg.model,
            language=self.cfg.language,
            prompt=self.cfg.prompt,
            temperature=self.cfg.temperature,
            proxy=self.cfg.proxy,
            base_url=self.cfg.base_url(),
        )

    def cancel(self) -> None:
        if self.state != RECORDING:
            return
        self._release_timer.stop()
        self._max_timer.stop()
        self.hotkey.grab_escape(False)
        self.recorder.stop()
        self.state = IDLE
        self.tray.set_idle(self._hotkey_label())
        self.overlay.show_info("Cancelado.")

    # -- resultado -----------------------------------------------------------
    def _on_text(self, text: str) -> None:
        self.state = IDLE
        self.tray.set_idle(self._hotkey_label())
        text = text.strip()
        if not text:
            self.overlay.show_info("Nada foi reconhecido.")
            return

        if self.cfg.keep_history:
            history.add(text, self._duration, self.cfg.model)
            self.tray.refresh_history()
            if self.settings and self.settings.isVisible():
                self.settings.refresh_history()

        clipboard = QGuiApplication.clipboard()
        self._clipboard_backup = clipboard.text() if self.cfg.restore_clipboard else None
        clipboard.setText(text)

        feedback = self.cfg.result_feedback
        if feedback == "none":
            self.overlay.dismiss()
        elif feedback == "check":
            self.overlay.show_success()
        else:
            self.overlay.show_success(text)

        if self.cfg.paste_mode != "none":
            QTimer.singleShot(90, lambda: paste.deliver(
                text, self.cfg.paste_mode, on_done=self._paste_bridge.done.emit))
        elif self._clipboard_backup is not None:
            self._clipboard_backup = None

    def _on_paste_done(self, ok: bool, error: str) -> None:
        if not ok:
            self.overlay.show_error(f"{error} O texto ficou na área de transferência.")
            self._clipboard_backup = None
            return
        if self._clipboard_backup is not None:
            backup, self._clipboard_backup = self._clipboard_backup, None
            QTimer.singleShot(1200, lambda: QGuiApplication.clipboard().setText(backup))

    def _on_api_error(self, message: str) -> None:
        self.state = IDLE
        self.tray.set_idle(self._hotkey_label())
        self.overlay.show_error(message)

    def _on_record_failed(self, message: str) -> None:
        if self.state == RECORDING:
            self._max_timer.stop()
            self.hotkey.grab_escape(False)
            self.recorder.stop()
            self.state = IDLE
            self.tray.set_idle(self._hotkey_label())
        self.overlay.show_error(message)

    def _on_hotkey_failed(self, message: str) -> None:
        self.overlay.show_error(message)
        self.tray.notify("Sussurro", message)

    # -- interface -----------------------------------------------------------
    def open_settings(self) -> None:
        if self.settings is None:
            self.settings = SettingsWindow(self.cfg)
            self.settings.applied.connect(self._apply_config)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def _first_run(self) -> None:
        self.open_settings()
        self.tray.notify(
            "Sussurro está pronto",
            "Cole sua chave de API para começar a ditar.",
        )

    def _apply_config(self) -> None:
        self.hotkey.set_hotkey(self.cfg.hotkey)
        self.overlay.set_position(self.cfg.overlay_position)
        self.tray.set_idle(self._hotkey_label())
        if theme.set_mode(self.cfg.theme):
            self._restyle()

    def _on_system_theme(self, _scheme=None) -> None:
        if theme.refresh():
            self._restyle()

    def _restyle(self) -> None:
        self.overlay.update()
        self.tray.refresh_icon()
        if self.settings is not None:
            self.settings.restyle()

    def _hotkey_label(self) -> str:
        return dict(HOTKEYS).get(self.cfg.hotkey, self.cfg.hotkey)

    def quit(self) -> None:
        self.app.setProperty("sussurro_quitting", True)
        if self.settings is not None:
            self.settings.prepare_shutdown()
        if self.server is not None:
            # Para de aceitar conexões antes de desligar: assim um relançamento
            # imediato sobe uma instância nova em vez de falar com esta, morrendo.
            self.server.close()
            self.server = None
        self.hotkey.stop()
        if self.state == RECORDING:
            self.recorder.stop()
        self.overlay.hide()
        self.app.quit()


class _PasteBridge(QObject):
    done = Signal(bool, str)
