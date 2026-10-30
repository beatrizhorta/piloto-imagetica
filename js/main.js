// Fluxo da sessão do pré-piloto.
// Timestamps (*_ts) são ms desde o início da sessão (clique em Começar na tela de boas-vindas), medidos com
// performance.now(); o instante absoluto do início está em session.json (t0_epoch_ms).
(function () {
  "use strict";

  const APP_VERSION = "0.7.0";
  const params = new URLSearchParams(location.search);
  const $ = (sel, root) => (root || document).querySelector(sel);

  let cfg, manifest, jsPsych, loader, monitor;
  const state = {
    pid: null,
    session_id: null,
    t0: null,
    plan: null,
    practice: [],
    cur: null, // trial em andamento
    nCompleted: 0,
    stop: false,
    terminationReason: null,
    saveQueue: Promise.resolve(),
    saveErrors: [],
    keyMap: null,
    modalOpen: false,
    checkpointIndex: 0,
    cpAction: "continue",
    recorder: null,
    micInfo: null,
    checks: null,
    micTests: [],
    phaseOnset: null,
  };

  // ---------- utilidades ----------

  const rel = (t) => (t === null || t === undefined ? null : Math.round((t - state.t0) * 10) / 10);
  const now = () => rel(performance.now());

  function enqueue(fn) {
    state.saveQueue = state.saveQueue.then(fn).catch((err) => {
      console.error("erro ao salvar", err);
      state.saveErrors.push(String((err && err.message) || err));
      showSaveError();
    });
    return state.saveQueue;
  }

  function showSaveError() {
    const el = $("#save-error");
    if (el) el.hidden = false;
  }

  function makeSessionId() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    const rnd = Array.from(crypto.getRandomValues(new Uint8Array(2))).map((b) => b.toString(16).padStart(2, "0")).join("");
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${rnd}`;
  }

  function fill(template) {
    return String(template).replace(/\{pid\}/g, state.pid).replace(/\{email\}/g, cfg.data_email);
  }

  function showGate(html) {
    const g = $("#gate");
    g.innerHTML = html;
    g.hidden = false;
  }

  // ---------- barra superior (progresso + botão de cansaço) ----------

  function setChrome({ progress, fatigue }) {
    $("#topbar").hidden = !(progress || fatigue);
    $("#progress").hidden = !progress;
    $("#fatigue-btn").hidden = !fatigue;
  }

  function updateProgress(practice) {
    $("#progress").textContent = practice ? "Prática" : `Concluídos: ${state.nCompleted}`;
  }

  function setPhase(phase) {
    if (monitor) monitor.setContext({ phase });
  }

  // ---------- atalhos de teclado ----------

  function isTyping() {
    const a = document.activeElement;
    return !!a && (a.tagName === "TEXTAREA" || (a.tagName === "INPUT" && /^(text|number|email|search)$/.test(a.type)) || a.isContentEditable);
  }

  document.addEventListener("keydown", (e) => {
    if (!state.keyMap || state.modalOpen || isTyping() || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
    const idx = state.keyMap[e.key.toLowerCase()];
    if (idx === undefined) return;
    const btn = $(`#jspsych-html-button-response-button-${idx} button`);
    if (btn && !btn.disabled) {
      e.preventDefault();
      btn.click();
    }
  });

  // ---------- modal / overlays ----------

  function openModal(html, buttons) {
    return new Promise((resolve) => {
      state.modalOpen = true;
      const m = $("#modal");
      $("#modal-body").innerHTML = html;
      const bar = $("#modal-buttons");
      bar.innerHTML = "";
      buttons.forEach(({ label, value, cls }) => {
        const b = document.createElement("button");
        b.className = "jspsych-btn " + (cls || "");
        b.textContent = label;
        b.addEventListener("click", () => {
          m.hidden = true;
          state.modalOpen = false;
          resolve(value);
        });
        bar.appendChild(b);
      });
      m.hidden = false;
    });
  }

  async function onFatigueClick() {
    const yes = await openModal(cfg.texts.fatigue_confirm, [
      { label: "Voltar para o estudo", value: false, cls: "btn-secondary" },
      { label: "Sim, encerrar", value: true },
    ]);
    if (!yes || state.stop) return;
    stopSession("fatigue_button");
  }

  function stopSession(reason) {
    state.stop = true;
    state.terminationReason = reason;
    const sid = state.session_id;
    enqueue(() => PilotDB.updateSession(sid, { termination_reason: reason, stop_requested_ts: now() }));
    abortCurrentTrial();
    jsPsychTimedPhase.cancelCurrent();
    const rec = state.recorder;
    if (rec && rec.state === "recording") {
      try { rec.stop(); } catch (e) { /* nada */ }
    }
    state.keyMap = null;
    jsPsych.endCurrentTimeline();
    jsPsych.finishTrial({ aborted: true });
  }

  // ---------- registro dos trials ----------

  function beginTrial(plan, isPractice) {
    const inf = loader.get(plan.stim_id) || {};
    state.cur = {
      participant_id: state.pid,
      session_id: state.session_id,
      trial_index: plan.trial_index,
      is_practice: isPractice ? 1 : 0,
      status: "in_progress",
      block: plan.block,
      stim_id: plan.stim_id,
      item_id: plan.item_id,
      stim_type: plan.stim_type,
      file: plan.file,
      is_repeat: plan.is_repeat,
      first_presentation_trial: plan.first_presentation_trial,
      lag: plan.lag,
      description_mode: plan.description_mode,
      vividness: null, vividness_rt_ms: null,
      dynamism: null, dynamism_rt_ms: null,
      recognized_old: null, recognition_correct: null, recognition_rt_ms: null,
      description_text: null, audio_file: null, audio_duration_ms: null, audio_bytes: null, description_rt_ms: null,
      stim_onset_ts: null, mask_onset_ts: null, imagery_onset_ts: null, response_onset_ts: null,
      stim_duration_measured_ms: null, mask_duration_measured_ms: null, imagery_duration_measured_ms: null,
      stim_onset_source: null,
      video_native_duration_ms: inf.native_duration_ms || null,
      video_truncated: plan.stim_type === "video" && inf.native_duration_ms ? (inf.native_duration_ms > cfg.stim_duration_ms + 50 ? 1 : 0) : null,
      mask_frames: null,
      trial_start_ts: now(), trial_end_ts: null, trial_duration_ms: null,
      n_focus_losses: 0, focus_lost_ms: 0,
      stim_load_ms: inf.load_ms === undefined ? null : inf.load_ms,
      load_error: null,
    };
    monitor.setContext({ trial_index: plan.trial_index, is_practice: isPractice ? 1 : 0 });
    updateProgress(isPractice);
  }

  function computeDurations(c) {
    const d = (a, b) => (a !== null && b !== null ? Math.round((b - a) * 10) / 10 : null);
    c.stim_duration_measured_ms = d(c.stim_onset_ts, c.mask_onset_ts);
    c.mask_duration_measured_ms = d(c.mask_onset_ts, c.imagery_onset_ts);
    c.imagery_duration_measured_ms = d(c.imagery_onset_ts, c.response_onset_ts);
  }

  function saveTrial(c, audioBlob) {
    const sid = state.session_id;
    const n = state.nCompleted;
    return enqueue(async () => {
      if (audioBlob) {
        c.audio_bytes = audioBlob.size;
        c.audio_duration_ms = await audioDurationMs(audioBlob);
        await PilotDB.addItem(sid, "audio", { filename: c.audio_file, trial_index: c.trial_index, is_practice: c.is_practice }, audioBlob);
      }
      await PilotDB.addItem(sid, "trial", c);
      await PilotDB.updateSession(sid, { n_trials_completed: n, last_activity_ts: c.trial_end_ts, last_activity_at: new Date().toISOString() });
    });
  }

  function completeTrial(audioBlob) {
    const c = state.cur;
    if (!c) return;
    state.cur = null;
    c.status = "completed";
    c.trial_end_ts = now();
    c.trial_duration_ms = Math.round((c.trial_end_ts - c.trial_start_ts) * 10) / 10;
    computeDurations(c);
    if (!c.is_practice) state.nCompleted++;
    updateProgress(c.is_practice);
    saveTrial(c, audioBlob);
  }

  function abortCurrentTrial() {
    const c = state.cur;
    if (!c) return;
    state.cur = null;
    c.status = "aborted";
    c.trial_end_ts = now();
    c.trial_duration_ms = Math.round((c.trial_end_ts - c.trial_start_ts) * 10) / 10;
    computeDurations(c);
    saveTrial(c, null);
  }

  function recordSkipped(plan, isPractice, inf) {
    const c = {
      participant_id: state.pid, session_id: state.session_id, trial_index: plan.trial_index,
      is_practice: isPractice ? 1 : 0, status: "skipped_load_error", block: plan.block,
      stim_id: plan.stim_id, item_id: plan.item_id, stim_type: plan.stim_type, file: plan.file,
      is_repeat: plan.is_repeat, first_presentation_trial: plan.first_presentation_trial, lag: plan.lag,
      description_mode: plan.description_mode, trial_start_ts: now(), trial_end_ts: now(),
      stim_load_ms: inf ? inf.load_ms : null, load_error: inf ? inf.error : "não carregado",
      n_focus_losses: 0, focus_lost_ms: 0,
    };
    saveTrial(c, null);
  }

  let decodeCtx = null;
  async function audioDurationMs(blob) {
    try {
      decodeCtx = decodeCtx || new OfflineAudioContext(1, 48000, 48000);
      const buf = await decodeCtx.decodeAudioData(await blob.arrayBuffer());
      return Math.round(buf.duration * 1000);
    } catch (e) {
      return null;
    }
  }

  function base64ToBlob(b64, type) {
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new Blob([arr], { type });
  }

  // ---------- telas de resposta ----------

  function markResponseOnset() {
    requestAnimationFrame((ts) => {
      if (state.cur && state.cur.response_onset_ts === null) state.cur.response_onset_ts = rel(ts);
    });
  }

  function scaleNode(field, question, anchors) {
    return {
      type: jsPsychHtmlButtonResponse,
      stimulus: `<div class="question">${question}</div>`,
      choices: anchors.map((a, i) => `<span class="scale-num">${i + 1}</span><span class="scale-anchor">${a}</span>`),
      button_html: '<button class="jspsych-btn scale-btn">%choice%</button>',
      margin_horizontal: "5px",
      margin_vertical: "4px",
      on_start: () => {
        setPhase(field);
        setChrome({ progress: true, fatigue: true });
      },
      on_load: () => {
        if (field === "vividness") markResponseOnset();
        state.keyMap = { 1: 0, 2: 1, 3: 2, 4: 3, 5: 4 };
      },
      on_finish: (d) => {
        state.keyMap = null;
        if (d.aborted || !state.cur || d.response === null) return;
        state.cur[field] = d.response + 1;
        state.cur[field + "_rt_ms"] = d.rt;
      },
    };
  }

  function recognitionNode() {
    return {
      type: jsPsychHtmlButtonResponse,
      stimulus: `<div class="question">${cfg.recognition_question}</div>`,
      choices: ["Sim <kbd>S</kbd>", "Não <kbd>N</kbd>"],
      button_html: '<button class="jspsych-btn yn-btn">%choice%</button>',
      margin_horizontal: "12px",
      on_start: () => {
        setPhase("recognition");
        setChrome({ progress: true, fatigue: true });
      },
      on_load: () => {
        state.keyMap = { s: 0, n: 1 };
      },
      on_finish: (d) => {
        state.keyMap = null;
        if (d.aborted || !state.cur || d.response === null) return;
        const old = d.response === 0 ? 1 : 0;
        state.cur.recognized_old = old;
        state.cur.recognition_correct = old === state.cur.is_repeat ? 1 : 0;
        state.cur.recognition_rt_ms = d.rt;
      },
    };
  }

  function writtenNode() {
    return {
      type: jsPsychSurveyHtmlForm,
      preamble: `<div class="question">${cfg.description_written_prompt}</div>`,
      html: '<textarea id="desc-text" name="description" rows="6" required></textarea>',
      button_label: "Continuar",
      autofocus: "desc-text",
      on_start: () => {
        setPhase("description");
        setChrome({ progress: true, fatigue: true });
      },
      on_load: () => {
        state.phaseOnset = performance.now();
        const ta = $("#desc-text");
        const check = () => ta.setCustomValidity(ta.value.trim() ? "" : "Escreva uma descrição antes de continuar.");
        ta.addEventListener("input", check);
        check();
      },
      on_finish: (d) => {
        if (d.aborted || !state.cur) return;
        state.cur.description_text = (d.response.description || "").trim();
        state.cur.description_rt_ms = d.rt;
        completeTrial(null);
      },
    };
  }

  function audioNode() {
    return {
      type: jsPsychHtmlAudioResponse,
      stimulus: `<div class="question">${cfg.description_audio_prompt}</div><div class="rec-indicator"><span class="rec-dot"></span>Gravando…</div>`,
      recording_duration: cfg.audio_max_ms,
      show_done_button: true,
      done_button_label: "Parar gravação",
      allow_playback: true,
      record_again_button_label: "Gravar de novo",
      accept_button_label: "Continuar",
      on_start: () => {
        setPhase("description");
        setChrome({ progress: true, fatigue: true });
      },
      on_load: () => {
        state.phaseOnset = performance.now();
      },
      on_finish: (d) => {
        if (d.aborted || !state.cur) return;
        const c = state.cur;
        c.description_rt_ms = Math.round(performance.now() - state.phaseOnset);
        let blob = null;
        if (d.response) {
          blob = base64ToBlob(d.response, "audio/webm");
          d.response = null; // não manter o áudio duplicado na memória do jsPsych
          c.audio_file = c.is_practice
            ? `${state.pid}_pt${String(c.trial_index).padStart(2, "0")}_${c.stim_id}.webm`
            : `${state.pid}_t${String(c.trial_index).padStart(3, "0")}_${c.stim_id}.webm`;
        }
        completeTrial(blob);
      },
    };
  }

  // ---------- trial completo ----------

  // O plano (sequência) só existe depois da tela de boas-vindas, mas o jsPsych 7 precisa da
  // estrutura da timeline desde o início; por isso o nó lê o plano sob demanda (getPlan).
  function trialNode(getPlan, isPractice) {
    const size = cfg.stim_size_px;
    const mode = () => (getPlan() || {}).description_mode;
    const phases = [
      {
        type: jsPsychTimedPhase,
        kind: "fixation",
        duration: cfg.fixation_ms,
        size,
        wait_for: () => monitor.waitForFullscreen(),
        on_start: () => {
          beginTrial(getPlan(), isPractice);
          setPhase("fixation");
          setChrome({ progress: false, fatigue: false });
        },
      },
      {
        type: jsPsychTimedPhase,
        kind: "stimulus",
        duration: cfg.stim_duration_ms,
        size,
        element: () => loader.get(getPlan().stim_id).el,
        on_start: () => setPhase("stimulus"),
        on_finish: (d) => {
          if (!state.cur || d.aborted) return;
          state.cur.stim_onset_ts = rel(d.onset_ts);
          state.cur.stim_onset_source = d.onset_source;
        },
      },
      {
        type: jsPsychTimedPhase,
        kind: "mask",
        duration: cfg.mask_duration_ms,
        size,
        on_start: () => setPhase("mask"),
        on_finish: (d) => {
          if (!state.cur || d.aborted) return;
          state.cur.mask_onset_ts = rel(d.onset_ts);
          state.cur.mask_frames = d.mask_frames;
        },
      },
      {
        type: jsPsychTimedPhase,
        kind: "imagery",
        duration: cfg.imagery_duration_ms,
        size,
        html: cfg.texts.imagery_command,
        on_start: () => setPhase("imagery"),
        on_finish: (d) => {
          if (!state.cur || d.aborted) return;
          state.cur.imagery_onset_ts = rel(d.onset_ts);
        },
      },
      scaleNode("vividness", cfg.vividness_question, cfg.vividness_anchors),
      scaleNode("dynamism", cfg.dynamism_question, cfg.dynamism_anchors),
      recognitionNode(),
      { timeline: [writtenNode()], conditional_function: () => !state.stop && mode() !== "audio" },
      { timeline: [audioNode()], conditional_function: () => !state.stop && mode() === "audio" },
    ];
    return {
      timeline: phases,
      conditional_function: () => {
        if (state.stop) return false;
        const plan = getPlan();
        if (!plan) return false;
        const inf = loader.get(plan.stim_id);
        if (!inf || !inf.ok) {
          recordSkipped(plan, isPractice, inf);
          return false;
        }
        return true;
      },
    };
  }

  // ---------- carregamento por mini-bloco ----------

  const blockLoads = new Map();
  function stimsOfBlock(b) {
    return state.plan.trials.filter((t) => t.block === b).map((t) => manifestById[t.stim_id]);
  }
  let manifestById = {};

  function ensureBlock(b) {
    if (b < 1 || b > nBlocks()) return Promise.resolve();
    if (!blockLoads.has(b)) {
      const t0 = performance.now();
      const p = loader.loadAll(stimsOfBlock(b)).then((infos) => {
        const failed = infos.filter((i) => !i.ok).map((i) => i.stim_id);
        const sid = state.session_id;
        enqueue(() => PilotDB.updateSession(sid, { preload_report: loader.report() }));
        return { block: b, ms: Math.round(performance.now() - t0), failed };
      });
      blockLoads.set(b, p);
    }
    return blockLoads.get(b);
  }

  function nBlocks() {
    return Math.max(0, ...state.plan.trials.map((t) => t.block));
  }

  function loadingNode(waitFn, phase) {
    return {
      timeline: [
        {
          type: jsPsychTimedPhase,
          kind: "loading",
          html: '<div class="card center"><p>Carregando os próximos estímulos…</p><div class="spinner"></div></div>',
          wait_for: waitFn,
          on_start: () => {
            setPhase(phase);
            setChrome({ progress: true, fatigue: !!state.plan && phase !== "loading_practice" });
          },
        },
      ],
      conditional_function: () => !state.stop,
    };
  }

  // ---------- formulários (likert etc.) ----------

  function likert(name, question, left, right, required) {
    let h = `<fieldset class="likert"><legend>${question}</legend><div class="likert-row"><span class="anchor">${left}</span>`;
    for (let i = 1; i <= 5; i++) {
      h += `<label class="likert-opt"><input type="radio" name="${name}" value="${i}" ${required !== false && i === 1 ? "required" : ""}><span>${i}</span></label>`;
    }
    return h + `<span class="anchor">${right}</span></div></fieldset>`;
  }

  function choice(name, question, options, required, otherName) {
    let h = `<fieldset class="choice"><legend>${question}</legend>`;
    options.forEach(([value, label], i) => {
      h += `<label class="choice-opt"><input type="radio" name="${name}" value="${value}" ${required !== false && i === 0 ? "required" : ""}> ${label}</label>`;
    });
    if (otherName) h += `<input type="text" name="${otherName}" class="other-text" placeholder="Qual?">`;
    return h + "</fieldset>";
  }

  function textQ(name, question, rows) {
    return `<fieldset class="text"><legend>${question}</legend><textarea name="${name}" rows="${rows || 2}"></textarea></fieldset>`;
  }

  function checkpointNode(afterTrial) {
    return {
      timeline: [
        {
          type: jsPsychSurveyHtmlForm,
          preamble: cfg.texts.checkpoint,
          html:
            likert("fatigue", "Quão cansado(a) você está agora?", "1 = nada cansado(a)", "5 = muito cansado(a)") +
            likert("concentration", "Quão concentrado(a) você está agora?", "1 = nada concentrado(a)", "5 = muito concentrado(a)") +
            '<div class="cp-actions"><button type="submit" class="jspsych-btn btn-secondary" id="cp-stop">Encerrar</button></div>',
          button_label: "Continuar",
          on_start: () => {
            setPhase("checkpoint");
            monitor.setContext({ trial_index: afterTrial, is_practice: 0 });
            setChrome({ progress: true, fatigue: false });
            // pré-carrega o próximo mini-bloco durante a pausa
            const nextBlock = Math.floor(afterTrial / cfg.block_size) + 1;
            ensureBlock(nextBlock);
          },
          on_load: () => {
            state.phaseOnset = performance.now();
            state.cpAction = "continue";
            const next = $("#jspsych-survey-html-form-next");
            $(".cp-actions").appendChild(next);
            $("#cp-stop").addEventListener("click", () => (state.cpAction = "stop"));
            next.addEventListener("click", () => (state.cpAction = "continue"));
            $("#jspsych-survey-html-form").addEventListener("keydown", (e) => {
              if (e.key === "Enter") e.preventDefault();
            });
          },
          on_finish: (d) => {
            if (d.aborted) return;
            state.checkpointIndex++;
            const row = {
              participant_id: state.pid,
              session_id: state.session_id,
              checkpoint_index: state.checkpointIndex,
              after_trial_index: afterTrial,
              n_trials_completed: state.nCompleted,
              fatigue: Number(d.response.fatigue),
              concentration: Number(d.response.concentration),
              action: state.cpAction,
              rt_ms: d.rt,
              onset_ts: rel(state.phaseOnset),
              response_ts: now(),
            };
            const sid = state.session_id;
            enqueue(() => PilotDB.addItem(sid, "checkpoint", row));
            if (state.cpAction === "stop") {
              state.stop = true;
              state.terminationReason = "checkpoint_stop";
              enqueue(() => PilotDB.updateSession(sid, { termination_reason: "checkpoint_stop" }));
            }
          },
        },
      ],
      conditional_function: () => !state.stop,
    };
  }

  // ---------- telas iniciais ----------

  function welcomeNode() {
    return {
      type: jsPsychHtmlButtonResponse,
      stimulus: `<div class="card text">${fill(cfg.texts.welcome)}</div>`,
      choices: ["Começar"],
      on_finish: async () => {
        await createSession();
      },
    };
  }

  async function createSession() {
    state.t0 = performance.now();
    state.session_id = makeSessionId();
    const seedParam = params.get("seed");
    const seed = seedParam !== null && /^\d+$/.test(seedParam) ? Number(seedParam) >>> 0 : crypto.getRandomValues(new Uint32Array(1))[0];
    const practiceIds = new Set(cfg.practice_stim_ids);
    const pool = manifest.filter((s) => !practiceIds.has(s.stim_id));
    const seq = PilotSequence.generateSequence(pool, cfg, seed);
    state.plan = seq;
    const validation = PilotSequence.validateSequence(seq.trials, cfg);
    state.practice = cfg.practice_stim_ids.map((id, i) => {
      const s = manifestById[id];
      return {
        trial_index: i + 1, block: 0, stim_id: s.stim_id, item_id: s.item_id, stim_type: s.type, file: s.file,
        is_repeat: 0, first_presentation_trial: i + 1, lag: 0,
        description_mode: cfg.practice_description_modes[i % cfg.practice_description_modes.length],
      };
    });
    const session = {
      session_id: state.session_id,
      pid: state.pid,
      app_version: APP_VERSION,
      status: "active",
      downloaded: false,
      download_count: 0,
      started_at: new Date().toISOString(),
      t0_epoch_ms: Math.round(performance.timeOrigin + state.t0),
      timestamps_note: "Todos os *_ts são ms desde o início da sessão (clique em Começar na tela de boas-vindas), via performance.now(). Início absoluto: t0_epoch_ms.",
      url: location.origin + location.pathname,
      seed,
      seed_from_url: seedParam !== null,
      config: cfg,
      environment: PilotChecks.environment(),
      planned_sequence: seq.trials,
      sequence_attempts: seq.attempts,
      sequence_warnings: seq.warnings,
      sequence_validation: { ok: validation.ok, problems: validation.problems, blocks: validation.blocks },
      practice_sequence: state.practice,
      termination_reason: null,
      n_trials_completed: 0,
    };
    await PilotDB.putSession(session);
    // já começa a baixar a prática e o primeiro mini-bloco
    loader.loadAll(state.practice.map((p) => manifestById[p.stim_id]));
    ensureBlock(1);
  }

  function requirementsNode() {
    return {
      type: jsPsychHtmlButtonResponse,
      stimulus: `<div class="card text"><h2>Checagem do computador</h2><p>Vamos conferir se está tudo certo para fazer o estudo. O navegador vai pedir permissão para usar o <b>microfone</b>: clique em <b>Permitir</b>.</p><ul id="req-list" class="req-list"></ul><p id="req-msg"></p><button type="button" class="jspsych-btn btn-secondary" id="req-retry">Verificar de novo</button></div>`,
      choices: ["Continuar"],
      on_load: () => {
        const btn = $("#jspsych-html-button-response-button-0 button");
        btn.disabled = true;
        let busy = false;
        const render = (results) => {
          $("#req-list").innerHTML = results
            .map((r) => `<li class="${r.ok ? "ok" : r.ok === null ? "pending" : "fail"}"><span class="mark">${r.ok ? "✓" : r.ok === null ? "…" : "✗"}</span> ${r.label}${r.ok === false ? `<div class="req-help">${r.msg}</div>` : ""}</li>`)
            .join("");
        };
        const run = async () => {
          if (busy) return;
          busy = true;
          const res = [PilotChecks.checkDevice(), PilotChecks.checkBrowser(cfg), PilotChecks.checkWindow(cfg), await PilotChecks.checkStorage()];
          const micPending = { id: "mic", label: "Microfone funcionando", ok: null };
          render(res.concat([micPending]));
          let mic = state.micInfo && state.micInfo.ok ? state.micInfo : await PilotChecks.requestMic();
          if (mic.ok && !state.recorder) {
            state.recorder = PilotChecks.makeRecorder(mic.stream, cfg.audio_bitrate_bps);
            // o html-audio-response usa o gravador registrado no jsPsych; registramos o nosso
            // (mono, Opus, bitrate configurável) em vez do padrão de initializeMicrophoneRecorder
            const rec = state.recorder;
            jsPsych.pluginAPI.getMicrophoneRecorder = () => rec;
          }
          state.micInfo = mic;
          const all = res.concat([mic]);
          render(all);
          state.checks = all.map(({ id, ok, detail }) => ({ id, ok, detail: detail === undefined ? null : detail }));
          const ok = all.every((r) => r.ok);
          btn.disabled = !ok;
          $("#req-msg").innerHTML = ok ? "Tudo certo! Clique em <b>Continuar</b>." : "Resolva os itens marcados com ✗ para continuar.";
          busy = false;
        };
        $("#req-retry").addEventListener("click", run);
        state._resizeHandler = () => {
          if (!busy) run();
        };
        window.addEventListener("resize", state._resizeHandler);
        run();
      },
      on_finish: () => {
        window.removeEventListener("resize", state._resizeHandler);
        const mic = state.micInfo || {};
        const sid = state.session_id;
        const rec = state.recorder;
        enqueue(() => PilotDB.updateSession(sid, {
          requirement_checks: state.checks,
          microphone: { label: mic.detail && mic.detail.label, settings: mic.detail && mic.detail.settings, recorder_mime: rec && rec.mimeType, audio_bitrate_bps: cfg.audio_bitrate_bps },
        }));
      },
    };
  }

  function micTestNode() {
    return {
      type: jsPsychHtmlButtonResponse,
      stimulus: `<div class="card text"><h2>Teste do microfone</h2><p>Clique em <b>Gravar 3 segundos</b> e fale qualquer frase (por exemplo, conte de 1 a 5). Depois, a gravação será tocada de volta para você.</p><p><button type="button" class="jspsych-btn" id="mic-rec">Gravar 3 segundos</button></p><div class="meter"><div id="mic-level"></div></div><p id="mic-status" class="status"></p><audio id="mic-play" controls hidden></audio><p id="mic-help" class="req-help" hidden>Se não ouviu nada: confira se o volume do computador está ligado, se o microfone certo está selecionado (ícone à esquerda do endereço do site) e grave de novo.</p></div>`,
      choices: ["Ouvi minha voz, continuar"],
      on_load: () => {
        const btn = $("#jspsych-html-button-response-button-0 button");
        btn.disabled = true;
        const stream = state.micInfo.stream;
        const recBtn = $("#mic-rec");
        const status = $("#mic-status");
        const audio = $("#mic-play");
        let actx = null;
        recBtn.addEventListener("click", async () => {
          recBtn.disabled = true;
          btn.disabled = true;
          audio.hidden = true;
          // medidor de nível, para mostrar que o microfone está captando
          let analyser = null, raf = null, peak = 0;
          try {
            actx = actx || new AudioContext();
            await actx.resume();
            const src = actx.createMediaStreamSource(stream);
            analyser = actx.createAnalyser();
            analyser.fftSize = 512;
            src.connect(analyser);
            const buf = new Uint8Array(analyser.fftSize);
            const tick = () => {
              analyser.getByteTimeDomainData(buf);
              let m = 0;
              for (let i = 0; i < buf.length; i++) m = Math.max(m, Math.abs(buf[i] - 128));
              peak = Math.max(peak, m);
              $("#mic-level").style.width = Math.min(100, (m / 64) * 100) + "%";
              raf = requestAnimationFrame(tick);
            };
            tick();
          } catch (e) { /* medidor é opcional */ }
          const chunks = [];
          const rec = PilotChecks.makeRecorder(stream, cfg.audio_bitrate_bps);
          rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
          const stopped = new Promise((r) => (rec.onstop = r));
          rec.start();
          let left = Math.round(cfg.mic_test_ms / 1000);
          status.textContent = `Gravando… ${left}`;
          const iv = setInterval(() => {
            left--;
            if (left > 0) status.textContent = `Gravando… ${left}`;
          }, 1000);
          await new Promise((r) => setTimeout(r, cfg.mic_test_ms));
          rec.stop();
          await stopped;
          clearInterval(iv);
          if (raf) cancelAnimationFrame(raf);
          $("#mic-level").style.width = "0%";
          const blob = new Blob(chunks, { type: "audio/webm" });
          state.micTests.push({ ts: now(), bytes: blob.size, peak_level: peak });
          audio.src = URL.createObjectURL(blob);
          audio.hidden = false;
          status.innerHTML = "Ouça a gravação abaixo. Se ouviu sua voz, clique em <b>Ouvi minha voz, continuar</b>. Se não, clique em <b>Gravar de novo</b>.";
          recBtn.textContent = "Gravar de novo";
          recBtn.disabled = false;
          $("#mic-help").hidden = state.micTests.length < 2;
          audio.play().catch(() => {});
          // só libera depois que a gravação começou a tocar
          audio.addEventListener("play", () => (btn.disabled = false), { once: true });
        });
      },
      on_finish: () => {
        const sid = state.session_id;
        const tests = state.micTests.slice();
        enqueue(() => PilotDB.updateSession(sid, { mic_tests: tests }));
      },
    };
  }

  function pageNode(html, label) {
    return {
      type: jsPsychHtmlButtonResponse,
      stimulus: `<div class="card text">${html}</div>`,
      choices: [label || "Continuar"],
    };
  }

  // ---------- questionário final ----------

  function questionnaireNode() {
    const extras = (cfg.extra_questions || []).map((q) => {
      if (q.type === "likert5") return likert(q.id, q.text, "1", "5", !!q.required);
      if (q.type === "choice") return choice(q.id, q.text, q.options.map((o) => [o, o]), !!q.required);
      return textQ(q.id, q.text, 2);
    }).join("");
    const html =
      choice("stop_reason", "1. Por que você parou?", [
        ["cansaco", "Cansaço"], ["tedio", "Tédio"], ["falta_de_tempo", "Falta de tempo"],
        ["problema_tecnico", "Problema técnico"], ["completei_tudo", "Completei tudo"], ["outro", "Outro:"],
      ], true, "stop_reason_other") +
      likert("instructions_clarity", "2. Quão claras foram as instruções?", "1 = nada claras", "5 = muito claras") +
      likert("vividness_definition_sense", "3. A definição de vividez fez sentido para você?", "1 = nenhum sentido", "5 = todo o sentido") +
      textQ("vividness_definition_comment", "Comentário sobre a definição de vividez (opcional):") +
      likert("dynamism_question_clarity", "4. A pergunta sobre dinamismo (movimento) da imagem imaginada foi clara?", "1 = nada clara", "5 = muito clara") +
      textQ("dynamism_question_comment", "Comentário sobre a pergunta de dinamismo (opcional):") +
      choice("imagery_time", "5. O tempo de imaginação (6 s) foi:", [["curto_demais", "Curto demais"], ["ok", "Ok"], ["longo_demais", "Longo demais"]], true) +
      choice("stim_mask_time", "6. A duração do estímulo (3 s) e da máscara de chuvisco (2 s) foi:", [["rapida_demais", "Rápida demais"], ["ok", "Ok"], ["longa_demais", "Longa demais"]], true) +
      likert("mask_annoyance", "7. Quanto a máscara de chuvisco incomodou?", "1 = nada", "5 = muito") +
      choice("description_preference", "8. Você preferiu descrever por escrito ou em áudio?", [["escrita", "Por escrito"], ["audio", "Em áudio"], ["indiferente", "Tanto faz"]], true) +
      textQ("description_preference_why", "Por quê?") +
      likert("recognition_difficulty", "9. Quão difícil foi julgar se você já tinha visto o estímulo antes?", "1 = muito fácil", "5 = muito difícil") +
      textQ("fatigue_onset", "10. Em que momento você começou a sentir cansaço?") +
      `<fieldset class="text"><legend>11. Quantos estímulos você acha viáveis numa sessão?</legend><input type="number" name="feasible_n_stimuli" min="0" max="1000" step="1" required></fieldset>` +
      choice("interruptions", "12. Houve interrupções no ambiente?", [["nao", "Não"], ["sim", "Sim"]], true) +
      textQ("interruptions_text", "Se sim, quais?") +
      textQ("technical_problems", "13. Teve algum problema técnico? Qual?") +
      textQ("comments", "14. Comentários livres sobre o piloto:", 4) +
      extras;
    return {
      type: jsPsychSurveyHtmlForm,
      preamble: cfg.final_questionnaire_intro,
      html: `<div class="questionnaire">${html}</div>`,
      button_label: "Enviar respostas",
      on_start: () => {
        if (!state.terminationReason) state.terminationReason = "completed_max";
        setPhase("questionnaire");
        monitor.setContext({ trial_index: null, is_practice: null });
        monitor.setRequireFullscreen(false);
        $("#fs-overlay").hidden = true;
        setChrome({ progress: false, fatigue: false });
        state.keyMap = null;
      },
      on_load: () => {
        const other = $('input[name="stop_reason_other"]');
        const form = $("#jspsych-survey-html-form");
        const sync = () => {
          const v = (form.querySelector('input[name="stop_reason"]:checked') || {}).value;
          other.required = v === "outro";
        };
        form.addEventListener("change", sync);
        form.addEventListener("keydown", (e) => {
          if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") e.preventDefault();
        });
      },
      on_finish: (d) => {
        const sid = state.session_id;
        const fb = Object.assign({ participant_id: state.pid, session_id: sid, rt_ms: d.rt, submitted_ts: now() }, d.response);
        enqueue(() => PilotDB.addItem(sid, "feedback", fb));
      },
    };
  }

  // ---------- final: download e instruções de envio ----------

  async function finalizeSession() {
    monitor.stop();
    await state.saveQueue;
    const end = now();
    await PilotDB.updateSession(state.session_id, {
      status: "finished",
      termination_reason: state.terminationReason,
      ended_at: new Date().toISOString(),
      session_duration_ms: Math.round(end),
      n_trials_completed: state.nCompleted,
      preload_report: loader.report(),
      save_errors: state.saveErrors,
    });
  }

  function sendInstructionsHTML(filename) {
    const email = cfg.data_email;
    const subject = fill(cfg.email_subject);
    const body = `Olá, Beatriz!\n\nSegue em anexo o arquivo ${filename} com os dados do piloto.\n\n(Lembrete: anexe o arquivo .zip antes de enviar.)`;
    const mailto = `mailto:${email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    return `
      <p>O arquivo <b class="filename">${filename}</b> foi baixado para o seu computador, normalmente na pasta <b>Downloads</b>.</p>
      <h3>Como enviar os dados</h3>
      <ol class="send-steps">
        <li>Envie um e-mail para <span class="email" id="email-addr">${email}</span> <button type="button" class="jspsych-btn btn-small" id="copy-email">Copiar</button></li>
        <li>Use o assunto <b>${subject}</b>. Se preferir, <a href="${mailto}" id="mailto-link">clique aqui para abrir o e-mail já preenchido</a>.</li>
        <li><b>Anexe manualmente o arquivo ${filename}</b> (ele está na pasta Downloads). O link acima <u>não</u> anexa o arquivo sozinho.</li>
      </ol>
      <p>Se o download não funcionou, clique em <button type="button" class="jspsych-btn btn-small" id="redownload">Baixar de novo</button></p>
      <p class="small">Os dados também ficam guardados neste navegador; se algo der errado, abra o seu link de novo que dá para baixá-los outra vez.</p>`;
  }

  function wireSendButtons(root, session_id) {
    const copy = $("#copy-email", root);
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(cfg.data_email);
        copy.textContent = "Copiado!";
      } catch (e) {
        const r = document.createRange();
        r.selectNodeContents($("#email-addr", root));
        const sel = getSelection();
        sel.removeAllRanges();
        sel.addRange(r);
        copy.textContent = "Selecionado: use Ctrl/Cmd+C";
      }
    });
    $("#redownload", root).addEventListener("click", () => PilotExport.exportSession(session_id));
  }

  function finalNode() {
    return {
      type: jsPsychHtmlButtonResponse,
      stimulus: '<div class="card text" id="final-card"><p>Preparando o arquivo com os seus dados…</p><div class="spinner"></div></div>',
      choices: [],
      on_start: () => setChrome({ progress: false, fatigue: false }),
      on_load: async () => {
        const card = $("#final-card");
        try {
          await finalizeSession();
          const out = await PilotExport.exportSession(state.session_id);
          card.innerHTML = cfg.texts.thanks + sendInstructionsHTML(out.filename);
          card.dataset.filename = out.filename;
          wireSendButtons(card, state.session_id);
        } catch (e) {
          console.error(e);
          card.innerHTML = `<h2>Ops, algo deu errado ao gerar o arquivo</h2><p>Seus dados estão salvos neste navegador. Recarregue a página (mesmo link) para tentar baixar de novo, ou avise a pesquisadora (${cfg.data_email}).</p><p class="small">${String(e)}</p>`;
        }
      },
    };
  }

  // ---------- recuperação de sessões anteriores ----------

  async function sessionSummary(s) {
    const items = await PilotDB.itemsBySession(s.session_id);
    const trials = items.filter((i) => i.kind === "trial").map((i) => i.data);
    return {
      s,
      nItems: items.length,
      nMain: trials.filter((t) => !t.is_practice && t.status === "completed").length,
      nPractice: trials.filter((t) => t.is_practice && t.status === "completed").length,
    };
  }

  function fmtDate(iso) {
    try {
      return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    } catch (e) {
      return iso;
    }
  }

  function showRecovery(list, onDone, all) {
    const rows = list.map(({ s, nMain, nPractice }) => `
      <li data-sid="${s.session_id}">
        <div>Sessão iniciada em <b>${fmtDate(s.started_at)}</b> — ${nMain} estímulo(s) concluído(s)${nPractice && !nMain ? " (só a prática)" : ""}${s.downloaded ? " — <i>já baixada antes</i>" : ""}</div>
        <button type="button" class="jspsych-btn dl-btn">Baixar os dados da sessão anterior</button>
        <span class="dl-status"></span>
      </li>`).join("");
    showGate(`<div class="card text">
      <h2>${all ? "Dados de sessões anteriores" : "Encontramos uma sessão anterior"}</h2>
      <p>${all ? "Estas são as sessões salvas neste navegador para o seu código." : "Há dados de uma sessão anterior salvos neste navegador que <b>ainda não foram baixados</b>. Baixe-os antes de qualquer outra coisa:"}</p>
      <ul class="recovery-list">${rows}</ul>
      <div id="recovery-send" hidden></div>
      <p><button type="button" class="jspsych-btn btn-secondary" id="new-session" ${all ? "" : "disabled"}>Começar uma nova sessão</button></p>
    </div>`);
    const pending = new Set(list.filter((x) => !x.s.downloaded).map((x) => x.s.session_id));
    document.querySelectorAll(".recovery-list li").forEach((li) => {
      const sid = li.dataset.sid;
      li.querySelector(".dl-btn").addEventListener("click", async () => {
        const st = li.querySelector(".dl-status");
        st.textContent = "Gerando arquivo…";
        try {
          const out = await PilotExport.exportSession(sid);
          st.innerHTML = `✓ baixado: <b>${out.filename}</b>`;
          pending.delete(sid);
          const send = $("#recovery-send");
          send.innerHTML = sendInstructionsHTML(out.filename);
          send.hidden = false;
          wireSendButtons(send, sid);
          if (!pending.size) $("#new-session").disabled = false;
        } catch (e) {
          st.textContent = "Erro: " + e;
        }
      });
    });
    $("#new-session").addEventListener("click", () => {
      $("#gate").hidden = true;
      onDone();
    });
  }

  // ---------- montagem da sessão ----------

  async function startExperiment() {
    jsPsych = initJsPsych({
      display_element: "jspsych-target",
      on_finish: () => {},
    });
    jsPsych.data.addProperties({ participant_id: state.pid });
    loader = new PilotStimuli.Loader("stimuli/");
    monitor = new PilotMonitor({
      now,
      onEpisodeStart: (ep) => {
        if (ep.event_type === "focus_loss" && state.cur) state.cur.n_focus_losses++;
      },
      onEpisodeEnd: (ep) => {
        const row = Object.assign({ participant_id: state.pid, session_id: state.session_id }, ep);
        row.start_ts = Math.round(row.start_ts * 10) / 10;
        row.end_ts = Math.round(row.end_ts * 10) / 10;
        row.duration_ms = Math.round(row.duration_ms * 10) / 10;
        if (ep.event_type === "focus_loss" && state.cur && state.cur.trial_index === ep.trial_index && state.cur.is_practice === ep.is_practice) {
          state.cur.focus_lost_ms = Math.round((state.cur.focus_lost_ms + row.duration_ms) * 10) / 10;
        }
        const sid = state.session_id;
        enqueue(() => PilotDB.addItem(sid, "event", row));
      },
      onFullscreenLost: () => {
        $("#fs-overlay").hidden = false;
      },
      onFullscreenBack: () => {
        $("#fs-overlay").hidden = true;
      },
    });

    const timeline = [welcomeNode(), requirementsNode(), micTestNode()];
    timeline.push(pageNode(cfg.texts.environment));
    timeline.push(pageNode(`<h2>Como funciona</h2>${cfg.texts.imagery_explanation}`));
    timeline.push(pageNode(`<h2>Vividez</h2>${cfg.texts.vividness_definition}<h3>Escala de vividez</h3><ul class="anchors">${cfg.vividness_anchors.map((a, i) => `<li><b>${i + 1}</b> — ${a}</li>`).join("")}</ul><h3>Escala de dinamismo</h3><ul class="anchors">${cfg.dynamism_anchors.map((a, i) => `<li><b>${i + 1}</b> — ${a}</li>`).join("")}</ul>`));
    timeline.push(pageNode(`<h2>Você pode parar quando quiser</h2>${cfg.texts.stop_notice}<p>A seguir, o estudo vai entrar em <b>tela cheia</b>. Se você sair da tela cheia, vamos pedir para voltar.</p>`));
    timeline.push({
      type: jsPsychFullscreen,
      fullscreen_mode: true,
      message: '<div class="card text"><p>Clique no botão abaixo para entrar em tela cheia e começar a prática.</p></div>',
      button_label: "Entrar em tela cheia",
      on_finish: () => {
        monitor.start();
        // só passa a exigir tela cheia quando ela de fato entrou (ou após 1,5 s), para não
        // registrar uma "saída" falsa enquanto o navegador ainda está entrando
        const arm = () => monitor.setRequireFullscreen(true);
        if (document.fullscreenElement) arm();
        else {
          const t = setTimeout(arm, 1500);
          document.addEventListener("fullscreenchange", () => { clearTimeout(t); arm(); }, { once: true });
        }
        const sid = state.session_id;
        const ts = now();
        enqueue(() => PilotDB.updateSession(sid, { practice_start_ts: ts }));
      },
    });
    timeline.push(pageNode(cfg.texts.practice_intro, "Começar a prática"));
    timeline.push(loadingNode(() => loader.loadAll(state.practice.map((p) => manifestById[p.stim_id])), "loading_practice"));

    for (let i = 0; i < cfg.practice_stim_ids.length; i++) {
      timeline.push(trialNode(() => state.practice[i], true));
    }
    timeline.push(Object.assign(pageNode(cfg.texts.practice_end, "Começar"), {
      on_start: () => {
        setPhase("between");
        monitor.setContext({ trial_index: null, is_practice: 0 });
        setChrome({ progress: false, fatigue: false });
        state.nCompleted = 0;
      },
      on_finish: () => {
        const sid = state.session_id;
        const ts = now();
        enqueue(() => PilotDB.updateSession(sid, { main_start_ts: ts }));
      },
    }));

    const main = [];
    const N = cfg.n_max_trials;
    for (let i = 0; i < N; i++) {
      const blockStart = i % cfg.block_size === 0;
      if (blockStart) {
        const b = i / cfg.block_size + 1;
        main.push(loadingNode(() => {
          ensureBlock(b + 1); // já adianta o próximo
          return ensureBlock(b);
        }, "loading"));
      }
      main.push(trialNode(() => state.plan.trials[i], false));
      const done = i + 1;
      if (done % cfg.checkpoint_every === 0 && done < N) main.push(lazyCheckpoint(done));
    }
    timeline.push({ timeline: main });
    timeline.push(questionnaireNode());
    timeline.push({
      type: jsPsychFullscreen,
      fullscreen_mode: false,
      delay_after: 0,
    });
    timeline.push(finalNode());
    jsPsych.run(timeline);
  }

  function lazyCheckpoint(afterTrial) {
    const node = checkpointNode(afterTrial);
    const orig = node.conditional_function;
    node.conditional_function = () => orig() && !!state.plan && state.plan.trials.length > afterTrial;
    return node;
  }

  // ---------- inicialização ----------

  async function boot() {
    try {
      cfg = await fetch("config.json", { cache: "no-store" }).then((r) => {
        if (!r.ok) throw new Error("config.json: HTTP " + r.status);
        return r.json();
      });
    } catch (e) {
      showGate(`<div class="card text"><h2>Erro ao carregar o estudo</h2><p>${e}</p></div>`);
      return;
    }
    document.title = cfg.study_name;
    $("#fatigue-btn").addEventListener("click", onFatigueClick);
    $("#fs-return").addEventListener("click", () => {
      document.documentElement.requestFullscreen().catch(() => {});
    });

    const pid = (params.get("pid") || "").trim().toUpperCase();
    if (!new RegExp(cfg.pid_pattern).test(pid)) {
      showGate(`<div class="card text"><h2>Link inválido</h2><p>Este endereço não tem um código de participante válido. Use exatamente o <b>link individual</b> que você recebeu (ele termina com algo como <code>?pid=P01</code>).</p><p>Se o problema continuar, escreva para <b>${cfg.data_email}</b>.</p></div>`);
      return;
    }
    state.pid = pid;

    try {
      manifest = await PilotStimuli.loadManifest("stimuli/manifest.csv");
      manifestById = Object.fromEntries(manifest.map((s) => [s.stim_id, s]));
      cfg.practice_stim_ids.forEach((id) => {
        if (!manifestById[id]) throw new Error(`estímulo de prática ${id} não está no manifest.csv`);
      });
    } catch (e) {
      showGate(`<div class="card text"><h2>Erro ao carregar os estímulos</h2><p>${e}</p><p>Avise a pesquisadora (${cfg.data_email}).</p></div>`);
      return;
    }

    let sessions = [];
    try {
      await PilotDB.open();
      sessions = await PilotDB.sessionsByPid(pid);
    } catch (e) {
      // sem IndexedDB a checagem de requisitos vai mostrar o problema
    }
    const summaries = await Promise.all(sessions.map(sessionSummary));
    if (params.has("recuperar")) {
      const withData = summaries.filter((x) => x.nItems > 0);
      if (withData.length) {
        showRecovery(withData, startExperiment, true);
        return;
      }
    }
    const pending = summaries.filter((x) => !x.s.downloaded && x.nItems > 0);
    if (pending.length) {
      showRecovery(pending, startExperiment, false);
      return;
    }
    startExperiment();
  }

  // ganchos para o teste automatizado (não usados pelo participante)
  window.__pilot = { state, get cfg() { return cfg; }, get loader() { return loader; } };

  boot();
})();
