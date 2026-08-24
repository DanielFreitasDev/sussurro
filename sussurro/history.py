"""Histórico local das transcrições (JSONL, últimas N entradas)."""

from __future__ import annotations

import json
import time

from .config import DATA_DIR

HISTORY_FILE = DATA_DIR / "history.jsonl"
MAX_ENTRIES = 200


def add(text: str, duration: float, model: str) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "text": text,
        "duration": round(duration, 2),
        "model": model,
    }
    with HISTORY_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _trim()


def recent(limit: int = 20) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    out: list[dict] = []
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


def clear() -> None:
    if HISTORY_FILE.exists():
        HISTORY_FILE.unlink()


def _trim() -> None:
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) > MAX_ENTRIES:
        HISTORY_FILE.write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n", encoding="utf-8")
