"""Sorteia vídeos naturais de banco gratuito (Pixabay e/ou Pexels) para os estímulos de vídeo do pré-piloto.

Substitui os vídeos S036-S070 em stimuli/ (e, com --practice, também PR02) por trechos de
vídeos naturais, sem mudar stim_id, item_id nem o tipo no manifest. As imagens ficam como
estão.

Busca (APIs oficiais, sem scraping):
  - Pixabay Videos API (padrão). Chave em PIXABAY_API_KEY, no ambiente ou em .env.
  - Pexels Videos API (--source pexels|both). Chave em PEXELS_API_KEY.
  Uma lista fixa de termos (CATEGORIES), 7 categorias x 5 vídeos. A resposta bruta de cada
  busca vai para ../stock_originais/search_cache.json (sem a chave) e é reaproveitada por
  24 h, como a documentação do Pixabay exige; --use-cache reaproveita sempre, sem chave.

Seleção (reprodutível: a mesma seed, os mesmos parâmetros e o mesmo cache geram o mesmo conjunto):
  1. Junta os resultados, tira duplicatas (o mesmo vídeo em dois termos fica no primeiro
     termo da lista) e ordena por (fonte, id).
  2. Elegíveis pelos metadados: duração entre MIN_DURATION e MAX_DURATION, lado menor >= 720 px,
     lado maior / lado menor <= MAX_RATIO, filmado (não animação nem gerado por IA), sem
     tags de time-lapse, câmera lenta, texto ou conteúdo sensível (TAG_BLACKLIST).
  3. Permutação com numpy.random.default_rng(seed), percorrida em ordem. Cada vídeo
     elegível entra na sua categoria se ela ainda tiver vaga e o autor tiver menos de
     MAX_PER_AUTHOR vídeos no conjunto. O vídeo é baixado e o trecho usado passa pelos
     filtros automáticos (abaixo); se reprovar, é rejeitado e o sorteio segue.
  4. Com as 35 vagas preenchidas, o próximo vídeo válido (de qualquer categoria) vira PR02.
  5. --exclude: os IDs rejeitados na revisão visual ocupam a vaga no sorteio como se
     fossem válidos (sem download), para que todo o resto continue igual, e depois a vaga
     é preenchida pelo próximo vídeo válido da mesma categoria, seguindo a permutação.
     Assim, rodar de novo com a mesma seed mantém os aprovados e só troca os excluídos.

Filtros automáticos no trecho usado (frames em cinza, recorte central, 128x128, 15 fps):
  - corte de cena: maior lavfi.scd.score do filtro scdet do ffmpeg > SCENE_THRESHOLD
    (10, o limiar padrão do scdet; o score é a diferença absoluta média entre frames
    consecutivos, em % da escala, e cortes de verdade ficam bem acima disso);
  - quase estático: diferença absoluta média entre frames consecutivos (0-255, a 15 fps)
    < MOTION_MIN;
  - fade: desvio-padrão do primeiro ou do último frame < FADE_STD_RATIO x a mediana do
    desvio-padrão dos frames do trecho (fade para preto, branco ou cor lisa).
  As métricas ficam em ../stock_originais/filter_cache.json, para não baixar de novo os
  vídeos rejeitados quando o script roda outra vez.

Processamento: trecho de 3 s (4 s no PR02) começando em --start (padrão 1 s), recorte central
quadrado, 512x512 (lanczos), no máximo 30 fps, H.264 yuv420p, sem áudio nem metadados,
-movflags +faststart. O CRF começa em CRF_START e sobe até cada arquivo ficar abaixo de
MAX_BYTES. Os originais vão sem alteração para ../stock_originais/<fonte>_<id>.mp4 (fora do
repositório), junto com stock_metadata.csv e contact_sheet.jpg (3 frames de cada estímulo).
Originais de execuções anteriores que não fazem mais parte do conjunto são apagados. A
atribuição vai para stimuli/CREDITS.csv, dentro do repositório.

Modo manual (--from-folder PASTA): processa vídeos baixados à mão, descritos em
PASTA/selecao.csv (colunas: arquivo, fonte, id, categoria, autor, page_url). A ordem do CSV
define os stim_ids (S036, S037, ..., e a 36ª linha vira PR02). Não há busca nem sorteio, e os
filtros automáticos só geram aviso. Um modelo vazio fica em ../stock_originais/selecao_modelo.csv.

Uso:
    python select_stock_videos.py                      # Pixabay, seed 2026
    python select_stock_videos.py --use-cache          # reusa as buscas salvas (sem chave)
    python select_stock_videos.py --exclude excluir.txt  # um ID por linha: pixabay:123, pexels:456
    python select_stock_videos.py --from-folder PASTA  # vídeos baixados à mão
    python select_stock_videos.py --no-practice        # não troca PR02

Dependências: numpy, Pillow, ffmpeg e ffprobe no PATH; certifi é usado se estiver instalado.
"""

