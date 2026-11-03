"""Gera estímulos placeholder para testar o fluxo do pré-piloto.

As imagens S001-S035 e PR01 hoje são fotos do COCO (select_coco_images.py). Por isso
este script nunca sobrescreve: pula todo arquivo que já existe em stimuli/ e só
escreve manifest.csv se ele não existir. Para regenerar um placeholder, apague o
arquivo antes.

Cria em stimuli/ (só o que estiver faltando):
  - 35 imagens .jpg (S001-S035) e 35 vídeos .mp4 de 3 s (S036-S070), 512x512,
    H.264 sem áudio, cada um com forma/cor/movimento diferentes e um número
    discreto no canto;
  - 2 estímulos de prática (PR01 imagem, PR02 vídeo). O vídeo de prática tem
    4 s de propósito, para exercitar o corte em 3 s;
  - stimuli/manifest.csv.

Uso:
    python make_placeholders.py            # usa ffmpeg do PATH ou do imageio-ffmpeg
    python make_placeholders.py --ffmpeg /caminho/para/ffmpeg

Dependências: Pillow, numpy e ffmpeg (ou `pip install imageio-ffmpeg`).
"""

import argparse
import csv
import colorsys
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SIZE = 512
FPS = 24
OUT = Path(__file__).parent / "stimuli"

SHAPES = ["circle", "square", "triangle", "star", "hexagon", "cross", "ring",
          "diamond", "crescent", "arrow", "ellipse", "pentagon"]
MOTIONS = ["horizontal", "vertical", "orbit", "rotate", "pulse", "bounce",
           "zigzag", "spiral", "figure8"]


def find_ffmpeg(explicit=None):
    if explicit:
        return explicit
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise SystemExit("ffmpeg não encontrado. Instale ffmpeg ou `pip install imageio-ffmpeg`.")


