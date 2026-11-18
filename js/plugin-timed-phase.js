// Plugin jsPsych para as etapas cronometradas do trial (fixação, estímulo, máscara,
// imaginação) e para a tela de "carregando".
//
// O onset é medido no primeiro frame em que a etapa aparece (requestAnimationFrame;
// para vídeos, requestVideoFrameCallback do primeiro frame do vídeo). O fim é agendado
// a partir desse onset, de modo que onset(etapa seguinte) - onset(etapa) ≈ duração pedida.
//
// Na etapa de imaginação (kind = "imagery") a descrição em voz alta acontece ao mesmo
// tempo: com record = true, o MediaRecorder é ligado antes de a tela aparecer, um bipe
// (beep_start) toca no onset, outro (beep_end) toca no fim, e o áudio da janela inteira
// volta em data.audio_blob.
var jsPsychTimedPhase = (function (jspsych) {
  "use strict";

  const info = {
    name: "timed-phase",
    parameters: {
      /** fixation | stimulus | mask | imagery | loading */
      kind: { type: jspsych.ParameterType.STRING, default: "fixation" },
      /** duração em ms (ignorada em loading) */
      duration: { type: jspsych.ParameterType.INT, default: 1000 },
      /** HTML exibido (fixation, imagery, loading) */
      html: { type: jspsych.ParameterType.HTML_STRING, default: "" },
      /** lado do quadrado do estímulo/máscara, em px */
      size: { type: jspsych.ParameterType.INT, default: 512 },
      /** elemento <img>/<video> já carregado (kind = stimulus) */
      element: { type: jspsych.ParameterType.OBJECT, default: null },
      /** função que devolve uma Promise; a etapa só começa (ou, em loading, só termina) quando ela resolve */
      wait_for: { type: jspsych.ParameterType.FUNCTION, default: null },
      /** chamado com o onset (ms, performance.now) assim que ele é medido */
      on_onset: { type: jspsych.ParameterType.FUNCTION, default: null },
      /** grava áudio durante a etapa (kind = imagery) */
      record: { type: jspsych.ParameterType.BOOL, default: false },
      /** função que devolve o MediaRecorder a usar */
      recorder: { type: jspsych.ParameterType.FUNCTION, default: null },
      /** mimeType do Blob montado a partir dos chunks */
      audio_mime: { type: jspsych.ParameterType.STRING, default: "audio/webm" },
      /** bipes de abertura e de fechamento: { freq, ms, volume, type } */
      beep_start: { type: jspsych.ParameterType.OBJECT, default: null },
      beep_end: { type: jspsych.ParameterType.OBJECT, default: null },
    },
  };

  function now() {
    return performance.now();
  }

  function beep(spec) {
    return spec && global_beep() ? global_beep().play(spec) : null;
  }

  function global_beep() {
    return window.PilotBeep || null;
  }

  class TimedPhasePlugin {
    constructor(jsPsych) {
      this.jsPsych = jsPsych;
    }

    trial(display_element, trial) {
      let done = false;
      const timers = [];
      const data = { phase: trial.kind, onset_ts: null, onset_source: null, end_request_ts: null };

      // ---------- gravação (kind = imagery) ----------
      const rec = trial.record && trial.recorder ? trial.recorder() : null;
      let chunks = [];
      let blobResolve = null;
      const onData = (e) => {
        if (e.data && e.data.size) chunks.push(e.data);
      };
      const onStop = () => {
        const blob = new Blob(chunks, { type: trial.audio_mime });
        chunks = [];
        const r = blobResolve;
        blobResolve = null;
        if (r) r(blob);
      };
      const detachRec = () => {
        if (!rec) return;
        rec.removeEventListener("dataavailable", onData);
        rec.removeEventListener("stop", onStop);
      };
      // Para o gravador e devolve o Blob (ou null se não havia gravação em curso).
      const stopRec = () => {
        if (!rec) return Promise.resolve(null);
        if (rec.state === "inactive") {
          detachRec();
          return Promise.resolve(null);
        }
        const p = new Promise((resolve) => (blobResolve = resolve));
        try {
          rec.stop();
        } catch (e) {
          data.record_error = String(e);
          blobResolve = null;
          detachRec();
          return Promise.resolve(null);
        }
        // rede de segurança: se o evento "stop" não vier, segue sem áudio
        const timeout = new Promise((resolve) => setTimeout(() => resolve(null), 2000));
        return Promise.race([p, timeout]).then((b) => {
          detachRec();
          return b;
        });
      };

      // keep_audio = true devolve uma Promise com o Blob; false descarta a gravação.
      const stopAll = (keep_audio) => {
        done = true;
        if (TimedPhasePlugin._cancel === cancel) TimedPhasePlugin._cancel = null;
        timers.forEach((t) => clearTimeout(t));
        if (trial.kind === "stimulus" && trial.element && trial.element.tagName === "VIDEO") {
          try { trial.element.pause(); } catch (e) { /* nada */ }
        }
        if (!keep_audio) {
          stopRec();
          return Promise.resolve(null);
        }
        return stopRec();
      };
      // usado quando a etapa é interrompida de fora (botão de cansaço): para tudo sem chamar finishTrial
      const cancel = () => stopAll(false);
      TimedPhasePlugin._cancel = cancel;

      const finish = (extra) => {
        if (done) return;
        data.end_request_ts = now();
        if (trial.beep_end) data.beep_end_ts = beep(trial.beep_end);
        if (!rec) {
          stopAll(false);
          Object.assign(data, extra || {});
          display_element.innerHTML = "";
          this.jsPsych.finishTrial(data);
          return;
        }
        // com gravação, a tela só é limpa depois que o Blob fica pronto (alguns ms),
        // para o bipe de fim tocar com a tela de imaginação ainda no lugar
        const pending = stopAll(true);
        Object.assign(data, extra || {});
        pending.then((blob) => {
          data.recorder_stop_ts = now();
          if (blob) {
            data.audio_blob = blob;
            data.audio_bytes = blob.size;
          }
          display_element.innerHTML = "";
          this.jsPsych.finishTrial(data);
        });
      };

      // agenda o fim para onset + duration. A etapa seguinte aparece no frame *depois* daquele
      // em que esta termina, então terminamos no último frame antes do alvo (estimando o
      // intervalo entre frames); por timeout se a aba estiver oculta.
      const scheduleEnd = (onset) => {
        const target = onset + trial.duration;
        let last = null;
        let frame = 1000 / 60;
        const frameEnd = () => {
          if (done) return;
          if (document.hidden) { finish(); return; }
          requestAnimationFrame((ts) => {
            if (done) return;
            if (last !== null && ts - last > 4 && ts - last < 50) frame = ts - last;
            last = ts;
            if (ts + frame >= target - frame / 2) finish();
            else frameEnd();
          });
        };
        timers.push(setTimeout(frameEnd, Math.max(0, target - now() - 30)));
        // rede de segurança caso requestAnimationFrame não dispare (aba em segundo plano)
        timers.push(setTimeout(() => finish({ ended_by_fallback: true }), Math.max(0, target - now() + 250)));
      };

      const setOnset = (ts, source) => {
        if (data.onset_ts !== null || done) return;
        data.onset_ts = ts;
        data.onset_source = source;
        if (trial.kind === "imagery" && trial.beep_start) data.beep_start_ts = beep(trial.beep_start);
        if (trial.on_onset) trial.on_onset(ts, trial.kind);
        if (trial.kind !== "loading") scheduleEnd(ts);
      };

      const onFirstFrame = (source) => {
        if (document.hidden) { setOnset(now(), "hidden"); return; }
        requestAnimationFrame((ts) => setOnset(ts, source));
        timers.push(setTimeout(() => setOnset(now(), "timeout"), 200));
      };

      const box = (inner) =>
        `<div class="stim-box" style="width:${trial.size}px;height:${trial.size}px">${inner}</div>`;

      // Liga o gravador e só mostra a tela de imaginação quando ele confirma o início,
      // para que a janela de 20 s esteja inteira dentro do arquivo de áudio.
      const startImagery = () => {
        const show = () => {
          if (done) return;
          display_element.innerHTML = box(`<div class="imagery-text">${trial.html}</div>`);
          onFirstFrame("raf");
        };
        if (!rec) { show(); return; }
        display_element.innerHTML = box("");
        chunks = [];
        rec.addEventListener("dataavailable", onData);
        rec.addEventListener("stop", onStop);
        const onStart = () => {
          if (data.recorder_start_ts === undefined || data.recorder_start_ts === null) {
            data.recorder_start_ts = now();
          }
          show();
        };
        rec.addEventListener("start", onStart, { once: true });
        try {
          if (rec.state !== "inactive") throw new Error("gravador ocupado: " + rec.state);
          rec.start();
        } catch (e) {
          data.record_error = String((e && e.message) || e);
          rec.removeEventListener("start", onStart);
          detachRec();
          show();
          return;
        }
        // se o evento "start" não vier, mostra a tela de qualquer jeito
        timers.push(setTimeout(() => {
          if (!done && data.onset_ts === null) {
            data.record_start_timeout = 1;
            rec.removeEventListener("start", onStart);
            show();
          }
        }, 500));
      };

      const start = () => {
        if (done) return;
        if (trial.kind === "fixation") {
          display_element.innerHTML = box('<div class="fixation">+</div>');
          onFirstFrame("raf");
        } else if (trial.kind === "imagery") {
          startImagery();
        } else if (trial.kind === "mask") {
          this.runMask(display_element, trial, () => done, setOnset, (frames) => (data.mask_frames = frames));
        } else if (trial.kind === "stimulus") {
          display_element.innerHTML = box("");
          const el = trial.element;
          const holder = display_element.querySelector(".stim-box");
          el.style.width = trial.size + "px";
          el.style.height = trial.size + "px";
          holder.appendChild(el);
          if (el.tagName === "VIDEO") {
            el.currentTime = 0;
            const useVFC = typeof el.requestVideoFrameCallback === "function";
            if (useVFC) {
              // usa o "now" do callback (mesma base de tempo do requestAnimationFrame); se vier
              // incoerente com performance.now(), cai para performance.now()
              el.requestVideoFrameCallback((t) => {
                const p = now();
                setOnset(Math.abs(t - p) < 200 ? t : p, Math.abs(t - p) < 200 ? "video_frame" : "video_frame_now");
              });
            }
            el.play().then(
              () => { if (!useVFC) onFirstFrame("raf_after_play"); },
              (err) => { data.play_error = String(err); onFirstFrame("raf_play_failed"); }
            );
            timers.push(setTimeout(() => setOnset(now(), "timeout"), 500));
          } else {
            onFirstFrame("raf");
          }
        } else if (trial.kind === "loading") {
          const t0 = now();
          data.onset_ts = t0;
          let shown = false;
          timers.push(setTimeout(() => {
            if (!done) { display_element.innerHTML = trial.html; shown = true; }
          }, 80));
          Promise.resolve(trial.wait_for ? trial.wait_for() : null).then((res) =>
            finish({ wait_ms: Math.round(now() - t0), loading_screen_shown: shown, result: res === undefined ? null : res })
          );
        }
      };

      if (trial.kind !== "loading" && trial.wait_for) {
        display_element.innerHTML = "";
        Promise.resolve(trial.wait_for()).then(start);
      } else {
        start();
      }
    }

    // Ruído dinâmico: pixels cinza aleatórios renovados a cada frame, no tamanho do estímulo.
    runMask(display_element, trial, isDone, setOnset, setFrames) {
      const n = trial.size;
      display_element.innerHTML = `<div class="stim-box" style="width:${n}px;height:${n}px"><canvas width="${n}" height="${n}"></canvas></div>`;
      const canvas = display_element.querySelector("canvas");
      const ctx = canvas.getContext("2d");
      const img = ctx.createImageData(n, n);
      const px = new Uint32Array(img.data.buffer);
      let s = (Math.random() * 0xffffffff) >>> 0 || 1;
      let frames = 0;
      const draw = (ts) => {
        if (isDone() || !canvas.isConnected) { setFrames(frames); return; }
        for (let i = 0; i < px.length; i++) {
          s ^= s << 13; s ^= s >>> 17; s ^= s << 5;
          const v = s & 255;
          px[i] = 0xff000000 | (v << 16) | (v << 8) | v;
        }
        ctx.putImageData(img, 0, 0);
        frames++;
        setFrames(frames);
        if (frames === 1) setOnset(ts, "raf");
        requestAnimationFrame(draw);
      };
      if (document.hidden) setOnset(performance.now(), "hidden");
      requestAnimationFrame(draw);
    }
  }
  TimedPhasePlugin.info = info;
  TimedPhasePlugin._cancel = null;
  TimedPhasePlugin.cancelCurrent = function () {
    if (TimedPhasePlugin._cancel) TimedPhasePlugin._cancel();
  };
  return TimedPhasePlugin;
})(jsPsychModule);
