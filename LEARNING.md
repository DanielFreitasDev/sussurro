# LEARNING.md

Diário técnico deste projeto: o que só se descobre construindo, com o sintoma que levou
até lá. O [CLAUDE.md](CLAUDE.md) resume as regras; aqui está o porquê de cada uma, com
números medidos nesta máquina — primeiro no Ubuntu 24.04 (Plasma sobre X11, PipeWire
1.0.5) e, da seção *Wayland* em diante, no Ubuntu 26.04 (Plasma 6.6, Wayland puro).

## X11 e teclado

**Grab de uma tecla só, não do teclado inteiro.** `XGrabKey` entrega press *e* release
apenas da tecla escolhida — é o que separa um atalho de um keylogger. Se outro cliente já
tiver a tecla, o servidor responde `BadAccess`: capture com `error.CatchError(BadAccess)`
seguido de `sync()` e avise o usuário, senão o atalho falha em silêncio.

**Registre o grab para cada combinação de teclas travadas.** Num Lock, Caps Lock e Scroll
Lock entram no `state` do evento. Sem registrar as 16 combinações de
`Lock|Mod2|Mod3|Mod5`, o atalho simplesmente para de funcionar com o Num Lock ligado —
uma falha que só aparece na máquina de quem usa.

**O auto-repeat inventa releases que nunca aconteceram.** Segurando a tecla, o X11 emite
pares press/release na taxa de repetição (medido aqui: 25/s, ou seja 40 ms, após 600 ms de
espera). Para ditado *segure-e-fale* isso encerra a gravação no meio da frase. O caminho
canônico seria `XkbSetDetectableAutoRepeat`, mas o `python-xlib` 0.33 não expõe a extensão
xkb. A saída é desligar o repeat só daquela tecla e restaurar ao sair:

```python
dsp.change_keyboard_control(key=keycode, auto_repeat_mode=X.AutoRepeatModeOff)
```

Para conferir: `xset q` mostra o mapa "auto repeating keys" em 32 bytes; a tecla *n* é o
bit `n % 8` do byte `n // 8`. Pause é o keycode 127, então byte 15, bit 7 — passa de `ff`
para `7f` quando o repeat dela está desligado. Um debounce de 60 ms na soltura continua no
código como rede de segurança, caso a chamada falhe.

**O `select()` não enxerga todos os eventos que já chegaram.** Sintoma relatado: às vezes
a cápsula não aparecia ao apertar o atalho; ao soltar, o ditado saía "muito curto", e a
tentativa seguinte funcionava. Acontecia "quando aperto várias vezes seguidas" — a pista
que fechou o caso.

O laço do atalho dormia em `select` sobre o `fileno()` do Display e só drenava a fila
quando o socket acusava dados. Mas todo `sync()` lê o socket inteiro procurando sua
resposta e **guarda na fila interna do `python-xlib` os eventos que chegaram no meio** —
depois disso o socket está vazio e o `select` nunca mais acusa nada. O evento fica preso
até a próxima tecla chegar. Prova em duas linhas:

```python
envia_evento(); time.sleep(0.1)
select.select([d.fileno()], [], [], 0.3)   # True  -> o select vê
envia_evento(); time.sleep(0.1); d.sync()
select.select([d.fileno()], [], [], 0.3)   # False -> mas pending_events() == 1
```

E o `sync()` acontece exatamente onde o usuário reaperta: `grab_escape(False)` é enviado
ao terminar a gravação e o laço o executa até 80 ms depois (o timeout do `select`),
fazendo `ungrab_key` × 16 + `sync`. Varrendo o intervalo entre soltar e reapertar em
passos de 5 ms, o press se perdia **8/8 vezes em 80 ms** e passava ileso nos vizinhos —
uma janela estreita, mas mirada de cheio por quem aperta em sequência. O press preso só
era entregue junto com o release seguinte: gravação de 60 ms, cápsula aparecendo só no
fim e o veredito "muito curto".

Regra que fica: **com `python-xlib`, drene `pending_events()` a cada volta do laço**, não
só quando o `select` acusar. O `select` responde sobre o socket; a fila de eventos é outra
coisa.

