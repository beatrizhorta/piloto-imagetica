# Pré-piloto: imagética de imagens e vídeos curtos

Site estático (jsPsych 7) para o pré-piloto comportamental remoto. Cada participante abre um link individual no próprio computador, faz até 100 trials (pode parar quando quiser) e, no final, o site baixa um `.zip` com todos os dados, que a pessoa envia por e-mail. Não há servidor de dados: tudo fica no navegador (IndexedDB) até o download.

```
index.html            página única
config.json           parâmetros e textos (edite aqui, sem mexer no código)
js/                   código do experimento
lib/                  jsPsych 7.3.4, plugins oficiais e JSZip (cópias locais, sem CDN)
stimuli/              estímulos + manifest.csv
make_placeholders.py  gera os estímulos placeholder
summarize.py          junta e resume os .zip recebidos
tests/                teste automatizado (Playwright) e verificação dos .zip
```

## Testar localmente

Na pasta do projeto:

```bash
python3 -m http.server 8000
```

Abra no Chrome: <http://localhost:8000/?pid=P01>. Abrir o `index.html` direto (file://) não funciona, porque o site precisa ler o `config.json` e os estímulos. O `localhost` permite gravar áudio, assim como o HTTPS do GitHub Pages.

Para recomeçar do zero no seu navegador, apague os dados do site (cadeado na barra de endereço → Configurações do site → Excluir dados) ou use outro `pid`.

## Publicar no GitHub Pages

Primeira vez (cria o repositório público e ativa o Pages na branch `main`):

```bash
gh repo create piloto-imagetica --public --source=. --push
```

```bash
gh api -X POST repos/{owner}/piloto-imagetica/pages -f "source[branch]=main" -f "source[path]=/"
```

O site fica em `https://<seu-usuario>.github.io/piloto-imagetica/` depois de um ou dois minutos.

Para atualizar (por exemplo, depois de mudar textos no `config.json` ou trocar estímulos):

```bash
git add -A && git commit -m "Atualiza textos" && git push
```

O GitHub Pages leva cerca de um minuto para publicar e pode manter o cache por até 10 minutos.

O repositório é público: qualquer pessoa com o link vê o código e os estímulos. Nenhum dado de participante passa pelo repositório.

## Link de cada participante

```
https://<seu-usuario>.github.io/piloto-imagetica/?pid=P01
```

- O `pid` precisa seguir o padrão de `pid_pattern` no `config.json` (padrão: `P` + 2 ou 3 dígitos, como `P01` ou `P105`). Sem `pid` válido, o site mostra um erro e não começa.
- Opcional, só para testes: `&seed=123` fixa a sequência de trials. Sem isso, cada sessão sorteia uma seed, que fica salva no `session.json`.
- Se a pessoa reabrir o link e houver uma sessão salva ainda não baixada, o site oferece "Baixar os dados da sessão anterior" antes de qualquer outra coisa. Com `&recuperar=1`, o site lista todas as sessões salvas daquele `pid` naquele navegador (inclusive as já baixadas), para baixar de novo.
- A recuperação só funciona **no mesmo computador e navegador**. Se a pessoa limpar os dados de navegação antes de baixar o .zip, os dados se perdem.

## Trocar os estímulos

1. Coloque os arquivos em `stimuli/`: imagens `.jpg` e vídeos `.mp4` (H.264, sem áudio), de preferência quadrados e leves, porque são baixados pela internet. Eles são exibidos em `stim_size_px` × `stim_size_px` (padrão 512).
2. Atualize `stimuli/manifest.csv`, uma linha por estímulo: `stim_id, file, type (image|video), item_id, notes`.
3. Os estímulos de prática são os listados em `practice_stim_ids` no `config.json` (hoje `PR01` e `PR02`); eles ficam fora da sequência principal.
4. Para 100 trials com 30% de repetição são necessários 70 estímulos novos, metade imagens e metade vídeos (35 + 35). Com menos, o gerador relaxa o balanceamento ou encurta a sessão e registra um aviso em `sequence_warnings` no `session.json`.

Para converter um vídeo para o formato certo (corta em 3 s, 512×512, sem áudio):

```bash
ffmpeg -i entrada.mov -t 3 -vf "scale=512:512:force_original_aspect_ratio=increase,crop=512:512" -an -c:v libx264 -crf 28 -pix_fmt yuv420p -movflags +faststart S036.mp4
```

