// Avisos sonoros (bipes) que abrem e fecham o período de imagética/descrição.
//
// O som é sintetizado com a Web Audio API (nenhum arquivo de áudio), com um envelope
// curto de subida e descida para não estalar. O AudioContext só pode ser criado depois
// de um gesto do participante, por isso o teste do microfone chama unlock()/play() antes
// de o estudo começar.
//
// O bipe sai pelos alto-falantes e, quando o participante não usa fones, acaba gravado
// junto com a descrição — serve como marca audível do início e do fim da janela no .webm.
(function (global) {
  "use strict";

  let ctx = null;

  function ensure() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  // spec: { freq, ms, volume, type }. Devolve o performance.now() do agendamento
  // (o som começa ~5 ms depois) ou null se não deu para tocar.
  function play(spec) {
    if (!spec) return null;
    try {
      const c = ensure();
      if (!c) return null;
      const vol = spec.volume === undefined ? 0.3 : spec.volume;
      const dur = (spec.ms || 250) / 1000;
      const ramp = Math.min(0.012, dur / 4);
      const t0 = c.currentTime + 0.005;
      const osc = c.createOscillator();
      const gain = c.createGain();
      osc.type = spec.type || "sine";
      osc.frequency.setValueAtTime(spec.freq || 880, t0);
      gain.gain.setValueAtTime(0, t0);
      gain.gain.linearRampToValueAtTime(vol, t0 + ramp);
      gain.gain.setValueAtTime(vol, t0 + dur - ramp);
      gain.gain.linearRampToValueAtTime(0, t0 + dur);
      osc.connect(gain);
      gain.connect(c.destination);
      osc.start(t0);
      osc.stop(t0 + dur + 0.02);
      return performance.now();
    } catch (e) {
      return null;
    }
  }

  // Cria/retoma o AudioContext dentro de um gesto do participante.
  function unlock() {
    try {
      return !!ensure();
    } catch (e) {
      return false;
    }
  }

  // Toca o par de bipes (início, pausa, fim) para o participante conferir que ouve os dois.
  function demo(start, end, gapMs) {
    unlock();
    play(start);
    const wait = (start && start.ms ? start.ms : 250) + (gapMs === undefined ? 900 : gapMs);
    return new Promise((resolve) => {
      setTimeout(() => {
        play(end);
        setTimeout(resolve, (end && end.ms ? end.ms : 500) + 150);
      }, wait);
    });
  }

  global.PilotBeep = { play, unlock, demo };
})(window);