**Duração mínima é sobre a tecla, não sobre o áudio.** Como o `pw-record` leva ~150 ms
até o primeiro byte, medir o mínimo pelo PCM capturado cobrava do usuário um atraso que
não era dele: segurar 0,55 s virava 0,4 s de áudio e o ditado era descartado. Agora quem
decide é o tempo de tecla segurada; áudio vazio com tecla segurada é falha da captura e
tem mensagem própria.

**XTEST serve para os dois lados.** Envia a colagem (`fake_input` com Ctrl+V) e também
dirige o próprio aplicativo nos testes, simulando a tecla física — foi assim que o ciclo
completo foi validado sem ninguém tocar no teclado.

**Descobrir a janela em foco** é `get_input_focus().focus` subindo pelos pais até achar um
`WM_CLASS`: a janela com foco costuma ser um filho sem classe própria. Com a classe em
mãos dá para escolher `Ctrl+Shift+V` em terminais, onde `Ctrl+V` não cola.

**A cápsula precisa ser override-redirect** (`X11BypassWindowManagerHint`). Uma janela
comum roubaria o foco da janela do usuário, e a colagem iria para o lugar errado.

## Wayland: o compositor é o dono do teclado

O Ubuntu 26.04 não empacota mais sessão X11: `/usr/share/xsessions/` não existe e só há
`kwin-wayland`. Não existe "voltar para o X11" — ou o aplicativo aprende Wayland, ou
para de funcionar. As duas funções que ele tira do sistema (capturar uma tecla global e
sintetizar a colagem) são exatamente as duas que o Wayland reserva ao compositor.

**O `XGrabKey` continua funcionando e continua surdo.** A conexão X abre normal pelo
XWayland, o grab é aceito, nenhum evento chega. Clientes X só recebem teclas quando a
janela em foco também é X — e hoje quase nenhuma é (`xlsclients` vem vazio numa sessão
inteira). O Plasma tem uma chave para afrouxar isso (`kcm_kwinxwayland`, que escreve
`[Xwayland] XwaylandEavesdrops` no `kwinrc`), mas ela entrega as teclas escolhidas a
*todos* os aplicativos X11 de uma vez. Para um programa cuja regra é não virar
keylogger, pagar esse preço para si mesmo e para o desktop inteiro não faz sentido.

**O XTEST é descartado sem erro nenhum.** O Xwayland do Plasma 6 roda com
`-enable-ei-portal` (confira com `pgrep -a Xwayland`): a injeção é desviada para o portal
RemoteDesktop, que, sem permissão registrada, simplesmente não faz nada. Não há exceção,
não há código de erro — a colagem só não acontece. O único vestígio está no journal:

```
xdp-kde-remotedesktop: MegaAuth: Failed to lookup permissions: "No entry for remote-desktop"
xdp-kde-remotedesktop: Only stream input
```

**`SetPresent` é o que instala o grab; sem ele o registro é decorativo.** O atalho global
passou a ser registrado no KGlobalAccel — o mesmo serviço que atende os atalhos do
Plasma, e que expõe `globalShortcutPressed` *e* `globalShortcutReleased`, que é o par que
o ditado "segure e fale" exige. O registro tem três passos e uma armadilha:

```python
kga.doRegister(action)                       # cria a ação
kga.setShortcut(action, [qt_key], 2 | 4)     # SetPresent | NoAutoloading
```

Com `flags=4` (só `NoAutoloading`) tudo *parece* dar certo: `setShortcut` devolve a tecla
pedida, o `kglobalshortcutsrc` ganha a linha, e `isGlobalShortcutAvailable` passa a
responder `False` — a tecla consta como ocupada. E nada dispara, porque o grab nunca foi
instalado no compositor. O oráculo que separa os dois casos é `isActive()` no objeto do
componente: `False` com `flags=4`, `True` com `flags=6`. Compare sempre com um componente
que funciona (`/component/plasmashell` responde `True`).

Duas outras recusas silenciosas na mesma família: `setForeignShortcut` escreve a
configuração mas não ativa nada, e uma ação nova não consegue tomar uma tecla que outra
ação *do mesmo componente* já segura — `setShortcut` devolve `[0]`, e limpar o arquivo no
disco não adianta, porque o daemon mantém o estado em memória e reescreve o arquivo.
Remova pelo D-Bus (`unRegister`), não pelo `kwriteconfig6`.

