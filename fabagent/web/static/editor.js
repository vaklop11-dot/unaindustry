// Editor rasporeda (fabric.js 5, lokalno): sto 0..W x 0..H mm, ravnala, pomjeranje/rotacija/skaliranje.
// Raspored se salje serveru kao matrice transformacija; server ponovo provjerava SVE (zid, razmak, granice).
(async function () {
  "use strict";
  const stage = document.getElementById("editor-stage");
  if (!stage || typeof fabric === "undefined") return;
  const job = stage.dataset.job;
  const msg = document.getElementById("editor-msg");
  const say = (text, cls) => { msg.textContent = text; msg.className = "small mb-2 " + (cls || "text-muted"); };

  const res = await fetch(`/api/jobs/${job}/editor-data`, {cache: "no-store"});
  const data = await res.json();
  if (!data.ok) { say(`${data.error}: ${data.message}`, "text-danger"); return; }
  const [W, H] = data.bed_mm;
  const RULER = 28;                                   // px
  const wrap = stage.querySelector(".editor-canvas-wrap");
  const sizePx = Math.max(300, Math.min(wrap.clientWidth || 700, window.innerHeight * 0.75));
  const zoom = (sizePx - 2) / Math.max(W, H);

  const canvas = new fabric.Canvas("editor-canvas", {width: W * zoom + 2, height: H * zoom + 2,
    selection: true, preserveObjectStacking: true, backgroundColor: "#ffffff"});
  canvas.setViewportTransform([zoom, 0, 0, zoom, 1, 1]);

  // mreza stola (10 mm) - nije objekat rasporeda
  const grid = [];
  for (let x = 0; x <= W; x += 10) grid.push(new fabric.Line([x, 0, x, H], {stroke: x % 50 ? "#353535" : "#4a4a4a"}));
  for (let y = 0; y <= H; y += 10) grid.push(new fabric.Line([0, y, W, y], {stroke: y % 50 ? "#353535" : "#4a4a4a"}));
  grid.push(new fabric.Rect({left: 0, top: 0, width: W, height: H, fill: "", stroke: "#8a8a8a"}));
  grid.forEach((g) => { g.set({selectable: false, evented: false, strokeWidth: 1 / zoom, objectCaching: false}); g.isGrid = true; canvas.add(g); });

  const SW = 1.2 / zoom;                              // debljina prikazane linije (mm) - nije dio geometrije
  const geomRect = (o) => {                             // granice bez debljine prikazane linije
    const b = o.getBoundingRect(true, true);
    return {left: b.left + SW / 2, top: b.top + SW / 2, width: b.width - SW, height: b.height - SW};
  };
  // boje prikaza na tamnom platnu: iste CSS varijable kao kvadratici u tabeli Cuts / Layers (app.css)
  const css = getComputedStyle(document.documentElement);
  const shown = (layer) => css.getPropertyValue("--layer-" + layer).trim() || data.colors[layer];
  const objects = data.items.map((it) => {
    const engrave = it.layer === "ENGRAVE";
    const p = new fabric.Path(it.d, {fill: engrave ? shown("ENGRAVE") : "", fillRule: "evenodd",
      stroke: engrave ? "" : shown(it.layer), strokeWidth: engrave ? 0 : SW, strokeUniform: true,
      objectCaching: false, lockScalingX: true, lockScalingY: true, lockSkewingX: true, lockSkewingY: true,
      cornerSize: 10, transparentCorners: false});
    p.fabIndex = it.index; p.fabLayer = it.layer; p.fabLabel = it.label;
    canvas.add(p);
    return p;
  });

  // ravnala: X lijevo->desno, Y masine odozdo prema gore (ishodiste dolje lijevo)
  function ruler(id, horizontal) {
    const c = document.getElementById(id);
    const len = (horizontal ? W : H) * zoom + 2;
    c.width = horizontal ? len : RULER; c.height = horizontal ? RULER : len;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#3a3a3a"; ctx.fillRect(0, 0, c.width, c.height);
    ctx.fillStyle = "#d0d0d0"; ctx.strokeStyle = "#9a9a9a"; ctx.font = "10px sans-serif";
    const total = horizontal ? W : H;
    for (let mm = 0; mm <= total; mm += 5) {
      const pos = 1 + mm * zoom;
      const major = mm % 50 === 0, mid = mm % 10 === 0;
      const t = major ? 14 : mid ? 9 : 5;
      ctx.beginPath();
      if (horizontal) { ctx.moveTo(pos, RULER); ctx.lineTo(pos, RULER - t); }
      else { ctx.moveTo(RULER, pos); ctx.lineTo(RULER - t, pos); }
      ctx.stroke();
      if (major) {
        const label = String(horizontal ? mm : total - mm);
        if (horizontal) ctx.fillText(label, pos + 2, 10);
        else { ctx.save(); ctx.translate(10, pos + 2); ctx.rotate(-Math.PI / 2); ctx.fillText(label, 0, 0); ctx.restore(); }
      }
    }
  }
  ruler("ruler-top", true);
  ruler("ruler-left", false);
  stage.style.setProperty("--ruler", RULER + "px");

  // ocitavanje i rucni unos pozicije (koordinate masine: donji lijevi ugao objekta)
  const fx = document.getElementById("ed-x"), fy = document.getElementById("ed-y");
  const fw = document.getElementById("ed-w"), fh = document.getElementById("ed-h"), fr = document.getElementById("ed-r");
  const round = (v) => Math.round(v * 100) / 100;
  function readout() {
    const o = canvas.getActiveObject();
    if (!o) { [fx, fy, fw, fh, fr].forEach((f) => { f.value = ""; }); return; }
    const b = geomRect(o);
    fx.value = round(b.left); fy.value = round(H - (b.top + b.height));
    fw.value = round(b.width); fh.value = round(b.height); fr.value = round(o.angle || 0);
    const outside = b.left < 0 || b.top < 0 || b.left + b.width > W || b.top + b.height > H;
    say(outside ? "Objekat je izvan radne površine - server ga neće prihvatiti." : "", outside ? "text-danger" : "");
  }
  canvas.on({"selection:created": readout, "selection:updated": readout, "selection:cleared": readout,
             "object:moving": readout, "object:rotating": readout, "object:scaling": readout, "object:modified": readout});
  function applyFields() {
    const o = canvas.getActiveObject();
    if (!o) return;
    const ang = parseFloat(fr.value);
    if (!Number.isNaN(ang)) { o.rotate(ang); o.setCoords(); }
    const b = geomRect(o);
    const nx = parseFloat(fx.value), ny = parseFloat(fy.value);
    if (!Number.isNaN(nx)) o.left += nx - b.left;
    if (!Number.isNaN(ny)) o.top += (H - ny - b.height) - b.top;
    o.setCoords(); canvas.requestRenderAll(); readout();
  }
  document.querySelectorAll(".obj-field").forEach((f) => f.addEventListener("change", applyFields));
  document.getElementById("ed-lock-scale").addEventListener("change", (e) => {
    objects.forEach((o) => o.set({lockScalingX: e.target.checked, lockScalingY: e.target.checked}));
  });
  document.getElementById("ed-select-all").addEventListener("click", () => {
    canvas.discardActiveObject();
    canvas.setActiveObject(new fabric.ActiveSelection(objects.filter((o) => o.visible), {canvas}));
    canvas.requestRenderAll(); readout();
  });
  document.addEventListener("fab:layer-visibility", (e) => {
    objects.filter((o) => o.fabLayer === e.detail.layer).forEach((o) => o.set({visible: e.detail.visible}));
    canvas.discardActiveObject(); canvas.requestRenderAll();
  });

  document.getElementById("ed-save").addEventListener("click", async () => {
    canvas.discardActiveObject();                  // grupa -> pojedinacne transformacije objekata
    canvas.requestRenderAll();
    const items = objects.map((o) => ({index: o.fabIndex, matrix: o.calcTransformMatrix(),
                                       path_offset: [o.pathOffset.x, o.pathOffset.y]}));
    say("Spremam i provjeravam raspored…");
    const r = await fetch(`/api/jobs/${job}/layout`, {method: "POST", headers: {"Content-Type": "application/json"},
                                                       body: JSON.stringify({items})});
    const out = await r.json();
    if (!out.ok) { say(`${out.error}: ${out.message}`, "text-danger"); return; }
    window.location.href = `/history/${out.job_id}`;
  });
  window.fabEditor = {canvas, objects, zoom, W, H};   // za testove u browseru
  // cijeli dizajn selektovan na pocetku - prevlacenje pomjera sve dijelove zajedno
  canvas.setActiveObject(new fabric.ActiveSelection(objects, {canvas}));
  canvas.requestRenderAll(); readout();
  say(`${objects.length} objekata · sto ${W}×${H} mm · cijeli dizajn je selektovan (klik na prazno = pojedinačni objekti)`);
})();
