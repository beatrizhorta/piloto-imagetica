// Registro de saídas de foco (troca de aba/janela) e de saídas da tela cheia.
// Cada episódio vira uma linha em events.csv, com início, fim, duração e o trial/etapa
// em que começou. Saídas de foco não invalidam o trial; só são registradas.
(function (global) {
  "use strict";

  class Monitor {
    constructor({ now, onEpisodeStart, onEpisodeEnd, onFullscreenLost, onFullscreenBack }) {
      this.now = now;
      this.onEpisodeStart = onEpisodeStart;
      this.onEpisodeEnd = onEpisodeEnd;
      this.onFullscreenLost = onFullscreenLost;
      this.onFullscreenBack = onFullscreenBack;
      this.context = { trial_index: null, is_practice: null, phase: "setup" };
      this.active = false;
      this.requireFullscreen = false;
      this.away = null; // episódio de perda de foco em andamento
      this.fsOut = null; // episódio fora da tela cheia em andamento
      this.fsWaiters = [];
      this._update = this._update.bind(this);
      this._fsChange = this._fsChange.bind(this);
      window.addEventListener("blur", this._update);
      window.addEventListener("focus", this._update);
      document.addEventListener("visibilitychange", this._update);
      document.addEventListener("fullscreenchange", this._fsChange);
    }

    setContext(patch) {
      Object.assign(this.context, patch);
    }

    start() {
      this.active = true;
      this._update();
    }

    stop() {
      // fecha episódios em aberto
      if (this.away) this._closeAway();
      if (this.fsOut) this._closeFs();
      this.active = false;
      this.requireFullscreen = false;
    }

    isFullscreen() {
      return !!document.fullscreenElement;
    }

    setRequireFullscreen(on) {
      this.requireFullscreen = on;
      if (on && !this.isFullscreen()) this._fsChange();
      if (!on) {
        const w = this.fsWaiters;
        this.fsWaiters = [];
        w.forEach((r) => r());
      }
    }

    // resolve quando a tela cheia estiver ativa (ou se ela não for exigida)
    waitForFullscreen() {
      if (!this.requireFullscreen || this.isFullscreen()) return Promise.resolve();
      return new Promise((resolve) => this.fsWaiters.push(resolve));
    }

    _update(ev) {
      if (!this.active) return;
      const isAway = document.hidden || !document.hasFocus();
      if (isAway && !this.away) {
        this.away = {
          event_type: "focus_loss",
          start_ts: this.now(),
          signals: new Set(),
          ...this.context,
        };
        this.onEpisodeStart(this.away);
      }
      if (this.away) {
        if (document.hidden) this.away.signals.add("tab_hidden");
        if (!document.hasFocus()) this.away.signals.add("window_blur");
      }
      if (!isAway && this.away) this._closeAway();
    }

    _closeAway() {
      const ep = this.away;
      this.away = null;
      ep.end_ts = this.now();
      ep.duration_ms = ep.end_ts - ep.start_ts;
      ep.detail = Array.from(ep.signals).join("+");
      delete ep.signals;
      this.onEpisodeEnd(ep);
    }

    _fsChange() {
      const fs = this.isFullscreen();
      if (!fs && this.active && this.requireFullscreen && !this.fsOut) {
        this.fsOut = { event_type: "fullscreen_exit", start_ts: this.now(), ...this.context };
        this.onEpisodeStart(this.fsOut);
        this.onFullscreenLost();
      } else if (fs && this.fsOut) {
        this._closeFs();
      }
      if (fs || !this.requireFullscreen) {
        const w = this.fsWaiters;
        this.fsWaiters = [];
        w.forEach((r) => r());
      }
    }

    _closeFs() {
      const ep = this.fsOut;
      this.fsOut = null;
      ep.end_ts = this.now();
      ep.duration_ms = ep.end_ts - ep.start_ts;
      ep.detail = "";
      this.onEpisodeEnd(ep);
      this.onFullscreenBack();
    }
  }

  global.PilotMonitor = Monitor;
})(window);
