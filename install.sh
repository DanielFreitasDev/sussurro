#!/usr/bin/env bash
# Instalação do Sussurro: ambiente virtual, lançador, ícone e atalho de sessão.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
AUTOSTART_DIR="$HOME/.config/autostart"

info() { printf '\033[38;5;141m▸\033[0m %s\n' "$1"; }
warn() { printf '\033[38;5;214m!\033[0m %s\n' "$1"; }

info "Preparando o ambiente virtual…"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv "$ROOT/.venv" >/dev/null
  else
    python3 -m venv "$ROOT/.venv"
  fi
fi
if command -v uv >/dev/null 2>&1; then
  uv pip install --quiet --python "$ROOT/.venv/bin/python" pyside6 python-xlib requests
else
  "$ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip
  "$ROOT/.venv/bin/python" -m pip install --quiet pyside6 python-xlib requests
fi

info "Instalando o lançador em $BIN_DIR/sussurro"
mkdir -p "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
ln -sf "$ROOT/bin/sussurro" "$BIN_DIR/sussurro"
cp -f "$ROOT/sussurro/assets/icon.svg" "$ICON_DIR/sussurro.svg"

cat > "$APPS_DIR/sussurro.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Sussurro
GenericName=Ditado por voz
Comment=Segure a tecla de atalho, fale e o texto aparece onde o cursor estiver
Exec=$BIN_DIR/sussurro
Icon=sussurro
Terminal=false
Categories=Utility;AudioVideo;
Keywords=ditado;voz;transcrição;whisper;
StartupNotify=false
DESKTOP

case "${1:-}" in
  --autostart)    reply="s" ;;
  --no-autostart) reply="n" ;;
  *) read -r -p "Iniciar o Sussurro junto com a sessão? [S/n] " reply ;;
esac
if [[ ! "$reply" =~ ^[Nn] ]]; then
  mkdir -p "$AUTOSTART_DIR"
  cp -f "$APPS_DIR/sussurro.desktop" "$AUTOSTART_DIR/sussurro.desktop"
  info "Autostart ativado."
fi

command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "Adicione $BIN_DIR ao seu PATH para chamar 'sussurro' de qualquer lugar." ;;
esac

info "Pronto. Rode 'sussurro' (ou procure por Sussurro no menu) e cole sua chave da Groq."
