// Upravljanje GRBL laserom preko Web Serial API-ja (Chrome/Edge). Bez servera izmedju: browser <-> COM port.
// Protokol slanja: linija po linija, ceka se "ok" (ili "error:N" -> prekid). Realtime: ? status, ! hold, ~ resume, 0x18 reset.
(function () {
  "use strict";
  const cfg = window.FAB_MACHINE || {baud: 115200, home: "$H"};
  const $ = (id) => document.getElementById(id);
  const ui = {status: $("m-status"), grbl: $("m-grbl"), pos: $("m-pos"), log: $("m-console"), progress: $("m-progress")};
  const jobBox = $("m-job");
  const job = jobBox ? jobBox.dataset.job : "";
  const state = {port: null, writer: null, reader: null, pending: [], running: false, paused: false, abort: false,
                 buffer: "", poll: null, connected: false};

  function log(text, cls) {
    const line = document.createElement("div");
    if (cls) line.className = cls;
    line.textContent = text;
    ui.log.appendChild(line);
    while (ui.log.childNodes.length > 500) ui.log.removeChild(ui.log.firstChild);
    ui.log.scrollTop = ui.log.scrollHeight;
  }
  function setStatus(text, cls) { ui.status.textContent = text; ui.status.className = "ms-auto badge machine-status text-bg-" + cls; }
  function setProgress(done, total) {
    const p = total ? Math.round((done / total) * 100) : 0;
    ui.progress.style.width = p + "%"; ui.progress.textContent = `${p} % (${done}/${total})`;
  }
  function refreshButtons() {
    document.querySelectorAll(".m-needs-conn").forEach((b) => { b.disabled = !state.connected; });
    document.querySelectorAll(".m-needs-job").forEach((b) => { b.disabled = !state.connected || !job || state.running; });
    $("m-connect").disabled = state.connected;
    $("m-disconnect").disabled = !state.connected || state.running;
    $("m-pause").disabled = !state.running;
    $("m-cmd").disabled = !state.connected || state.running;
    $("m-home").disabled = !state.connected || state.running;
    $("m-unlock").disabled = !state.connected || state.running;
    objasni();
  }

  // Dugme koje je iskljuceno, a ne kaze zasto, za operatera je pokvareno dugme.
  // Frame i Start traze i vezu i napravljen posao - ovdje pise sta tacno fali.
  function objasni() {
    const box = $("m-why");
    if (!box) return;
    const fali = [];
    if (!("serial" in navigator)) fali.push("ovaj browser nema Web Serial (treba Chrome ili Edge)");
    else if (!state.connected) fali.push("nisi povezan na mašinu - pritisni Poveži i izaberi COM port");
    if (!job) fali.push("nema napravljenog posla - otvori Razgovor, odgovori na pitanja, priloži fajl i pritisni Napravi posao");
    if (state.running) fali.push("posao je u toku");
    if (fali.length) {
      box.className = "alert alert-warning small mt-3 mb-0 py-2";
      box.textContent = "Frame i Start su isključeni jer: " + fali.join("; ") + ".";
    } else {
      box.className = "alert alert-success small mt-3 mb-0 py-2";
      box.textContent = "Frame i Start su spremni. Redoslijed: Home, pa Frame (laser ugašen), pa Start.";
    }
  }

  if (!("serial" in navigator)) { $("serial-unsupported").hidden = false; $("m-connect").disabled = true; }

  // ------------------------------------------------------------ citanje
  function onLine(line) {
    if (!line) return;
    if (line.startsWith("<")) {                       // status izvjestaj <Idle|MPos:0.000,0.000,0.000|...>
      const parts = line.slice(1, -1).split("|");
      ui.grbl.textContent = parts[0];
      const pos = parts.find((p) => p.startsWith("MPos:") || p.startsWith("WPos:"));
      if (pos) ui.pos.textContent = pos.replace(":", " ");
      if (parts[0].startsWith("Alarm")) setStatus("Alarm", "danger");
      return;
    }
    log(line, line.startsWith("error") || line.startsWith("ALARM") ? "err" : "");
    if (line === "ok" || line.startsWith("error")) {
      const p = state.pending.shift();
      if (p) { if (line === "ok") p.resolve(line); else p.reject(new Error(line)); }
    }
    if (line.startsWith("ALARM")) { setStatus("Alarm", "danger"); state.abort = true; }
    if (line.startsWith("Grbl")) {                    // reset: sve na cekanju je ponisteno
      state.pending.splice(0).forEach((p) => p.reject(new Error("reset")));
    }
  }
  async function readLoop() {
    const decoder = new TextDecoder();
    try {
      while (state.reader) {
        const {value, done} = await state.reader.read();
        if (done) break;
        state.buffer += decoder.decode(value, {stream: true});
        let i;
        while ((i = state.buffer.search(/\r?\n/)) >= 0) {
          const line = state.buffer.slice(0, i).trim();
          state.buffer = state.buffer.slice(state.buffer[i] === "\r" ? i + 2 : i + 1);
          onLine(line);
        }
      }
    } catch (e) { log("Greška čitanja: " + e.message, "err"); }
  }

  // ------------------------------------------------------------ pisanje
  const enc = new TextEncoder();
  async function realtime(byteOrChar) {
    const data = typeof byteOrChar === "number" ? new Uint8Array([byteOrChar]) : enc.encode(byteOrChar);
    await state.writer.write(data);
  }
  function sendLine(line) {
    return new Promise(async (resolve, reject) => {
      state.pending.push({resolve, reject});
      log("> " + line, "tx");
      try { await state.writer.write(enc.encode(line + "\n")); } catch (e) { state.pending.pop(); reject(e); }
    });
  }
  async function stream(lines, label) {
    const cmds = lines.map((l) => l.replace(/;.*$/, "").trim()).filter((l) => l.length);
    state.running = true; state.abort = false; state.paused = false; refreshButtons();
    setStatus("U toku", "warning"); log(`--- ${label}: ${cmds.length} linija`);
    let done = 0;
    try {
      for (const c of cmds) {
        if (state.abort) throw new Error("prekinuto");
        await sendLine(c);
        done += 1; setProgress(done, cmds.length);
      }
      setStatus("Povezano", "success"); log(`--- ${label} poslan do kraja`);
    } catch (e) {
      setStatus(state.abort ? "Zaustavljeno" : "Greška", "danger"); log(`--- ${label} prekinut: ${e.message}`, "err");
    } finally {
      state.running = false; state.paused = false; $("m-pause").textContent = "⏸ Pause"; refreshButtons();
    }
  }

  // ------------------------------------------------------------ veza
  async function connect() {
    try {
      state.port = await navigator.serial.requestPort();
      await state.port.open({baudRate: cfg.baud});
    } catch (e) { log("Povezivanje otkazano: " + e.message, "err"); return; }
    state.writer = state.port.writable.getWriter();
    state.reader = state.port.readable.getReader();
    state.connected = true; setStatus("Povezano", "success"); refreshButtons();
    log("Povezano. Čekam GRBL…");
    readLoop();
    await realtime(0x18);                               // soft reset -> "Grbl x.y ['$' for help]"
    state.poll = setInterval(() => { if (state.connected) realtime("?").catch(() => {}); }, 300);
  }
  async function disconnect() {
    clearInterval(state.poll);
    try { if (state.reader) { await state.reader.cancel(); state.reader.releaseLock(); } } catch (e) { /* zatvaranje */ }
    try { if (state.writer) state.writer.releaseLock(); } catch (e) { /* zatvaranje */ }
    try { if (state.port) await state.port.close(); } catch (e) { /* zatvaranje */ }
    Object.assign(state, {port: null, reader: null, writer: null, connected: false, running: false});
    setStatus("Otkopčano", "secondary"); refreshButtons(); log("Veza prekinuta.");
  }

  // ------------------------------------------------------------ posao
  function placement() {
    const body = {};
    if (jobBox && jobBox.dataset.onBed !== "true") {
      const x = $("m-x").value, y = $("m-y").value;
      if (x === "" || y === "") throw new Error("Pozicija X i Y su obavezne (ili postavi dizajn u editoru).");
      body.x = parseFloat(x); body.y = parseFloat(y);
    }
    if ($("m-sheet")) body.sheet = parseInt($("m-sheet").value, 10);
    return body;
  }
  async function fetchGcode(frame) {
    const r = await fetch(`/api/jobs/${job}/gcode`, {method: "POST", headers: {"Content-Type": "application/json"},
                                                      body: JSON.stringify(Object.assign(placement(), {frame}))});
    const out = await r.json();
    if (!out.ok) throw new Error(`${out.error}: ${out.message}`);
    if (frame) return out.gcode;
    const t = await fetch(out.url, {cache: "no-store"});
    log(`G-code: ${out.file} (${out.lines} linija, procjena ${Math.round(out.stats.time_s)} s)`);
    return await t.text();
  }

  $("m-connect").addEventListener("click", connect);
  $("m-disconnect").addEventListener("click", disconnect);
  $("m-clear").addEventListener("click", () => { ui.log.textContent = ""; });
  $("m-home").addEventListener("click", () => stream([cfg.home], "Home"));
  $("m-unlock").addEventListener("click", () => stream(["$X"], "Otključavanje"));
  $("m-frame").addEventListener("click", async () => {
    try { await stream((await fetchGcode(true)).split("\n"), "Frame"); } catch (e) { log(e.message, "err"); }
  });
  $("m-start").addEventListener("click", async () => {
    if (jobBox.dataset.approved !== "true") { log("Job nije odobren - odobri ga prije rezanja.", "err"); return; }
    if (!$("m-safety").checked) { log("Označi sigurnosnu potvrdu prije starta.", "err"); return; }
    if (!window.confirm("Pokrenuti laser? Laser će se upaliti.")) return;
    try { await stream((await fetchGcode(false)).split("\n"), "Posao"); } catch (e) { log(e.message, "err"); }
  });
  $("m-pause").addEventListener("click", async () => {
    state.paused = !state.paused;
    await realtime(state.paused ? "!" : "~");          // feed hold / cycle start
    $("m-pause").textContent = state.paused ? "▶ Nastavi" : "⏸ Pause";
    setStatus(state.paused ? "Pauza" : "U toku", "warning");
  });
  $("m-stop").addEventListener("click", async () => {
    state.abort = true;
    try {
      await realtime("!");                              // zaustavi kretanje
      await realtime(0x18);                             // soft reset: GRBL gasi laser i brise bafer
      state.pending.splice(0).forEach((p) => p.reject(new Error("stop")));
      log("STOP: soft reset poslan (laser ugašen). Za nastavak: Otključaj ($X) pa Home.", "err");
    } catch (e) { log("Stop greška: " + e.message, "err"); }
    setStatus("Zaustavljeno", "danger");
  });
  const sendManual = async () => {
    const c = $("m-cmd").value.trim();
    if (!c) return;
    $("m-cmd").value = "";
    try { await sendLine(c); } catch (e) { /* greska je vec u konzoli */ }
  };
  $("m-send").addEventListener("click", sendManual);
  $("m-cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") sendManual(); });
  refreshButtons();
  window.fabMachine = {state, onLine};                 // za testove u browseru
})();