Vídeos mais longos que `stim_duration_ms` são cortados na apresentação, e isso fica marcado em `video_truncated` no `trials.csv`.

Os placeholders atuais foram gerados com `python make_placeholders.py` (precisa de Pillow, numpy e ffmpeg ou `pip install imageio-ffmpeg`). O vídeo de prática `PR02` tem 4 s de propósito, para exercitar o corte.

## Configuração (`config.json`)

Durações (`fixation_ms`, `stim_duration_ms`, `mask_duration_ms`, `imagery_duration_ms`), `n_max_trials`, `block_size`, `checkpoint_every`, `repeat_proportion`, `min_lag`, `max_lag`, `stim_size_px`, `audio_bitrate_bps`, `audio_max_ms`, requisitos de janela, e-mail de envio e todos os textos (boas-vindas, instruções, comando de imaginação, definição de vividez, âncoras de vividez e dinamismo, perguntas extras do questionário). Os textos aceitam HTML.

As chaves listadas em `_textos_provisorios` ainda são rascunhos meus e precisam ser trocadas pelos textos finais.

Perguntas extras no questionário: preencha `extra_questions` com objetos `{"id": "q1", "text": "...", "type": "text" | "likert5" | "choice", "options": [...], "required": false}`.

## O que vem no .zip

```
P01_<session_id>/
  session.json         config usada, seed, sequência planejada, navegador, tela, microfone,
                       horários, motivo do término, duração, relatório de carregamento
  trials.csv           uma linha por trial (prática com is_practice=1)
  checkpoints.csv      cansaço e concentração a cada 10 trials
  events.csv           saídas de foco (troca de aba/janela) e da tela cheia
  final_feedback.json  questionário final
  audio/P01_t012_S034.webm   (prática: P01_pt02_PR02.webm)
```

- Todos os `*_ts` são milissegundos desde o início da sessão (clique em Começar na tela de boas-vindas). O instante absoluto do início está em `t0_epoch_ms` no `session.json`.
- `stim_duration_measured_ms`, `mask_duration_measured_ms` e `imagery_duration_measured_ms` são as diferenças entre os onsets medidos no navegador (primeiro frame de cada etapa).
- `status` do trial: `completed`, `aborted` (interrompido pelo botão de cansaço) ou `skipped_load_error` (estímulo não carregou, depois de 2 tentativas).
- `termination_reason`: `fatigue_button`, `checkpoint_stop`, `completed_max` ou `incomplete` (sessão interrompida e recuperada depois; o motivo anterior, se houver, fica em `termination_reason_before_interruption`).
- Áudio em Opus mono, ~32 kbps: uma sessão completa fica em torno de 5 MB (com descrições de 30 s) a 11 MB (60 s).

## Resumir os dados recebidos

Salve os .zip numa pasta e rode:

```bash
python3 summarize.py pasta_com_os_zips/
```

Gera `trials_all.csv` e `sessions_all.csv` na mesma pasta e imprime, por participante: trials completados, motivo do término, tempo médio por trial, curvas de cansaço e concentração, vividez e dinamismo médios por tipo de estímulo, acurácia de reconhecimento e saídas de foco. Só usa a biblioteca padrão do Python. Se a mesma sessão vier em dois .zip, fica a versão com mais trials.

## Teste automatizado

```bash
python3 -m venv .venv && .venv/bin/pip install playwright imageio-ffmpeg && .venv/bin/python -m playwright install chromium
```

Com o servidor local rodando na porta 8765 (`python3 -m http.server 8765`):

```bash
.venv/bin/python tests/e2e_test.py && .venv/bin/python tests/verify_zip.py tests/output
```

A sessão A faz prática, 12 trials, um checkpoint, uma troca de aba, uma saída da tela cheia, encerra pelo botão de cansaço e preenche o questionário. A sessão B fecha a janela no meio e confere a recuperação. No Chromium headless a troca de aba de verdade não muda a visibilidade da página, então o teste simula os eventos que o Chrome dispara (`visibilitychange` + `blur`).

## Requisitos e limitações

- Só Chrome ou Edge, em computador. Safari e Firefox são bloqueados na checagem inicial.
- O participante precisa permitir o microfone. Se negar, o site explica como liberar.
- Os dados ficam no navegador até o download; nada é apagado depois do download, a sessão só é marcada como baixada.
