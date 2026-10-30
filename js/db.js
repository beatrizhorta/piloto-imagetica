// Armazenamento local (IndexedDB). Cada trial, checkpoint, evento e áudio é gravado
// assim que termina, com durability "strict", para não se perder se a janela fechar.
// Nada é apagado por este código: sessões já baixadas só são marcadas como baixadas.
(function (global) {
  "use strict";

  const NAME = "piloto_imagetica";
  const VERSION = 1;
  let dbPromise = null;

  function open() {
    if (!dbPromise) {
      dbPromise = new Promise((resolve, reject) => {
        let req;
        try {
          req = indexedDB.open(NAME, VERSION);
        } catch (e) {
          reject(e);
          return;
        }
        req.onupgradeneeded = () => {
          const db = req.result;
          const s = db.createObjectStore("sessions", { keyPath: "session_id" });
          s.createIndex("pid", "pid");
          const it = db.createObjectStore("items", { keyPath: "id", autoIncrement: true });
          it.createIndex("session_id", "session_id");
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
        req.onblocked = () => reject(new Error("IndexedDB bloqueado por outra aba aberta"));
      });
    }
    return dbPromise;
  }

  function wrap(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function run(store, mode, fn) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const opts = mode === "readwrite" ? { durability: "strict" } : undefined;
      const tx = db.transaction(store, mode, opts);
      let result;
      Promise.resolve(fn(tx.objectStore(store))).then((r) => (result = r), reject);
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error("transação abortada"));
    });
  }

  const DB = {
    open,
    putSession(session) {
      return run("sessions", "readwrite", (s) => wrap(s.put(session)));
    },
    getSession(id) {
      return run("sessions", "readonly", (s) => wrap(s.get(id)));
    },
    async updateSession(id, patch) {
      return run("sessions", "readwrite", async (s) => {
        const cur = await wrap(s.get(id));
        if (!cur) return;
        Object.assign(cur, patch);
        await wrap(s.put(cur));
        return cur;
      });
    },
    sessionsByPid(pid) {
      return run("sessions", "readonly", (s) => wrap(s.index("pid").getAll(pid)));
    },
    addItem(session_id, kind, data, blob) {
      const rec = { session_id, kind, saved_at: new Date().toISOString(), data };
      if (blob) rec.blob = blob;
      return run("items", "readwrite", (s) => wrap(s.add(rec)));
    },
    itemsBySession(session_id) {
      return run("items", "readonly", (s) => wrap(s.index("session_id").getAll(session_id)));
    },
  };

  global.PilotDB = DB;
})(window);
