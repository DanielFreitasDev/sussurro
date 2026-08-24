"""Captura de áudio via PipeWire (pw-record) com medição de nível em tempo real.

O áudio é lido como PCM bruto (s16 mono 16 kHz) — exatamente o formato para
o qual o Whisper reamostra internamente — e só ganha cabeçalho WAV na hora
de subir para a API.
"""

from __future__ import annotations

import io
import math
import shutil
import struct
import subprocess
import threading
import wave

from PySide6.QtCore import QObject, Signal

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2                       # s16
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
CHUNK = 2048                           # 64 ms


class Recorder(QObject):
    """Grava em memória enquanto emite o nível do sinal para a interface."""

    level = Signal(float)     # 0.0 – 1.0, já em escala perceptual
    started = Signal()        # primeiro áudio realmente capturado
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._running = False
        self._peak = 0.0

    # -- ciclo de vida -------------------------------------------------------
    def start(self, device: str = "") -> bool:
        if self._running:
            return True
        if not shutil.which("pw-record"):
            self.failed.emit("pw-record não encontrado (instale o pacote pipewire-bin).")
            return False

        cmd = [
            "pw-record",
            "--rate", str(SAMPLE_RATE),
            "--channels", str(CHANNELS),
            "--format", "s16",
            "--latency", "20ms",
            "--media-role", "Communication",
        ]
        if device:
            cmd += ["--target", device]
        cmd.append("-")

        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
            )
        except OSError as exc:
            self.failed.emit(f"Falha ao iniciar a captura de áudio: {exc}")
            return False

        with self._lock:
            self._buffer = bytearray()
        self._peak = 0.0
        self._running = True
        self._thread = threading.Thread(target=self._pump, name="sussurro-rec", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> bytes:
        """Encerra a captura e devolve o PCM bruto acumulado."""
        self._running = False
        proc, self._proc = self._proc, None
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        with self._lock:
            data = bytes(self._buffer)
            self._buffer = bytearray()
        return data

    # -- métricas ------------------------------------------------------------
    @property
    def duration(self) -> float:
        with self._lock:
            return len(self._buffer) / BYTES_PER_SECOND

    @property
    def peak(self) -> float:
        return self._peak

    # -- interno -------------------------------------------------------------
    def _pump(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        first = True
        while self._running:
            try:
                data = proc.stdout.read(CHUNK)
            except (ValueError, OSError):
                break
            if not data:
                break
            if first:
                first = False
                self.started.emit()
            with self._lock:
                self._buffer += data
            self.level.emit(self._analyse(data))

        if first and self._running:
            # Terminou sem entregar um único byte: microfone indisponível.
            stderr = b""
            if proc.stderr:
                try:
                    stderr = proc.stderr.read() or b""
                except Exception:
                    pass
            msg = stderr.decode("utf-8", "replace").strip().splitlines()
            self.failed.emit(msg[-1] if msg else "Nenhum áudio capturado do microfone.")

    def _analyse(self, data: bytes) -> float:
        count = len(data) // 2
        if not count:
            return 0.0
        samples = struct.unpack(f"<{count}h", data[: count * 2])
        step = max(1, count // 256)
        picked = samples[::step]
        rms = math.sqrt(sum(s * s for s in picked) / len(picked)) / 32768.0
        if rms <= 1e-6:
            return 0.0
        # dBFS -> 0..1 com piso em -55 dB (escala mais agradável que a linear)
        db = 20 * math.log10(rms)
        level = max(0.0, min(1.0, (db + 55.0) / 50.0))
        self._peak = max(self._peak, level)
        return level


def to_wav(pcm: bytes) -> bytes:
    """Empacota o PCM bruto em um WAV de 16 kHz mono."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return buf.getvalue()


def list_sources() -> list[tuple[str, str]]:
    """Fontes de áudio do PipeWire como (node.name, descrição)."""
    import json

    try:
        out = subprocess.run(["pw-dump"], capture_output=True, timeout=4, text=True)
        nodes = json.loads(out.stdout)
    except Exception:
        return []
    sources: list[tuple[str, str]] = []
    for node in nodes:
        props = ((node.get("info") or {}).get("props") or {})
        if props.get("media.class") != "Audio/Source":
            continue
        name = props.get("node.name")
        if not name:
            continue
        sources.append((name, props.get("node.description") or name))
    return sources
