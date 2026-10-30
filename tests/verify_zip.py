"""Confere os .zip gerados pelo teste automatizado (tests/e2e_test.py).

Uso:
    python tests/verify_zip.py tests/output
Precisa de ffmpeg (no PATH ou via `pip install imageio-ffmpeg`) para checar os áudios.
"""

import csv
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REQUIRED_COLS = (
    "participant_id, session_id, trial_index, is_practice, stim_id, item_id, stim_type, file, is_repeat, "
    "first_presentation_trial, lag, description_mode, vividness, vividness_rt_ms, dynamism, dynamism_rt_ms, "
    "recognized_old, recognition_correct, recognition_rt_ms, description_text, audio_file, audio_duration_ms, "
    "description_rt_ms, stim_onset_ts, mask_onset_ts, imagery_onset_ts, trial_start_ts, trial_end_ts, "
    "n_focus_losses, focus_lost_ms, stim_load_ms"
).split(", ")
TOL = {"stim": 20, "mask": 20, "imagery": 40}  # ms (≈ 1 frame; imaginação inclui o 1º frame da tela de resposta)

results = []


def check(cond, msg):
    results.append((bool(cond), msg))
    print(("  OK   " if cond else "  FAIL ") + msg)


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def audio_info(data):
    with tempfile.NamedTemporaryFile(suffix=".webm") as f:
        f.write(data)
        f.flush()
        probe = subprocess.run([ffmpeg(), "-hide_banner", "-i", f.name], capture_output=True, text=True).stderr
        dec = subprocess.run([ffmpeg(), "-v", "error", "-i", f.name, "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
                             capture_output=True)
    m = re.search(r"Audio: (\w+), (\d+) Hz, (\w+)", probe)
    return {
        "codec": m.group(1) if m else None,
        "channels": m.group(3) if m else None,
        "decoded_s": len(dec.stdout) / 2 / 48000,
        "decode_ok": dec.returncode == 0 and not dec.stderr.strip(),
    }


def load(zpath):
    z = zipfile.ZipFile(zpath)
    names = z.namelist()
    root = names[0].split("/")[0]
    rd = lambda n: z.read(f"{root}/{n}").decode("utf-8")  # noqa: E731
    return z, root, names, rd


def validate_sequence(seq, cfg):
    probs = []
    seen, rep_once = {}, set()
    blocks = {}
    for t in seq:
        b = blocks.setdefault(t["block"], {"n": 0, "image": 0, "video": 0, "rep": 0, "written": 0, "iw": 0, "vw": 0})
        b["n"] += 1
        b[t["stim_type"]] += 1
        if t["description_mode"] == "written":
            b["written"] += 1
            b["iw" if t["stim_type"] == "image" else "vw"] += 1
        if t["is_repeat"]:
            b["rep"] += 1
            if t["stim_id"] not in seen or t["stim_id"] in rep_once:
                probs.append(f"repetição inválida no trial {t['trial_index']}")
            rep_once.add(t["stim_id"])
            lag = t["trial_index"] - seen.get(t["stim_id"], -999)
            if lag != t["lag"] or not cfg["min_lag"] <= lag <= cfg["max_lag"]:
                probs.append(f"lag inválido no trial {t['trial_index']}: {lag}")
        else:
            if t["stim_id"] in seen:
                probs.append(f"{t['stim_id']} 'novo' repetido")
            seen[t["stim_id"]] = t["trial_index"]
    for k, b in blocks.items():
        if b["rep"] != round(b["n"] * cfg["repeat_proportion"]):
            probs.append(f"bloco {k}: {b['rep']} repetições")
        if abs(b["image"] - b["video"]) > 1:
            probs.append(f"bloco {k}: tipo desbalanceado")
        if abs(2 * b["written"] - b["n"]) > 1:
            probs.append(f"bloco {k}: modalidade desbalanceada")
        if abs(2 * b["iw"] - b["image"]) > 1 or abs(2 * b["vw"] - b["video"]) > 1:
            probs.append(f"bloco {k}: modalidade não cruzada com o tipo")
    # prefixos: qualquer prefixo de blocos completos tem a proporção exata
    n_rep = sum(t["is_repeat"] for t in seq)
    return probs, n_rep, len(seq)


def verify_main(zpath, expect):
    print(f"\n### {zpath.name}")
    z, root, names, rd = load(zpath)
    rel = {n[len(root) + 1:] for n in names}
    for f in ["session.json", "trials.csv", "checkpoints.csv", "events.csv", "final_feedback.json"]:
        check(f in rel, f"contém {f}")
    s = json.loads(rd("session.json"))
    trials = list(csv.DictReader(io.StringIO(rd("trials.csv"))))
    cps = list(csv.DictReader(io.StringIO(rd("checkpoints.csv"))))
    evs = list(csv.DictReader(io.StringIO(rd("events.csv"))))
    fb = json.loads(rd("final_feedback.json"))
    cols = trials[0].keys() if trials else []
    missing = [c for c in REQUIRED_COLS if c not in cols]
    check(not missing, f"trials.csv tem todas as colunas mínimas {('(faltam ' + str(missing) + ')') if missing else ''}")
    main = [t for t in trials if t["is_practice"] == "0" and t["status"] == "completed"]
    prac = [t for t in trials if t["is_practice"] == "1" and t["status"] == "completed"]
    check(s["termination_reason"] == expect["reason"], f"motivo do término = {s['termination_reason']} (esperado {expect['reason']})")
    check(len(main) == expect["n_main"], f"{len(main)} trials principais completos (esperado {expect['n_main']})")
    check(len(prac) == 2, f"{len(prac)} trials de prática completos")
    check(s.get("n_trials_completed") == expect["n_main"], "session.json n_trials_completed confere")

    # trials batem com a sequência planejada
    plan = {t["trial_index"]: t for t in s["planned_sequence"]}
    mism = [t["trial_index"] for t in main if plan[int(t["trial_index"])]["stim_id"] != t["stim_id"]
            or plan[int(t["trial_index"])]["description_mode"] != t["description_mode"]
            or int(t["is_repeat"]) != plan[int(t["trial_index"])]["is_repeat"]]
    check(not mism, "trials executados seguem a sequência planejada (estímulo, modalidade, repetição)")

    # durações
    devs = {"stim": [], "mask": [], "imagery": []}
    for t in main + prac:
        devs["stim"].append(float(t["stim_duration_measured_ms"]) - 3000)
        devs["mask"].append(float(t["mask_duration_measured_ms"]) - 2000)
        devs["imagery"].append(float(t["imagery_duration_measured_ms"]) - 6000)
        # confere também pelos onsets brutos
        assert abs(float(t["mask_onset_ts"]) - float(t["stim_onset_ts"]) - float(t["stim_duration_measured_ms"])) < 0.2
    for k, v in devs.items():
        mx = max(abs(x) for x in v)
        check(mx <= TOL[k], f"duração {k}: desvio máx {mx:.1f} ms, médio {sum(v) / len(v):+.1f} ms (tolerância {TOL[k]} ms)")
    vt = [t for t in prac if t["stim_type"] == "video"]
    check(vt and vt[0]["video_truncated"] == "1", "vídeo de prática de 4 s marcado como cortado (video_truncated=1)")

    # respostas
    check(all(t["recognition_correct"] == "1" for t in main), "reconhecimento correto em todos (o teste responde certo)")
    check(all(t["vividness"] and t["dynamism"] for t in main), "vividez e dinamismo preenchidos")
    check(all(t["stim_load_ms"] != "" for t in main), "stim_load_ms registrado")
    written = [t for t in main + prac if t["description_mode"] == "written"]
    check(all("1 2 3 S N" in t["description_text"] for t in written), "texto digitado com números/S/N intacto (atalhos desativados na caixa de texto)")

    # áudios
    audio_trials = [t for t in main + prac if t["description_mode"] == "audio"]
    files = sorted(n for n in rel if n.startswith("audio/") and n != "audio/")
    check(sorted("audio/" + t["audio_file"] for t in audio_trials) == files, f"{len(files)} arquivos de áudio, um por trial em áudio")
    total_bytes = total_s = 0
    bad = []
    for t in audio_trials:
        data = z.read(f"{root}/audio/{t['audio_file']}")
        info = audio_info(data)
        total_bytes += len(data)
        total_s += info["decoded_s"]
        if not (info["decode_ok"] and info["codec"] == "opus" and info["channels"] == "mono" and info["decoded_s"] > 0.5):
            bad.append((t["audio_file"], info))
        if t["audio_duration_ms"] and abs(info["decoded_s"] * 1000 - float(t["audio_duration_ms"])) > 150:
            bad.append((t["audio_file"], "duração difere", info["decoded_s"], t["audio_duration_ms"]))
    check(not bad, f"áudios decodificam (ffmpeg), opus mono, com som e duração coerente {bad if bad else ''}")
    kbps = total_bytes * 8 / total_s / 1000 if total_s else 0

    if expect.get("checkpoint"):
        check(len(cps) == 1 and cps[0]["fatigue"] == "2" and cps[0]["concentration"] == "4" and cps[0]["action"] == "continue",
              "1 checkpoint com cansaço=2, concentração=4, continuar")
    if expect.get("focus_trial"):
        fl = [e for e in evs if e["event_type"] == "focus_loss"]
        t3 = [t for t in main if t["trial_index"] == str(expect["focus_trial"])][0]
        check(len(fl) == 1 and fl[0]["trial_index"] == str(expect["focus_trial"]) and float(fl[0]["duration_ms"]) > 1000,
              f"1 saída de foco registrada no trial {expect['focus_trial']} ({fl[0]['duration_ms'] if fl else '-'} ms, {fl[0]['detail'] if fl else ''}, fase {fl[0]['phase'] if fl else ''})")
        check(t3["n_focus_losses"] == "1" and float(t3["focus_lost_ms"]) > 1000, "trials.csv: n_focus_losses/focus_lost_ms no trial certo")
        fs = [e for e in evs if e["event_type"] == "fullscreen_exit"]
        check(len(fs) == 1 and fs[0]["trial_index"] == "5", "1 saída da tela cheia registrada no trial 5")
    if expect.get("feedback"):
        check(fb and fb.get("stop_reason") == "cansaco" and fb.get("feasible_n_stimuli") == "60", "final_feedback.json preenchido")

    probs, n_rep, n = validate_sequence(s["planned_sequence"], s["config"])
    check(not probs and n == s["config"]["n_max_trials"],
          f"sequência planejada ({n} trials, {n_rep} repetições = {n_rep / n:.0%}): lags em [{s['config']['min_lag']},{s['config']['max_lag']}], blocos balanceados {probs[:3] if probs else ''}")
    return {"zip_bytes": zpath.stat().st_size, "audio_kbps": kbps, "n_audio": len(files), "audio_s": total_s}


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "output")
    zips = sorted(out.glob("*.zip"))
    a = [z for z in zips if z.name.startswith("P90_")]
    b = [z for z in zips if z.name.startswith("RECOVERED_P91_")]
    stats = None
    if a:
        stats = verify_main(a[0], {"reason": "fatigue_button", "n_main": 12, "checkpoint": True, "focus_trial": 3, "feedback": True})
    if b:
        sb = verify_main(b[0], {"reason": "incomplete", "n_main": 3})
        print(f"  (sessão interrompida: {b[0].stat().st_size / 1024:.1f} KB)")
    res = json.loads((out / "e2e_result.json").read_text()) if (out / "e2e_result.json").exists() else {}
    if "b" in res:
        check(res["b"]["new_session_disabled_before"], "recuperação: 'Começar uma nova sessão' bloqueado até baixar")
        check(not res["b"]["shows_recovery_again"], "depois de baixada, a sessão não é oferecida de novo")
    if "a" in res:
        check("beatriz.horta@idor.org" in res["a"]["final_text"] and "Downloads" in res["a"]["final_text"], "tela final com e-mail e pasta Downloads")
        check(res["a"]["mailto"].startswith("mailto:beatriz.horta@idor.org?subject=Piloto%20imag"), "link mailto com assunto preenchido")
        check(not res["a"]["fullscreen_after_end"], "saiu da tela cheia no final")
        check(not [e for e in res["a"]["errors"] if "favicon" not in e], f"sem erros de JavaScript na página {res['a']['errors'][:3]}")
    if stats:
        # projeção para 100 trials: 50 em áudio; supõe 30 s de fala por descrição
        proj_audio = 50 * 30 * stats["audio_kbps"] * 1000 / 8
        print(f"\nTamanho do zip da sessão A: {stats['zip_bytes'] / 1024:.1f} KB "
              f"({stats['n_audio']} áudios, {stats['audio_s']:.1f} s, {stats['audio_kbps']:.1f} kbps efetivos)")
        print(f"Projeção para 100 trials (50 descrições em áudio de 30 s): ~{(proj_audio + 150e3) / 1e6:.1f} MB; "
              f"com 60 s por descrição: ~{(2 * proj_audio + 150e3) / 1e6:.1f} MB")
    n_fail = sum(1 for ok, _ in results if not ok)
    print(f"\n{len(results) - n_fail}/{len(results)} verificações OK")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
