// Radni ekran - laser: GRBL preko Web Serial API-ja (Chrome/Edge), direktno browser <-> COM port.
// Protokol: linija po linija, ceka se "ok" (ili "error:N" -> prekid). Realtime: ? status, ! hold, ~ nastavak, 0x18 reset.
// G-code pravi server (/api/ws/gcode) iz onoga sto je trenutno na platnu.
(function () {
  "use strict";
  const WS = window.fabWS;
  if (!WS) return;
  const CFG = WS.CFG;
  const $ = (id) => document.getElementById(id);
  const st = {port: null, writer: null, reader: null, pending: [], running: false, paused: false, abort: false, buffer: "",
              poll: null, connected: false};
  const enc = new TextEncoder();

  function log(text, cls) {
    const box = $("ws-console"), line = document.createElement("div");
    if (cls) line.className = cls;
    line.textContent = text;
    box.appendChild(line);
    while (box.childNodes.length > 500) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  }
  function status(text, cls) { const b = $("ws-lstatus"); b.textContent = text; b.className = "ws-badge " + (cls || ""); }
  function progress(done, total) {
    const p = total ? Math.round(done / total * 100) : 0;
    $("ws-progress").style.width = p + "%";
    $("ws-progress-text").textContent = total ? `${p} % (${done}/${total})` : "0 %";
  }
  function refresh() {
    document.querySelectorAll(".ws-conn").forEach((b) => { b.disabled = !st.connected || st.running; });
    $("ws-connect").disabled = st.connected;
    $("ws-disconnect").disabled = !st.connected || st.running;
    $("ws-pause").disabled = !st.running;
    $("ws-stop").disabled = !st.connected;
    $("ws-cmd").disabled = !st.connected || st.running;
  }
  function fail(what, e) { log(`${what}: ${e.message}`, "err"); WS.say(`${what}: ${e.message}`, "err"); }

  // ------------------------------------------------------------ citanje
  function onLine(line) {
    if (!line) return;
    if (line.startsWith("<")) {                           // <Idle|MPos:0.000,0.000,0.000|...>
      const parts = line.slice(1, -1).split("|");
      $("ws-grbl").textContent = parts[0];
      const pos = parts.find((p) => p.startsWith("MPos:") || p.startsWith("WPos:"));
      if (pos) $("ws-pos").textContent = pos.replace(":", " ");
      if (parts[0].startsWith("Alarm")) status("Alarm", "bad");
      return;
    }
    log(line, line.startsWith("error") || line.startsWith("ALARM") ? "err" : "");
    if (line === "ok" || line.startsWith("error")) {
      const p = st.pending.shift();
      if (p) (line === "ok" ? p.resolve : p.reject)(new Error(line));
    }
    if (line.startsWith("ALARM")) { status("Alarm", "bad"); st.abort = true; }
    if (line.startsWith("Grbl")) st.pending.splice(0).forEach((p) => p.reject(new Error("reset")));
  }
  async function readLoop() {
    const dec = new TextDecoder();
    try {
      while (st.reader) {
        const {value, done} = await st.reader.read();
        if (done) break;
        st.buffer += dec.decode(value, {stream: true});
        let i;
        while ((i = st.buffer.search(/\r?\n/)) >= 0) {
          const line = st.buffer.slice(0, i).trim();
          st.buffer = st.buffer.slice(st.buffer[i] === "\r" ? i + 2 : i + 1);
          onLine(line);
        }
      }
    } catch (e) { log("Greška čitanja: " + e.message, "err"); }
  }

  // ------------------------------------------------------------ pisanje
  async function realtime(b) { await st.writer.write(typeof b === "number" ? new Uint8Array([b]) : enc.encode(b)); }
  function sendLine(line) {
    return new Promise((resolve, reject) => {
      const p = {resolve, reject};
      st.pending.push(p);
      log("> " + line, "tx");
      st.writer.write(enc.encode(line + "\n")).catch((e) => { const i = st.pending.indexOf(p); if (i >= 0) st.pending.splice(i, 1); reject(e); });
    });
  }
  async function stream(lines, label) {
    const cmds = lines.map((l) => l.replace(/;.*$/, "").trim()).filter((l) => l.length);
    st.running = true; st.abort = false; st.paused = false; refresh();
    status("U toku", "busy"); log(`--- ${label}: ${cmds.length} linija`);
    progress(0, cmds.length);
    let done = 0, ok = false;
    try {
      for (const c of cmds) {
        if (st.abort) throw new Error("prekinuto");
        await sendLine(c);
        done += 1; progress(done, cmds.length);
      }
      ok = true;
      status("Povezano", "ok"); log(`--- ${label} poslan do kraja`);
    } catch (e) {
      status(st.abort ? "Zaustavljeno" : "Greška", "bad"); log(`--- ${label} prekinut na liniji ${done + 1}: ${e.message}`, "err");
      if (!st.abort && st.connected) sendLine("M5").catch(() => {});      // greska usred posla: laser se gasi
    } finally {
      st.running = false; st.paused = false; $("ws-pause").textContent = "Pause"; refresh();
    }
    return ok;
  }

  // ------------------------------------------------------------ veza
  async function attach(port) {       // port: otvoren serijski port (readable + writable)
    st.port = port;
    st.writer = port.writable.getWriter();
    st.reader = port.readable.getReader();
    st.connected = true; status("Povezano", "ok"); refresh();
    log("Povezano. Čekam GRBL…");
    readLoop();
    await realtime(0x18);                                   // soft reset -> "Grbl x.y ['$' for help]"
    st.poll = setInterval(() => { if (st.connected) realtime("?").catch(() => {}); }, 300);
  }
  async function connect() {
    let port;
    try {
      port = await navigator.serial.requestPort();
      await port.open({baudRate: CFG.baud});
    } catch (e) { log("Povezivanje nije uspjelo: " + e.message, "err"); return; }
    await attach(port);
  }
  async function disconnect() {
    clearInterval(st.poll);
    try { if (st.reader) { await st.reader.cancel(); st.reader.releaseLock(); } } catch (e) { /* zatvaranje */ }
    try { if (st.writer) st.writer.releaseLock(); } catch (e) { /* zatvaranje */ }
    try { if (st.port && st.port.close) await st.port.close(); } catch (e) { /* zatvaranje */ }
    Object.assign(st, {port: null, reader: null, writer: null, connected: false, running: false, pending: []});
    status("Otkopčano", ""); $("ws-grbl").textContent = "—"; $("ws-pos").textContent = "—"; refresh(); log("Veza prekinuta.");
  }

  // ------------------------------------------------------------ posao
  async function gcode(frame) {
    const only = $("ws-sel-only").checked, doc = WS.buildDoc(only);
    if (!doc.shapes.length && !doc.images.length) throw new Error(only ? "ništa nije selektovano" : "platno je prazno");
    return WS.api("/api/ws/gcode", Object.assign(doc, {frame}));
  }
  const fmtTime = (s) => { const m = Math.floor(s / 60); return (m >= 60 ? Math.floor(m / 60) + " h " : "") + (m % 60) + " min " + Math.round(s % 60) + " s"; };
  function startDialog(out, doc) {
    return new Promise((resolve) => {
      const d = $("ws-dlg-start"), s = out.stats, b = s.bbox_mm, info = $("ws-start-info"), safe = $("ws-start-safe"), ok = $("ws-start-ok");
      const rows = [["G-code", `${s.lines} linija`], ["Procijenjeno vrijeme", fmtTime(s.time_s)],
                    ["Okvir posla", `X ${b[0]} – ${b[2]} mm, Y ${b[1]} – ${b[3]} mm`],
                    ...doc.layers.filter((l) => s.layers.includes(l.index)).map((l) => [`Sloj ${String(l.index).padStart(2, "0")}`,
                      `${l.mode}, ${l.speed_mm_min} mm/min, ${l.power_pct} %, ${l.passes}×` + (l.mode === "Fill" ? `, interval ${l.interval_mm} mm` : "")])];
      info.textContent = "";
      const table = document.createElement("table");
      rows.forEach(([k, v]) => { const tr = table.insertRow(); tr.insertCell().textContent = k; tr.insertCell().textContent = v; });
      info.appendChild(table);
      (s.warnings || []).forEach((w) => { const p = document.createElement("p"); p.className = "ws-err"; p.textContent = w; info.appendChild(p); });
      safe.checked = false; ok.disabled = true;
      safe.onchange = () => { ok.disabled = !safe.checked; };
      const finish = (v) => { d.close(); resolve(v); };
      ok.onclick = () => finish(true);
      $("ws-start-cancel").onclick = () => finish(false);
      d.oncancel = () => resolve(false);
      d.showModal();
    });
  }

  if (!("serial" in navigator)) { $("ws-serial-unsupported").hidden = false; $("ws-connect").disabled = true; }
  $("ws-connect").addEventListener("click", connect);
  $("ws-disconnect").addEventListener("click", disconnect);
  $("ws-clear").addEventListener("click", () => { $("ws-console").textContent = ""; });
  $("ws-home").addEventListener("click", () => stream([CFG.home], "Home"));
  $("ws-unlock").addEventListener("click", () => stream(["$X"], "Otključavanje"));
  $("ws-origin").addEventListener("click", () => stream(["G90", "G0 X0 Y0"], "Na ishodište"));
  $("ws-frame").addEventListener("click", async () => {
    try { const out = await gcode(true); await stream(out.gcode.split("\n"), "Frame"); } catch (e) { fail("Frame", e); }
  });
  $("ws-start").addEventListener("click", async () => {
    try {
      const only = $("ws-sel-only").checked, doc = WS.buildDoc(only), out = await gcode(false);
      if (!(await startDialog(out, doc))) return;
      WS.say("Posao je poslan na laser.");
      const ok = await stream(out.gcode.split("\n"), "Posao");
      WS.say(ok ? "Posao je završen." : "Posao je prekinut – pogledaj konzolu.", ok ? "ok" : "err");
    } catch (e) { fail("Start", e); }
  });
  $("ws-save-gcode").addEventListener("click", async () => {
    try {
      const out = await gcode(false);
      WS.download("fabagent.gcode", out.gcode, "text/plain");
      WS.say(`G-code sačuvan u folder Preuzimanja (${out.stats.lines} linija, procjena ${fmtTime(out.stats.time_s)}).`, "ok");
    } catch (e) { fail("Sačuvaj G-code", e); }
  });
  $("ws-pause").addEventListener("click", async () => {
    st.paused = !st.paused;
    await realtime(st.paused ? "!" : "~");                 // feed hold / nastavak
    $("ws-pause").textContent = st.paused ? "Nastavi" : "Pause";
    status(st.paused ? "Pauza" : "U toku", "busy");
  });
  $("ws-stop").addEventListener("click", async () => {
    st.abort = true;
    try {
      await realtime("!");                                  // zaustavi kretanje
      await realtime(0x18);                                 // soft reset: GRBL gasi laser i brise bafer
      st.pending.splice(0).forEach((p) => p.reject(new Error("stop")));
      log("STOP: soft reset poslan (laser ugašen). Za nastavak: Otključaj pa Home.", "err");
    } catch (e) { log("Stop greška: " + e.message, "err"); }
    status("Zaustavljeno", "bad");
  });
  document.querySelectorAll("[data-jog]").forEach((b) => b.addEventListener("click", () => {
    const [dx, dy] = b.dataset.jog.split(",").map(Number), step = parseFloat($("ws-jog-step").value);
    sendLine(`$J=G91 G21 X${dx * step} Y${dy * step} F${CFG.jogFeed}`).catch(() => {});
  }));
  const manual = () => {
    const c = $("ws-cmd").value.trim();
    if (!c) return;
    $("ws-cmd").value = "";
    sendLine(c).catch(() => {});
  };
  $("ws-send").addEventListener("click", manual);
  $("ws-cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") manual(); });
  refresh();
  WS.laser = {st, attach, disconnect, onLine, stream, gcode};   // za provjeru u browseru (lazni port)
})();
