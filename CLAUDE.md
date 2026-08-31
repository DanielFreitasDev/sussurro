# CLAUDE.md

Guia para agentes trabalhando neste repositório. Complementa o [README](README.md), que
descreve o uso; aqui está como o código funciona e onde ele morde. O
[LEARNING.md](LEARNING.md) guarda o diário técnico — o sintoma e a investigação por trás
de cada regra daqui.

## Stack e comandos

Python 3.11+ com PySide6 (Qt 6), `python-xlib`, `requests` e `jeepney`. Ambiente virtual
em `.venv`, criado pelo `install.sh` (via `uv` se existir, senão `python -m venv`).

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
| `hotkey.py` | Atalho global: `XGrabKey` numa conexão X11 própria **ou** KGlobalAccel por D-Bus no Wayland; emite `pressed`/`released`/`cancelled` |
| `recorder.py` | `pw-record` em subprocesso, PCM cru do stdout, nível do sinal em tempo real |
| `transcriber.py` | POST multipart para o serviço ativo (Groq ou servidor próprio compatível com OpenAI), com uma retentativa e erros traduzidos |
| `paste.py` | Envia a colagem por XTEST (X11) ou pelo portal (Wayland); no X11 ainda descobre a janela em foco por WM_CLASS |
| `portal.py` | Sessão do portal RemoteDesktop via `jeepney`: é quem sintetiza teclas no Wayland |
| `session.py` | Única decisão de plataforma do projeto: a sessão é Wayland? |
| `overlay.py` | Cápsula flutuante, pintada à mão quadro a quadro |
| `settings_window.py` | Janela sem moldura, widgets animados próprios |
| `tray.py` | Ícone de bandeja e menu |
| `theme.py` | Paleta monocromática ativa (claro/escuro) |
| `config.py` | Dataclass persistida em JSON + listas de opções da interface |

**Fluxo.** Tecla pressionada → `overlay.begin()` + `recorder.start()` + grab temporário do
`Esc`. Tecla solta → debounce de 60 ms (rede de segurança contra auto-repeat) → o PCM vira
WAV em memória → upload → texto na área de transferência → colagem 90 ms depois.

**Threads.** A interface vive só na thread principal. Rodam à parte: o laço X11 do atalho
(`select` sobre o `fileno()` do Display — no Wayland não há thread nenhuma, os sinais do
KGlobalAccel chegam pelo laço de eventos do Qt), a leitura do stdout do `pw-record`, o
request da API e o envio da colagem. Todas se comunicam por Signals do Qt — conexões em fila, porque
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
2. **(X11) O auto-repeat gera pares press/release falsos** enquanto a tecla está
   segurada (25/s no padrão), o que encerraria a gravação no meio da fala. A solução é
   `change_keyboard_control(key=..., auto_repeat_mode=Off)` só para a tecla do atalho,
   restaurando ao sair. `XkbSetDetectableAutoRepeat` não é opção: `python-xlib` 0.33 não
   expõe a extensão xkb. No Wayland o problema não existe: o KWin manda a repetição num
   sinal separado (`globalShortcutRepeated`), que o backend simplesmente não escuta.
3. **(X11) `select()` sozinho perde eventos.** Todo `sync()` — os grabs fazem um por
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

### Wayland (Plasma 6)

Estas custaram uma sessão inteira. O detalhe comum às três primeiras é que **falham em
silêncio**: nada levanta exceção, nada aparece no log do aplicativo.

11. **`setShortcut` sem `SetPresent` registra o atalho e não instala o grab.** As flags
    são `SetPresent=2 | NoAutoloading=4`; com `4` sozinho a tecla passa a constar como
    ocupada, o arquivo do KDE ganha a linha, e o atalho nunca dispara. O teste que
    distingue os dois casos é `isActive()` no objeto do componente.
12. **`QDBusInterface.call()` não converte tipos; a chamada dinâmica sim.** Use
    `iface.setShortcut(...)`, nunca `iface.call("setShortcut", ...)` — esta manda `av`/`i`
    onde o serviço espera `ai`/`u` e a mensagem é recusada inteira.
13. **`uint` dentro de `a{sv}` é impossível no QtDBus do PySide6.** É por isso, e só por
    isso, que `portal.py` usa `jeepney`. Não tente "simplificar" trocando por QtDBus: o
    portal responde `Expected type 'u' for option 'types', got 'i'`.
14. **A interface precisa rodar sobre XWayland** (`QT_QPA_PLATFORM=xcb`, forçado no
    `__main__.py`). No Wayland nativo a cápsula não consegue se posicionar. Atalho e
    colagem não dependem disso.
15. **Não há detecção de janela em foco no Wayland.** `active_window_class()` devolve `""`
    e o modo "automático" cai no Ctrl+V. Não existe conserto barato: o KWin só expõe
    `queryWindowInfo`, que exige o usuário clicar numa janela.

## Como testar

Dirija a interface de verdade em vez de confiar na leitura do código:

**No Wayland não dá para simular a tecla.** O XTEST é descartado pelo compositor (veja o
LEARNING.md), então *alguém precisa apertar a tecla de verdade*. O que dá para verificar
sozinho é se o registro ficou de pé — e essas três perguntas separam "registrado" de
"funcionando":

```bash
# 1. a tecla está capturada?  False = o Sussurro pegou
# 2. o componente está ativo? False = registrado porém decorativo (veja armadilha 11)
.venv/bin/python -c "
import sys
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import Qt
from PySide6.QtDBus import QDBusConnection, QDBusInterface
app = QCoreApplication(sys.argv); bus = QDBusConnection.sessionBus()
k = QDBusInterface('org.kde.kglobalaccel', '/kglobalaccel', 'org.kde.KGlobalAccel', bus)
print('livre :', k.isGlobalShortcutAvailable(int(Qt.Key.Key_Pause.value), 'x'))
c = QDBusInterface('org.kde.kglobalaccel', k.getComponent('sussurro').path(),
                   'org.kde.kglobalaccel.Component', bus)
print('ativo :', c.isActive(), c.shortcutNames())"

# 3. testemunha independente: o compositor está mesmo emitindo os sinais?
dbus-monitor --session "type='signal',interface='org.kde.kglobalaccel.Component'"
```

No X11 a simulação continua valendo:

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
