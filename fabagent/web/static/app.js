// FabAgent forma: bez frameworka. Server uvijek ponovo validira sve.
(function () {
  "use strict";
  const form = document.getElementById("forma");
  if (!form) return;
  const fields = Array.from(form.querySelectorAll(".field"));
  const btn = document.getElementById("btn-preview");
  const note = document.getElementById("missing-note");
  const useDefaults = document.getElementById("use_defaults");

  const input = (f) => f.querySelector("input, select");

  function applicability() {
    fields.forEach((f) => {
      const param = f.dataset.appliesParam;
      if (!param) return;
      const ctrl = form.querySelector(`[name="${param}"]`);
      const ok = ctrl && f.dataset.appliesValues.split(",").includes(ctrl.value);
      f.classList.toggle("not-applicable", !ok);
      input(f).disabled = !ok;          // neprimjenjiv parametar se ne salje
    });
  }

  function checkRequired(showAll) {
    let missing = 0;
    fields.forEach((f) => {
      const el = input(f);
      const empty = !el.disabled && f.dataset.required === "true" && el.value.trim() === "";
      if (empty) missing++;
      if (showAll || el.dataset.touched) f.classList.toggle("show-client-error", empty);
    });
    btn.disabled = missing > 0;
    note.classList.toggle("d-none", missing === 0);
  }

  function fillDefaults(overwrite) {
    fields.forEach((f) => {
      const el = input(f);
      if (f.dataset.required === "true" || el.disabled) return;
      if (overwrite || el.value === "") el.value = f.dataset.default;
    });
  }

  fields.forEach((f) => {
    const el = input(f);
    ["input", "change"].forEach((ev) => el.addEventListener(ev, () => {
      el.dataset.touched = "1";
      f.querySelectorAll(".server-error").forEach((e) => e.remove());
      el.classList.remove("is-invalid");
      applicability();
      checkRequired(false);
    }));
  });
  useDefaults.addEventListener("change", () => { if (useDefaults.checked) fillDefaults(false); });
  document.getElementById("btn-reset").addEventListener("click", () => { fillDefaults(true); checkRequired(false); });
  form.addEventListener("submit", (e) => {
    checkRequired(true);
    if (btn.disabled) e.preventDefault();
    else { btn.textContent = "Generišem…"; }
  });

  applicability();
  checkRequired(false);
})();

// Interaktivni pregled: pan/zoom (svg-pan-zoom, lokalno) + upravljanje slojevima.
(function () {
  "use strict";
  document.querySelectorAll(".layer-viewer").forEach((viewer) => {
    const zoomers = [];
    viewer.querySelectorAll(".svg-container[data-pan-zoom]").forEach((box) => {
      const svg = box.querySelector("svg");
      if (!svg) return;
      svg.setAttribute("width", "100%");
      svg.setAttribute("height", "100%");
      if (typeof svgPanZoom === "function") {
        zoomers.push(svgPanZoom(svg, {
          zoomEnabled: true, panEnabled: true, controlIconsEnabled: true,
          fit: true, center: true, minZoom: 0.5, maxZoom: 400, zoomScaleSensitivity: 0.3,
        }));
      }
    });
    viewer.querySelectorAll(".layer-toggle").forEach((cb) => {
      cb.addEventListener("change", () => {
        viewer.querySelectorAll(`.svg-container g[id="${cb.dataset.layer}"]`).forEach((g) => {
          g.classList.toggle("layer-hidden", !cb.checked);
        });
      });
    });
    const reset = viewer.querySelector(".pz-reset");
    if (reset) reset.addEventListener("click", () => zoomers.forEach((z) => { z.resize(); z.reset(); }));
  });
})();

