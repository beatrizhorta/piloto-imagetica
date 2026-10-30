"""Resume os .zip recebidos dos participantes do pré-piloto.

Uso:
    python summarize.py pasta_com_os_zips/            # grava trials_all.csv e sessions_all.csv nessa pasta
    python summarize.py pasta_com_os_zips/ --out resultados/

Só usa a biblioteca padrão do Python (3.8+). Procura .zip recursivamente. Se a mesma sessão
aparecer em mais de um .zip (por exemplo, "Baixar de novo"), fica a versão com mais trials.
"""

import argparse
import csv
import io
import json
import statistics
import sys
import zipfile
from pathlib import Path


def read_zip(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()

        def find(suffix):
            for n in names:
                if n.endswith("/" + suffix) or n == suffix:
                    return n
            return None

        def csv_rows(suffix):
            n = find(suffix)
            if not n:
                return []
            return list(csv.DictReader(io.StringIO(z.read(n).decode("utf-8"))))

        sjson = find("session.json")
        if not sjson:
            raise ValueError("session.json não encontrado")
        session = json.loads(z.read(sjson).decode("utf-8"))
        fb_name = find("final_feedback.json")
        feedback = json.loads(z.read(fb_name).decode("utf-8")) if fb_name else None
        audio = [n for n in names if "/audio/" in n and not n.endswith("/")]
        return {
            "zip": str(path),
            "session": session,
            "trials": csv_rows("trials.csv"),
            "checkpoints": csv_rows("checkpoints.csv"),
            "events": csv_rows("events.csv"),
            "feedback": feedback,
            "n_audio_files": len(audio),
        }


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 2) if xs else None


def summarize(d):
    s = d["session"]
    main = [t for t in d["trials"] if t.get("is_practice") == "0" and t.get("status") == "completed"]
    by_type = {ty: [t for t in main if t.get("stim_type") == ty] for ty in ("image", "video")}
    cps = sorted(d["checkpoints"], key=lambda c: num(c.get("checkpoint_index")) or 0)
    focus = [e for e in d["events"] if e.get("event_type") == "focus_loss"]
    fs = [e for e in d["events"] if e.get("event_type") == "fullscreen_exit"]
    rec = [num(t.get("recognition_correct")) for t in main]
    reps = [t for t in main if t.get("is_repeat") == "1"]
    news = [t for t in main if t.get("is_repeat") == "0"]
    fb = d["feedback"] or {}
    return {
        "participant_id": s.get("pid"),
        "session_id": s.get("session_id"),
        "started_at": s.get("started_at"),
        "termination_reason": s.get("termination_reason"),
        "termination_reason_before_interruption": s.get("termination_reason_before_interruption"),
        "n_trials_completed": len(main),
        "n_trials_aborted": sum(1 for t in d["trials"] if t.get("status") == "aborted"),
        "n_trials_skipped_load_error": sum(1 for t in d["trials"] if t.get("status") == "skipped_load_error"),
        "session_duration_min": round((num(s.get("session_duration_ms")) or 0) / 60000, 2),
        "mean_trial_duration_s": mean([(num(t.get("trial_duration_ms")) or 0) / 1000 for t in main]),
        "n_checkpoints": len(cps),
        "fatigue_curve": " ".join(c.get("fatigue", "") for c in cps),
        "concentration_curve": " ".join(c.get("concentration", "") for c in cps),
        "vividness_image": mean([num(t.get("vividness")) for t in by_type["image"]]),
        "vividness_video": mean([num(t.get("vividness")) for t in by_type["video"]]),
        "dynamism_image": mean([num(t.get("dynamism")) for t in by_type["image"]]),
        "dynamism_video": mean([num(t.get("dynamism")) for t in by_type["video"]]),
        "recognition_accuracy": mean(rec),
        "hit_rate": mean([num(t.get("recognized_old")) for t in reps]),
        "false_alarm_rate": mean([num(t.get("recognized_old")) for t in news]),
        "n_focus_losses": len(focus),
        "focus_lost_s": round(sum(num(e.get("duration_ms")) or 0 for e in focus) / 1000, 1),
        "n_fullscreen_exits": len(fs),
        "n_audio_files": d["n_audio_files"],
        "stop_reason_reported": fb.get("stop_reason"),
        "feasible_n_stimuli_reported": fb.get("feasible_n_stimuli"),
        "zip": d["zip"],
    }


def write_csv(path, rows):
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--out", default=None, help="pasta de saída (padrão: a mesma dos zips)")
    args = ap.parse_args()
    folder = Path(args.folder)
    out = Path(args.out) if args.out else folder
    out.mkdir(parents=True, exist_ok=True)

    sessions = {}
    for z in sorted(folder.rglob("*.zip")):
        try:
            d = read_zip(z)
        except Exception as e:  # noqa: BLE001
            print(f"!! ignorando {z}: {e}", file=sys.stderr)
            continue
        sid = d["session"].get("session_id")
        if sid in sessions and len(sessions[sid]["trials"]) >= len(d["trials"]):
            continue
        sessions[sid] = d
    if not sessions:
        sys.exit(f"nenhum .zip válido em {folder}")

    data = sorted(sessions.values(), key=lambda d: (d["session"].get("pid") or "", d["session"].get("started_at") or ""))
    trials_all = [t for d in data for t in d["trials"]]
    summary = [summarize(d) for d in data]
    write_csv(out / "trials_all.csv", trials_all)
    write_csv(out / "sessions_all.csv", summary)

    for r in summary:
        r = {k: ("-" if v is None else v) for k, v in r.items()}
        print(f"\n=== {r['participant_id']}  (sessão {r['session_id']}, {r['started_at']})")
        print(f"  trials completados: {r['n_trials_completed']}   motivo do término: {r['termination_reason']}"
              + (f" (antes da interrupção: {r['termination_reason_before_interruption']})" if r["termination_reason_before_interruption"] != "-" else ""))
        print(f"  duração da sessão: {r['session_duration_min']} min   tempo médio por trial: {r['mean_trial_duration_s']} s")
        print(f"  cansaço nos checkpoints:      {r['fatigue_curve'] or '-'}")
        print(f"  concentração nos checkpoints: {r['concentration_curve'] or '-'}")
        print(f"  vividez média   imagem {r['vividness_image']}  vídeo {r['vividness_video']}")
        print(f"  dinamismo médio imagem {r['dynamism_image']}  vídeo {r['dynamism_video']}")
        print(f"  reconhecimento: acurácia {r['recognition_accuracy']}  (acertos em repetidos {r['hit_rate']}, falsos alarmes {r['false_alarm_rate']})")
        print(f"  saídas de foco: {r['n_focus_losses']} ({r['focus_lost_s']} s)   saídas da tela cheia: {r['n_fullscreen_exits']}")
        if r["stop_reason_reported"] != "-" or r["feasible_n_stimuli_reported"] != "-":
            print(f"  questionário: parou por '{r['stop_reason_reported']}', acha viável {r['feasible_n_stimuli_reported']} estímulos")
    print(f"\n{len(summary)} sessão(ões). Gravados: {out / 'trials_all.csv'} e {out / 'sessions_all.csv'}")


if __name__ == "__main__":
    main()
