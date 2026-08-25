# CLAUDE.md

Guia para agentes trabalhando neste repositório. Complementa o [README](README.md), que
descreve o uso; aqui está como o código funciona e onde ele morde. O
[LEARNING.md](LEARNING.md) guarda o diário técnico — o sintoma e a investigação por trás
de cada regra daqui.

## Stack e comandos

Python 3.11+ com PySide6 (Qt 6), `python-xlib` e `requests`. Ambiente virtual em `.venv`,
criado pelo `install.sh` (via `uv` se existir, senão `python -m venv`).

```bash
PYTHONPATH=. .venv/bin/python -m sussurro            # executa
PYTHONPATH=. .venv/bin/python -m sussurro --settings  # já abre as configurações
PYTHONPATH=. .venv/bin/python tools/preview.py rec    # rec|work|ok|check|err|info
.venv/bin/python -m pyflakes sussurro/*.py tools/*.py # lint (única ferramenta usada)
```

Não há suíte de testes automatizados: a verificação é feita dirigindo a interface de
verdade (veja *Como testar*).

## Arquitetura

Uma máquina de estados (`IDLE → RECORDING → WORKING → IDLE`) em `app.py` costura módulos
independentes, cada um com uma responsabilidade:

| Módulo | Responsabilidade |
| --- | --- |
| `hotkey.py` | `XGrabKey` da tecla escolhida numa conexão X11 própria; emite `pressed`/`released`/`cancelled` |
| `recorder.py` | `pw-record` em subprocesso, PCM cru do stdout, nível do sinal em tempo real |
| `transcriber.py` | POST multipart para o serviço ativo (Groq ou servidor próprio compatível com OpenAI), com uma retentativa e erros traduzidos |
| `paste.py` | Descobre a janela em foco (WM_CLASS) e envia a colagem por XTEST |
| `overlay.py` | Cápsula flutuante, pintada à mão quadro a quadro |
| `settings_window.py` | Janela sem moldura, widgets animados próprios |
| `tray.py` | Ícone de bandeja e menu |
| `theme.py` | Paleta monocromática ativa (claro/escuro) |
| `config.py` | Dataclass persistida em JSON + listas de opções da interface |

**Fluxo.** Tecla pressionada → `overlay.begin()` + `recorder.start()` + grab temporário do
`Esc`. Tecla solta → debounce de 60 ms (rede de segurança contra auto-repeat) → o PCM vira
WAV em memória → upload → texto na área de transferência → colagem 90 ms depois.

**Threads.** A interface vive só na thread principal. Rodam à parte: o laço X11 do atalho
(`select` sobre o `fileno()` do Display), a leitura do stdout do `pw-record`, o request da
API e o envio da colagem. Todas se comunicam por Signals do Qt — conexões em fila, porque
os objetos moram na thread principal. Nunca toque em widget fora dela.

**Áudio.** `pw-record --rate 16000 --channels 1 --format s16 -` entrega PCM cru (sem
cabeçalho) no stdout; o cabeçalho WAV só é montado no envio. O primeiro byte leva ~140 ms
para chegar — por isso a cápsula só mostra o estado "ao vivo" quando o áudio começa.

**Instância única.** `QLocalServer` num socket por UID (`/tmp/sussurro-$UID`). Uma segunda
execução não sobe outro processo: manda a primeira abrir as configurações.

## Convenções

- Textos de interface, comentários e documentação em **pt-BR**; identificadores em inglês.
- Comentário só quando explica *por quê*, não *o quê* — a maioria dos comentários do
  código marca uma armadilha da plataforma.
- Nada de dependência nova sem necessidade real; a lista atual cabe em três pacotes.
- Widgets internos da janela de configurações são prefixados com `_` (`_Button`, `_Combo`).

## Armadilhas do stack

Cada uma custou uma sessão de depuração. Não desfaça sem entender o motivo.

1. **`closeEvent` que ignora o evento cancela o `app.quit()` inteiro.** No Qt 6, `quit()`
   fecha todas as janelas antes de sair, e uma recusa aborta o encerramento. Sintoma: o
   aplicativo ignorava `SIGTERM` — e travaria o logout — só quando a janela de
   configurações estava aberta. Por isso existem `prepare_shutdown()` e a propriedade
   `sussurro_quitting` no QApplication.
2. **O auto-repeat do X11 gera pares press/release falsos** enquanto a tecla está
   segurada (25/s no padrão), o que encerraria a gravação no meio da fala. A solução é
   `change_keyboard_control(key=..., auto_repeat_mode=Off)` só para a tecla do atalho,
   restaurando ao sair. `XkbSetDetectableAutoRepeat` não é opção: `python-xlib` 0.33 não
   expõe a extensão xkb.