**Por que KGlobalAccel e não o portal.** O caminho multiplataforma seria
`org.freedesktop.portal.GlobalShortcuts`, que existe nesta máquina e declara `Activated`
e `Deactivated` — o app do Claude usa esse portal aqui. Ele foi descartado por um motivo
que depois deixou de valer: o `BindShortcuts` exige `a(sa{sv})`, que o QtDBus do PySide6
não monta, e naquele momento ainda não havia `jeepney` no projeto. Com o `jeepney` já
presente por causa da colagem, o obstáculo sumiu.

Ficou como está por decisão consciente, com dois pontos a favor: o KGlobalAccel deixa o
*app* escolher a tecla (no portal quem define o gatilho é o usuário, pela interface do
compositor — `preferred_trigger` é só sugestão), e a interface se manteve estável do
Plasma 5 para o 6. Os dois custos, para quem for reavaliar: é interface interna do KDE
(o caminho público é a biblioteca C++ `KGlobalAccel`, sem contrato de estabilidade para
quem chama o D-Bus direto), e prende o Sussurro ao Plasma — GNOME e Sway ficam de fora.
Se um dia o atalho parar de funcionar depois de uma atualização do Plasma, é aqui que se
começa a olhar, e o portal é o plano B pronto. Falta medir uma coisa antes de trocar: se
o `Deactivated` do portal do KDE chega de forma confiável ao soltar a tecla — sem isso o
"segure e fale" não funciona.

**O auto-repeat deixou de ser problema.** No X11 era preciso desligar o repeat da tecla
(veja acima) porque ele fabricava pares press/release. O KWin manda a repetição num sinal
à parte, `globalShortcutRepeated`, que o `hotkey.py` simplesmente não escuta: `pressed` e
`released` chegam uma vez cada. Medido aqui: 1,51 s de tecla segurada = 1 press, 24
repeats, 1 release.

**A cápsula precisa do XWayland.** No Wayland uma janela comum não pode escolher onde
fica na tela — o compositor decide —, e `X11BypassWindowManagerHint` é um conceito X11.
Sem `layer-shell` (que não tem binding para PySide6), a saída é rodar a interface inteira
sobre XWayland com `QT_QPA_PLATFORM=xcb`: a janela override-redirect volta a mandar na
própria posição e a cápsula aparece exatamente como antes. O atalho e a colagem não
dependem disso — falam D-Bus, não X.

**A detecção de terminal morreu.** `active_window_class()` usava o WM_CLASS da janela em
foco; no Wayland o compositor não conta a ninguém quem está em foco, e o XWayland só
enxerga clientes X. O KWin expõe `queryWindowInfo`, mas ela exige que o *usuário* clique
numa janela. O modo "automático" passa a cair no Ctrl+V, e quem dita em terminal precisa
escolher Ctrl+Shift+V nas configurações.

### QtDBus do PySide6: o que ele faz e o que não faz

Três descobertas que custaram a tarde inteira, na ordem em que aparecem:

**Conectar sinal exige o macro `SLOT()`.** `QDBusConnection.connect(...)` recusa qualquer
string de slot montada à mão — `b"onSig(...)"`, `b"1onSig(...)"`, todas levantam
`ValueError: called with wrong argument values`, que não diz nada sobre a causa. O que
funciona é `from PySide6.QtCore import SLOT` e passar `SLOT("onSig(QString,QString,qlonglong)")`,
com o slot declarado por `@Slot(...)` e com assinatura idêntica à do sinal D-Bus.
Alternativa mais limpa quando há introspecção: `iface.NomeDoSinal.connect(callable)`.

**Chame pelo metaobject, não por `.call()`.** `QDBusInterface.call("setShortcut", ...)`
manda a lista como `av` e a flag como `i`; o KGlobalAccel espera `ai` e `u` e recusa a
mensagem inteira por assinatura errada. `iface.setShortcut(...)` — a forma dinâmica —
converte pelos tipos que vieram da introspecção e acerta. Foi essa diferença que fez o
atalho registrar em teste e falhar dentro do aplicativo, com o mesmo código aparente.

