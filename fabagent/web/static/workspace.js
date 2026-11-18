// Radni ekran (fabric.js 5, sve lokalno): crtanje, slojevi, cvorovi, Bulove operacije, offset, tekst, uvoz, projekti.
// Jedinica platna = 1 mm; ishodiste platna gore-lijevo, Y prema dolje. Polja X/Y i ravnala prikazuju koordinate
// masine (ishodiste dolje-lijevo). Laser je u workspace_laser.js i koristi window.fabWS.
(function () {
  "use strict";
  const CFG = window.FAB_WS;
  if (!CFG || typeof fabric === "undefined") return;
  const [BW, BH] = CFG.bed;
  const U = fabric.util;
  const $ = (id) => document.getElementById(id);
  const PROPS = ["lay", "fabText", "imgOpts"];
  // boje prikaza slojeva 00..29 na tamnoj podlozi (samo prikaz - na laser idu postavke sloja, ne boja)
  const PALETTE = ["#f2f2f2", "#4d8dff", "#ff3030", "#00e000", "#d0d000", "#ff8000", "#00e0e0", "#ff00ff", "#b4b4b4", "#6a6aff",
    "#c04040", "#40a040", "#a0a000", "#c08040", "#00a0ff", "#a000c0", "#808080", "#7d87b9", "#bb7784", "#4a6fe3",
    "#d33f6a", "#8cd78c", "#f0b98d", "#f6c4e1", "#b5bbe3", "#c040c0", "#e07b39", "#33a0a0", "#77dd77", "#ffe680"];
  const REF = PALETTE.slice();          // boje za prepoznavanje sloja pri uvozu: crna -> 00, plava -> 01
  REF[0] = "#000000"; REF[1] = "#0000ff";
  const FLAT_TOL = 0.02, RULER = 22, PX_MM = 25.4 / 96, KAPPA = 0.5522847498, UNDO_MAX = 40;
  const NEW_LAYER = {mode: "Line", speed: 6000, power: 20, passes: 1, interval: 0.1, output: true, show: true};
  const NEW_IMAGE = {mode: "dither", algorithm: CFG.dither.includes("jarvis") ? "jarvis" : CFG.dither[0], threshold: 128, negative: false};
  const S = {tool: "select", layer: 0, layers: {}, order: [], undo: [], redo: [], clip: null, node: null, draw: null,
             pan: null, space: false, busy: false, images: new Map(), layerSig: ""};

  fabric.Object.NUM_FRACTION_DIGITS = 8;    // bez ovoga se pri cuvanju/undo sve zaokruzuje na 0.01 (gubi se tacnost)
  fabric.Object.prototype.set({objectCaching: false, strokeUniform: true, transparentCorners: false, cornerColor: "#4d8dff",
    cornerStrokeColor: "#ffffff", cornerSize: 8, borderColor: "#9ec5fe", originX: "center", originY: "center"});
  const stage = $("ws-stage");
  const canvas = new fabric.Canvas("ws-canvas", {preserveObjectStacking: true, fireMiddleClick: true, fireRightClick: true,
    stopContextMenu: true, width: stage.clientWidth || 800, height: stage.clientHeight || 600});

  // ------------------------------------------------------------------ poruke
  function say(text, kind) {
    const m = $("ws-msg");
    m.textContent = text || "";
    m.className = kind || "";
  }
  async function api(path, body) {
    const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    const out = await r.json();
    if (!out.ok) {                    // citljiva poruka: opseg vrijednosti ili razlog, bez internih oznaka
      const v = (out.violations || []).map((x) => x.note || `${x.param} = ${x.value} nije u dozvoljenom opsegu ${x.min} – ${x.max} ${x.unit || ""}`.trim());
      throw new Error(v.length ? v.join("; ") : String(out.message || out.error || "greška").replace(/^\[[^\]]*\]\s*/, ""));
    }
    return out;
  }
  const r4 = (v) => Math.round(v * 10000) / 10000;
  const r2 = (v) => Math.round(v * 100) / 100;
  const zoom = () => canvas.getZoom();
  const strokeW = () => 1.4 / zoom();

  // ------------------------------------------------------------------ geometrija objekata
  const isGroup = (o) => o.type === "group" || o.type === "activeSelection";
  const leaves = (o) => (isGroup(o) ? o.getObjects().flatMap(leaves) : [o]);
  const allObjs = () => canvas.getObjects().filter((o) => !o.isHelper);
  const sel = () => canvas.getActiveObjects().filter((o) => !o.isHelper);

  function ellipseCmds(cx, cy, rx, ry) {
    const kx = rx * KAPPA, ky = ry * KAPPA;
    return [["M", cx + rx, cy], ["C", cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry], ["C", cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy],
            ["C", cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry], ["C", cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy], ["Z"]];
  }
  function rectCmds(x0, y0, x1, y1, rx, ry) {
    if (!rx || !ry) return [["M", x0, y0], ["L", x1, y0], ["L", x1, y1], ["L", x0, y1], ["Z"]];
    const kx = rx * (1 - KAPPA), ky = ry * (1 - KAPPA);
    return [["M", x0 + rx, y0], ["L", x1 - rx, y0], ["C", x1 - kx, y0, x1, y0 + ky, x1, y0 + ry], ["L", x1, y1 - ry],
            ["C", x1, y1 - ky, x1 - kx, y1, x1 - rx, y1], ["L", x0 + rx, y1], ["C", x0 + kx, y1, x0, y1 - ky, x0, y1 - ry],
            ["L", x0, y0 + ry], ["C", x0, y0 + ky, x0 + kx, y0, x0 + rx, y0], ["Z"]];
  }
  function localCmds(o) {            // naredbe M/L/C/Q/Z u koordinatama objekta (ishodiste = centar objekta)
    switch (o.type) {
      case "path": return o.path.map((c) => c.map((v, i) => (i === 0 ? v : v - (i % 2 ? o.pathOffset.x : o.pathOffset.y))));
      case "rect": return rectCmds(-o.width / 2, -o.height / 2, o.width / 2, o.height / 2, Math.min(o.rx || 0, o.width / 2), Math.min(o.ry || 0, o.height / 2));
      case "circle": return ellipseCmds(0, 0, o.radius, o.radius);
      case "ellipse": return ellipseCmds(0, 0, o.rx, o.ry);
      case "line": { const l = o.calcLinePoints(); return [["M", l.x1, l.y1], ["L", l.x2, l.y2]]; }
      case "polyline": case "polygon": {
        const c = o.points.map((p, i) => [i ? "L" : "M", p.x - o.pathOffset.x, p.y - o.pathOffset.y]);
        if (o.type === "polygon") c.push(["Z"]);
        return c;
      }
      default: return null;
    }
  }
  function xfCmds(cmds, m) {
    return cmds.map((c) => {
      const d = [c[0]];
      for (let i = 1; i < c.length; i += 2) { const p = U.transformPoint({x: c[i], y: c[i + 1]}, m); d.push(p.x, p.y); }
      return d;
    });
  }
  function worldCmds(o) { const l = localCmds(o); return l ? xfCmds(l, o.calcTransformMatrix()) : null; }

  function bez(out, p0, p1, p2, p3) {
    const dx = p3[0] - p0[0], dy = p3[1] - p0[1], len = Math.hypot(dx, dy);
    const off = (p) => (len < 1e-9 ? Math.hypot(p[0] - p0[0], p[1] - p0[1]) : Math.abs((p[0] - p0[0]) * dy - (p[1] - p0[1]) * dx) / len);
    const along = len < 1e-9 ? 0 : Math.max(0, Math.hypot(p1[0] - p0[0], p1[1] - p0[1]) + Math.hypot(p2[0] - p3[0], p2[1] - p3[1]) - len);
    const n = Math.max(1, Math.min(400, Math.ceil(Math.sqrt(0.75 * (Math.max(off(p1), off(p2)) + along) / FLAT_TOL))));
    for (let k = 1; k <= n; k++) {
      const t = k / n, a = 1 - t;
      out.push([a * a * a * p0[0] + 3 * a * a * t * p1[0] + 3 * a * t * t * p2[0] + t * t * t * p3[0],
                a * a * a * p0[1] + 3 * a * a * t * p1[1] + 3 * a * t * t * p2[1] + t * t * t * p3[1]]);
    }
  }
  function flatten(cmds) {           // -> [{closed, pts: [[x, y], ...]}] (krivulje u ravne dijelove, tolerancija FLAT_TOL)
    const out = [];
    let cur = null, start = null, last = null;
    const push = () => {
      if (cur) {
        const p = cur.pts;
        if (cur.closed && p.length > 1 && Math.hypot(p[0][0] - p[p.length - 1][0], p[0][1] - p[p.length - 1][1]) < 1e-9) p.pop();
        if (p.length > 1) out.push(cur);
      }
      cur = null;
    };
    for (const c of cmds) {
      if (c[0] === "M") { push(); start = last = [c[1], c[2]]; cur = {closed: false, pts: [start.slice()]}; continue; }
      if (!cur) { if (!start) continue; cur = {closed: false, pts: [start.slice()]}; last = start; }
      if (c[0] === "L") { last = [c[1], c[2]]; cur.pts.push(last.slice()); }
      else if (c[0] === "C") { bez(cur.pts, last, [c[1], c[2]], [c[3], c[4]], [c[5], c[6]]); last = [c[5], c[6]]; }
      else if (c[0] === "Q") {
        const q = [c[1], c[2]], e = [c[3], c[4]];
        bez(cur.pts, last, [last[0] + 2 / 3 * (q[0] - last[0]), last[1] + 2 / 3 * (q[1] - last[1])],
            [e[0] + 2 / 3 * (q[0] - e[0]), e[1] + 2 / 3 * (q[1] - e[1])], e);
        last = e;
      } else if (c[0] === "Z") { cur.closed = true; push(); last = start; }
    }
    push();
    return out;
  }
  function polysOf(o) {              // list (ne grupa, ne slika) -> poligoni u mm, sa kesom po transformaciji
    const key = o.calcTransformMatrix().map((v) => v.toFixed(6)).join(",");
    if (o.__fk !== key) { const c = worldCmds(o); o.__fp = c ? flatten(c) : []; o.__fk = key; }
    return o.__fp;
  }
  function imageCorners(o) {
    const m = o.calcTransformMatrix(), w = o.width / 2, h = o.height / 2;
    return [[-w, -h], [w, -h], [w, h], [-w, h]].map(([x, y]) => { const p = U.transformPoint({x, y}, m); return [p.x, p.y]; });
  }
  function boundsOf(objs) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const add = ([x, y]) => { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; };
    objs.forEach((o) => leaves(o).forEach((l) => {
      if (l.type === "image") imageCorners(l).forEach(add);
      else polysOf(l).forEach((p) => p.pts.forEach(add));
    }));
    return x0 === Infinity ? null : {x0, y0, x1, y1, w: x1 - x0, h: y1 - y0};
  }
  function closedRings(o) {
    return leaves(o).filter((l) => l.type !== "image").flatMap((l) => polysOf(l).filter((p) => p.closed && p.pts.length > 2).map((p) => p.pts));
  }
  const ringsToCmds = (rings) => rings.flatMap((r) => [...r.map((p, i) => [i ? "L" : "M", p[0], p[1]]), ["Z"]]);

  function mkPath(cmds, lay, extra) {
    const p = new fabric.Path(cmds.map((c) => c.slice()), {fill: "", stroke: PALETTE[lay], strokeWidth: strokeW(), lay});
    p.set({originX: "center", originY: "center", left: p.pathOffset.x, top: p.pathOffset.y});
    if (extra) p.set(extra);
    p.setCoords();
    return p;
  }
  function prep(o) {                 // objekat iz JSON-a / uvoza: pravila prikaza radnog ekrana
    leaves(o).forEach((l) => {
      l.lay = l.lay | 0;
      l.set({objectCaching: false, strokeUniform: true});
      if (l.type !== "image") l.set({stroke: PALETTE[l.lay], strokeWidth: strokeW(), fill: ""});
      ensureLayer(l.lay);
      l.visible = S.layers[l.lay].show !== false;
    });
    o.set({objectCaching: false});
    o.setCoords();
  }
  function applyMatrix(o, m) { U.applyTransformToObject(o, U.multiplyTransformMatrices(m, o.calcTransformMatrix())); o.setCoords(); }
  function reselect(objs) {
    canvas.discardActiveObject();
    objs = objs.filter((o) => canvas.contains(o));
    if (objs.length === 1) canvas.setActiveObject(objs[0]);
    else if (objs.length > 1) canvas.setActiveObject(new fabric.ActiveSelection(objs, {canvas}));
    canvas.requestRenderAll();
    updateFields();
  }
  function transformSelection(m) {   // m = matrica u koordinatama platna, primjenjuje se na svaki izabrani objekat
    const objs = sel();
    if (!objs.length) return;
    canvas.discardActiveObject();
    objs.forEach((o) => applyMatrix(o, m));
    reselect(objs);
    commit();
  }

  // ------------------------------------------------------------------ slojevi
  function ensureLayer(i) {
    if (!S.layers[i]) S.layers[i] = Object.assign({}, NEW_LAYER);
    if (!S.order.includes(i)) S.order.push(i);
  }
  function usedLayers() {
    const used = new Set();
    allObjs().forEach((o) => leaves(o).forEach((l) => used.add(l.lay | 0)));
    used.forEach(ensureLayer);
    return S.order.filter((i) => used.has(i));
  }
  const pad2 = (i) => String(i).padStart(2, "0");
  function renderLayers(force) {
    const used = usedLayers();
    const sig = used.join(",") + "|" + S.layer;
    $("ws-layers-empty").hidden = used.length > 0;
    if (!force && sig === S.layerSig) return;
    S.layerSig = sig;
    const body = $("ws-layers").tBodies[0];
    body.textContent = "";
    used.forEach((i, pos) => {
      const L = S.layers[i], tr = document.createElement("tr"), R = CFG.ranges;
      tr.dataset.layer = i;
      if (i === S.layer) tr.className = "current";
      tr.innerHTML = `<td><span class="ws-chip" style="background:${PALETTE[i]}" title="Sloj ${pad2(i)} - klik ga čini tekućim">${pad2(i)}</span></td>
        <td><select class="ws-l-mode">${CFG.modes.map((m) => `<option${m === L.mode ? " selected" : ""}>${m}</option>`).join("")}</select></td>
        <td><input type="number" class="ws-l-speed" min="${R.speed[0]}" max="${R.speed[1]}" step="50" value="${L.speed}"></td>
        <td><input type="number" class="ws-l-power" min="${R.power[0]}" max="${R.power[1]}" step="1" value="${L.power}"></td>
        <td><input type="number" class="ws-l-passes" min="${R.passes[0]}" max="${R.passes[1]}" step="1" value="${L.passes}"></td>
        <td><input type="number" class="ws-l-interval" min="${R.interval[0]}" max="${R.interval[1]}" step="0.01" value="${L.interval}"></td>
        <td><input type="checkbox" class="ws-l-output"${L.output ? " checked" : ""}></td>
        <td><input type="checkbox" class="ws-l-show"${L.show !== false ? " checked" : ""}></td>
        <td><button type="button" class="ws-l-up" title="Ranije na laseru"${pos === 0 ? " disabled" : ""}>▲</button><button type="button" class="ws-l-down" title="Kasnije na laseru"${pos === used.length - 1 ? " disabled" : ""}>▼</button></td>`;
      const num = (cls, key) => tr.querySelector(cls).addEventListener("change", (e) => {
        const v = parseFloat(e.target.value);
        L[key] = Number.isNaN(v) ? "" : v;
        commit();
      });
      num(".ws-l-speed", "speed"); num(".ws-l-power", "power"); num(".ws-l-passes", "passes"); num(".ws-l-interval", "interval");
      tr.querySelector(".ws-l-mode").addEventListener("change", (e) => { L.mode = e.target.value; commit(); });
      tr.querySelector(".ws-l-output").addEventListener("change", (e) => { L.output = e.target.checked; commit(); });
      tr.querySelector(".ws-l-show").addEventListener("change", (e) => {
        L.show = e.target.checked;
        canvas.discardActiveObject();
        allObjs().forEach((o) => leaves(o).forEach((l) => { if ((l.lay | 0) === i) l.visible = L.show; }));
        canvas.requestRenderAll(); updateFields(); commit();
      });
      tr.querySelector(".ws-chip").addEventListener("click", () => setLayer(i));
      const move = (d) => () => {
        const a = S.order.indexOf(i), b = S.order.indexOf(used[pos + d]);
        [S.order[a], S.order[b]] = [S.order[b], S.order[a]];
        commit();
      };
      tr.querySelector(".ws-l-up").addEventListener("click", move(-1));
      tr.querySelector(".ws-l-down").addEventListener("click", move(1));
      body.appendChild(tr);
    });
  }
  function setLayer(i) {             // tekuci sloj; ako postoji selekcija, prebacuje je u taj sloj
    S.layer = i;
    ensureLayer(i);
    const objs = sel();
    if (objs.length) {
      objs.forEach((o) => leaves(o).forEach((l) => { l.lay = i; if (l.type !== "image") l.set("stroke", PALETTE[i]); l.visible = S.layers[i].show !== false; }));
      canvas.requestRenderAll();
      commit();
    }
    document.querySelectorAll(".ws-swatch").forEach((b) => b.classList.toggle("current", +b.dataset.layer === i));
    renderLayers();
  }
  (function buildPalette() {
    const box = $("ws-palette");
    for (let i = 0; i < CFG.layerCount; i++) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "ws-swatch" + (i === 0 ? " current" : ""); b.dataset.layer = i;
      b.style.background = PALETTE[i]; b.textContent = pad2(i); b.title = `Sloj ${pad2(i)}`;
      b.addEventListener("click", () => setLayer(i));
      box.appendChild(b);
    }
  })();

  // ------------------------------------------------------------------ stanje: undo, projekat, automatsko cuvanje
  function exportObjects(full) {
    const objs = canvas.toObject(PROPS).objects;
    if (full) return objs;
    const walk = (o) => {             // u undo istoriji slika se pamti jednom, ne u svakom koraku
      if (o.type === "image" && o.src) {
        let id = [...S.images.entries()].find(([, v]) => v === o.src);
        id = id ? id[0] : "@img:" + S.images.size;
        S.images.set(id, o.src);
        o.src = id;
      }
      (o.objects || []).forEach(walk);
    };
    objs.forEach(walk);
    return objs;
  }
  const snapshot = () => JSON.stringify({layers: S.layers, order: S.order, objects: exportObjects(false)});
  function projectData() { return {app: "FabAgent", version: 1, bed: CFG.bed, layers: S.layers, order: S.order, objects: exportObjects(true)}; }
  function restore(data, done) {
    exitNode(true);
    const unpack = (o) => { if (o.type === "image" && S.images.has(o.src)) o.src = S.images.get(o.src); (o.objects || []).forEach(unpack); };
    const objects = JSON.parse(JSON.stringify(data.objects || []));
    objects.forEach(unpack);
    S.busy = true;
    U.enlivenObjects(objects, (objs) => {
      canvas.discardActiveObject();
      canvas.remove(...canvas.getObjects());
      S.layers = JSON.parse(JSON.stringify(data.layers || {}));
      S.order = (data.order || []).slice();
      objs.forEach((o) => { prep(o); canvas.add(o); });
      S.busy = false;
      S.layerSig = "";
      refresh();
      if (done) done();
    });
  }
  function commit() {
    if (S.busy) return;
    const s = snapshot();
    if (S.undo[S.undo.length - 1] !== s) {
      S.undo.push(s);
      if (S.undo.length > UNDO_MAX) S.undo.shift();
      S.redo = [];
      try { localStorage.setItem("fabagent.radni", JSON.stringify(projectData())); } catch (e) { /* pun prostor - bez automatskog cuvanja */ }
    }
    refresh();
  }
  function undo() {
    if (S.busy || S.undo.length < 2) return;
    S.redo.push(S.undo.pop());
    restore(JSON.parse(S.undo[S.undo.length - 1]));
  }
  function redo() {
    if (S.busy || !S.redo.length) return;
    const s = S.redo.pop();
    S.undo.push(s);
    restore(JSON.parse(s));
  }
  function refresh() {
    renderLayers(true);
    updateFields();
    $("ws-undo").disabled = S.undo.length < 2;
    $("ws-redo").disabled = !S.redo.length;
    canvas.requestRenderAll();
  }

  // ------------------------------------------------------------------ gornja traka: X / Y / sirina / visina / rotacija
  const F = {x: $("ws-x"), y: $("ws-y"), w: $("ws-w"), h: $("ws-h"), r: $("ws-r")};
  function updateFields() {
    const objs = sel(), b = boundsOf(objs);
    Object.values(F).forEach((f) => { f.disabled = !b; });
    if (!b) { Object.values(F).forEach((f) => { f.value = ""; }); updateImagePanel(); return; }
    F.x.value = r2(b.x0); F.y.value = r2(BH - b.y1); F.w.value = r2(b.w); F.h.value = r2(b.h);
    F.r.value = objs.length === 1 ? r2(((360 - objs[0].angle) % 360 + 360) % 360) : 0;
    updateImagePanel();
  }
  function fieldChange(which) {
    const objs = sel(), b = boundsOf(objs);
    if (!b) return;
    const v = parseFloat(F[which].value);
    if (Number.isNaN(v)) { updateFields(); return; }
    if (which === "x") transformSelection([1, 0, 0, 1, v - b.x0, 0]);
    else if (which === "y") transformSelection([1, 0, 0, 1, 0, (BH - v) - b.y1]);
    else if (which === "r") {
      const cur = objs.length === 1 ? ((360 - objs[0].angle) % 360 + 360) % 360 : 0;
      const a = -(v - cur) * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
      transformSelection([c, s, -s, c, cx - c * cx + s * cy, cy - s * cx - c * cy]);
    } else {
      if (v <= 0 || (which === "w" ? b.w : b.h) < 1e-9) { updateFields(); return; }
      let sx = which === "w" ? v / b.w : 1, sy = which === "h" ? v / b.h : 1;
      if ($("ws-lock").checked) sx = sy = which === "w" ? sx : sy;
      transformSelection([sx, 0, 0, sy, b.x0 - sx * b.x0, b.y1 - sy * b.y1]);     // sidro: donji lijevi ugao
    }
  }
  Object.keys(F).forEach((k) => F[k].addEventListener("change", () => fieldChange(k)));

  // ------------------------------------------------------------------ prikaz: mreza, ravnala, zoom
  function gridStep(minPx, steps) { return steps.find((s) => s * zoom() >= minPx) || steps[steps.length - 1]; }
  canvas._renderBackground = function (ctx) {
    const v = this.viewportTransform, z = v[0], W = this.width, H = this.height;
    const sx = (x) => v[4] + x * z, sy = (y) => v[5] + y * z;
    // Radna povrsina je BIJELA: crna gravura i crni obrisi se na tamnoj
    // podlozi nisu vidjeli. Okolina ostaje tamna da se ploca jasno odvaja.
    ctx.fillStyle = "#1b1b1b"; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = "#ffffff"; ctx.fillRect(sx(0), sy(0), BW * z, BH * z);
    const minor = gridStep(8, [1, 5, 10, 50, 100]), major = gridStep(50, [5, 10, 20, 50, 100, 200]);
    ctx.lineWidth = 1;
    const lines = (step, color) => {
      ctx.strokeStyle = color; ctx.beginPath();
      for (let x = 0; x <= BW + 1e-6; x += step) { const px = Math.round(sx(x)) + 0.5; ctx.moveTo(px, sy(0)); ctx.lineTo(px, sy(BH)); }
      for (let y = 0; y <= BH + 1e-6; y += step) { const py = Math.round(sy(BH - y)) + 0.5; ctx.moveTo(sx(0), py); ctx.lineTo(sx(BW), py); }
      ctx.stroke();
    };
    lines(minor, "#e3e3e3"); lines(major, "#c2c2c2");   // mreza na bijelom
    ctx.strokeStyle = "#555555"; ctx.strokeRect(Math.round(sx(0)) + 0.5, Math.round(sy(0)) + 0.5, BW * z, BH * z);
    ctx.fillStyle = "#3fbf5f"; ctx.fillRect(sx(0) - 4, sy(BH) - 4, 8, 8);            // ishodiste masine (0, 0) dolje-lijevo
  };
  canvas.on("after:render", (opt) => {
    const ctx = opt.ctx, v = canvas.viewportTransform, z = v[0], W = canvas.width, H = canvas.height;
    if (S.node) drawNodeLinks(ctx, v);
    const step = gridStep(55, [1, 2, 5, 10, 20, 50, 100, 200]);
    ctx.save();
    ctx.fillStyle = "#3a3a3a"; ctx.fillRect(0, 0, W, RULER); ctx.fillRect(0, 0, RULER, H);
    ctx.strokeStyle = "#8a8a8a"; ctx.fillStyle = "#d0d0d0"; ctx.font = "10px sans-serif"; ctx.lineWidth = 1; ctx.beginPath();
    const x0 = Math.floor(-v[4] / z / step) * step, y0 = Math.floor((BH - (H - v[5]) / z) / step) * step;
    for (let x = x0; v[4] + x * z < W; x += step) {
      const px = Math.round(v[4] + x * z) + 0.5;
      if (px < RULER) continue;
      ctx.moveTo(px, RULER); ctx.lineTo(px, RULER - 7); ctx.fillText(String(Math.round(x * 100) / 100), px + 2, 10);
    }
    for (let y = y0; v[5] + (BH - y) * z > RULER; y += step) {
      const py = Math.round(v[5] + (BH - y) * z) + 0.5;
      if (py > H) continue;
      ctx.moveTo(RULER, py); ctx.lineTo(RULER - 7, py);
      ctx.save(); ctx.translate(10, py - 2); ctx.rotate(-Math.PI / 2); ctx.fillText(String(Math.round(y * 100) / 100), 0, 0); ctx.restore();
    }
    ctx.stroke();
    ctx.fillStyle = "#3a3a3a"; ctx.fillRect(0, 0, RULER, RULER);
    ctx.restore();
  });
  function restroke() {
    const w = strokeW();
    canvas.getObjects().forEach((o) => {
      if (o.isNode) { const s = 1 / zoom(); o.set({scaleX: s, scaleY: s}); o.setCoords(); }
      else leaves(o).forEach((l) => { if (l.type !== "image") l.set("strokeWidth", w); });
    });
  }
  function setView(z, tx, ty) {
    canvas.setViewportTransform([z, 0, 0, z, tx, ty]);
    restroke();
    canvas.requestRenderAll();
  }
  function zoomAt(factor, px, py) {
    const v = canvas.viewportTransform, z = Math.max(0.3, Math.min(200, v[0] * factor)), k = z / v[0];
    setView(z, px - (px - v[4]) * k, py - (py - v[5]) * k);
  }
  function zoomTo(b) {
    const W = canvas.width - RULER, H = canvas.height - RULER, m = 30;
    const z = Math.max(0.3, Math.min(200, Math.min((W - 2 * m) / Math.max(b.w, 1e-6), (H - 2 * m) / Math.max(b.h, 1e-6))));
    setView(z, RULER + (W - b.w * z) / 2 - b.x0 * z, RULER + (H - b.h * z) / 2 - b.y0 * z);
  }
  const zoomFit = () => zoomTo({x0: 0, y0: 0, w: BW, h: BH});
  let fitted = false;
  function resize() {
    canvas.setDimensions({width: Math.max(200, stage.clientWidth), height: Math.max(200, stage.clientHeight)});
    canvas.requestRenderAll();
  }
  new ResizeObserver(() => {
    resize();
    if (!fitted) { fitted = true; zoomFit(); }      // kada je raspored stranice gotov: cijela radna povrsina u prikazu
  }).observe(stage);

  // ------------------------------------------------------------------ alati
  const HINTS = {
    select: "Klik selektuje, prevlačenje pomjera; Shift+klik dodaje u selekciju. Točkić = zoom, srednji taster = pomjeranje prikaza.",
    pan: "Prevuci da pomjeriš prikaz.", nodes: "Klikni oblik pa vuci čvorove. Dvoklik na liniju dodaje čvor, Delete briše izabrani čvor.",
    line: "Klik dodaje tačku; dvoklik ili Enter završava; klik na prvu tačku zatvara oblik; Esc odustaje.",
    rect: "Prevuci da nacrtaš pravougaonik (Shift = kvadrat).", ellipse: "Prevuci da nacrtaš elipsu (Shift = krug).",
    polygon: "Prevuci da nacrtaš poligon (Shift = pravilan).", text: "Klikni na platno gdje tekst počinje."};
  function setTool(t) {
    if (S.tool === "nodes" && t !== "nodes") exitNode();
    cancelDraw();
    S.tool = t;
    document.querySelectorAll(".ws-tool[data-tool]").forEach((b) => b.classList.toggle("active", b.dataset.tool === t));
    canvas.discardActiveObject();
    canvas.selection = t === "select";
    canvas.skipTargetFind = !(t === "select" || t === "nodes");
    allObjs().forEach((o) => { o.selectable = t === "select"; o.evented = true; });
    canvas.defaultCursor = t === "select" || t === "nodes" ? "default" : t === "pan" ? "grab" : "crosshair";
    canvas.hoverCursor = t === "select" ? "move" : t === "nodes" ? "pointer" : canvas.defaultCursor;
    $("ws-hint").textContent = HINTS[t] || "";
    canvas.requestRenderAll();
    updateFields();
  }
  document.querySelectorAll(".ws-tool[data-tool]").forEach((b) => b.addEventListener("click", () => setTool(b.dataset.tool)));

  function cancelDraw() {
    if (S.draw && S.draw.obj) canvas.remove(S.draw.obj);
    S.draw = null;
    canvas.requestRenderAll();
  }
  function preview(cmds) {
    if (S.draw.obj) canvas.remove(S.draw.obj);
    S.draw.obj = mkPath(cmds, S.layer, {isHelper: true, excludeFromExport: true, selectable: false, evented: false, strokeDashArray: [4 / zoom(), 3 / zoom()]});
    canvas.add(S.draw.obj);
  }
  function addShape(cmds, extra) {
    ensureLayer(S.layer);
    const p = mkPath(cmds, S.layer, extra);
    p.visible = S.layers[S.layer].show !== false;
    p.selectable = S.tool === "select";
    canvas.add(p);
    return p;
  }
  function boxCmds(d, p, shift) {
    let w = p.x - d.x0, h = p.y - d.y0;
    if (shift) { const m = Math.max(Math.abs(w), Math.abs(h)); w = Math.sign(w || 1) * m; h = Math.sign(h || 1) * m; }
    const x0 = Math.min(d.x0, d.x0 + w), y0 = Math.min(d.y0, d.y0 + h), x1 = x0 + Math.abs(w), y1 = y0 + Math.abs(h);
    if (x1 - x0 < 0.3 || y1 - y0 < 0.3) return null;
    if (S.tool === "rect") return rectCmds(x0, y0, x1, y1);
    if (S.tool === "ellipse") return ellipseCmds((x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2);
    const n = Math.max(3, Math.min(60, parseInt($("ws-sides").value, 10) || 6)), cmds = [];
    for (let i = 0; i < n; i++) {
      const a = -Math.PI / 2 + i * 2 * Math.PI / n;
      cmds.push([i ? "L" : "M", (x0 + x1) / 2 + (x1 - x0) / 2 * Math.cos(a), (y0 + y1) / 2 + (y1 - y0) / 2 * Math.sin(a)]);
    }
    cmds.push(["Z"]);
    return cmds;
  }
  function lineCmds(pts, closed) { return [...pts.map((p, i) => [i ? "L" : "M", p[0], p[1]]), ...(closed ? [["Z"]] : [])]; }
  function finishLine(closed) {
    const d = S.draw;
    if (!d || !d.pts) return;
    const pts = d.pts.filter((p, i) => !i || Math.hypot(p[0] - d.pts[i - 1][0], p[1] - d.pts[i - 1][1]) > 1e-6);
    cancelDraw();
    if (pts.length < 2 || (closed && pts.length < 3)) return;
    addShape(lineCmds(pts, closed));
    commit();
  }

  function startPan(e) {
    S.pan = {x: e.clientX, y: e.clientY};
    const move = (ev) => {
      const v = canvas.viewportTransform;
      setView(v[0], v[4] + ev.clientX - S.pan.x, v[5] + ev.clientY - S.pan.y);
      S.pan = {x: ev.clientX, y: ev.clientY};
    };
    const up = () => { S.pan = null; window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }
  canvas.on("mouse:down", (opt) => {
    const e = opt.e;
    if (e.button === 1 || ((S.space || S.tool === "pan") && e.button === 0)) { e.preventDefault(); startPan(e); return; }
    if (e.button === 2) { if (S.tool === "line") finishLine(false); return; }
    if (e.button !== 0) return;
    const p = canvas.getPointer(e);
    if (S.tool === "rect" || S.tool === "ellipse" || S.tool === "polygon") S.draw = {x0: p.x, y0: p.y, obj: null};
    else if (S.tool === "line") {
      if (!S.draw) S.draw = {pts: [], obj: null};
      const f = S.draw.pts[0];
      if (S.draw.pts.length > 2 && Math.hypot(p.x - f[0], p.y - f[1]) * zoom() < 9) { finishLine(true); return; }
      S.draw.pts.push([p.x, p.y]);
    } else if (S.tool === "text") openText({at: [p.x, p.y]});
    else if (S.tool === "nodes" && !(opt.target && opt.target.isNode)) {
      const t = canvas.findTarget(e, true);
      if (t && !t.isHelper && (!S.node || t !== S.node.obj)) enterNode(t);
    }
  });
  canvas.on("mouse:move", (opt) => {
    const p = canvas.getPointer(opt.e);
    $("ws-coords").textContent = `x: ${p.x.toFixed(2)}  y: ${(BH - p.y).toFixed(2)} mm`;
    const d = S.draw;
    if (!d || S.pan) return;
    if (d.pts) { if (d.pts.length) preview(lineCmds([...d.pts, [p.x, p.y]], false)); }
    else { const c = boxCmds(d, p, opt.e.shiftKey); if (c) preview(c); }
    canvas.requestRenderAll();
  });
  canvas.on("mouse:up", (opt) => {
    const d = S.draw;
    if (!d || d.pts || opt.e.button !== 0) return;
    const c = boxCmds(d, canvas.getPointer(opt.e), opt.e.shiftKey);
    cancelDraw();
    if (c) { addShape(c); commit(); }
  });
  canvas.on("mouse:dblclick", (opt) => {
    if (S.tool === "line") finishLine(false);
    else if (S.tool === "nodes" && S.node) nodeInsert(canvas.getPointer(opt.e));
    else if (S.tool === "select" && opt.target && opt.target.fabText) openText({edit: opt.target});
  });
  canvas.on("mouse:wheel", (opt) => {
    opt.e.preventDefault(); opt.e.stopPropagation();
    zoomAt(opt.e.deltaY < 0 ? 1.2 : 1 / 1.2, opt.e.offsetX, opt.e.offsetY);
  });
  canvas.on("object:modified", (opt) => { if (!(opt.target && opt.target.isNode)) commit(); });
  canvas.on("object:moving", (opt) => { if (opt.target.isNode) nodeMoved(opt.target); else updateFields(); });
  ["object:scaling", "object:rotating", "selection:created", "selection:updated", "selection:cleared"].forEach((ev) => canvas.on(ev, updateFields));

  // ------------------------------------------------------------------ cvorovi
  const anchorOff = (c) => (c[0] === "M" || c[0] === "L" ? 1 : c[0] === "C" ? 5 : c[0] === "Q" ? 3 : -1);
  function enterNode(o) {
    exitNode();
    if (o.type === "image" || isGroup(o)) { say("Čvorovi se uređuju na pojedinačnom obliku: grupu prvo razgrupiši; slike nemaju čvorove.", "err"); return; }
    const cmds = worldCmds(o);
    if (!cmds) return;
    const idx = canvas.getObjects().indexOf(o), np = mkPath(cmds, o.lay | 0, {selectable: false, evented: false});
    canvas.remove(o);
    canvas.insertAt(np, idx);
    S.node = {obj: np, cmds: np.path.map((c) => c.slice()), handles: []};
    buildHandles();
    say("Uređivanje čvorova: " + (o.fabText ? "tekst je pretvoren u običan oblik. " : "") + "vuci kvadratiće (čvorovi) i kružiće (ručke krivulje).");
  }
  function buildHandles() {
    const N = S.node;
    N.handles.forEach((h) => canvas.remove(h));
    N.handles = [];
    const s = 1 / zoom();
    const mk = (ci, off, k) => {
      const c = N.cmds[ci], anchor = k === "a";
      const h = new (anchor ? fabric.Rect : fabric.Circle)(Object.assign(anchor ? {width: 8, height: 8} : {radius: 4}, {
        left: c[off], top: c[off + 1], scaleX: s, scaleY: s, fill: anchor ? "#ffffff" : "#ffb000", stroke: "#1b1b1b", strokeWidth: 1,
        strokeUniform: true, hasControls: false, hasBorders: false, isHelper: true, isNode: true, excludeFromExport: true,
        hoverCursor: "move", ci, off, k}));
      N.handles.push(h);
      canvas.add(h);
    };
    N.cmds.forEach((c, i) => {
      if (c[0] === "C") { mk(i, 1, "c"); mk(i, 3, "c"); }
      if (c[0] === "Q") mk(i, 1, "c");
      if (anchorOff(c) > 0) mk(i, anchorOff(c), "a");
    });
    canvas.requestRenderAll();
  }
  function rebuildNodePath() {
    const N = S.node, idx = canvas.getObjects().indexOf(N.obj);
    const np = mkPath(N.cmds, N.obj.lay | 0, {selectable: false, evented: false});
    canvas.remove(N.obj);
    canvas.insertAt(np, idx);
    N.obj = np;
  }
  function nodeMoved(h) {
    const N = S.node, c = N.cmds[h.ci], nx = h.left, ny = h.top, ax = c[h.off], ay = c[h.off + 1], dx = nx - ax, dy = ny - ay;
    if (!dx && !dy) return;
    if (h.k === "a") {               // cvor nosi svoje rucke; cvorovi na istom mjestu (pocetak = kraj) idu zajedno
      N.cmds.forEach((d, j) => {
        const a = anchorOff(d);
        if (a < 0 || Math.abs(d[a] - ax) > 1e-9 || Math.abs(d[a + 1] - ay) > 1e-9) return;
        d[a] = nx; d[a + 1] = ny;
        if (d[0] === "C") { d[3] += dx; d[4] += dy; }
        const n = N.cmds[j + 1];
        if (n && n[0] === "C") { n[1] += dx; n[2] += dy; }
      });
    } else { c[h.off] = nx; c[h.off + 1] = ny; }
    rebuildNodePath();
    N.handles.forEach((g) => { if (g !== h) { g.set({left: N.cmds[g.ci][g.off], top: N.cmds[g.ci][g.off + 1]}); g.setCoords(); } });
  }
  function nodePrev() {              // za svaku naredbu: tacka od koje segment krece i pocetak njenog pod-oblika
    const prev = [], starts = [];
    let last = null, start = null;
    S.node.cmds.forEach((c, i) => {
      prev[i] = last; starts[i] = start;
      if (c[0] === "M") start = last = [c[1], c[2]];
      else if (c[0] === "Z") last = start;
      else { const a = anchorOff(c); last = [c[a], c[a + 1]]; }
    });
    return {prev, starts};
  }
  function drawNodeLinks(ctx, v) {
    const {prev} = nodePrev(), P = (x, y) => [v[4] + x * v[0], v[5] + y * v[0]];
    ctx.save(); ctx.strokeStyle = "#ffb000"; ctx.lineWidth = 1; ctx.beginPath();
    const link = (a, b) => { ctx.moveTo(...P(a[0], a[1])); ctx.lineTo(...P(b[0], b[1])); };
    S.node.cmds.forEach((c, i) => {
      if (!prev[i]) return;
      if (c[0] === "C") { link(prev[i], [c[1], c[2]]); link([c[5], c[6]], [c[3], c[4]]); }
      if (c[0] === "Q") { link(prev[i], [c[1], c[2]]); link([c[3], c[4]], [c[1], c[2]]); }
    });
    ctx.stroke(); ctx.restore();
  }
  const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  function nodeInsert(p) {
    const N = S.node, {prev, starts} = nodePrev();
    let best = null;
    const test = (i, t, q) => { const d = Math.hypot(q[0] - p.x, q[1] - p.y); if (!best || d < best.d) best = {i, t, d}; };
    N.cmds.forEach((c, i) => {
      const a = prev[i];
      if (!a || c[0] === "M") return;
      if (c[0] === "L" || c[0] === "Z") {
        const b = c[0] === "L" ? [c[1], c[2]] : starts[i], l2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2;
        if (l2 < 1e-12) return;
        const t = Math.max(0.02, Math.min(0.98, ((p.x - a[0]) * (b[0] - a[0]) + (p.y - a[1]) * (b[1] - a[1])) / l2));
        test(i, t, lerp(a, b, t));
      } else for (let k = 1; k < 40; k++) {
        const t = k / 40;
        if (c[0] === "C") { const q = lerp(lerp(lerp(a, [c[1], c[2]], t), lerp([c[1], c[2]], [c[3], c[4]], t), t), lerp(lerp([c[1], c[2]], [c[3], c[4]], t), lerp([c[3], c[4]], [c[5], c[6]], t), t), t); test(i, t, q); }
        else { const q = lerp(lerp(a, [c[1], c[2]], t), lerp([c[1], c[2]], [c[3], c[4]], t), t); test(i, t, q); }
      }
    });
    if (!best || best.d * zoom() > 9) return;
    const i = best.i, t = best.t, c = N.cmds[i], a = prev[i];
    if (c[0] === "L" || c[0] === "Z") { const q = lerp(a, c[0] === "L" ? [c[1], c[2]] : starts[i], t); N.cmds.splice(i, 0, ["L", q[0], q[1]]); }
    else if (c[0] === "C") {
      const p1 = [c[1], c[2]], p2 = [c[3], c[4]], p3 = [c[5], c[6]];
      const q0 = lerp(a, p1, t), q1 = lerp(p1, p2, t), q2 = lerp(p2, p3, t), s0 = lerp(q0, q1, t), s1 = lerp(q1, q2, t), m = lerp(s0, s1, t);
      N.cmds.splice(i, 1, ["C", ...q0, ...s0, ...m], ["C", ...s1, ...q2, ...p3]);
    } else {
      const p1 = [c[1], c[2]], p2 = [c[3], c[4]], q0 = lerp(a, p1, t), q1 = lerp(p1, p2, t), m = lerp(q0, q1, t);
      N.cmds.splice(i, 1, ["Q", ...q0, ...m], ["Q", ...q1, ...p2]);
    }
    rebuildNodePath(); buildHandles(); commit();
  }
  function nodeDelete(h) {
    const N = S.node, cm = N.cmds, i = h.ci, c = cm[i];
    if (h.k !== "a") return;
    if (c[0] === "M") {
      const n = cm[i + 1];
      let end = i + 1;
      while (end < cm.length && cm[end][0] !== "M") end++;
      if (!n || anchorOff(n) < 0) cm.splice(i, end - i);                        // pod-oblik od jedne tacke
      else {
        const last = cm[end - 1][0] === "Z" ? cm[end - 2] : null, a = anchorOff(n), np = [n[a], n[a + 1]];
        if (last && last !== n && anchorOff(last) > 0 && Math.abs(last[anchorOff(last)] - c[1]) < 1e-9 && Math.abs(last[anchorOff(last) + 1] - c[2]) < 1e-9) {
          if (last[0] === "L") cm.splice(cm.indexOf(last), 1);                  // zatvarajuca tacka je bila ista kao pocetna
          else { const o = anchorOff(last); last[o] = np[0]; last[o + 1] = np[1]; }
        }
        cm.splice(i, 2, ["M", np[0], np[1]]);
      }
    } else {
      const n = cm[i + 1];
      if (n && n[0] === "C" && c[0] === "C") { n[1] = c[1]; n[2] = c[2]; }
      cm.splice(i, 1);
    }
    for (let k = cm.length - 1; k >= 0; k--) {                                   // ukloni pod-oblike bez ijednog segmenta
      if (cm[k][0] === "M" && (!cm[k + 1] || cm[k + 1][0] === "M" || cm[k + 1][0] === "Z")) cm.splice(k, cm[k + 1] && cm[k + 1][0] === "Z" ? 2 : 1);
    }
    if (!cm.some((d) => d[0] !== "M" && d[0] !== "Z")) {
      const o = N.obj;
      N.handles.forEach((g) => canvas.remove(g));
      S.node = null;
      canvas.remove(o);
      commit();
      return;
    }
    rebuildNodePath(); buildHandles(); commit();
  }
  function exitNode(silent) {
    const N = S.node;
    if (!N) return;
    N.handles.forEach((h) => canvas.remove(h));
    N.obj.set({selectable: S.tool === "select", evented: true});
    S.node = null;
    canvas.discardActiveObject();
    if (!silent) commit();
  }

  // ------------------------------------------------------------------ uredjivanje: kopiranje, brisanje, grupe
  function removeSel() {
    const a = canvas.getActiveObject();
    if (S.node && a && a.isNode) { nodeDelete(a); return; }
    const objs = sel();
    if (!objs.length) return;
    canvas.discardActiveObject();
    objs.forEach((o) => canvas.remove(o));
    commit();
  }
  function copySel() {
    const objs = sel();
    if (!objs.length) return false;
    canvas.discardActiveObject();
    S.clip = objs.map((o) => o.toObject(PROPS));
    reselect(objs);
    return true;
  }
  function paste(dx) {
    if (!S.clip) return;
    U.enlivenObjects(JSON.parse(JSON.stringify(S.clip)), (objs) => {
      objs.forEach((o) => { prep(o); o.selectable = S.tool === "select"; applyMatrix(o, [1, 0, 0, 1, dx, dx]); canvas.add(o); });
      reselect(objs);
      commit();
    });
  }
  function selectAll() { if (S.tool !== "select") setTool("select"); reselect(allObjs().filter((o) => o.visible)); }
  function group() {
    const a = canvas.getActiveObject();
    if (!a || a.type !== "activeSelection") { say("Za grupisanje selektuj 2 ili više objekata.", "err"); return; }
    const g = a.toGroup();
    g.set({objectCaching: false});
    canvas.requestRenderAll(); commit();
  }
  function ungroup() {
    const a = canvas.getActiveObject();
    if (!a || a.type !== "group") { say("Za razgrupisanje selektuj jednu grupu.", "err"); return; }
    a.toActiveSelection();
    canvas.requestRenderAll(); commit();
  }
  function nudge(dx, dy) { transformSelection([1, 0, 0, 1, dx, dy]); }

  // ------------------------------------------------------------------ spajanje, oduzimanje, presjek, offset
  async function boolOp(op) {
    const objs = sel(), names = {union: "Spajanje", difference: "Oduzimanje", intersection: "Presjek"};
    if (objs.length < 2 || (op !== "union" && objs.length !== 2)) {
      say(op === "union" ? "Spajanje: selektuj 2 ili više zatvorenih oblika." : `${names[op]}: selektuj tačno 2 zatvorena oblika (prvo onaj od kojeg se oduzima).`, "err");
      return;
    }
    const shapes = objs.map(closedRings);
    if (shapes.some((s) => !s.length)) { say(`${names[op]} radi samo sa zatvorenim oblicima (linije i slike ne mogu).`, "err"); return; }
    try {
      const out = await api("/api/ws/boolean", {op, shapes});
      const lay = leaves(objs[0])[0].lay | 0;
      canvas.discardActiveObject();
      objs.forEach((o) => canvas.remove(o));
      if (!out.rings.length) { say(`${names[op]}: rezultat je prazan (oblici nemaju zajednički dio) – originali su uklonjeni; Poništi ih vraća.`, "err"); commit(); return; }
      const p = mkPath(ringsToCmds(out.rings), lay);
      canvas.add(p);
      reselect([p]);
      commit();
      say(`${names[op]}: gotovo, površina ${out.area_mm2} mm².`, "ok");
    } catch (e) { say(`${names[op]}: ${e.message}`, "err"); }
  }
  function dialog(id, onOk) {         // modalni dijalog: <id>-ok i <id>-cancel dugmad, <id>-err za greske
    const d = $("ws-dlg-" + id), err = $(`ws-${id}-err`);
    if (err) err.textContent = "";
    const ok = $(`ws-${id}-ok`), cancel = $(`ws-${id}-cancel`);
    ok.onclick = async () => {
      ok.disabled = true;
      try { await onOk(); d.close(); } catch (e) { if (err) err.textContent = e.message; else say(e.message, "err"); }
      ok.disabled = false;
    };
    cancel.onclick = () => d.close();
    d.showModal();
    return d;
  }
  function offsetDialog() {
    const objs = sel().filter((o) => closedRings(o).length);
    if (!objs.length) { say("Offset: selektuj bar jedan zatvoren oblik.", "err"); return; }
    dialog("offset", async () => {
      const dist = parseFloat($("ws-offset-dist").value) * parseFloat(document.querySelector("input[name=ws-offset-dir]:checked").value);
      if (Number.isNaN(dist)) throw new Error("Upiši udaljenost u mm.");
      const made = [];
      for (const o of objs) {
        const out = await api("/api/ws/offset", {rings: closedRings(o), distance: dist});
        if (out.rings.length) made.push(mkPath(ringsToCmds(out.rings), leaves(o)[0].lay | 0));
      }
      if (!made.length) throw new Error("Rezultat je prazan – oblik je manji od dvostruke udaljenosti.");
      canvas.discardActiveObject();
      if ($("ws-offset-del").checked) objs.forEach((o) => canvas.remove(o));
      made.forEach((p) => canvas.add(p));
      reselect(made);
      commit();
      say(`Offset ${dist > 0 ? "prema van" : "prema unutra"} ${Math.abs(dist)} mm: ${made.length} novih oblika.`, "ok");
    });
  }

  // ------------------------------------------------------------------ tekst
  let fontsLoaded = false;
  async function loadFonts() {
    if (fontsLoaded) return;
    const out = await (await fetch("/api/ws/fonts", {cache: "no-store"})).json();
    const s = $("ws-text-font");
    s.textContent = "";
    out.fonts.forEach((f) => { const o = document.createElement("option"); o.value = f.id; o.textContent = f.name; s.appendChild(o); });
    const arial = out.fonts.find((f) => f.id.toLowerCase() === "arial.ttf");
    if (arial) s.value = arial.id;
    fontsLoaded = true;
  }
  async function openText(opt) {
    if (!fontsLoaded) say("Učitavam listu fontova sa ovog računara…");
    try { await loadFonts(); } catch (e) { say("Fontovi se ne mogu učitati: " + e.message, "err"); return; }
    say("");
    if (!$("ws-text-font").options.length) { say("Na ovom računaru nije nađen nijedan TTF/OTF font.", "err"); return; }
    const old = opt.edit;
    if (old) { $("ws-text-value").value = old.fabText.text; $("ws-text-font").value = old.fabText.font; $("ws-text-height").value = old.fabText.height; }
    dialog("text", async () => {
      const meta = {text: $("ws-text-value").value, font: $("ws-text-font").value, height: parseFloat($("ws-text-height").value)};
      const out = await api("/api/ws/text", {text: meta.text, font: meta.font, height_mm: Number.isNaN(meta.height) ? null : meta.height});
      let p;
      if (old) {                      // isti polozaj, rotacija i razmjera kao prije izmjene
        const b = boundsOf([old]);
        p = mkPath(new fabric.Path(out.d).path, old.lay | 0, {fabText: meta});
        const idx = canvas.getObjects().indexOf(old);
        p.set({angle: old.angle, scaleX: old.scaleX, scaleY: old.scaleY, flipX: old.flipX, flipY: old.flipY}); p.setCoords();
        const nb = boundsOf([p]);
        applyMatrix(p, [1, 0, 0, 1, b.x0 - nb.x0, b.y1 - nb.y1]);
        canvas.remove(old); canvas.insertAt(p, idx);
      } else {
        p = addShape(xfCmds(new fabric.Path(out.d).path, [1, 0, 0, 1, opt.at[0], opt.at[1]]), {fabText: meta});
      }
      if (S.tool !== "select") setTool("select");
      reselect([p]);
      commit();
      say(out.missing.length ? `Font nema znakove: ${out.missing.join(" ")} – izostavljeni su.` : "Tekst je dodan kao konture slova.", out.missing.length ? "err" : "ok");
    });
    setTimeout(() => $("ws-text-value").focus(), 30);
  }

  // ------------------------------------------------------------------ slike
  function updateImagePanel() {
    const objs = sel(), img = objs.length === 1 && objs[0].type === "image" ? objs[0] : null;
    $("ws-image-panel").hidden = !img;
    $("ws-trace").disabled = !img;
    if (!img) return;
    const o = img.imgOpts || (img.imgOpts = Object.assign({}, NEW_IMAGE));
    $("ws-img-mode").value = o.mode; $("ws-img-alg").value = o.algorithm; $("ws-img-thr").value = o.threshold; $("ws-img-neg").checked = !!o.negative;
    $("ws-img-alg-wrap").hidden = o.mode !== "dither";
    $("ws-img-thr-wrap").hidden = o.mode !== "threshold";
  }
  (function imagePanel() {
    const names = {threshold: "prag (crno/bijelo)", dither: "dither (tačkice)", grayscale: "sivi tonovi (snaga)"};
    CFG.imageModes.forEach((m) => { const o = document.createElement("option"); o.value = m; o.textContent = names[m] || m; $("ws-img-mode").appendChild(o); });
    CFG.dither.forEach((m) => { const o = document.createElement("option"); o.value = m; o.textContent = m; $("ws-img-alg").appendChild(o); });
    const upd = () => {
      const objs = sel();
      if (objs.length !== 1 || objs[0].type !== "image") return;
      const thr = parseInt($("ws-img-thr").value, 10);
      objs[0].imgOpts = {mode: $("ws-img-mode").value, algorithm: $("ws-img-alg").value, threshold: Number.isNaN(thr) ? "" : thr, negative: $("ws-img-neg").checked};
      updateImagePanel(); commit();
    };
    ["ws-img-mode", "ws-img-alg", "ws-img-thr", "ws-img-neg"].forEach((id) => $(id).addEventListener("change", upd));
  })();
  function addImage(url) {
    fabric.Image.fromURL(url, (img) => {
      if (!img || !img.width) { say("Slika se ne može učitati.", "err"); return; }
      ensureLayer(S.layer);
      const k = Math.min(PX_MM, BW * 0.5 / img.width, BH * 0.5 / img.height);     // 96 dpi, ali najvise pola radne povrsine
      img.set({left: BW / 2, top: BH / 2, scaleX: k, scaleY: k, lay: S.layer, imgOpts: Object.assign({}, NEW_IMAGE), objectCaching: false});
      img.visible = S.layers[S.layer].show !== false;
      canvas.add(img);
      if (S.tool !== "select") setTool("select");
      reselect([img]);
      commit();
      say(`Slika je dodana (${r2(img.width * k)} × ${r2(img.height * k)} mm) u sloj ${pad2(S.layer)}. Interval sloja određuje gustinu linija.`, "ok");
    });
  }
  function traceDialog() {
    const objs = sel(), img = objs.length === 1 && objs[0].type === "image" ? objs[0] : null;
    if (!img) { say("Trasiranje: selektuj jednu sliku.", "err"); return; }
    if (Math.abs(img.angle % 360) > 1e-6 || img.flipX || img.flipY) { say("Trasiranje radi na slici koja nije rotirana ni zrcaljena (rotacija 0).", "err"); return; }
    $("ws-trace-thr").value = CFG.traceThreshold;
    dialog("trace", async () => {
      const thr = parseInt($("ws-trace-thr").value, 10), b = boundsOf([img]);
      if (Number.isNaN(thr)) throw new Error("Upiši prag 0–255.");
      const out = await api("/api/ws/trace", {data: img.getSrc(), threshold: thr, width_mm: r4(b.w)});
      const made = await importSvgText(out.svg, {at: [b.x0, b.y0], quiet: true});
      if ($("ws-trace-del").checked) { canvas.remove(img); reselect(made); }
      commit();
      say(`Slika je trasirana: ${made.length} oblika u sloju ${pad2(S.layer)}.`, "ok");
    });
  }

  // ------------------------------------------------------------------ uvoz i projekti
  function nearestLayer(color) {
    const c = new fabric.Color(color || "#000000").getSource();
    let best = 0, bd = Infinity;
    REF.forEach((ref, i) => { const r = new fabric.Color(ref).getSource(), d = (r[0] - c[0]) ** 2 + (r[1] - c[1]) ** 2 + (r[2] - c[2]) ** 2; if (d < bd) { bd = d; best = i; } });
    return best;
  }
  function importSvgText(text, opt) {
    opt = opt || {};
    return new Promise((resolve, reject) => {
      fabric.loadSVGFromString(text, (objects) => {
        const made = [];
        let skipped = 0;
        const usable = (v) => v && v !== "none" && v !== "transparent" && typeof v === "string";
        (objects || []).forEach((o) => leaves(o).forEach((l) => {
          const k = [PX_MM, 0, 0, PX_MM, 0, 0];
          if (l.type === "image") { applyMatrix(l, k); l.lay = S.layer; l.imgOpts = Object.assign({}, NEW_IMAGE); made.push(l); return; }
          const cmds = worldCmds(l);
          if (!cmds || !cmds.length) { skipped++; return; }
          const lay = opt.at ? S.layer : nearestLayer(usable(l.stroke) ? l.stroke : usable(l.fill) ? l.fill : "#000000");
          made.push(mkPath(xfCmds(cmds, k), lay));
        }));
        if (!made.length) { reject(new Error("U fajlu nema oblika koji se mogu uvesti" + (skipped ? " (tekst u SVG-u se ne uvozi – pretvori ga u krivulje u programu u kojem je nacrtan)." : "."))); return; }
        let b = boundsOf(made);
        if (opt.at) {                 // trasirana slika: ishodiste SVG-a = gornji lijevi ugao slike, pa konture leze tacno preko nje
          made.forEach((o) => applyMatrix(o, [1, 0, 0, 1, opt.at[0], opt.at[1]]));
        } else if (b.x0 < 0 || b.y0 < 0 || b.x1 > BW || b.y1 > BH) {
          made.forEach((o) => applyMatrix(o, [1, 0, 0, 1, (BW - b.w) / 2 - b.x0, (BH - b.h) / 2 - b.y0]));
        }
        made.forEach((o) => { prep(o); canvas.add(o); });
        b = boundsOf(made);
        if (S.tool !== "select") setTool("select");
        reselect(made);
        if (!opt.quiet) {
          commit();
          const big = b.w > BW || b.h > BH ? ` Crtež (${r2(b.w)} × ${r2(b.h)} mm) je veći od radne površine – smanji ga.` : "";
          say(`Uvezeno ${made.length} oblika (${r2(b.w)} × ${r2(b.h)} mm).` + (skipped ? ` ${skipped} elemenata (tekst) nije uvezeno.` : "") + big, big || skipped ? "err" : "ok");
        }
        resolve(made);
      });
    });
  }
  async function importLbrn(file) {   // LightBurn projekat (.lbrn, .lbrn2): oblici, slike i postavke slojeva, na istim koordinatama
    say(`Čitam ${file.name}…`);
    const out = await api("/api/ws/lbrn", {name: file.name, data: (await readAs(file, "readAsDataURL")).split(",")[1]});
    out.layers.forEach((L) => { S.layers[L.index] = {mode: L.mode, speed: L.speed, power: L.power, passes: L.passes, interval: L.interval, output: L.output, show: L.show}; });
    S.order = S.order.filter((i) => !out.layers.some((L) => L.index === i)).concat(out.layers.map((L) => L.index));
    const objs = await Promise.all(out.items.map((it) => (it.type === "path"
      ? Promise.resolve(mkPath(U.parsePath(it.d), it.layer))
      : new Promise((res) => fabric.Image.fromURL(out.sources[it.src], (img) => {
        if (!img || !img.width) { res(null); return; }
        img.set({lay: it.layer, imgOpts: Object.assign({}, it.opts)});
        U.applyTransformToObject(img, U.multiplyTransformMatrices(it.matrix, [it.w / img.width, 0, 0, it.h / img.height, 0, 0]));
        res(img);
      })))));
    const made = objs.filter(Boolean);
    if (!made.length) throw new Error("u projektu nema oblika koji se mogu uvesti");
    made.forEach((o) => { prep(o); canvas.add(o); });
    if (S.tool !== "select") setTool("select");
    if (made.length <= 300) reselect(made); else canvas.discardActiveObject();
    commit();
    const b = boundsOf(made), x0 = Math.min(0, b.x0), y0 = Math.min(0, b.y0), x1 = Math.max(BW, b.x1), y1 = Math.max(BH, b.y1);
    zoomTo({x0, y0, w: x1 - x0, h: y1 - y0});
    const outside = b.x0 < -1e-6 || b.y0 < -1e-6 || b.x1 > BW + 1e-6 || b.y1 > BH + 1e-6;
    const skipped = Object.entries(out.skipped).map(([k, n]) => `${n}× ${k}`).join(", ");
    say(`LightBurn projekat: uvezeno ${out.counts.paths} oblika i ${out.counts.images} slika, slojevi i postavke preuzeti iz fajla.`
      + (outside ? " Dio crteža je izvan radne površine 400×400 (kao u LightBurn-u) – pomjeri ga na površinu prije slanja na laser." : "")
      + (skipped ? ` Preskočeno: ${skipped}.` : "") + (out.notes.length ? " " + out.notes.join(" ") : ""), outside || skipped ? "err" : "ok");
  }
  function toPng(url) {               // formati koje server ne cita (GIF, WEBP) -> PNG
    return new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => { const cv = document.createElement("canvas"); cv.width = im.naturalWidth; cv.height = im.naturalHeight; cv.getContext("2d").drawImage(im, 0, 0); res(cv.toDataURL("image/png")); };
      im.onerror = () => rej(new Error("slika se ne može pročitati"));
      im.src = url;
    });
  }
  const readAs = (file, how) => new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = () => rej(new Error("fajl se ne može pročitati")); fr[how](file); });
  async function importFile(file) {
    const ext = file.name.toLowerCase().split(".").pop();
    try {
      if (ext === "svg") await importSvgText(await readAs(file, "readAsText"));
      else if (ext === "lbrn" || ext === "lbrn2") await importLbrn(file);
      else if (ext === "fabagent" || ext === "json") loadProject(JSON.parse(await readAs(file, "readAsText")));
      else if (["png", "jpg", "jpeg", "bmp"].includes(ext)) addImage(await readAs(file, "readAsDataURL"));
      else if (ext === "gif" || ext === "webp") addImage(await toPng(await readAs(file, "readAsDataURL")));
      else if (ext === "dxf" || ext === "pdf") {
        const data = (await readAs(file, "readAsDataURL")).split(",")[1];
        let out;
        try { out = await api("/api/ws/convert", {name: file.name, data}); }
        catch (e) {
          if (ext !== "dxf" || !/jedinic/i.test(e.message)) throw e;
          const units = window.prompt(e.message + "\n\nUpiši jedinice crteža (mm, cm ili in):", "mm");
          if (!units) return;
          out = await api("/api/ws/convert", {name: file.name, data, dxf_units: units.trim()});
        }
        await importSvgText(out.svg);
      } else say(`Format .${ext} nije podržan. Podržano: LightBurn (LBRN, LBRN2), SVG, DXF, PDF, PNG, JPG, BMP, GIF, WEBP.`, "err");
    } catch (e) { say(`Uvoz ${file.name}: ${e.message}`, "err"); }
  }
  function download(name, text, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], {type: type || "application/json"}));
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function loadProject(data) {
    if (!data || data.app !== "FabAgent" || !Array.isArray(data.objects)) { say("Ovo nije FabAgent projekat.", "err"); return; }
    restore(data, () => { S.undo = []; S.redo = []; commit(); zoomFit(); say("Projekat je otvoren.", "ok"); });
  }

  // ------------------------------------------------------------------ podaci za laser
  function buildDoc(onlySelected) {
    const shapes = [], images = [], used = new Set();
    (onlySelected ? sel() : allObjs()).forEach((o) => leaves(o).forEach((l) => {
      const lay = l.lay | 0;
      used.add(lay);
      if (l.type === "image") {
        const io = l.imgOpts || NEW_IMAGE;
        images.push({layer: lay, corners: imageCorners(l).map((p) => [r4(p[0]), r4(p[1])]), data: l.getSrc(), mode: io.mode,
                     algorithm: io.algorithm, threshold: io.threshold === "" ? null : io.threshold, negative: !!io.negative});
      } else polysOf(l).forEach((p) => shapes.push({layer: lay, closed: p.closed, points: p.pts.map((q) => [r4(q[0]), r4(q[1])])}));
    }));
    used.forEach(ensureLayer);
    const num = (v) => (v === "" || v === undefined ? null : v);
    const layers = S.order.filter((i) => used.has(i)).map((i) => {
      const L = S.layers[i];
      return {index: i, mode: L.mode, speed_mm_min: num(L.speed), power_pct: num(L.power), passes: num(L.passes), interval_mm: num(L.interval), output: L.output !== false};
    });
    return {layers, shapes, images};
  }

  // ------------------------------------------------------------------ dugmad i tastatura
  const on = (id, fn) => $(id).addEventListener("click", fn);
  on("ws-new", () => { if (!allObjs().length || window.confirm("Obrisati sve sa platna i početi novi crtež?")) { exitNode(true); canvas.discardActiveObject(); canvas.remove(...canvas.getObjects()); commit(); say("Novi crtež."); } });
  on("ws-open", () => $("ws-file-project").click());
  on("ws-save", () => { exitNode(); download("crtez.fabagent", JSON.stringify(projectData())); say("Projekat je sačuvan u folder Preuzimanja (crtez.fabagent).", "ok"); });
  on("ws-import", () => $("ws-file").click());
  $("ws-file").addEventListener("change", async (e) => { for (const f of e.target.files) await importFile(f); e.target.value = ""; });
  $("ws-file-project").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    if (/\.lbrn2?$/i.test(f.name)) { await importFile(f); return; }             // LightBurn projekat se otvara kao uvoz
    try { loadProject(JSON.parse(await readAs(f, "readAsText"))); } catch (err) { say("Projekat se ne može otvoriti: " + err.message, "err"); }
  });
  on("ws-undo", undo); on("ws-redo", redo);
  on("ws-cut", () => { if (copySel()) removeSel(); }); on("ws-copy", copySel); on("ws-paste", () => paste(5));
  on("ws-dup", () => { if (copySel()) paste(5); }); on("ws-del", removeSel); on("ws-all", selectAll);
  on("ws-group", group); on("ws-ungroup", ungroup);
  on("ws-zoom-in", () => zoomAt(1.25, canvas.width / 2, canvas.height / 2)); on("ws-zoom-out", () => zoomAt(0.8, canvas.width / 2, canvas.height / 2));
  on("ws-zoom-fit", zoomFit); on("ws-zoom-sel", () => { const b = boundsOf(sel()); if (b) zoomTo(b); else say("Nema selekcije.", "err"); });
  on("ws-union", () => boolOp("union")); on("ws-subtract", () => boolOp("difference")); on("ws-intersect", () => boolOp("intersection"));
  on("ws-offset", offsetDialog); on("ws-trace", traceDialog);
  document.querySelectorAll(".ws-app button").forEach((b) => b.addEventListener("mouseup", () => b.blur()));

  window.addEventListener("keydown", (e) => {
    if (document.querySelector("dialog[open]")) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
    const k = e.key.toLowerCase(), ctrl = e.ctrlKey || e.metaKey;
    const hit = () => e.preventDefault();
    if (k === " ") { hit(); if (!S.space) { S.space = true; canvas.skipTargetFind = true; canvas.selection = false; canvas.setCursor("grab"); } return; }
    if (k === "escape") { hit(); if (S.draw) cancelDraw(); else if (S.tool !== "select") setTool("select"); else canvas.discardActiveObject(), canvas.requestRenderAll(); return; }
    if (k === "enter" && S.tool === "line") { hit(); finishLine(false); return; }
    if (k === "delete" || k === "backspace") { hit(); removeSel(); return; }
    if (ctrl && k === "z") { hit(); if (e.shiftKey) redo(); else undo(); return; }
    if (ctrl && k === "y") { hit(); redo(); return; }
    if (ctrl && k === "c") { hit(); copySel(); return; }
    if (ctrl && k === "x") { hit(); if (copySel()) removeSel(); return; }
    if (ctrl && k === "v") { hit(); paste(5); return; }
    if (ctrl && k === "d") { hit(); if (copySel()) paste(5); return; }
    if (ctrl && k === "a") { hit(); selectAll(); return; }
    if (ctrl && k === "g") { hit(); group(); return; }
    if (ctrl && k === "u") { hit(); ungroup(); return; }
    const step = e.shiftKey ? 10 : ctrl ? 0.1 : 1;
    if (k === "arrowleft") { hit(); nudge(-step, 0); } else if (k === "arrowright") { hit(); nudge(step, 0); }
    else if (k === "arrowup") { hit(); nudge(0, -step); } else if (k === "arrowdown") { hit(); nudge(0, step); }
  });
  window.addEventListener("keyup", (e) => {
    if (e.key === " " && S.space) { S.space = false; canvas.skipTargetFind = !(S.tool === "select" || S.tool === "nodes"); canvas.selection = S.tool === "select"; canvas.setCursor(canvas.defaultCursor); }
  });
  stage.addEventListener("dragover", (e) => e.preventDefault());
  stage.addEventListener("drop", async (e) => { e.preventDefault(); for (const f of e.dataTransfer.files) await importFile(f); });

  // ------------------------------------------------------------------ pokretanje
  window.fabWS = {canvas, S, CFG, PALETTE, say, api, sel, allObjs, leaves, boundsOf, polysOf, closedRings, worldCmds, flatten, mkPath,
                  addShape, rectCmds, ellipseCmds, commit, undo, redo, setTool, setLayer, buildDoc, importSvgText, addImage, projectData,
                  loadProject, boolOp, enterNode, exitNode, nodeInsert, nodeDelete, reselect, transformSelection, zoomFit, download};
  resize();
  zoomFit();
  setTool("select");
  loadFonts().catch(() => {});        // lista fontova se cita unaprijed da alat Tekst ne ceka
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("fabagent.radni") || "null"); } catch (e) { saved = null; }
  if (saved && saved.app === "FabAgent" && Array.isArray(saved.objects) && saved.objects.length) {
    restore(saved, () => { S.undo = []; S.redo = []; commit(); say("Vraćen je posljednji crtež (automatski sačuvan u ovom browseru)."); });
  } else commit();
})();
