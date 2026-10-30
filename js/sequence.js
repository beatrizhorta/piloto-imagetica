// Geração da sequência de trials (com seed), em mini-blocos balanceados.
//
// Cada mini-bloco de `block_size` trials é balanceado quanto a:
//   - tipo do estímulo (imagem/vídeo): metade cada (sobra alterna entre blocos);
//   - novo/repetido: round(block_size * repeat_proportion) repetições;
//   - modalidade da descrição (escrita/áudio): metade cada, cruzada com o tipo.
// Repetição = o mesmo estímulo (mesmo arquivo) já apresentado; cada estímulo se
// repete no máximo uma vez, com lag (diferença de trial_index) entre min_lag e max_lag.
(function (global) {
  "use strict";

  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function shuffle(arr, rng) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function pick(arr, rng) {
    return arr[Math.floor(rng() * arr.length)];
  }

  // Uma tentativa de montar a sequência inteira. Devolve null se ficar sem opção válida.
  // `slack` relaxa o balanceamento de tipo por bloco (0 = exato).
  function attempt(pool, cfg, rng, slack) {
    const N = cfg.n_max_trials;
    const B = cfg.block_size;
    const remaining = {
      image: shuffle(pool.filter((s) => s.type === "image"), rng),
      video: shuffle(pool.filter((s) => s.type === "video"), rng),
    };
    const seq = [];
    const open = []; // apresentados pela primeira vez e ainda não repetidos: {stim, t}
    let extraImageNext = rng() < 0.5; // quem leva a sobra quando o bloco tem tamanho ímpar
    const writtenBalance = { image: 0, video: 0 }; // escritas - áudios acumulado por tipo

    for (let b = 0; b * B < N; b++) {
      const size = Math.min(B, N - b * B);
      const nRep = Math.round(size * cfg.repeat_proportion);
      let nImg = Math.floor(size / 2) + (size % 2 && extraImageNext ? 1 : 0);
      if (size % 2) extraImageNext = !extraImageNext;
      const quota = { image: nImg, video: size - nImg };

      // posições de repetição: só onde já pode existir um item com lag >= min_lag
      const t0 = b * B + 1;
      const eligible = [];
      for (let i = 0; i < size; i++) if (t0 + i - cfg.min_lag >= 1) eligible.push(i);
      if (eligible.length < nRep) return null;
      const repPos = new Set(shuffle(eligible, rng).slice(0, nRep));

      const block = [];
      for (let i = 0; i < size; i++) {
        const t = t0 + i;
        const okType = (type) => quota[type] > -slack;
        let entry = null;
        if (repPos.has(i)) {
          const cands = open.filter(
            (o) => t - o.t >= cfg.min_lag && t - o.t <= cfg.max_lag && okType(o.stim.type)
          );
          if (!cands.length) return null;
          // prioriza o que está mais perto de "vencer" o max_lag, para não desperdiçar repetições
          cands.sort((x, y) => x.t - y.t);
          const o = rng() < 0.5 ? cands[0] : pick(cands, rng);
          open.splice(open.indexOf(o), 1);
          entry = { stim: o.stim, is_repeat: 1, first_presentation_trial: o.t, lag: t - o.t };
        } else {
          const types = ["image", "video"].filter((ty) => okType(ty) && remaining[ty].length);
          if (!types.length) return null;
          // sorteia o tipo ponderando pela cota restante do bloco e pelo estoque restante
          const w = types.map((ty) => Math.max(quota[ty], 0.25) * remaining[ty].length);
          let r = rng() * w.reduce((a, c) => a + c, 0);
          let ty = types[0];
          for (let k = 0; k < types.length; k++) {
            r -= w[k];
            if (r <= 0) { ty = types[k]; break; }
          }
          const stim = remaining[ty].pop();
          open.push({ stim, t });
          entry = { stim, is_repeat: 0, first_presentation_trial: t, lag: 0 };
        }
        quota[entry.stim.type]--;
        block.push(entry);
      }

      // modalidade da descrição: metade escrita no bloco, cruzada com o tipo
      const byType = { image: [], video: [] };
      block.forEach((e, i) => byType[e.stim.type].push(i));
      const mode = new Array(size);
      let writtenLeft = Math.floor(size / 2) + (size % 2 && rng() < 0.5 ? 1 : 0);
      const order = shuffle(["image", "video"], rng);
      order.forEach((ty, k) => {
        const idx = shuffle(byType[ty], rng);
        let w;
        if (k === 0) {
          const half = idx.length / 2;
          // arredonda a favor de quem está atrás no acumulado
          w = Number.isInteger(half) ? half : writtenBalance[ty] <= 0 ? Math.ceil(half) : Math.floor(half);
          w = Math.min(w, writtenLeft);
        } else {
          w = Math.min(writtenLeft, idx.length);
        }
        writtenLeft -= w;
        idx.forEach((pos, j) => (mode[pos] = j < w ? "written" : "audio"));
        writtenBalance[ty] += w - (idx.length - w);
      });

      block.forEach((e, i) => {
        seq.push({
          trial_index: t0 + i,
          block: b + 1,
          stim_id: e.stim.stim_id,
          item_id: e.stim.item_id,
          stim_type: e.stim.type,
          file: e.stim.file,
          is_repeat: e.is_repeat,
          first_presentation_trial: e.first_presentation_trial,
          lag: e.lag,
          description_mode: mode[i],
        });
      });
    }
    return seq;
  }

  // Gera a sequência. Se o balanceamento exato for impossível (por ex. poucos estímulos),
  // relaxa aos poucos e registra avisos; em último caso encurta a sequência.
  function generateSequence(pool, cfg, seed) {
    const rng = mulberry32(seed);
    const warnings = [];
    const maxAttempts = 4000;
    let tries = 0;
    for (let slack = 0; slack <= 2; slack++) {
      for (let k = 0; k < maxAttempts; k++) {
        tries++;
        const seq = attempt(pool, cfg, rng, slack);
        if (seq) {
          if (slack > 0) warnings.push(`balanceamento de tipo relaxado em ±${slack} por bloco`);
          return { trials: seq, seed, attempts: tries, warnings };
        }
      }
    }
    // Último recurso: encurta a sequência até caber no conjunto de estímulos.
    for (let n = cfg.n_max_trials - 1; n >= cfg.block_size; n--) {
      const c = Object.assign({}, cfg, { n_max_trials: n });
      for (let k = 0; k < 500; k++) {
        tries++;
        const seq = attempt(pool, c, rng, 2);
        if (seq) {
          warnings.push(`sequência encurtada para ${n} trials (estímulos insuficientes)`);
          return { trials: seq, seed, attempts: tries, warnings };
        }
      }
    }
    throw new Error("Não foi possível gerar a sequência de trials com estes parâmetros.");
  }

  // Confere proporções, lags e balanceamento por mini-bloco. Usada nos testes e salva no session.json.
  function validateSequence(seq, cfg) {
    const problems = [];
    const seen = new Map();
    const repeatedOnce = new Set();
    const blocks = {};
    seq.forEach((t) => {
      const b = (blocks[t.block] = blocks[t.block] || { n: 0, image: 0, video: 0, repeat: 0, written: 0, audio: 0, image_written: 0, video_written: 0 });
      b.n++; b[t.stim_type]++; b[t.description_mode]++;
      if (t.description_mode === "written") b[t.stim_type + "_written"]++;
      if (t.is_repeat) {
        b.repeat++;
        if (!seen.has(t.stim_id)) problems.push(`trial ${t.trial_index}: repetição sem apresentação anterior`);
        if (repeatedOnce.has(t.stim_id)) problems.push(`trial ${t.trial_index}: ${t.stim_id} repetido mais de uma vez`);
        repeatedOnce.add(t.stim_id);
        const first = seen.get(t.stim_id);
        if (first !== t.first_presentation_trial) problems.push(`trial ${t.trial_index}: first_presentation_trial errado`);
        if (t.lag !== t.trial_index - first) problems.push(`trial ${t.trial_index}: lag errado`);
        if (t.lag < cfg.min_lag || t.lag > cfg.max_lag) problems.push(`trial ${t.trial_index}: lag ${t.lag} fora de [${cfg.min_lag}, ${cfg.max_lag}]`);
      } else {
        if (seen.has(t.stim_id)) problems.push(`trial ${t.trial_index}: ${t.stim_id} marcado como novo mas já visto`);
        seen.set(t.stim_id, t.trial_index);
      }
    });
    Object.entries(blocks).forEach(([k, b]) => {
      const expRep = Math.round(b.n * cfg.repeat_proportion);
      if (b.repeat !== expRep) problems.push(`bloco ${k}: ${b.repeat} repetições (esperado ${expRep})`);
      if (Math.abs(b.image - b.video) > 1) problems.push(`bloco ${k}: tipo desbalanceado (${b.image} img / ${b.video} vid)`);
      if (Math.abs(b.written - b.audio) > 1) problems.push(`bloco ${k}: descrição desbalanceada (${b.written} escrita / ${b.audio} áudio)`);
      if (Math.abs(2 * b.image_written - b.image) > 1 || Math.abs(2 * b.video_written - b.video) > 1)
        problems.push(`bloco ${k}: modalidade não cruzada com o tipo`);
    });
    return { ok: problems.length === 0, problems, blocks };
  }

  global.PilotSequence = { mulberry32, shuffle, generateSequence, validateSequence };
})(window);
