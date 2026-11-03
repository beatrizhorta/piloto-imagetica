# Pré-piloto: imagética de imagens e vídeos curtos

Site estático (jsPsych 7) para o pré-piloto comportamental remoto. Cada participante abre um link individual no próprio computador, faz até 100 trials (pode parar quando quiser) e, no final, o site baixa um `.zip` com todos os dados, que a pessoa envia por e-mail. Não há servidor de dados: tudo fica no navegador (IndexedDB) até o download.

```
index.html             página única
config.json            parâmetros e textos (edite aqui, sem mexer no código)
js/                    código do experimento
lib/                   jsPsych 7.3.4, plugins oficiais e JSZip (cópias locais, sem CDN)
stimuli/               estímulos + manifest.csv + CREDITS.csv (atribuição das fotos e dos vídeos)
select_coco_images.py  sorteia as fotos do COCO usadas como imagens (S001-S035, PR01)
select_stock_videos.py sorteia os vídeos do Pixabay/Pexels usados como vídeos (S036-S070, PR02)
make_placeholders.py   só para testes: gera vídeos placeholder sintéticos (não sobrescreve arquivos)
summarize.py           junta e resume os .zip recebidos
tests/                 teste automatizado (Playwright) e verificação dos .zip
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

Os vídeos S036-S070 e PR02 vêm de `select_stock_videos.py` (veja abaixo). O `make_placeholders.py` serve só para regenerar os placeholders sintéticos de teste (formas geométricas em movimento) usados antes dos vídeos naturais; ele precisa de Pillow, numpy e ffmpeg ou `pip install imageio-ffmpeg`, só cria o que estiver faltando em `stimuli/` e só escreve o `manifest.csv` se ele não existir, então não sobrescreve as fotos nem os vídeos atuais. Para voltar a um placeholder, apague o arquivo antes.

### Imagens do COCO

As imagens S001-S035 e a imagem de prática PR01 são fotos naturais do [MS COCO](https://cocodataset.org) 2017, split val, sorteadas por `select_coco_images.py`:

- Elegíveis pelos metadados: lado menor ≥ 480 px, razão entre os lados (maior/menor) ≤ 1,5 e licença CC BY, CC BY-SA ou "No known copyright restrictions". Ficam de fora as licenças NonCommercial e NoDerivs: o site é público e o recorte conta como modificação. No val sobram 516 elegíveis.
- Seed 2026: permutação dos `image_id` elegíveis, percorrida em ordem. Imagens que não abrem ou em tons de cinza (R, G e B idênticos em todos os pixels) são rejeitadas. As 35 primeiras válidas viram S001-S035, na ordem do sorteio, e a 36ª vira PR01.
- Processamento: recorte central quadrado, 512×512 (LANCZOS), RGB, JPEG qualidade 85, sem EXIF.
- No `manifest.csv`, essas linhas mantêm `stim_id`, `type` e `item_id`; `notes` traz `coco2017val:<image_id>`.
- Os originais, sem alteração, ficam **fora do repositório**, em `../coco_originais/coco_<image_id com 12 dígitos>.jpg`, junto com `coco_metadata.csv` (licença, URLs do Flickr e do COCO, categorias, número de instâncias e legendas de cada imagem) e `contact_sheet.jpg` (as 35 imagens com o `stim_id`, para revisar o conjunto).

Para refazer (a mesma seed gera as mesmas imagens; precisa de Pillow e numpy):

```bash
.venv/bin/python select_coco_images.py
```

O script baixa as anotações oficiais (`annotations_trainval2017.zip`, ~250 MB) num diretório temporário, lê `instances_val2017.json` e `captions_val2017.json` e apaga tudo no fim. Se já tiver esses dois arquivos extraídos, use `--annotations-dir PASTA`. Cada imagem é baixada pelo `coco_url` (até 3 tentativas); originais já presentes em `../coco_originais/` são reaproveitados, e os que não fazem mais parte do conjunto são apagados. `--no-practice` mantém o PR01 atual.

**Licenças:** cada imagem mantém a licença original do Flickr com que foi publicada no COCO (hoje 29 CC BY 2.0 e 7 CC BY-SA 2.0). A atribuição exigida pelas licenças está em `stimuli/CREDITS.csv`, publicada junto com o site: fonte, licença, `flickr_url`, `coco_url` e a modificação feita (recorte e redimensionamento). As versões recortadas das imagens BY-SA ficam sob a mesma licença BY-SA. O COCO não informa o nome dos autores; o crédito é feito pelo link do Flickr. Os mesmos dados, mais categorias e legendas, estão em `../coco_originais/coco_metadata.csv`.

### Vídeos do Pixabay (ou Pexels)

Os vídeos S036-S070 e o vídeo de prática PR02 são trechos de vídeos naturais do [Pixabay](https://pixabay.com/videos/), sorteados por `select_stock_videos.py` pelas APIs oficiais (Pixabay por padrão; o Pexels também é suportado com `--source pexels` ou `both`).

**Chave da API.** Crie uma conta no Pixabay e copie a chave em <https://pixabay.com/api/docs/> (Pexels: <https://www.pexels.com/api/>, mas a emissão de chaves novas está suspensa). Guarde a chave **só** em `.env`, na pasta do projeto, que está no `.gitignore`:

```
PIXABAY_API_KEY=sua_chave
```

Ou defina a variável de ambiente `PIXABAY_API_KEY` (`PEXELS_API_KEY`). O repositório é público: a chave nunca vai para o código, os logs, o cache nem os metadados.

**Seleção** (a mesma seed, os mesmos parâmetros e o mesmo cache geram o mesmo conjunto):

- 7 categorias × 5 vídeos, com 5 termos de busca fixos cada: pessoas em ações cotidianas, esportes, animais, veículos e transporte, água e natureza em movimento, mãos manipulando objetos e cenas urbanas (lista em `CATEGORIES`, no início do script). Cada busca pede 50 resultados (`video_type=film`, `safesearch=true`). A resposta bruta vai para `../stock_originais/search_cache.json`, sem a chave, e é reaproveitada por 24 h, como o Pixabay pede; `--use-cache` reaproveita sempre (dispensa a chave).
- Elegíveis pelos metadados: duração entre 5 e 60 s, lado menor ≥ 720 px, razão entre os lados ≤ 1,8, filmagem (não animação nem gerado por IA) e sem tags de time-lapse, câmera lenta, texto/logo ou conteúdo sensível ou médico (`TAG_BLACKLIST`). No máximo 2 vídeos do mesmo autor; duplicatas entre buscas ficam só na primeira.
- Seed 2026: permutação dos candidatos ordenados por (fonte, id), percorrida em ordem até completar cada categoria. Cada vídeo é baixado (o menor arquivo com lado menor ≥ 720 px) e o trecho usado passa por filtros automáticos; se reprovar, o sorteio segue:
  - corte de cena: maior `lavfi.scd.score` do filtro `scdet` do ffmpeg > 10 (o limiar padrão do `scdet`);
  - quase estático: diferença absoluta média entre frames consecutivos < 2 (escala 0-255, frames em cinza 128×128 a 15 fps, no recorte central). O participante avalia o dinamismo, então o movimento precisa ser visível;
  - fade: desvio-padrão do primeiro ou do último frame < 0,5 × a mediana do trecho.
- Os 35 primeiros válidos viram S036-S070 (S036-S040 a 1ª categoria, S041-S045 a 2ª, e assim por diante), e o próximo válido de qualquer categoria vira PR02.
- Processamento: trecho de 3 s a partir de 1 s (`--start`), 4 s no PR02 (de propósito, para exercitar o corte), recorte central quadrado, 512×512, no máximo 30 fps, H.264 `yuv420p`, sem áudio nem metadados, `+faststart`. O CRF começa em 23 e sobe até o arquivo ficar abaixo de ~1 MB.
- No `manifest.csv`, essas linhas mantêm `stim_id`, `type` e `item_id`; `notes` traz `pixabay:<id>; <categoria>`.
- Fora do repositório, em `../stock_originais/`: os originais sem alteração (`pixabay_<id>.mp4`), `stock_metadata.csv` (fonte, id, categoria, termo de busca, autor, URLs, duração e resolução originais, trecho usado, CRF e as métricas dos filtros), `filter_cache.json` (métricas já calculadas, para não baixar de novo os rejeitados), `search_cache.json` e `contact_sheet.jpg`.

```bash
.venv/bin/python select_stock_videos.py
```

Precisa de numpy, Pillow, `ffmpeg` e `ffprobe` no PATH (macOS: `brew install ffmpeg`) e, no Python do python.org, de `pip install certifi` para os certificados HTTPS.

**Revisão visual.** Abra `../stock_originais/contact_sheet.jpg`: uma linha por vídeo, com o `stim_id`, o ID da fonte, a categoria, o termo de busca, o autor, as métricas de movimento e de corte de cena e 3 frames do trecho final (início, meio e fim). Os filtros automáticos não pegam tudo (texto, marca d'água, rosto em close, câmera lenta, conteúdo inadequado), então olhe linha por linha. Para trocar um vídeo, anote o ID num arquivo, um por linha, e rode de novo com a mesma seed:

```
# excluir.txt
pixabay:123456   # marca d'água no canto
pixabay:654321
```

```bash
.venv/bin/python select_stock_videos.py --use-cache --exclude excluir.txt
```

Os excluídos ocupam a vaga no sorteio como se fossem válidos, para que todo o resto continue igual, e depois cada vaga é preenchida pelo próximo vídeo válido da mesma categoria. Os aprovados ficam no mesmo `stim_id`; vale reabrir o contact sheet para conferir os substitutos. Mantenha o `excluir.txt` (acrescentando IDs) entre as rodadas.

**Modo manual, sem API.** Baixe os vídeos à mão pelo site do Pixabay ou do Pexels, coloque-os numa pasta e descreva-os em `PASTA/selecao.csv` (modelo vazio em `../stock_originais/selecao_modelo.csv`), com as colunas `arquivo, fonte, id, categoria, autor, page_url`. A ordem do CSV define os `stim_id` (1ª linha S036, ..., 35ª S070, 36ª PR02). Não há busca nem sorteio, e os filtros automáticos só geram aviso. O resto (processamento, originais, metadados, contact sheet e créditos) funciona igual:

```bash
.venv/bin/python select_stock_videos.py --from-folder PASTA
```

`--no-practice` mantém o PR02 atual.

**Licenças.** Os vídeos do Pixabay estão sob a [Pixabay Content License](https://pixabay.com/service/terms/) (os do Pexels, sob a [Pexels License](https://www.pexels.com/license/)). Nenhuma das duas exige atribuição, mas o registro completo está em `stimuli/CREDITS.csv`: fonte e ID, licença, autor, página do vídeo e a modificação feita. As duas licenças proíbem vender ou redistribuir o conteúdo "standalone", isto é, praticamente sem alteração; aqui os vídeos são usados como estímulos dentro do experimento. O Pixabay também proíbe download sistemático em massa e hotlinking: o script faz uma busca por termo, baixa só os vídeos que examina e os arquivos são servidos pelo próprio site.

`CREDITS.csv` é compartilhado pelos dois scripts: `select_coco_images.py` reescreve só as linhas das imagens e `select_stock_videos.py` só as dos vídeos. As colunas `flickr_url` e `coco_url` são das fotos; `author` e `page_url`, dos vídeos (vazias nas fotos, porque o COCO não informa o autor).

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