3. **`select()` sozinho perde eventos do X11.** Todo `sync()` — os grabs fazem um por
   gravação — lê o socket inteiro atrás da resposta e leva junto os eventos que chegaram
   no meio; eles ficam na fila interna do `python-xlib`, onde o `select` não os enxerga.
   Sintoma: reapertar o atalho ~80 ms depois de soltar engolia o press até a tecla
   seguinte — a cápsula não aparecia e o ditado saía "muito curto". Por isso o laço de
   `hotkey.py` drena `pending_events()` a cada volta, e não só quando o `select` acusa.
4. **Folhas de estilo do Qt não animam** (não existe `transition`). Botões e barra de
   rolagem são pintados à mão com `QPropertyAnimation` sobre propriedades próprias.
5. **Widget com texto longo estica a largura mínima da página.** Com a barra horizontal
   desligada, o excedente é cortado em silêncio — foi o que escondeu a borda dos cartões.
   Qualquer widget de texto variável precisa de `QSizePolicy.Ignored` + elisão manual.
6. **`QComboBox` muda de valor com a roda do mouse.** `_Combo.wheelEvent` ignora o evento
   de propósito, e `AdjustToMinimumContentsLengthWithIcon` permite encolher.
7. **`setDesktopSettingsAware(False)` quebra a detecção de tema**: `colorScheme()` passa a
   devolver `Unknown`. Não volte a chamá-lo.
8. **Sinais Unix só são tratados entre bytecodes do Python.** O `QTimer` de 400 ms no
   `__main__.py` existe só para devolver o controle ao interpretador; sem ele o
   `SIGTERM` nunca chega.
9. **Aplicativo iniciado pela sessão gráfica não herda o ambiente do shell.** Proxy
   exportado no `.zshrc` existe só em terminais: pelo autostart, o `requests` sai direto
   e a transcrição falha. Por isso `transcriber.proxies_for()` cai para a configuração de
   proxy do sistema (`kioslaverc`) quando não há variáveis de ambiente.
10. **A cápsula é override-redirect** (`X11BypassWindowManagerHint`): nunca recebe foco,
   que é o que permite colar na janela do usuário logo depois. A opacidade é uma
   propriedade interna (`fade`), não `windowOpacity`, para não depender do compositor.

## Como testar

Dirija a interface de verdade em vez de confiar na leitura do código:

```bash
# simula segurar e soltar a tecla (XTEST), como se fosse o teclado físico
.venv/bin/python -c "
import time
from Xlib import display, X, XK
from Xlib.ext import xtest
d = display.Display(); kc = d.keysym_to_keycode(XK.string_to_keysym('Pause'))
xtest.fake_input(d, X.KeyPress, kc); d.sync(); time.sleep(2)
xtest.fake_input(d, X.KeyRelease, kc); d.sync()"

# localiza a janela e captura só ela
xwininfo -root -tree | grep 'Configurações": ("sussurro"'
import -window root -crop 620x840+2570+120 +repage /tmp/janela.png
```

Três cuidados que evitam estrago e confusão:

- **Use uma configuração isolada** (`XDG_CONFIG_HOME` e `XDG_DATA_HOME` apontando para um
  diretório temporário) com `"paste_mode": "none"`. Sem isso o teste cola texto de
  verdade na janela que estiver em foco.
- **Encerre a instância real antes**: o socket de instância única é por UID e ignora as
  variáveis XDG, então a instância de teste apenas conversa com a que já roda e sai.
- **`pgrep -f "…sussurro"` casa com o próprio shell do comando.** Filtre `$$` e `$PPID`
  antes de matar, ou você derruba a sessão em vez do aplicativo.

Para avaliar o visual sem gastar chamadas de API, `tools/preview.py` desenha qualquer
estado da cápsula, e `SUSSURRO_TEMA=dark|light` força a paleta.

## Segurança

- As chaves de API (Groq e servidor próprio) vivem em `~/.config/sussurro/config.json`
  (modo `600`); a da Groq também pode vir da variável `GROQ_API_KEY`. **Nunca** versione
  nenhuma delas — o `.gitignore` cobre o arquivo, mas confira antes de qualquer commit.
- O aplicativo faz grab de teclado e abre o microfone. Mudanças em `hotkey.py` não podem
  ampliar a captura para além da tecla configurada: interceptar o teclado inteiro
  transformaria isto num keylogger.
- O áudio não é gravado em disco em nenhum momento. Se precisar depurar áudio, escreva o
  arquivo num diretório temporário e apague depois — não deixe isso no código.
