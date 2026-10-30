// Manifesto e pré-carregamento dos estímulos.
// Cada arquivo é baixado inteiro (fetch -> Blob -> URL local) e decodificado antes de ser
// usado, para que nada dependa da rede durante a apresentação. Tempo de carregamento e
// falhas ficam registrados por estímulo.
(function (global) {
  "use strict";

  function parseCSV(text) {
    const rows = [];
    let row = [], field = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) {
        if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
        else if (c === '"') q = false;
        else field += c;
      } else if (c === '"') q = true;
      else if (c === ",") { row.push(field); field = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(field); field = "";
        if (row.some((x) => x.trim() !== "")) rows.push(row);
        row = [];
      } else field += c;
    }
    row.push(field);
    if (row.some((x) => x.trim() !== "")) rows.push(row);
    const header = rows.shift().map((h) => h.trim());
    return rows.map((r) => Object.fromEntries(header.map((h, i) => [h, (r[i] || "").trim()])));
  }

  async function loadManifest(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`não foi possível ler ${url} (${res.status})`);
    const rows = parseCSV(await res.text());
    rows.forEach((r) => {
      if (r.type !== "image" && r.type !== "video") throw new Error(`manifest: tipo inválido em ${r.stim_id}: ${r.type}`);
    });
    return rows;
  }

  const TIMEOUT_MS = 45000;

  class Loader {
    constructor(baseDir) {
      this.baseDir = baseDir;
      this.cache = new Map(); // stim_id -> promise of info
      this.info = new Map(); // stim_id -> info resolvida
    }

    load(stim) {
      if (!this.cache.has(stim.stim_id)) {
        const p = this._load(stim).then((info) => {
          this.info.set(stim.stim_id, info);
          return info;
        });
        this.cache.set(stim.stim_id, p);
      }
      return this.cache.get(stim.stim_id);
    }

    loadAll(stims) {
      return Promise.all(stims.map((s) => this.load(s)));
    }

    get(stim_id) {
      return this.info.get(stim_id);
    }

    // tenta de novo uma vez se o primeiro download falhar (rede instável em casa)
    async _load(stim) {
      const first = await this._loadOnce(stim);
      if (first.ok) return first;
      const second = await this._loadOnce(stim);
      second.first_error = first.error;
      second.attempts = 2;
      second.load_ms += first.load_ms;
      return second;
    }

    async _loadOnce(stim) {
      const t0 = performance.now();
      const info = { stim_id: stim.stim_id, file: stim.file, type: stim.type, ok: false, attempts: 1, load_ms: null, error: null, bytes: null, native_duration_ms: null, el: null, url: null };
      const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error("tempo esgotado")), TIMEOUT_MS));
      try {
        await Promise.race([timeout, (async () => {
          const res = await fetch(this.baseDir + stim.file);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const blob = await res.blob();
          info.bytes = blob.size;
          info.url = URL.createObjectURL(blob);
          if (stim.type === "image") {
            const img = new Image();
            img.src = info.url;
            await img.decode();
            img.draggable = false;
            info.el = img;
          } else {
            const v = document.createElement("video");
            v.muted = true;
            v.defaultMuted = true;
            v.playsInline = true;
            v.preload = "auto";
            v.controls = false;
            v.disablePictureInPicture = true;
            await new Promise((resolve, reject) => {
              v.addEventListener("loadeddata", resolve, { once: true });
              v.addEventListener("error", () => reject(new Error("vídeo não decodificável: " + (v.error ? v.error.code + " " + v.error.message : "?"))), { once: true });
              v.src = info.url;
              v.load();
            });
            info.native_duration_ms = Math.round(v.duration * 1000);
            info.el = v;
          }
          info.ok = true;
        })()]);
      } catch (e) {
        info.ok = false;
        info.error = String((e && e.message) || e);
      }
      info.load_ms = Math.round(performance.now() - t0);
      return info;
    }

    // resumo serializável (sem os elementos DOM) para o session.json
    report() {
      return Array.from(this.info.values()).map(({ el, url, ...rest }) => rest);
    }
  }

  global.PilotStimuli = { parseCSV, loadManifest, Loader };
})(window);
