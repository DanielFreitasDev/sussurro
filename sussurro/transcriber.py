"""Cliente da API de transcrição da Groq (Whisper)."""

from __future__ import annotations

import threading
import time

import requests
from PySide6.QtCore import QObject, Signal

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELS_ENDPOINT = "https://api.groq.com/openai/v1/models"
TIMEOUT = (10, 90)          # (conexão, leitura)
MAX_UPLOAD = 25 * 1024 * 1024


class TranscriptionError(Exception):
    pass


def transcribe(wav: bytes, *, api_key: str, model: str, language: str = "",
               prompt: str = "", temperature: float = 0.0) -> str:
    if not api_key:
        raise TranscriptionError("Nenhuma chave de API configurada.")
    if len(wav) > MAX_UPLOAD:
        raise TranscriptionError("Áudio longo demais para o limite de 25 MB da conta.")

    data = {
        "model": model,
        "temperature": str(temperature),
        "response_format": "json",
    }
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt[:1000]

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            resp = requests.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files={"file": ("audio.wav", wav, "audio/wav")},
                timeout=TIMEOUT,
            )
        except requests.Timeout:
            last_error = TranscriptionError("A API demorou demais para responder.")
            exc_retry = True
        except requests.RequestException as exc:
            last_error = TranscriptionError(f"Falha de rede: {_short(exc)}")
            exc_retry = True
        else:
            if resp.status_code == 200:
                try:
                    return (resp.json().get("text") or "").strip()
                except ValueError:
                    raise TranscriptionError("Resposta inesperada da API.")
            if resp.status_code in (500, 502, 503, 504) and attempt == 0:
                last_error = TranscriptionError(_api_message(resp))
                exc_retry = True
            else:
                raise TranscriptionError(_api_message(resp))

        if exc_retry and attempt == 0:
            time.sleep(0.8)

    raise last_error or TranscriptionError("Falha desconhecida na transcrição.")


def check_key(api_key: str) -> tuple[bool, str]:
    """Valida a chave listando os modelos disponíveis."""
    if not api_key:
        return False, "Informe uma chave de API."
    try:
        resp = requests.get(
            MODELS_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(10, 20),
        )
    except requests.RequestException as exc:
        return False, f"Falha de rede: {_short(exc)}"
    if resp.status_code == 200:
        try:
            ids = {m.get("id") for m in resp.json().get("data", [])}
        except ValueError:
            ids = set()
        whisper = sorted(i for i in ids if i and "whisper" in i)
        if whisper:
            return True, "Chave válida — modelos: " + ", ".join(whisper)
        return True, "Chave válida."
    return False, _api_message(resp)


def _api_message(resp: requests.Response) -> str:
    detail = ""
    try:
        payload = resp.json()
        detail = ((payload.get("error") or {}).get("message")
                  or payload.get("message") or "")
    except ValueError:
        detail = (resp.text or "").strip()[:200]

    if resp.status_code == 401:
        return "Chave de API inválida ou revogada."
    if resp.status_code == 413:
        return "Áudio maior que o limite aceito pela API."
    if resp.status_code == 429:
        retry = resp.headers.get("retry-after")
        extra = f" Tente em {retry}s." if retry else ""
        return f"Limite de uso da conta atingido.{extra}"
    if resp.status_code >= 500:
        return "A Groq está instável no momento."
    return detail or f"Erro HTTP {resp.status_code}."


def _short(exc: Exception) -> str:
    text = str(exc)
    return text[:120] + "…" if len(text) > 120 else text


class TranscribeWorker(QObject):
    """Executa uma transcrição em thread separada."""

    finished = Signal(str)
    failed = Signal(str)

    def run(self, wav: bytes, **kwargs) -> None:
        def job() -> None:
            try:
                self.finished.emit(transcribe(wav, **kwargs))
            except TranscriptionError as exc:
                self.failed.emit(str(exc))
            except Exception as exc:  # rede/SSL inesperados
                self.failed.emit(_short(exc))

        threading.Thread(target=job, name="sussurro-api", daemon=True).start()
