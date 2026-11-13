// Layer Manager: ucitava i cuva postavke slojeva joba (PUT /api/jobs/<id>/layers). Server validira sve.
(function () {
  "use strict";
  document.querySelectorAll(".layer-manager-card").forEach(async (card) => {
    const job = card.dataset.job;
    const tbody = card.querySelector("tbody");
    const msg = card.querySelector(".lm-msg");
    const res = await fetch(`/api/jobs/${job}/layers`, {cache: "no-store"});
    const data = await res.json();
    if (!data.ok) { msg.textContent = data.message; msg.className = "lm-msg small text-danger"; return; }
    card.querySelector(".lm-source").textContent = "izvor: " + data.source;
    data.used.forEach((layer) => {
      const s = data.layers[layer] || {};
      const tr = document.createElement("tr");
      tr.dataset.layer = layer;
      const opts = data.modes.map((m) => `<option ${m === s.mode ? "selected" : ""}>${m}</option>`).join("");
      const lock = data.locked_interval !== null && data.locked_interval !== undefined;
      tr.innerHTML = `
        <td><i class="sw" style="background:${data.colors[layer]}"></i></td><td>${layer}</td>
        <td><select class="form-select form-select-sm lm-mode" ${lock ? "disabled" : ""}>${opts}</select></td>
        <td><input class="form-control form-control-sm lm-speed" type="number" step="0.1" min="${data.ranges.speed_mm_s[0]}" max="${data.ranges.speed_mm_s[1]}" value="${s.speed_mm_s ?? ""}"></td>
        <td><input class="form-control form-control-sm lm-power" type="number" step="1" min="0" max="100" value="${s.power_pct ?? ""}"></td>
        <td><input class="form-control form-control-sm lm-passes" type="number" step="1" min="${data.ranges.passes[0]}" max="${data.ranges.passes[1]}" value="${s.passes ?? ""}"></td>
        <td><input class="form-control form-control-sm lm-interval" type="number" step="0.01" min="${data.ranges.interval_mm[0]}" max="${data.ranges.interval_mm[1]}" value="${s.interval_mm ?? ""}" ${lock ? "readonly" : ""}></td>
        <td><input class="form-check-input lm-show" type="checkbox" checked></td>`;
      tbody.appendChild(tr);
      const mode = tr.querySelector(".lm-mode"), interval = tr.querySelector(".lm-interval");
      const sync = () => { interval.disabled = mode.value !== "Engrave"; };
      mode.addEventListener("change", sync); sync();
      tr.querySelector(".lm-show").addEventListener("change", (e) => {
        document.dispatchEvent(new CustomEvent("fab:layer-visibility", {detail: {layer, visible: e.target.checked}}));
      });
    });
    card.querySelector(".lm-save").addEventListener("click", async () => {
      const layers = {};
      tbody.querySelectorAll("tr").forEach((tr) => {
        const v = (c) => tr.querySelector(c).value;
        layers[tr.dataset.layer] = {mode: v(".lm-mode"), speed_mm_s: v(".lm-speed"), power_pct: v(".lm-power"),
                                    passes: v(".lm-passes"), interval_mm: v(".lm-mode") === "Engrave" ? v(".lm-interval") : null};
      });
      const r = await fetch(`/api/jobs/${job}/layers`, {method: "PUT", headers: {"Content-Type": "application/json"},
                                                         body: JSON.stringify({layers})});
      const out = await r.json();
      msg.className = "lm-msg small " + (out.ok ? "text-success" : "text-danger");
      msg.textContent = out.ok ? "Sačuvano u job.json." : `${out.error}: ${out.message}`;
      if (out.ok) card.querySelector(".lm-source").textContent = "izvor: sacuvano u job.json";
    });
  });
})();
