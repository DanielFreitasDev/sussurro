"""Cliente da API de transcrição da Groq (Whisper)."""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QObject, Signal

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELS_ENDPOINT = "https://api.groq.com/openai/v1/models"
TIMEOUT = (10, 90)          # (conexão, leitura)
MAX_UPLOAD = 25 * 1024 * 1024


class TranscriptionError(Exception):
    pass


# -- proxy -------------------------------------------------------------------
_ENV_PROXY_VARS = ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY",
                   "all_proxy", "ALL_PROXY")


def proxies_for(explicit: str = "") -> dict[str, str] | None:
    """Proxy a usar, em ordem: ajuste do app, ambiente, configuração do KDE.

    Aplicativos iniciados pela sessão gráfica não herdam o `http_proxy` que o
    shell exporta, então descobrir o proxy sozinho é o que faz o ditado
    funcionar tanto pelo autostart quanto pelo terminal.
    """
    if explicit:
        return {"http": explicit, "https": explicit}
    if any(os.environ.get(var) for var in _ENV_PROXY_VARS):
        return None                      # o requests já resolve pelo ambiente
    desktop = _desktop_proxy()
    if desktop:
        return {"http": desktop, "https": desktop}
    return None


def _desktop_proxy() -> str:
    """Lê o proxy manual configurado no KDE (~/.config/kioslaverc)."""
    path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kioslaverc"
    try:
        linhas = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""

    values: dict[str, str] = {}
    for line in linhas:
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip())

    # 1 = manual, 4 = usar variáveis de ambiente (já tratado acima)
    if values.get("ProxyType") != "1":
        return ""

    host = urlparse(ENDPOINT).hostname or ""
    for entry in values.get("NoProxyFor", "").split(","):
        entry = entry.strip().lstrip(".")
        if entry and (host == entry or host.endswith("." + entry)):
            return ""

    raw = values.get("httpsProxy") or values.get("httpProxy") or ""
    # O KDE grava "http://127.0.0.1 3128" — com espaço no lugar dos dois-pontos.
    parts = raw.split()
    if len(parts) == 2 and parts[1].isdigit():
        raw = f"{parts[0]}:{parts[1]}"
    if raw and "://" not in raw:
        raw = "http://" + raw
    return raw


def transcribe(wav: bytes, *, api_key: str, model: str, language: str = "",
               prompt: str = "", temperature: float = 0.0, proxy: str = "") -> str:
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
                proxies=proxies_for(proxy),
            )
        except requests.Timeout:
            last_error = TranscriptionError("A API demorou demais para responder.")
            exc_retry = True
        except requests.RequestException as exc:
            last_error = TranscriptionError(_network_message(exc, proxy))
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


def check_key(api_key: str, proxy: str = "") -> tuple[bool, str]:
    """Valida a chave listando os modelos disponíveis."""
    if not api_key:
        return False, "Informe uma chave de API."
    try:
        resp = requests.get(
            MODELS_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(10, 20),
            proxies=proxies_for(proxy),
        )
    except requests.RequestException as exc:
        return False, _network_message(exc, proxy)
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


# O requests usa ProxyError tanto para "não alcancei o proxy" quanto para "o
# proxy respondeu e recusou o túnel"; só a causa aninhada separa os dois.
_TUNNEL_RE = re.compile(r"Tunnel connection failed: (\d{3}) ([^'\"\\)]*)")


def _network_message(exc: Exception, proxy: str = "") -> str:
    """Traduz erros de conexão para algo que aponte a causa provável."""
    if isinstance(exc, requests.exceptions.ProxyError):
        return _proxy_message(exc, proxy)
    if isinstance(exc, requests.exceptions.SSLError):
        return "Falha no TLS ao falar com a Groq — pode ser inspeção do proxy."
    if isinstance(exc, requests.exceptions.ConnectionError):
        address = _proxy_address(proxy)
        if address:
            return f"Sem acesso a {_host()} pelo proxy {address}."
        return (f"Sem acesso a {_host()}. Se sua rede exige proxy, informe-o "
                "nas configurações.")
    return f"Falha de rede: {_short(exc)}"


def _proxy_message(exc: Exception, proxy: str = "") -> str:
    """Distingue proxy inalcançável de proxy que recusou o túnel.

    Os dois casos pedem ações opostas: conferir o endereço só resolve o
    primeiro. No segundo o proxy respondeu — e o código que ele devolveu é
    que aponta a causa, então mostrá-lo evita mandar depurar o que está certo.
    """
    match = _TUNNEL_RE.search(str(exc))
    if not match:
        address = _proxy_address(proxy)
        onde = f" {address}" if address else ""
        return (f"Não foi possível conectar ao proxy{onde}. Confira o endereço "
                "nas configurações.")

    code, reason = match.group(1), match.group(2).strip()
    if code == "407":
        return ("O proxy exige autenticação. Informe usuário e senha no endereço "
                "do proxy (http://usuario:senha@host:porta).")
    # 500 entra aqui porque o squid o devolve quando nenhum pai está de pé
    # (HIER_NONE no log) — transitório como os 5xx de gateway, não erro de uso.
    if code in ("500", "502", "503", "504"):
        return (f"O proxy não alcançou {_host()} agora ({code} {reason}). "
                "Costuma ser passageiro — tente de novo.")
    return f"O proxy recusou o túnel até {_host()} ({code} {reason})."


def _proxy_address(proxy: str = "") -> str:
    """Endereço do proxy em uso — inclusive quando quem resolve é o requests."""
    mapping = proxies_for(proxy)
    if mapping:
        url = mapping.get("https") or mapping.get("http") or ""
    else:
        url = next((os.environ[var] for var in _ENV_PROXY_VARS
                    if os.environ.get(var)), "")
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else "http://" + url)
    if not parsed.hostname:
        return ""
    return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname


def _host() -> str:
    return urlparse(ENDPOINT).hostname or "a Groq"


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
