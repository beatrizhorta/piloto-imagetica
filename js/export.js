// Monta o .zip de uma sessão a partir do que está salvo no IndexedDB.
(function (global) {
  "use strict";

  const TRIAL_COLUMNS = [
    "participant_id", "session_id", "trial_index", "is_practice", "status", "block",
    "stim_id", "item_id", "stim_type", "file",
    "is_repeat", "first_presentation_trial", "lag", "description_mode",
    "vividness", "vividness_rt_ms", "dynamism", "dynamism_rt_ms",
    "recognized_old", "recognition_correct", "recognition_rt_ms",
    "description_text", "audio_file", "audio_duration_ms", "audio_bytes", "description_rt_ms",
    "stim_onset_ts", "mask_onset_ts", "imagery_onset_ts", "response_onset_ts",
    "stim_duration_measured_ms", "mask_duration_measured_ms", "imagery_duration_measured_ms",
    "stim_onset_source", "video_native_duration_ms", "video_truncated", "mask_frames",
    "trial_start_ts", "trial_end_ts", "trial_duration_ms",
    "n_focus_losses", "focus_lost_ms", "stim_load_ms", "load_error",
  ];
  const CHECKPOINT_COLUMNS = [
    "participant_id", "session_id", "checkpoint_index", "after_trial_index", "n_trials_completed",
    "fatigue", "concentration", "action", "rt_ms", "onset_ts", "response_ts",
  ];
  const EVENT_COLUMNS = [
    "participant_id", "session_id", "event_type", "detail", "trial_index", "is_practice", "phase",
    "start_ts", "end_ts", "duration_ms",
  ];

  function csvCell(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(1);
    if (typeof v === "boolean") return v ? "1" : "0";
    const s = String(v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function toCSV(rows, columns) {
    const lines = [columns.join(",")];
    rows.forEach((r) => lines.push(columns.map((c) => csvCell(r[c])).join(",")));
    return lines.join("\n") + "\n";
  }

  async function buildZip(session_id) {
    const session = await PilotDB.getSession(session_id);
    if (!session) throw new Error("sessão não encontrada: " + session_id);
    const items = await PilotDB.itemsBySession(session_id);
    const of = (kind) => items.filter((i) => i.kind === kind);

    const trials = of("trial").map((i) => i.data)
      .sort((a, b) => (b.is_practice - a.is_practice) || (a.trial_index - b.trial_index));
    const checkpoints = of("checkpoint").map((i) => i.data);
    const events = of("event").map((i) => i.data).sort((a, b) => a.start_ts - b.start_ts);
    const feedback = of("feedback").map((i) => i.data);
    const audios = of("audio");

    const finished = session.status === "finished";
    const completed = trials.filter((t) => !t.is_practice && t.status === "completed");
    const lastTs = Math.max(
      0,
      ...trials.map((t) => t.trial_end_ts || 0),
      ...checkpoints.map((c) => c.response_ts || 0),
      ...events.map((e) => e.end_ts || 0)
    );
    const meta = Object.assign({}, session, {
      termination_reason: finished ? session.termination_reason : "incomplete",
      termination_reason_before_interruption: finished ? null : session.termination_reason || null,
      n_trials_completed: completed.length,
      n_practice_completed: trials.filter((t) => t.is_practice && t.status === "completed").length,
      n_trials_skipped_load_error: trials.filter((t) => t.status === "skipped_load_error").length,
      n_trials_aborted: trials.filter((t) => t.status === "aborted").length,
      session_duration_ms: finished ? session.session_duration_ms : Math.round(lastTs),
      exported_at: new Date().toISOString(),
      n_audio_files: audios.length,
    });
    delete meta.downloaded;
    delete meta.download_count;

    const folder = `${session.pid}_${session_id}`;
    const zip = new JSZip();
    const dir = zip.folder(folder);
    dir.file("session.json", JSON.stringify(meta, null, 2));
    dir.file("trials.csv", toCSV(trials, TRIAL_COLUMNS));
    dir.file("checkpoints.csv", toCSV(checkpoints, CHECKPOINT_COLUMNS));
    dir.file("events.csv", toCSV(events, EVENT_COLUMNS));
    dir.file("final_feedback.json", JSON.stringify(feedback.length ? feedback[feedback.length - 1] : null, null, 2));
    const adir = dir.folder("audio");
    audios.forEach((a) => adir.file(a.data.filename, a.blob, { compression: "STORE" }));

    // áudio já é comprimido (Opus): guardar sem recomprimir; textos com DEFLATE
    const blob = await zip.generateAsync({ type: "blob", compression: "DEFLATE", compressionOptions: { level: 6 } });
    return { blob, filename: `${folder}.zip`, n_trials: completed.length };
  }

  function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 60000);
  }

  // Gera o zip, baixa e marca a sessão como baixada (os dados continuam no navegador).
  async function exportSession(session_id) {
    const out = await buildZip(session_id);
    triggerDownload(out.blob, out.filename);
    const s = await PilotDB.getSession(session_id);
    await PilotDB.updateSession(session_id, {
      downloaded: true,
      download_count: ((s && s.download_count) || 0) + 1,
      last_download_at: new Date().toISOString(),
    });
    return out;
  }

  global.PilotExport = { buildZip, exportSession, triggerDownload, toCSV, TRIAL_COLUMNS };
})(window);
