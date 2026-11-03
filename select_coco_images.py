"""Sorteia fotos do MS COCO 2017 (val) para os estímulos de imagem do pré-piloto.

Substitui as imagens S001-S035 em stimuli/ (e, com --practice, também PR01) por fotos
naturais do COCO, sem mudar stim_id, item_id nem o tipo no manifest. Os vídeos ficam
como estão.

Seleção (reprodutível: a mesma seed gera as mesmas imagens):
  1. Lê instances_val2017.json e captions_val2017.json (só a biblioteca padrão `json`).
  2. Elegíveis pelos metadados: lado menor >= 480 px, lado maior / lado menor <= 1,5 e
     licença CC BY, CC BY-SA ou "No known copyright restrictions" (sem NC nem ND).
  3. Permutação dos image_id elegíveis (ordenados) com numpy.random.default_rng(seed).
  4. Percorre a permutação baixando cada imagem pelo coco_url. Rejeita as que não abrem
     e as em tons de cinza (R, G e B idênticos em todos os pixels, não só o modo).
     Para quando junta as válidas necessárias.

Processamento: orientação EXIF aplicada, recorte central quadrado, 512x512 (LANCZOS),
RGB, JPEG qualidade 85, sem EXIF. Os originais vão sem alteração para
../coco_originais/coco_<image_id com 12 dígitos>.jpg (fora do repositório), junto com
coco_metadata.csv (licença, URLs, categorias, legendas) e contact_sheet.jpg. Originais
de execuções anteriores que não fazem mais parte do conjunto são apagados. A atribuição
exigida pelas licenças vai para stimuli/CREDITS.csv, dentro do repositório (só as linhas
destas imagens; as dos vídeos, de select_stock_videos.py, são mantidas).

Uso:
    python select_coco_images.py                     # baixa as anotações num diretório temporário
    python select_coco_images.py --annotations-dir DIR  # usa instances/captions_val2017.json já extraídos
    python select_coco_images.py --no-practice       # não troca PR01

Dependências: Pillow e numpy.
"""

import argparse
import csv
import io
import json
import logging
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
STIM_DIR = HERE / "stimuli"
ORIG_DIR = HERE.parent / "coco_originais"
ANN_URL = "http://images.cocodataset.org/annotations/annotations_trainval2017.zip"
ANN_FILES = ("instances_val2017.json", "captions_val2017.json")

SEED = 2026
N_IMAGES = 35
MIN_SIDE = 480
MAX_RATIO = 1.5
# só licenças sem NonCommercial nem NoDerivs (o recorte é uma modificação)
ALLOWED_LICENSES = ("Attribution License", "Attribution-ShareAlike License", "No known copyright restrictions")
MODIFICATION = "recorte central quadrado e redimensionamento para 512x512"
# colunas de stimuli/CREDITS.csv, comuns às fotos e aos vídeos (author e page_url são dos vídeos)
CREDITS_HEADER = ["stim_id", "file", "source", "license_name", "license_url", "flickr_url", "coco_url",
                  "modification", "author", "page_url"]
SIZE = 512  # stim_size_px do config.json
JPEG_QUALITY = 85
RETRIES = 3

log = logging.getLogger("select_coco")


class PermanentDownloadError(Exception):
    """Erro que não muda tentando de novo (ex.: HTTP 404): a imagem é rejeitada."""


def fetch(url, retries=RETRIES):
    """Baixa `url` com até `retries` tentativas. Erros 4xx são definitivos."""
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise PermanentDownloadError(f"HTTP {e.code}") from e
            err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
        log.warning("falha ao baixar %s (tentativa %d/%d): %s", url, attempt, retries, err)
        if attempt < retries:
            time.sleep(2 * attempt)
    # Falha de rede não é propriedade da imagem: pular a imagem mudaria o sorteio
    # entre execuções, então é melhor parar e rodar de novo.
    raise SystemExit(f"não foi possível baixar {url} depois de {retries} tentativas")


def load_annotations(ann_dir):
    with open(ann_dir / "instances_val2017.json") as f:
        inst = json.load(f)
    with open(ann_dir / "captions_val2017.json") as f:
        caps = json.load(f)
    return inst, caps