**`uint` dentro de `a{sv}` é impossível.** Nenhum valor Python vira `u`: `int` vira `i`,
`bool` vira `b`, `float` vira `d`, inteiro grande vira `x`, `QDBusVariant` vira `v`
aninhado — o portal responde `Expected type 'u' for option 'types', got 'i'` para todos.
Também não adianta "lavar" o tipo pedindo um `u` ao próprio barramento e devolvendo o
`QDBusVariant` recebido: ele chega como `v`. Como o portal RemoteDesktop exige
`{"types": u, "persist_mode": u}`, e omitir `types` faz o KDE conceder `devices: 0`
(sessão sem teclado), o `portal.py` usa `jeepney` — D-Bus puro em Python, 520 KB, sem
código compilado — só para essa conversa. O resto do aplicativo segue no QtDBus.

## Áudio com PipeWire

`pw-record --rate 16000 --channels 1 --format s16 -` escreve **PCM cru no stdout**, sem
cabeçalho RIFF — perfeito para calcular o nível do sinal em tempo real enquanto grava. O
cabeçalho WAV é montado só na hora do upload, com o módulo `wave` em memória.

O primeiro byte demora **~140 ms** (média de três medições: 152, 138, 139 ms). É por isso
que a cápsula só troca para o estado "ao vivo" quando o áudio realmente começa a chegar:
mostrar "gravando" antes disso é mentira, e o usuário fala em cima do silêncio.

Nível em escala perceptual, não linear: `20·log10(rms)` normalizado com piso em −55 dB. Em
escala linear a barra quase não se mexe com voz normal.

`pw-dump` em JSON lista as fontes (`media.class == "Audio/Source"`), e `--target` aceita o
`node.name` — é o caminho para escolher microfone sem depender do PulseAudio.

## Qt 6 e PySide6

**`app.quit()` fecha todas as janelas antes de sair, e um `closeEvent` que ignora o evento
cancela o encerramento inteiro.** Sintoma: o aplicativo ignorava `SIGTERM` — só quando a
janela de configurações estava aberta. A animação de fade ao fechar era a culpada. Se você
intercepta o fechamento para animar, precise de um caminho de desligamento que não
intercepte (aqui, `prepare_shutdown()` + uma propriedade no QApplication).

**Sinais Unix só rodam entre bytecodes do Python.** Dentro de `app.exec()` o interpretador
fica parado em C++: sem um `QTimer` periódico executando qualquer código Python, o
handler de `SIGTERM` nunca é chamado. Um timer de 400 ms com um slot vazio resolve.

**Folhas de estilo do Qt não animam.** Não existe `transition`; `:hover` troca o valor de
um quadro para o outro. Para transição de verdade é preciso pintar o widget à mão e
animar uma `Property` própria com `QPropertyAnimation`. Foi o que motivou os botões e a
barra de rolagem customizados.

**Widget de texto longo estica a largura mínima da página inteira.** `QPushButton` não
encurta texto: com o histórico cheio, cada linha exigia 504 px, a página passou a exigir
586 px contra 552 disponíveis e — com a barra horizontal desligada — o excedente foi
cortado **sem aviso**, escondendo a borda direita dos cartões. Todo widget de texto
variável precisa de `QSizePolicy.Ignored` mais elisão manual no `resizeEvent`.

Lição geral: desligar `ScrollBarAlwaysOff` esconde o sintoma, não a causa. Quando algo
"some" na borda, meça `page.minimumSizeHint().width()` contra `viewport().width()`.

**`QComboBox` muda de valor com a roda do mouse** enquanto a página rola — o usuário
altera ajustes sem perceber. `wheelEvent` que chama `event.ignore()` devolve a rolagem
para a área e mantém o valor. E `minimumSizeHint` do combo é a largura do item mais
largo: sem `AdjustToMinimumContentsLengthWithIcon` ele também trava o encolhimento da
janela.

**`setDesktopSettingsAware(False)` quebra a detecção de tema:** `styleHints().colorScheme()`
passa a devolver `Unknown`. Como reserva, o portal responde por D-Bus
(`org.freedesktop.portal.Settings.Read org.freedesktop.appearance color-scheme`, com
`1 = escuro`, `2 = claro`).

**Detalhes menores que custaram tempo:** `animation.finished.disconnect()` sem conexão
emite `RuntimeWarning` (use uma flag de estado em vez de conectar e desconectar);
`QApplication.setApplicationDisplayName` faz o Qt anexar o nome ao título de cada janela
("Sussurro — Configurações — Sussurro"); `RESOURCE_NAME` no ambiente define o `WM_CLASS`,
que é o que o KDE usa para casar a janela com o atalho `.desktop`.