def hsv(h, s=0.75, v=0.9):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def font(size):
    for name in ["Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def polygon(cx, cy, r, n, rot=0.0):
    return [(cx + r * math.cos(rot + 2 * math.pi * k / n),
             cy + r * math.sin(rot + 2 * math.pi * k / n)) for k in range(n)]


def draw_shape(d, shape, cx, cy, r, color, rot=0.0, bg=(0, 0, 0)):
    if shape == "circle":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    elif shape == "square":
        d.polygon(polygon(cx, cy, r * 1.2, 4, rot + math.pi / 4), fill=color)
    elif shape == "triangle":
        d.polygon(polygon(cx, cy, r * 1.2, 3, rot - math.pi / 2), fill=color)
    elif shape == "pentagon":
        d.polygon(polygon(cx, cy, r * 1.1, 5, rot - math.pi / 2), fill=color)
    elif shape == "hexagon":
        d.polygon(polygon(cx, cy, r * 1.1, 6, rot), fill=color)
    elif shape == "diamond":
        pts = [(cx + r * 0.7 * math.cos(rot + a), cy + r * 1.3 * math.sin(rot + a))
               for a in (0, math.pi / 2, math.pi, 3 * math.pi / 2)]
        d.polygon(pts, fill=color)
    elif shape == "star":
        pts = []
        for k in range(10):
            rr = r * 1.2 if k % 2 == 0 else r * 0.5
            a = rot - math.pi / 2 + math.pi * k / 5
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        d.polygon(pts, fill=color)
    elif shape == "cross":
        w = r * 0.35
        for a in (rot, rot + math.pi / 2):
            ca, sa = math.cos(a), math.sin(a)
            pts = [(-r, -w), (r, -w), (r, w), (-r, w)]
            d.polygon([(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in pts], fill=color)
    elif shape == "ring":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        d.ellipse([cx - r * 0.55, cy - r * 0.55, cx + r * 0.55, cy + r * 0.55], fill=bg)
    elif shape == "crescent":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        ox, oy = r * 0.45 * math.cos(rot), r * 0.45 * math.sin(rot)
        d.ellipse([cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy], fill=bg)
    elif shape == "arrow":
        pts = [(-r, -r * 0.25), (r * 0.2, -r * 0.25), (r * 0.2, -r * 0.6), (r, 0),
               (r * 0.2, r * 0.6), (r * 0.2, r * 0.25), (-r, r * 0.25)]
        ca, sa = math.cos(rot), math.sin(rot)
        d.polygon([(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in pts], fill=color)
    elif shape == "ellipse":
        d.ellipse([cx - r * 1.3, cy - r * 0.7, cx + r * 1.3, cy + r * 0.7], fill=color)


def background(i, seed_h):
    """Fundo com gradiente e padrão simples, diferente para cada estímulo."""
    h = (seed_h + 0.5) % 1.0
    top = np.array(hsv(h, 0.25, 0.35 + 0.1 * (i % 3)), dtype=float)
    bot = np.array(hsv(h + 0.1, 0.35, 0.15 + 0.05 * (i % 4)), dtype=float)
    t = np.linspace(0, 1, SIZE)[:, None, None]
    arr = (top * (1 - t) + bot * t).repeat(SIZE, axis=1)
    pattern = i % 4
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    if pattern == 1:
        arr += 12 * ((xx // 32 + yy // 32) % 2)[..., None]
    elif pattern == 2:
        arr += 10 * np.sin(xx / 18.0)[..., None]
    elif pattern == 3:
        arr += 10 * (((xx + yy) // 24) % 2)[..., None]
    return np.clip(arr, 0, 255).astype(np.uint8)


def label(img, text):
    d = ImageDraw.Draw(img)
    d.text((SIZE - 12, SIZE - 10), text, fill=(200, 200, 200), font=font(16), anchor="rd")


def make_image(i, stim_id, path):
    shape = SHAPES[i % len(SHAPES)]
    h = (i * 0.137) % 1.0
    bg_arr = background(i, h)
    img = Image.fromarray(bg_arr)
    d = ImageDraw.Draw(img)
    bgc = tuple(int(v) for v in bg_arr[SIZE // 2, SIZE // 2])
    # forma principal + duas formas secundárias menores, em posições distintas por estímulo
    rng = np.random.default_rng(1000 + i)
    cx, cy = 256 + rng.integers(-60, 60), 256 + rng.integers(-60, 60)
    draw_shape(d, shape, cx, cy, 120, hsv(h), rot=i * 0.4, bg=bgc)
    for k in range(2):
        s2 = SHAPES[(i * 5 + k * 7 + 3) % len(SHAPES)]
        ang = rng.uniform(0, 2 * math.pi)
        draw_shape(d, s2, 256 + 175 * math.cos(ang), 256 + 175 * math.sin(ang), 38,
                   hsv(h + 0.33 + 0.2 * k, 0.6, 1.0), rot=ang, bg=bgc)
    label(img, stim_id)
    img.save(path, "JPEG", quality=78, optimize=True)


def motion_pos(motion, t, T):
    """Posição (cx, cy), raio e rotação no instante t (s) de um vídeo de T s."""
    u = t / T
    cx, cy, r, rot = 256.0, 256.0, 80.0, 0.0
    if motion == "horizontal":
        cx = 100 + 312 * u
    elif motion == "vertical":
        cy = 412 - 312 * u
    elif motion == "orbit":
        cx += 140 * math.cos(2 * math.pi * u)
        cy += 140 * math.sin(2 * math.pi * u)
    elif motion == "rotate":
        r, rot = 130, 2 * math.pi * u
    elif motion == "pulse":
        r = 60 + 70 * abs(math.sin(2 * math.pi * u * 1.5))
    elif motion == "bounce":
        cx = 100 + 312 * u
        cy = 400 - 300 * abs(math.sin(3 * math.pi * u))
    elif motion == "zigzag":
        cx = 100 + 312 * u
        cy = 256 + 120 * (2 * abs((u * 4) % 2 - 1) - 1)
    elif motion == "spiral":
        rr = 160 * (1 - u)
        cx += rr * math.cos(6 * math.pi * u)
        cy += rr * math.sin(6 * math.pi * u)
        rot = 4 * math.pi * u
    elif motion == "figure8":
        cx += 150 * math.sin(2 * math.pi * u)
        cy += 90 * math.sin(4 * math.pi * u)
    return cx, cy, r, rot


def make_video(i, stim_id, path, ffmpeg, duration=3.0):
    shape = SHAPES[(i * 7) % len(SHAPES)]
    motion = MOTIONS[i % len(MOTIONS)]
    h = (i * 0.211 + 0.05) % 1.0
    bg_arr = background(i + 50, h)
    bgc = tuple(int(v) for v in bg_arr[SIZE // 2, SIZE // 2])
    n = int(round(duration * FPS))
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{SIZE}x{SIZE}", "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264",
           "-preset", "slow", "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(n):
        t = f / FPS
        img = Image.fromarray(bg_arr.copy())
        d = ImageDraw.Draw(img)
        cx, cy, r, rot = motion_pos(motion, t, duration)
        draw_shape(d, shape, cx, cy, r, hsv(h), rot=rot, bg=bgc)
        # forma secundária fixa, para diferenciar ainda mais os vídeos
        s2 = SHAPES[(i * 3 + 5) % len(SHAPES)]
        draw_shape(d, s2, 70 + (i % 5) * 90, 60, 26, hsv(h + 0.5, 0.5, 1.0), bg=bgc)
        label(img, stim_id)
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg falhou em {path}")
    return shape, motion


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--n-images", type=int, default=35)
    ap.add_argument("--n-videos", type=int, default=35)
    args = ap.parse_args()
    ffmpeg = find_ffmpeg(args.ffmpeg)
    OUT.mkdir(exist_ok=True)

    def missing(path):
        if path.exists():
            print(f"{path.name} já existe, mantido")
            return False
        return True

    rows = []
    k = 0
    for i in range(args.n_images):
        k += 1
        sid = f"S{k:03d}"
        if missing(OUT / f"{sid}.jpg"):
            make_image(i, sid, OUT / f"{sid}.jpg")
        rows.append([sid, f"{sid}.jpg", "image", f"item_{sid}",
                     f"placeholder: {SHAPES[i % len(SHAPES)]}"])
    for i in range(args.n_videos):
        k += 1
        sid = f"S{k:03d}"
        shape, motion = SHAPES[(i * 7) % len(SHAPES)], MOTIONS[i % len(MOTIONS)]
        if missing(OUT / f"{sid}.mp4"):
            make_video(i, sid, OUT / f"{sid}.mp4", ffmpeg)
        rows.append([sid, f"{sid}.mp4", "video", f"item_{sid}", f"placeholder: {shape}/{motion}"])

    # prática: fora do conjunto principal (ids listados em config.json -> practice_stim_ids)
    if missing(OUT / "PR01.jpg"):
        make_image(97, "PR01", OUT / "PR01.jpg")
    rows.append(["PR01", "PR01.jpg", "image", "item_PR01", "practice"])
    if missing(OUT / "PR02.mp4"):
        make_video(98, "PR02", OUT / "PR02.mp4", ffmpeg, duration=4.0)
    rows.append(["PR02", "PR02.mp4", "video", "item_PR02",
                 "practice; 4 s de propósito para testar o corte em 3 s"])

    if (OUT / "manifest.csv").exists():
        print("manifest.csv já existe, mantido")
    else:
        with open(OUT / "manifest.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["stim_id", "file", "type", "item_id", "notes"])
            w.writerows(rows)

    total = sum(p.stat().st_size for p in OUT.iterdir() if p.suffix in (".jpg", ".mp4"))
    print(f"{len(rows)} estímulos em {OUT} ({total / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
