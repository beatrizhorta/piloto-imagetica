// Checagem de requisitos (computador, navegador, janela, armazenamento, microfone).
(function (global) {
  "use strict";

  const AUDIO_MIME = "audio/webm;codecs=opus";

  function checkDevice() {
    const ua = navigator.userAgent;
    const uaMobile = navigator.userAgentData && navigator.userAgentData.mobile;
    const iPadOS = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;
    const mobile = uaMobile || iPadOS || /Android|iPhone|iPad|iPod|Mobile|Tablet|Kindle|Silk/i.test(ua);
    return {
      id: "device",
      label: "Computador (não celular nem tablet)",
      ok: !mobile,
      msg: mobile ? "Este estudo precisa ser feito num computador (notebook ou desktop). Abra o mesmo link num computador." : "",
    };
  }

  function browserName() {
    const ua = navigator.userAgent;
    if (/Edg\//.test(ua)) return "edge";
    if (/OPR\/|Opera|SamsungBrowser|YaBrowser|Vivaldi|UCBrowser|Firefox|FxiOS|CriOS/.test(ua)) return "other";
    if (/Chrome\//.test(ua)) return "chrome";
    return "other";
  }

  function checkBrowser(cfg) {
    const name = browserName();
    const ok = cfg.allowed_browsers.includes(name);
    return {
      id: "browser",
      label: "Navegador Google Chrome ou Microsoft Edge",
      ok,
      detail: name,
      msg: ok ? "" : "Abra este mesmo link no <b>Google Chrome</b> ou no <b>Microsoft Edge</b> (copie o endereço da barra e cole no outro navegador).",
    };
  }

  function checkWindow(cfg) {
    const w = window.innerWidth, h = window.innerHeight;
    const ok = w >= cfg.min_window_width && h >= cfg.min_window_height;
    return {
      id: "window",
      label: `Janela com pelo menos ${cfg.min_window_width} × ${cfg.min_window_height} px`,
      ok,
      detail: `${w}x${h}`,
      msg: ok ? "" : `Sua janela está com ${w} × ${h} px. <b>Maximize a janela</b> do navegador (ou use uma tela maior) e reduza o zoom (Ctrl/Cmd e −) se necessário. A checagem é refeita automaticamente.`,
    };
  }

  async function checkStorage() {
    try {
      await PilotDB.open();
      let persisted = null;
      if (navigator.storage && navigator.storage.persist) {
        try { persisted = await navigator.storage.persist(); } catch (e) { /* opcional */ }
      }
      return { id: "storage", label: "Armazenamento local disponível", ok: true, detail: { persisted }, msg: "" };
    } catch (e) {
      return {
        id: "storage",
        label: "Armazenamento local disponível",
        ok: false,
        detail: String(e),
        msg: "O navegador não permitiu salvar dados localmente. Não use janela anônima e verifique se os cookies/dados de sites não estão bloqueados.",
      };
    }
  }

  function checkRecorderSupport() {
    const ok = typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(AUDIO_MIME);
    return { ok, mime: AUDIO_MIME };
  }

  async function requestMic() {
    const sup = checkRecorderSupport();
    const base = { id: "mic", label: "Microfone funcionando" };
    if (!sup.ok) return Object.assign(base, { ok: false, msg: "Este navegador não consegue gravar áudio. Atualize o Chrome/Edge para a versão mais recente." });
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Object.assign(base, { ok: false, msg: "O navegador não permite usar o microfone nesta página. Abra o link exatamente como recebido (começando com https://)." });
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: { ideal: 1 }, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const track = stream.getAudioTracks()[0];
      if (!track || track.readyState !== "live") throw Object.assign(new Error("sem trilha de áudio ativa"), { name: "NoTrack" });
      return Object.assign(base, { ok: true, stream, detail: { label: track.label, settings: track.getSettings() }, msg: "" });
    } catch (e) {
      let msg;
      if (e.name === "NotAllowedError" || e.name === "SecurityError") {
        msg = "O acesso ao microfone foi bloqueado. Clique no ícone à esquerda do endereço do site (cadeado ou controles), em <b>Microfone</b> escolha <b>Permitir</b> e depois <b>recarregue a página</b>.";
      } else if (e.name === "NotFoundError" || e.name === "OverconstrainedError") {
        msg = "Nenhum microfone foi encontrado. Conecte um microfone (ou fone com microfone) e clique em <b>Verificar de novo</b>.";
      } else if (e.name === "NotReadableError") {
        msg = "O microfone está sendo usado por outro programa (por exemplo, uma videochamada). Feche esse programa e clique em <b>Verificar de novo</b>.";
      } else {
        msg = "Não foi possível acessar o microfone (" + e.name + "). Verifique o microfone e clique em <b>Verificar de novo</b>.";
      }
      return Object.assign(base, { ok: false, detail: e.name + ": " + e.message, msg });
    }
  }

  function makeRecorder(stream, bitrate) {
    return new MediaRecorder(stream, { mimeType: AUDIO_MIME, audioBitsPerSecond: bitrate });
  }

  function environment() {
    const uad = navigator.userAgentData;
    return {
      user_agent: navigator.userAgent,
      ua_brands: uad ? uad.brands.map((b) => `${b.brand} ${b.version}`).join("; ") : null,
      ua_platform: uad ? uad.platform : null,
      browser: browserName(),
      screen: `${screen.width}x${screen.height}`,
      screen_avail: `${screen.availWidth}x${screen.availHeight}`,
      window_inner: `${window.innerWidth}x${window.innerHeight}`,
      device_pixel_ratio: window.devicePixelRatio,
      language: navigator.language,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      hardware_concurrency: navigator.hardwareConcurrency || null,
      device_memory_gb: navigator.deviceMemory || null,
    };
  }

  global.PilotChecks = { checkDevice, checkBrowser, checkWindow, checkStorage, requestMic, makeRecorder, environment, browserName, AUDIO_MIME };
})(window);