## API da Groq

Endpoint `POST /openai/v1/audio/transcriptions`, multipart. O modelo reamostra tudo para
16 kHz mono, então enviar já nesse formato é o mais rápido e o mais leve. Limite de 25 MB
na conta gratuita (100 MB na paga), mínimo cobrado de 10 s por requisição. `language` em
ISO-639-1 acelera e evita tradução acidental; `prompt` aceita até 224 tokens de
vocabulário. `response_format` aceita `json`, `verbose_json` e `text` — `json` basta
quando só se quer o texto.

Vale mapear os erros para frases que o usuário entenda: 401 é chave inválida, 413 é áudio
grande demais, 429 traz `retry-after` no cabeçalho, 5xx merece uma retentativa. O
`requests` respeita as variáveis de proxy do ambiente sozinho.

## Ambiente: terminal e sessão gráfica são mundos diferentes

O aplicativo funcionava perfeitamente quando eu o iniciava do terminal e falhou no
primeiro login depois de um reboot: `HTTPSConnectionPool(host='api.groq.com')`. Causa:
esta rede exige proxy, as variáveis `http_proxy` são exportadas no `.zshrc` — e o
autostart do KDE não roda `.zshrc`. Do terminal o processo herdava o proxy; da sessão,
não.

A correção não é pedir para o usuário exportar variáveis: é o aplicativo descobrir o
proxy sozinho, na ordem ajuste explícito → variáveis de ambiente → configuração do
sistema. No KDE isso está em `~/.config/kioslaverc`, com dois detalhes que só se descobre
lendo o arquivo: `ProxyType=1` significa manual (`4` é "usar o ambiente"), e o endereço é
gravado como `http://127.0.0.1 3128` — **espaço no lugar dos dois-pontos**. O
`NoProxyFor` da mesma seção precisa ser respeitado, senão tráfego interno vai parar no
proxy.

Vale ainda traduzir o erro: um `HTTPSConnectionPool(...)` cru não diz nada a quem usa.
"Sem acesso a api.groq.com. Se sua rede exige proxy, informe-o nas configurações" diz.

Regra que fica: **teste sempre no ambiente em que o programa realmente roda.** Iniciar
pelo terminal durante todo o desenvolvimento escondeu a falha até o primeiro reboot.
Reproduzir foi simples — `env -u http_proxy -u https_proxy … ./app` imita a sessão.

### `ProxyError` são dois erros com o mesmo nome

O ditado falhou com "O proxy recusou a conexão. Confira o endereço nas configurações" e
voltou sozinho minutos depois, sem ninguém tocar em nada. O endereço estava certo o tempo
todo: o squid local estava de pé havia 17 horas, sem reinício. A mensagem mandou depurar
justamente o que não tinha problema.

Causa: o `requests` usa `ProxyError` para dois casos opostos, e o texto de topo é
idêntico nos dois (`Unable to connect to proxy`). Só a causa aninhada os separa:

- `NewConnectionError: [Errno 111] Connection refused` — o proxy está inalcançável.
  Aqui, sim, conferir o endereço resolve.
- `OSError('Tunnel connection failed: 503 Service Unavailable')` — o proxy **respondeu** e
  recusou abrir o túnel `CONNECT` até o destino. O endereço está certo; quem falhou foi o
  proxy ao alcançar o upstream, normalmente por DNS ou instabilidade momentânea. Foi este
  o caso — daí o retorno espontâneo.

`_proxy_message()` separa os dois por regex sobre o `Tunnel connection failed: NNN`, e o
código devolvido vira parte da frase: 407 vira instrução de credenciais, 502/503/504
avisam que costuma ser passageiro, o resto mostra o código cru. Reproduzir não precisa de
proxy de verdade — um socket que aceita a conexão e responde a linha de status desejada
já produz a exceção exata.

Regra que fica: **quando uma exceção cobre duas causas com ações opostas, a mensagem
precisa olhar a causa aninhada.** Escolher uma das duas e escrever a frase como se fosse
sempre ela transforma o erro em pista falsa.