import argparse
import csv
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
STIM_DIR = HERE / "stimuli"
ORIG_DIR = HERE.parent / "stock_originais"
CACHE_FILE = ORIG_DIR / "search_cache.json"
FILTER_CACHE_FILE = ORIG_DIR / "filter_cache.json"
ENV_FILE = HERE / ".env"

SEED = 2026
FIRST_STIM, N_VIDEOS = 36, 35  # S036-S070
# 7 categorias x 5 vídeos; cada termo é uma busca. A ordem dos termos decide em que
# categoria fica um vídeo que aparece em mais de uma busca.
CATEGORIES = {
    "pessoas_cotidiano": ["person cooking", "people walking", "woman gardening", "man sweeping", "people dancing"],
    "esportes": ["soccer", "skateboarding", "basketball", "surfing", "tennis"],
    "animais": ["dog running", "cat playing", "birds flying", "horse running", "ducks swimming"],
    "veiculos_transporte": ["train", "car traffic", "boat sailing", "bicycle riding", "airplane"],
    "agua_natureza": ["waterfall", "ocean waves", "river flowing", "trees wind", "rain"],
    "maos_objetos": ["hands kneading dough", "pouring coffee", "chopping vegetables", "hands painting", "hands knitting"],
    "cenas_urbanas": ["city street", "crowd walking", "market", "escalator", "construction site"],
}
PER_CATEGORY = N_VIDEOS // len(CATEGORIES)
assert PER_CATEGORY * len(CATEGORIES) == N_VIDEOS

MIN_DURATION = 5  # s, para pular o início
MAX_DURATION = 60  # s, para não baixar arquivos enormes por causa de 3 s
MIN_SIDE = 720
MAX_RATIO = 1.8  # 16:9 = 1,78; corta formatos panorâmicos (2,39:1 etc.)
MAX_PER_AUTHOR = 2
# Pixabay: tags que indicam o que queremos evitar (comparação por palavra inteira, minúsculas)
TAG_BLACKLIST = (
    "timelapse", "time lapse", "time-lapse", "hyperlapse", "slow motion", "slowmotion", "slow-motion", "slomo",
    "text", "logo", "title", "intro", "animation", "3d", "render", "cgi", "green screen", "chroma key",
    "blood", "injury", "surgery", "hospital", "medical", "injection", "syringe", "weapon", "gun", "war",
    "accident", "fight", "dead", "death", "selfie", "portrait",
)

PER_PAGE = {"pixabay": 50, "pexels": 50}
CACHE_MAX_AGE_S = 24 * 3600  # a documentação do Pixabay exige cache de 24 h
REQUEST_INTERVAL_S = {"pixabay": 0.7, "pexels": 18.5}  # Pixabay: 100 req/min; Pexels: 200 req/h

START_S = 1.0
CLIP_S = 3.0
PRACTICE_S = 4.0  # PR02 tem 4 s de propósito, para exercitar o corte em 3 s
SIZE = 512  # stim_size_px do config.json
MAX_FPS = 30
CRF_START, CRF_STEP, CRF_MAX = 23, 2, 37
MAX_BYTES = 1_000_000

ANALYSIS_SIZE, ANALYSIS_FPS = 128, 15
SCENE_THRESHOLD = 10.0  # limiar padrão do scdet
MOTION_MIN = 2.0  # diferença absoluta média entre frames (0-255, a 15 fps)
FADE_STD_RATIO = 0.5

LICENSES = {
    "pixabay": ("Pixabay Content License", "https://pixabay.com/service/terms/"),
    "pexels": ("Pexels License", "https://www.pexels.com/license/"),
}
SOURCE_NAMES = {"pixabay": "Pixabay", "pexels": "Pexels"}
MODIFICATION = "trecho de {dur:g} s a partir de {start:g} s, recorte central quadrado, 512x512, sem áudio"
SELECTION_COLUMNS = ["arquivo", "fonte", "id", "categoria", "autor", "page_url"]
CREDITS_HEADER = ["stim_id", "file", "source", "license_name", "license_url", "flickr_url", "coco_url",
                  "modification", "author", "page_url"]
USER_AGENT = "piloto-imagetica/1.0 (selecao de estimulos de pesquisa)"
RETRIES = 3

log = logging.getLogger("select_stock")
SECRETS = []  # chaves carregadas; redact() as tira de qualquer texto que vá para log, erro ou arquivo


def redact(text):
    text = str(text)
    for s in SECRETS:
        text = text.replace(s, "***")
    return text


class PermanentDownloadError(Exception):
    """Erro que não muda tentando de novo (ex.: HTTP 404): o vídeo é rejeitado."""


def ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = ssl_context()