def download_annotations(dest):
    log.info("baixando %s", ANN_URL)
    data = fetch(ANN_URL)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in ANN_FILES:
            (dest / name).write_bytes(z.read(f"annotations/{name}"))
    log.info("anotações extraídas em %s", dest)


def eligible_ids(images, licenses):
    out = []
    for im in images:
        w, h = im["width"], im["height"]
        lo, hi = min(w, h), max(w, h)
        if lo >= MIN_SIDE and hi / lo <= MAX_RATIO and licenses[im["license"]]["name"] in ALLOWED_LICENSES:
            out.append(im["id"])
    return sorted(out)


def check_image(data):
    """Abre a imagem e devolve (PIL.Image em RGB, orientação EXIF aplicada?) ou o motivo da rejeição."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001 - qualquer falha de decodificação rejeita
        return None, f"não abre ({type(e).__name__})"
    if img.mode in ("1", "L", "LA", "I", "I;16", "F"):
        return None, f"tons de cinza (modo {img.mode})"
    rgb = img.convert("RGB")
    a = np.asarray(rgb)
    if np.array_equal(a[..., 0], a[..., 1]) and np.array_equal(a[..., 1], a[..., 2]):
        return None, f"tons de cinza (modo {img.mode}, R=G=B)"
    oriented = ImageOps.exif_transpose(img)
    rotated = oriented.size != img.size or img.getexif().get(0x0112, 1) != 1
    return oriented.convert("RGB"), rotated


def make_stimulus(img, path):
    w, h = img.size
    s = min(w, h)
    left, top = (w - s) // 2, (h - s) // 2
    sq = img.crop((left, top, left + s, top + s)).resize((SIZE, SIZE), Image.LANCZOS)
    # sem exif/icc_profile: o Pillow não grava metadados que não forem passados
    sq.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)


def update_manifest(path, notes_by_stim):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    i_id, i_type, i_notes = header.index("stim_id"), header.index("type"), header.index("notes")
    for r in rows[1:]:
        if r[i_id] in notes_by_stim:
            assert r[i_type] == "image", f"{r[i_id]} não é imagem no manifest"
            r[i_notes] = notes_by_stim[r[i_id]]
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def font(size):
    for name in ["Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheet(stim_ids, out, cols=7, rows=5, label_h=44):
    sheet = Image.new("RGB", (cols * SIZE, rows * (SIZE + label_h)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    f = font(30)
    for k, sid in enumerate(stim_ids[: cols * rows]):
        x, y = (k % cols) * SIZE, (k // cols) * (SIZE + label_h)
        with Image.open(STIM_DIR / f"{sid}.jpg") as im:
            sheet.paste(im, (x, y))
        d.text((x + SIZE // 2, y + SIZE + label_h // 2), sid, fill=(0, 0, 0), font=f, anchor="mm")
    sheet.save(out, "JPEG", quality=80, optimize=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotations-dir", type=Path, default=None,
                    help="pasta com instances_val2017.json e captions_val2017.json (sem isso, baixa num diretório temporário)")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--no-practice", action="store_true", help="não troca PR01 pela 36ª imagem válida")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    if args.annotations_dir:
        inst, caps = load_annotations(args.annotations_dir)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            download_annotations(Path(tmp))
            inst, caps = load_annotations(Path(tmp))

    images = {im["id"]: im for im in inst["images"]}
    licenses = {lic["id"]: lic for lic in inst["licenses"]}
    cat_names = {c["id"]: c["name"] for c in inst["categories"]}
    anns = defaultdict(list)
    for a in inst["annotations"]:
        anns[a["image_id"]].append(a)
    captions = defaultdict(list)
    for c in sorted(caps["annotations"], key=lambda c: c["id"]):
        captions[c["image_id"]].append(" ".join(c["caption"].split()))

    elig = eligible_ids(inst["images"], licenses)
    order = np.random.default_rng(args.seed).permutation(elig)
    log.info("%d imagens no val2017, %d elegíveis (lado menor >= %d, razão <= %.1f, licenças %s), seed %d",
             len(images), len(elig), MIN_SIDE, MAX_RATIO, ", ".join(ALLOWED_LICENSES), args.seed)

    stim_ids = [f"S{k:03d}" for k in range(1, N_IMAGES + 1)]
    if not args.no_practice:
        stim_ids.append("PR01")
    ORIG_DIR.mkdir(exist_ok=True)

    chosen, rejected = [], []
    for coco_id in order:
        if len(chosen) == len(stim_ids):
            break
        coco_id = int(coco_id)
        im = images[coco_id]
        orig = ORIG_DIR / f"coco_{coco_id:012d}.jpg"
        if orig.exists():
            data = orig.read_bytes()
        else:
            try:
                data = fetch(im["coco_url"])
            except PermanentDownloadError as e:
                rejected.append((coco_id, f"download ({e})"))
                log.info("rejeitada %d: download (%s)", coco_id, e)
                continue
        img, info = check_image(data)
        if img is None:
            rejected.append((coco_id, info))
            log.info("rejeitada %d: %s", coco_id, info)
            continue
        orig.write_bytes(data)
        sid = stim_ids[len(chosen)]
        make_stimulus(img, STIM_DIR / f"{sid}.jpg")
        if info:
            log.info("%s: orientação EXIF aplicada em %d", sid, coco_id)
        chosen.append((sid, coco_id))
        log.info("%s <- coco %d", sid, coco_id)

    if len(chosen) < len(stim_ids):
        raise SystemExit(f"só {len(chosen)} imagens válidas; esperava {len(stim_ids)}")

    update_manifest(STIM_DIR / "manifest.csv", {sid: f"coco2017val:{cid}" for sid, cid in chosen})

    cat_counter = Counter()
    with open(ORIG_DIR / "coco_metadata.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stim_id", "coco_id", "original_file", "width", "height", "license_name", "license_url",
                    "flickr_url", "coco_url", "categories", "n_instances", "captions"])
        for sid, cid in chosen:
            im = images[cid]
            lic = licenses[im["license"]]
            cats = sorted({cat_names[a["category_id"]] for a in anns[cid]})
            if sid != "PR01":
                cat_counter.update(cats)
            w.writerow([sid, cid, f"coco_{cid:012d}.jpg", im["width"], im["height"], lic["name"], lic["url"],
                        im.get("flickr_url", ""), im["coco_url"], ";".join(cats), len(anns[cid]),
                        " | ".join(captions[cid])])

    # CREDITS.csv é compartilhado com select_stock_videos.py: só as linhas destas imagens são
    # trocadas. author e page_url ficam vazios aqui (o COCO não informa o autor).
    credits_path = STIM_DIR / "CREDITS.csv"
    mine = {sid for sid, _ in chosen}
    others = []
    if credits_path.exists():
        with open(credits_path, newline="") as f:
            others = [r for r in csv.DictReader(f) if r["stim_id"] not in mine]
    rows = []
    for sid, cid in chosen:
        im = images[cid]
        lic = licenses[im["license"]]
        rows.append({"stim_id": sid, "file": f"{sid}.jpg", "source": f"MS COCO 2017 val, image_id {cid} (Flickr)",
                     "license_name": lic["name"], "license_url": lic["url"], "flickr_url": im.get("flickr_url", ""),
                     "coco_url": im["coco_url"], "modification": MODIFICATION})
    rows = sorted(rows + others, key=lambda r: (r["stim_id"].startswith("PR"), r["stim_id"]))
    with open(credits_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CREDITS_HEADER, restval="")
        w.writeheader()
        w.writerows(rows)

    keep = {f"coco_{cid:012d}.jpg" for _, cid in chosen}
    for old in sorted(ORIG_DIR.glob("coco_[0-9]*.jpg")):
        if old.name not in keep:
            old.unlink()
            log.info("original fora do conjunto apagado: %s", old.name)

    contact_sheet([sid for sid, _ in chosen if sid != "PR01"], ORIG_DIR / "contact_sheet.jpg")

    reasons = Counter(r for _, r in rejected)
    log.info("%d imagens selecionadas; %d candidatas rejeitadas: %s", len(chosen), len(rejected), dict(reasons))
    log.info("categorias mais frequentes (n imagens): %s", cat_counter.most_common(10))
    log.info("licenças: %s", dict(Counter(licenses[images[cid]["license"]]["name"] for _, cid in chosen)))
    log.info("originais, metadados e contact sheet em %s; créditos em %s", ORIG_DIR, STIM_DIR / "CREDITS.csv")


if __name__ == "__main__":
    main()