## Em aberto: o rodapé que perde os botões

Relatado e confirmado na instância real: depois de abrir e fechar a janela algumas vezes,
"Fechar" e "Salvar" somem do rodapé — e não voltam nem fechando e reabrindo (o objeto da
janela é reaproveitado; só reiniciar o aplicativo resolve).

O que a medição diz, comparando a captura defeituosa com uma sadia (`import -window`, e a
linha `border-top` do rodapé como régua): a linha fica em y=758 em vez de y=739, isto é, o
rodapé encolheu de **64 px para 44 px** — sobrou a altura das margens mais o rótulo de
status. Widget oculto não ocupa espaço em layout nenhum, então os dois botões saíram da
conta. Redimensionar a janela por fora força um relayout e **não** os traz de volta, o que
descarta geometria suja. Nada aparece no log (o stderr do app vai para o journal, e não há
uma linha sequer).

O que já foi descartado: esmagamento de layout (o rodapé aguenta 400 px de largura sem
espremer os botões), `sizeHint` falhando (o Qt cai no valor anterior e mantém 36 px de
altura), e coleta de lixo do PySide (com `gc.collect()` a cada rodada os botões
sobrevivem). Não reproduziu em ~200 ciclos de abrir/fechar/rolar/salvar/trocar tema,
dirigidos por XTEST e pelo socket de instância única.

Enquanto a causa não aparece, `settings_window.py` faz duas coisas: um `eventFilter` nos
dois botões desfaz o sumiço na hora (`HideToParent` com `isHidden()` só chega quando o
próprio widget é escondido — esconder a janela não dispara), e `_anotar()` grava em
`~/.local/share/sussurro/diagnostico.log` a pilha de quem escondeu. Na próxima vez que
acontecer, o culpado estará no arquivo.

## Processo: como verificar de verdade

**Meça pixels em vez de confiar no olho.** "Parece cortado" virou certeza ao comparar as
transições de cor de uma linha da captura: borda esquerda do cartão em x=51, direita
ausente — e, depois da correção, em x=561, simétrica.

**Dirija a interface, não só o código.** Tecla via XTEST, ponteiro via `warp_pointer`,
roda via `fake_input(ButtonPress, 5)`, captura via `import -window root -crop` usando a
geometria que `xwininfo -root -tree` informa. Foi assim que "a roda não muda mais o
combo" deixou de ser suposição: o valor ficou em 0 e a página andou 0 → 921.

**Um teste sintético pode mentir.** `app.sendEvent(combo, wheel)` não propaga o evento
ignorado para o pai como o Qt faz na entrega real — o teste "falhou" com o código certo.
Quando o resultado desafiar a lógica, suba um degrau na fidelidade do teste.

**Cuidado com o ambiente do próprio teste.** Duas armadilhas custaram caro aqui: o socket
de instância única é por UID, então uma instância de teste com `XDG_CONFIG_HOME` isolado
conversa com a instância real e sai sem subir; e uma janela remanescente por cima engole
os eventos do ponteiro, fazendo um teste correto falhar.

**Shell:** `pkill -f "padrão"` casa com a linha de comando do próprio shell que o executa
— e mata a sessão (aqui, saída 144, duas vezes). Filtre `$$` e `$PPID`. Some a isso que o
zsh **não** faz word-splitting de variáveis: `for p in $PIDS` itera uma vez com a string
inteira, enquanto `for p in $(pgrep …)` itera certo. E o padrão precisa casar a linha real:
um `-u` no meio do comando (`python -u -m sussurro`) já derruba o `pgrep`.

## Decisões de produto que se provaram certas

- **Segure para falar**, sem alternar: o gesto delimita a fala e evita gravação esquecida.
- **`Esc` cancela** durante a gravação — arrependimento é comum ao ditar.
- **Guardas de silêncio e de duração mínima** poupam requisições: toque acidental não vira
  chamada de API (e cada chamada custa 10 s no mínimo).
- **O que aparece ao terminar é escolha do usuário.** Mostrar o texto transcrito é útil no
  começo e vira ruído depois, já que o texto aparece colado no destino de qualquer forma.
- **Texto sempre na área de transferência**, mesmo quando a colagem falha: nunca se perde
  uma transcrição por causa de um aplicativo que ignora colagem sintética.