def fetch(url, headers=None, retries=RETRIES, label=None):
    """Baixa `url` com até `retries` tentativas. Erros 4xx (menos 429) são definitivos.

    `label` é o que aparece no log no lugar da URL (as URLs das buscas levam a chave)."""
    label = redact(label or url)
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=120, context=SSL_CTX) as res:
                return res.read(), res.headers
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("X-RateLimit-Reset") or 60)
                log.warning("limite de requisições atingido em %s; esperando %d s", label, wait)
                time.sleep(wait)
                err = e
            elif 400 <= e.code < 500:
                raise PermanentDownloadError(f"HTTP {e.code}") from None
            else:
                err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
        log.warning("falha ao baixar %s (tentativa %d/%d): %s", label, attempt, retries, redact(err))
        if attempt < retries:
            time.sleep(2 * attempt)
    # Falha de rede não é propriedade do vídeo: pular o vídeo mudaria o sorteio entre
    # execuções, então é melhor parar e rodar de novo.
    raise SystemExit(f"não foi possível baixar {label} depois de {retries} tentativas")


# ---------------------------------------------------------------- chaves e buscas

def load_key(source):
    var = f"{source.upper()}_API_KEY"
    key = os.environ.get(var, "").strip()
    if not key and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == var:
                key = value.strip().strip("'\"")
    if not key:
        where = {"pixabay": "crie uma conta em https://pixabay.com e copie a chave em https://pixabay.com/api/docs/",
                 "pexels": "peça uma chave em https://www.pexels.com/api/ (a emissão de chaves novas pode estar suspensa)"}
        raise SystemExit(f"chave {var} não encontrada. Para obter: {where[source]}. Depois defina a variável de "
                         f"ambiente {var} ou acrescente a linha {var}=... em {ENV_FILE} (que está no .gitignore). "
                         "Sem chave, dá para usar --use-cache (se houver buscas salvas) ou --from-folder.")
    SECRETS.append(key)
    return key


def search_params(source, term):
    if source == "pixabay":
        return {"q": term, "lang": "en", "video_type": "film", "safesearch": "true", "order": "popular",
                "per_page": PER_PAGE[source], "page": 1}
    return {"query": term, "locale": "en-US", "size": "medium", "per_page": PER_PAGE[source], "page": 1}


def api_search(source, key, params):
    if source == "pixabay":
        url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode({"key": key, **params})
        data, headers = fetch(url, label=f"pixabay busca '{params['q']}'")
    else:
        url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(params)
        data, headers = fetch(url, headers={"Authorization": key}, label=f"pexels busca '{params['query']}'")
    remaining = headers.get("X-RateLimit-Remaining") or headers.get("X-Ratelimit-Remaining")
    return json.loads(data), remaining


def run_searches(sources, use_cache):
    """Devolve {(fonte, termo): resposta bruta}, buscando só o que não estiver no cache válido."""
    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {"entries": {}}
    now = time.time()
    keys, out, n_api = {}, {}, 0
    for source in sources:
        for terms in CATEGORIES.values():
            for term in terms:
                ck = f"{source}|{term}"
                params = search_params(source, term)
                entry = cache["entries"].get(ck)
                fresh = entry and entry["params"] == params and (use_cache or now - entry["fetched_unix"] < CACHE_MAX_AGE_S)
                if not fresh:
                    if use_cache:
                        raise SystemExit(f"--use-cache, mas a busca {ck} não está em {CACHE_FILE}")
                    if source not in keys:
                        keys[source] = load_key(source)
                    if n_api:
                        time.sleep(REQUEST_INTERVAL_S[source])
                    resp, remaining = api_search(source, keys[source], params)
                    n_api += 1
                    entry = {"params": params, "fetched_unix": now,
                             "fetched_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
                             "response": resp}
                    cache["entries"][ck] = entry
                    log.info("busca %s '%s': %d resultados (restam %s requisições na janela)",
                             source, term, len(resp.get("hits", resp.get("videos", []))), remaining)
                out[(source, term)] = entry["response"]
    if n_api:
        text = json.dumps(cache, ensure_ascii=False, indent=1)
        assert all(s not in text for s in SECRETS), "chave apareceu no cache"
        CACHE_FILE.write_text(text)
        log.info("%d buscas feitas na API; cache salvo em %s", n_api, CACHE_FILE)
    else:
        log.info("todas as buscas vieram do cache (%s)", CACHE_FILE)
    return out