// Opcije trasiranja: klizac <-> broj, a fiksni/adaptivni prag mijenja obaveznost praga.
(function () {
  "use strict";
  document.querySelectorAll(".range-link").forEach((range) => {
    const num = document.getElementById(range.dataset.target);
    const badge = range.parentElement.querySelector(".range-out");
    range.addEventListener("input", () => { num.value = range.value; if (badge) badge.textContent = range.value; });
    num.addEventListener("input", () => { if (num.value !== "") range.value = num.value; if (badge) badge.textContent = num.value || "—"; });
  });
  document.querySelectorAll(".trace-mode").forEach((sel) => {
    const scope = sel.closest("form") || document;
    const sync = () => scope.querySelectorAll(".thr-req").forEach((s) => { s.hidden = sel.value === "adaptivni"; });
    sel.addEventListener("change", sync);
    sync();
  });
})();

// Rezultat uploada: Prikazi original / obradjenu bitmapu / rezultat.
(function () {
  "use strict";
  document.querySelectorAll(".view-switch").forEach((group) => {
    const card = group.closest(".card");
    group.querySelectorAll("[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => {
        group.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b === btn));
        card.querySelectorAll(".view-pane").forEach((p) => { p.hidden = p.dataset.pane !== btn.dataset.view; });
      });
    });
  });
})();

// Kamera preko pregleda lasera: slika radne povrsine transformisana u koordinate lasera (mm).
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  document.querySelectorAll(".camera-controls").forEach((ctl) => {
    const viewer = ctl.closest(".layer-viewer");
    const msg = ctl.querySelector(".camera-msg");
    const offX = ctl.querySelector(".cam-x"), offY = ctl.querySelector(".cam-y");
    let status = null;

    function images() { return viewer.querySelectorAll("image.camera-overlay"); }
    function place() {
      const x = parseFloat(offX.value), y = parseFloat(offY.value);
      if (Number.isNaN(x) || Number.isNaN(y) || !status) return;
      images().forEach((img) => { img.setAttribute("x", -x); img.setAttribute("y", -y); });
    }
    function setSrc() {
      const url = "/camera/warped.jpg?t=" + Date.now();
      images().forEach((img) => img.setAttribute("href", url));
    }
    async function show() {
      msg.textContent = "Učitavam kameru…";
      try {
        status = await (await fetch("/camera/status", {cache: "no-store"})).json();
      } catch (e) { msg.textContent = "Kamera nije dostupna."; return; }
      if (!status.calibrated) {
        msg.innerHTML = 'Kamera nije kalibrisana. <a href="/camera">Kalibriši je ovdje</a>.';
        return;
      }
      viewer.querySelectorAll(".svg-container svg").forEach((svg) => {
        if (svg.querySelector("image.camera-overlay")) return;
        const vp = svg.querySelector(".svg-pan-zoom_viewport") || svg;
        const img = document.createElementNS(NS, "image");
        img.setAttribute("class", "camera-overlay");
        img.setAttribute("width", status.area_mm[0]);
        img.setAttribute("height", status.area_mm[1]);
        img.setAttribute("preserveAspectRatio", "none");
        img.setAttribute("opacity", ctl.dataset.opacity);
        img.addEventListener("error", () => { msg.textContent = "Slika kamere nije dostupna (kamera isključena?)."; });
        vp.insertBefore(img, vp.firstChild);          // ispod CUT/ENGRAVE/SCORE slojeva
      });
      setSrc();
      place();
      msg.textContent = "Kamera prikazana (" + status.source + ").";
      ctl.querySelector(".cam-refresh").disabled = false;
      ctl.querySelector(".cam-hide").disabled = false;
    }
    ctl.querySelector(".cam-show").addEventListener("click", show);
    ctl.querySelector(".cam-refresh").addEventListener("click", () => { setSrc(); msg.textContent = "Osvježeno."; });
    ctl.querySelector(".cam-hide").addEventListener("click", () => {
      images().forEach((img) => img.remove());
      msg.textContent = "";
      ctl.querySelector(".cam-refresh").disabled = true;
      ctl.querySelector(".cam-hide").disabled = true;
    });
    [offX, offY].forEach((el) => el.addEventListener("input", place));
  });
})();