def normalize(source, hit, category, term):
    """Converte um resultado da API num dicionário comum às duas fontes."""
    if source == "pixabay":
        rends = [dict(v, name=k) for k, v in hit["videos"].items() if v.get("url")]
        files = [{"url": r["url"], "width": r["width"], "height": r["height"], "quality": r["name"]} for r in rends]
        author, author_url, page = hit["user"], hit.get("userURL", ""), hit["pageURL"]
        tags = [t.strip().lower() for t in hit.get("tags", "").split(",") if t.strip()]
        flags = {"isAiGenerated": hit.get("isAiGenerated", False), "isLowQuality": hit.get("isLowQuality", False),
                 "isGRated": hit.get("isGRated", True), "type": hit.get("type", "film")}
    else:
        files = [{"url": f["link"], "width": f["width"] or 0, "height": f["height"] or 0, "quality": f.get("quality") or ""}
                 for f in hit["video_files"] if f.get("file_type") == "video/mp4" and f.get("link")]
        author, author_url, page = hit["user"]["name"], hit["user"].get("url", ""), hit["url"]
        tags, flags = [], {}
    return {"source": source, "id": int(hit["id"]), "category": category, "term": term, "duration": hit["duration"],
            "author": author, "author_url": author_url, "page_url": page, "tags": tags, "flags": flags, "files": files}


def pick_file(cand):
    """Menor arquivo com lado menor >= MIN_SIDE (menos banda); None se nenhum serve."""
    ok = [f for f in cand["files"] if min(f["width"], f["height"]) >= MIN_SIDE]
    return min(ok, key=lambda f: f["width"] * f["height"]) if ok else None


def metadata_reason(cand):
    """Motivo da inelegibilidade pelos metadados, ou None."""
    if not MIN_DURATION <= cand["duration"] <= MAX_DURATION:
        return "duração"
    f = pick_file(cand)
    if f is None:
        return "resolução"
    if max(f["width"], f["height"]) / min(f["width"], f["height"]) > MAX_RATIO:
        return "proporção"
    fl = cand["flags"]
    if fl.get("isAiGenerated") or fl.get("type", "film") != "film":
        return "gerado por IA ou animação"
    if fl.get("isLowQuality") or not fl.get("isGRated", True):
        return "baixa qualidade ou não livre"
    text = " " + " ".join(cand["tags"]) + " "
    hit = next((t for t in TAG_BLACKLIST if re.search(rf"\b{re.escape(t)}\b", text)), None)
    if hit:
        return f"tag '{hit}'"
    return None


def candidates_from(responses, sources):
    seen, cross, out, dups = set(), {}, [], 0
    for source in sources:
        for category, terms in CATEGORIES.items():
            for term in terms:
                resp = responses[(source, term)]
                for hit in resp.get("hits", resp.get("videos", [])):
                    c = normalize(source, hit, category, term)
                    key = (source, c["id"])
                    # entre fontes não há ID comum: autor + duração é a melhor pista de duplicata
                    xkey = (c["author"].strip().lower(), c["duration"])
                    if key in seen or (xkey in cross and cross[xkey] != source):
                        dups += 1
                        continue
                    seen.add(key)
                    cross.setdefault(xkey, source)
                    out.append(c)
    out.sort(key=lambda c: (c["source"], c["id"]))
    return out, dups


# ---------------------------------------------------------------- vídeo

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], text=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in v.get("avg_frame_rate", "0/1").split("/"))
    rotation = 0
    for sd in v.get("side_data_list", []):
        rotation = int(sd.get("rotation", rotation))
    w, h = int(v["width"]), int(v["height"])
    if rotation % 180:
        w, h = h, w
    return {"width": w, "height": h, "fps": num / den if den else 0.0, "duration": float(info["format"]["duration"]),
            "codec": v["codec_name"], "pix_fmt": v.get("pix_fmt"), "has_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
            "bytes": int(info["format"]["size"])}


CROP = "crop='min(iw,ih)':'min(iw,ih)'"


def analyse(path, start, dur):
    """Métricas de corte de cena, movimento e fade no trecho [start, start+dur)."""
    scd = run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start}", "-t", f"{dur}", "-i", str(path), "-an",
               "-vf", f"{CROP},scdet=threshold={SCENE_THRESHOLD},metadata=mode=print:key=lavfi.scd.score:file=-",
               "-f", "null", "-"], text=True).stdout
    scores = [float(x) for x in re.findall(r"lavfi\.scd\.score=([\d.]+)", scd)]
    raw = run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start}", "-t", f"{dur}", "-i", str(path), "-an",
               "-vf", f"{CROP},fps={ANALYSIS_FPS},scale={ANALYSIS_SIZE}:{ANALYSIS_SIZE}:flags=area,format=gray",
               "-f", "rawvideo", "-"]).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, ANALYSIS_SIZE, ANALYSIS_SIZE).astype(np.float32)
    if len(frames) < 2:
        return {"n_frames": len(frames), "scene_max": 0.0, "motion": 0.0, "fade_std_ratio": 0.0}
    motion = float(np.abs(np.diff(frames, axis=0)).mean())
    stds = frames.reshape(len(frames), -1).std(axis=1)
    med = float(np.median(stds)) or 1.0
    return {"n_frames": int(len(frames)), "scene_max": round(max(scores[1:], default=0.0), 3),
            "motion": round(motion, 3), "fade_std_ratio": round(float(min(stds[0], stds[-1]) / med), 3)}


def filter_reason(m):
    if m["n_frames"] < 2:
        return "trecho vazio"
    if m["scene_max"] > SCENE_THRESHOLD:
        return "corte de cena"
    if m["motion"] < MOTION_MIN:
        return "quase estático"
    if m["fade_std_ratio"] < FADE_STD_RATIO:
        return "fade"
    return None


def encode(src, dst, start, dur, fps_in):
    fps = f",fps={MAX_FPS}" if fps_in > MAX_FPS + 0.5 else ""
    vf = f"{CROP},scale={SIZE}:{SIZE}:flags=lanczos{fps},setsar=1,format=yuv420p"
    for crf in range(CRF_START, CRF_MAX + 1, CRF_STEP):
        run(["ffmpeg", "-hide_banner", "-y", "-loglevel", "error", "-ss", f"{start}", "-i", str(src), "-t", f"{dur}",
             "-vf", vf, "-an", "-sn", "-dn", "-map_metadata", "-1", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)])
        if dst.stat().st_size <= MAX_BYTES:
            return crf
    log.warning("%s ficou com %d bytes mesmo com CRF %d", dst.name, dst.stat().st_size, CRF_MAX)
    return CRF_MAX


# ---------------------------------------------------------------- seleção

def orig_name(source, vid):
    return f"{source}_{vid}.mp4"


def get_original(cand):
    path = ORIG_DIR / orig_name(cand["source"], cand["id"])
    if not path.exists():
        f = pick_file(cand)
        data, _ = fetch(f["url"], label=f"{cand['source']}:{cand['id']} ({f['width']}x{f['height']})")
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.rename(path)
    return path


def select(cands, args, excluded, filter_cache):
    """Percorre a permutação e devolve ({stim_id: candidato}, [(id, categoria, motivo da rejeição)])."""
    order = np.random.default_rng(args.seed).permutation(len(cands))
    cat_names = list(CATEGORIES)
    slots = {cat: [f"S{FIRST_STIM + i * PER_CATEGORY + k:03d}" for k in range(PER_CATEGORY)] for i, cat in enumerate(cat_names)}
    filled = {cat: [] for cat in cat_names}
    authors, rejected = Counter(), []
    chosen, vacancies = {}, []  # vacancies: (stim_id, categoria ou None) dos excluídos, na ordem do sorteio

    def check(c, dur):
        """None se o vídeo serve; senão o motivo. Baixa o vídeo se precisar."""
        ck = f"{c['source']}:{c['id']}|{args.start}|{dur}"
        if ck not in filter_cache:
            try:
                path = get_original(c)
            except PermanentDownloadError as e:
                return f"download ({e})"
            if probe(path)["duration"] < args.start + dur:
                filter_cache[ck] = {"too_short": True}
            else:
                filter_cache[ck] = analyse(path, args.start, dur)
        m = filter_cache[ck]
        return "duração real" if m.get("too_short") else filter_reason(m)

    it = iter(order)

    def next_valid(wants, dur):
        """Próximo candidato da permutação que `wants` aceita e passa nos critérios.

        Devolve (candidato, excluído?). Um ID de --exclude passa pelos mesmos critérios de
        metadados e de autor (para o sorteio ser o mesmo da execução em que foi aprovado),
        mas não é baixado: ocupa a vaga e é trocado depois."""
        for idx in it:
            c = cands[idx]
            if not wants(c):
                continue
            cid = f"{c['source']}:{c['id']}"
            reason = metadata_reason(c)
            if reason is None and authors[(c["source"], c["author"])] >= MAX_PER_AUTHOR:
                reason = f"mais de {MAX_PER_AUTHOR} do mesmo autor"
            exc = reason is None and cid in excluded
            if reason is None and not exc:
                reason = check(c, dur)
                if reason:
                    log.info("rejeitado %s (%s): %s", cid, c["category"], reason)
            if reason:
                rejected.append((cid, c["category"], reason))
                continue
            authors[(c["source"], c["author"])] += 1
            return c, exc
        return None, None

    def take(sid, cat, c, exc, note=""):
        if exc:
            vacancies.append((sid, cat))
            log.info("%s: %s:%d excluído na revisão; a vaga será preenchida depois", sid, c["source"], c["id"])
        else:
            chosen[sid] = c
            log.info("%s <- %s:%d (%s, '%s', %s)%s", sid, c["source"], c["id"], c["category"], c["term"], c["author"], note)

    # fase 1: as 35 vagas, cada vídeo na sua categoria
    while any(len(filled[cat]) < PER_CATEGORY for cat in cat_names):
        c, exc = next_valid(lambda c: len(filled[c["category"]]) < PER_CATEGORY, CLIP_S)
        if c is None:
            raise SystemExit(f"candidatos insuficientes: {', '.join(f'{k} {len(v)}/{PER_CATEGORY}' for k, v in filled.items())}")
        cat = c["category"]
        sid = slots[cat][len(filled[cat])]
        filled[cat].append(sid)
        take(sid, cat, c, exc)
    # fase 2: PR02, o próximo vídeo válido de qualquer categoria
    if args.practice:
        c, exc = next_valid(lambda c: True, PRACTICE_S)
        if c is None:
            raise SystemExit("nenhum vídeo sobrou para PR02")
        take("PR02", None, c, exc)
    # fase 3: vagas dos excluídos, na ordem do sorteio, cada uma com a mesma categoria
    for sid, cat in vacancies:
        dur = PRACTICE_S if sid == "PR02" else CLIP_S
        while True:
            c, exc = next_valid(lambda c: cat is None or c["category"] == cat, dur)
            if c is None:
                raise SystemExit(f"nenhum vídeo sobrou para substituir {sid} ({cat})")
            if not exc:
                break
        take(sid, cat, c, False, " [substitui excluído]")
    return chosen, rejected


def read_exclude(path):
    if not path:
        return set()
    ids = set()
    for line in Path(path).read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        if not re.fullmatch(r"(pixabay|pexels):\d+", line):
            raise SystemExit(f"ID inválido em {path}: {line!r} (use pixabay:123 ou pexels:456)")
        ids.add(line)
    return ids


def from_folder(folder, args):
    """Modo manual: lê PASTA/selecao.csv e devolve {stim_id: candidato} na ordem do CSV."""
    sel = folder / "selecao.csv"
    if not sel.exists():
        raise SystemExit(f"{sel} não existe; copie {ORIG_DIR / 'selecao_modelo.csv'} e preencha")
    with open(sel, newline="", encoding="utf-8-sig") as f:
        rows = [{k.strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    missing = set(SELECTION_COLUMNS) - set(rows[0] if rows else {})
    if missing:
        raise SystemExit(f"{sel}: faltam as colunas {sorted(missing)}")
    stim_ids = [f"S{FIRST_STIM + k:03d}" for k in range(N_VIDEOS)] + (["PR02"] if args.practice else [])
    if len(rows) > len(stim_ids):
        log.warning("%s tem %d linhas; só as %d primeiras são usadas", sel, len(rows), len(stim_ids))
    if len(rows) < len(stim_ids):
        log.warning("%s tem %d linhas; %s ficam como estão", sel, len(rows), ", ".join(stim_ids[len(rows):]))
    chosen = {}
    for sid, r in zip(stim_ids, rows):
        source = r["fonte"].lower()
        if source not in LICENSES:
            raise SystemExit(f"{sel}: fonte {r['fonte']!r} (use pixabay ou pexels)")
        src = folder / r["arquivo"]
        if not src.exists():
            raise SystemExit(f"{src} não existe")
        dst = ORIG_DIR / orig_name(source, int(r["id"]))
        if not dst.exists():
            shutil.copy2(src, dst)
        meta = probe(dst)
        chosen[sid] = {"source": source, "id": int(r["id"]), "category": r["categoria"], "term": "(manual)",
                       "duration": meta["duration"], "author": r["autor"], "author_url": "", "page_url": r["page_url"],
                       "tags": [], "flags": {}, "files": [{"url": "", "width": meta["width"], "height": meta["height"], "quality": "manual"}]}
    return chosen


# ---------------------------------------------------------------- saídas

def update_manifest(path, notes_by_stim):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    i_id, i_type, i_notes = header.index("stim_id"), header.index("type"), header.index("notes")
    for r in rows[1:]:
        if r[i_id] in notes_by_stim:
            assert r[i_type] == "video", f"{r[i_id]} não é vídeo no manifest"
            r[i_notes] = notes_by_stim[r[i_id]]
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def update_credits(path, new_rows):
    """Troca as linhas dos stim_ids em `new_rows` e mantém as outras (fotos do COCO)."""
    rows = []
    if path.exists():
        with open(path, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["stim_id"] not in new_rows]
    rows += new_rows.values()
    order = {sid: k for k, sid in enumerate(r["stim_id"] for r in rows)}
    rows.sort(key=lambda r: (r["stim_id"].startswith("PR"), r["stim_id"], order[r["stim_id"]]))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CREDITS_HEADER, restval="")
        w.writeheader()
        w.writerows(rows)


def font(size):
    for name in ["Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def frame_at(path, t, size):
    raw = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
               "-vf", f"scale={size}:{size}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
    return Image.frombytes("RGB", (size, size), raw[: size * size * 3])


def contact_sheet(items, out, cell=240, label_w=420):
    """Uma linha por vídeo: rótulo + frames do início, do meio e do fim do trecho final."""
    sheet = Image.new("RGB", (label_w + 3 * cell, len(items) * cell), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    f_big, f_small = font(34), font(20)
    for k, (sid, cand, metrics) in enumerate(items):
        path = STIM_DIR / f"{sid}.mp4"
        dur = probe(path)["duration"]
        y = k * cell
        for j, t in enumerate((0.0, dur / 2, max(dur - 0.1, 0.0))):
            sheet.paste(frame_at(path, t, cell), (label_w + j * cell, y))
        d.text((16, y + 20), sid, fill=(0, 0, 0), font=f_big)
        lines = [f"{cand['source']}:{cand['id']}", cand["category"], f"'{cand['term']}'", cand["author"][:28]]
        if metrics:
            lines.append(f"mov {metrics.get('motion', 0):.1f}  cena {metrics.get('scene_max', 0):.1f}")
        for i, line in enumerate(lines):
            d.text((16, y + 70 + 30 * i), line, fill=(60, 60, 60), font=f_small)
        d.line([(0, y + cell - 1), (sheet.width, y + cell - 1)], fill=(200, 200, 200), width=2)
    sheet.save(out, "JPEG", quality=82, optimize=True)


def write_selection_template():
    tpl = ORIG_DIR / "selecao_modelo.csv"
    if not tpl.exists():
        with open(tpl, "w", newline="") as f:
            csv.writer(f).writerow(SELECTION_COLUMNS)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["pixabay", "pexels", "both"], default="pixabay")
    ap.add_argument("--use-cache", action="store_true", help="reusa as buscas de search_cache.json, mesmo com mais de 24 h")
    ap.add_argument("--exclude", type=Path, default=None, help="arquivo com IDs rejeitados na revisão (pixabay:123, um por linha)")
    ap.add_argument("--from-folder", type=Path, default=None, help="pasta com vídeos baixados à mão e selecao.csv")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--start", type=float, default=START_S, help="início do trecho no vídeo original, em s")
    ap.add_argument("--practice", action=argparse.BooleanOptionalAction, default=True,
                    help="troca também o PR02 por um vídeo natural de 4 s (padrão: sim)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for exe in ("ffmpeg", "ffprobe"):
        if not shutil.which(exe):
            raise SystemExit(f"{exe} não encontrado no PATH (macOS: brew install ffmpeg)")

    ORIG_DIR.mkdir(exist_ok=True)
    write_selection_template()
    filter_cache = json.loads(FILTER_CACHE_FILE.read_text()) if FILTER_CACHE_FILE.exists() else {}

    rejected, n_cands, n_elig = [], 0, 0
    if args.from_folder:
        chosen = from_folder(args.from_folder, args)
        manual = True
    else:
        manual = False
        sources = ["pixabay", "pexels"] if args.source == "both" else [args.source]
        excluded = read_exclude(args.exclude)
        responses = run_searches(sources, args.use_cache)
        cands, dups = candidates_from(responses, sources)
        n_cands = len(cands)
        elig_reasons = Counter(metadata_reason(c) or "elegível" for c in cands)
        n_elig = elig_reasons.pop("elegível", 0)
        log.info("%d candidatos únicos (%d duplicatas descartadas), %d elegíveis pelos metadados; inelegíveis: %s",
                 n_cands, dups, n_elig, dict(elig_reasons.most_common()))
        log.info("por categoria (elegíveis): %s",
                 dict(Counter(c["category"] for c in cands if metadata_reason(c) is None)))
        log.info("seed %d, trecho de %g s a partir de %g s; %d IDs excluídos na revisão", args.seed, CLIP_S, args.start, len(excluded))
        chosen, rejected = select(cands, args, excluded, filter_cache)
        unused = excluded - {f"{c['source']}:{c['id']}" for c in cands}
        if unused:
            log.warning("IDs de --exclude que não estão entre os candidatos: %s", sorted(unused))

    # processamento
    metrics, info = {}, {}
    stim_order = sorted(chosen, key=lambda s: (s.startswith("PR"), s))
    for sid in stim_order:
        c = chosen[sid]
        dur = PRACTICE_S if sid == "PR02" else CLIP_S
        orig = get_original(c)
        meta = probe(orig)
        if meta["duration"] < args.start + dur:
            msg = f"{sid}: original {c['source']}:{c['id']} tem {meta['duration']:.2f} s, menos que {args.start + dur:g} s"
            if manual:
                raise SystemExit(msg)
            log.warning(msg)
        ck = f"{c['source']}:{c['id']}|{args.start}|{dur}"
        if ck not in filter_cache:
            filter_cache[ck] = analyse(orig, args.start, dur)
        metrics[sid] = filter_cache[ck]
        if manual and filter_reason(metrics[sid]):
            log.warning("%s (%s:%d): %s (modo manual: só aviso) %s", sid, c["source"], c["id"],
                        filter_reason(metrics[sid]), metrics[sid])
        crf = encode(orig, STIM_DIR / f"{sid}.mp4", args.start, dur, meta["fps"])
        out = probe(STIM_DIR / f"{sid}.mp4")
        info[sid] = {"orig": meta, "crf": crf, "out": out}
        log.info("%s: %s:%d, %dx%d %.2f fps %.1f s -> %.2f s, CRF %d, %d kB", sid, c["source"], c["id"], meta["width"],
                 meta["height"], meta["fps"], meta["duration"], out["duration"], crf, out["bytes"] // 1000)
    FILTER_CACHE_FILE.write_text(json.dumps(filter_cache, indent=1, sort_keys=True))

    notes = {}
    for sid, c in chosen.items():
        n = f"{c['source']}:{c['id']}; {c['category']}"
        notes[sid] = n + ("; practice; 4 s de propósito para testar o corte em 3 s" if sid == "PR02" else "")
    update_manifest(STIM_DIR / "manifest.csv", notes)

    with open(ORIG_DIR / "stock_metadata.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stim_id", "source", "id", "category", "search_term", "author", "author_url", "page_url", "file_url",
                    "original_file", "orig_duration_s", "orig_width", "orig_height", "orig_fps", "api_duration_s",
                    "clip_start_s", "clip_duration_s", "out_duration_s", "out_bytes", "crf",
                    "scene_max_score", "scene_threshold", "motion", "motion_min", "fade_std_ratio", "fade_std_ratio_min", "tags"])
        for sid in stim_order:
            c, m, inf = chosen[sid], metrics[sid], info[sid]
            dur = PRACTICE_S if sid == "PR02" else CLIP_S
            fl = pick_file(c) if not manual else None
            w.writerow([sid, c["source"], c["id"], c["category"], c["term"], c["author"], c["author_url"], c["page_url"],
                        redact(fl["url"]) if fl else "", orig_name(c["source"], c["id"]), round(inf["orig"]["duration"], 3),
                        inf["orig"]["width"], inf["orig"]["height"], round(inf["orig"]["fps"], 3), c["duration"],
                        args.start, dur, round(inf["out"]["duration"], 3), inf["out"]["bytes"], inf["crf"],
                        m["scene_max"], SCENE_THRESHOLD, m["motion"], MOTION_MIN, m["fade_std_ratio"], FADE_STD_RATIO,
                        ";".join(c["tags"])])

    credits = {}
    for sid in stim_order:
        c = chosen[sid]
        lic_name, lic_url = LICENSES[c["source"]]
        dur = PRACTICE_S if sid == "PR02" else CLIP_S
        credits[sid] = {"stim_id": sid, "file": f"{sid}.mp4", "source": f"{SOURCE_NAMES[c['source']]}, vídeo {c['id']}",
                        "license_name": lic_name, "license_url": lic_url, "modification": MODIFICATION.format(dur=dur, start=args.start),
                        "author": c["author"], "page_url": c["page_url"]}
    update_credits(STIM_DIR / "CREDITS.csv", credits)

    keep = {orig_name(c["source"], c["id"]) for c in chosen.values()}
    for old in sorted([*ORIG_DIR.glob("pixabay_[0-9]*.mp4"), *ORIG_DIR.glob("pexels_[0-9]*.mp4")]):
        if old.name not in keep:
            old.unlink()
            log.info("original fora do conjunto apagado: %s", old.name)

    contact_sheet([(sid, chosen[sid], metrics[sid]) for sid in stim_order], ORIG_DIR / "contact_sheet.jpg")

    for path in [CACHE_FILE, FILTER_CACHE_FILE, ORIG_DIR / "stock_metadata.csv", STIM_DIR / "CREDITS.csv", STIM_DIR / "manifest.csv"]:
        if path.exists() and SECRETS:
            assert all(s not in path.read_text() for s in SECRETS), f"chave apareceu em {path}"

    main_ids = [s for s in stim_order if s != "PR02"]
    reasons = Counter(r for _, _, r in rejected)
    post = {k: v for k, v in reasons.items() if k in ("corte de cena", "quase estático", "fade", "trecho vazio", "duração real") or k.startswith("download")}
    log.info("%d vídeos selecionados (%d + PR02)" if "PR02" in chosen else "%d vídeos selecionados (%d)", len(chosen), len(main_ids))
    log.info("por categoria: %s", dict(Counter(chosen[s]["category"] for s in main_ids)))
    log.info("rejeitados depois do download (filtros automáticos): %d %s", sum(post.values()), post)
    log.info("pulados no sorteio por metadados ou autor: %d %s", sum(reasons.values()) - sum(post.values()),
             {k: v for k, v in reasons.items() if k not in post})
    log.info("fontes: %s; autores: %d", dict(Counter(c["source"] for c in chosen.values())),
             len({(c["source"], c["author"]) for c in chosen.values()}))
    log.info("originais, metadados e contact sheet em %s; créditos em %s", ORIG_DIR, STIM_DIR / "CREDITS.csv")


if __name__ == "__main__":
    main()
