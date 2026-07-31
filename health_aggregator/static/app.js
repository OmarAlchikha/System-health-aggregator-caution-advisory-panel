/* Panel client: polls /api/state at 2 Hz and re-renders.
   Polling (vs SSE/websocket) is a deliberate choice — see README. */
"use strict";

const $ = (s) => document.querySelector(s);

async function post(path, body) {
  await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify(body || {}) });
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ---- CAS message stack ---- */
function renderCas(msgs) {
  const list = $("#cas-list");
  list.innerHTML = msgs.map((m) => `
    <li class="sev-${m.severity}${m.acked ? "" : " unacked"}">
      <span class="sev-tag">${m.severity}</span>
      <span class="cas-msg">${esc(m.message)}${m.condition_present ? "" : " ✓"}</span>
      <span class="cas-meta">${m.latched ? "LATCHED · " : ""}${m.age_s}s · SIM</span>
    </li>`).join("");
  $("#cas-empty").classList.toggle("hidden", msgs.length > 0);
}

function renderMasters(masters) {
  for (const [sev, el] of [["WARNING", $("#master-warning")], ["CAUTION", $("#master-caution")]]) {
    const st = masters[sev] || {};
    el.classList.toggle("lit", !!st.lit);
    el.classList.toggle("flashing", !!st.flashing);
  }
}

/* ---- SSPC tile ---- */
function renderSspc(tile) {
  const tb = $("#sspc-table tbody");
  if (!tile) return;
  const rows = Object.entries(tile.channels).sort(([a], [b]) => a.localeCompare(b));
  tb.innerHTML = rows.map(([ch, v]) => {
    const pct = Math.min(100, v.i2t_pct);
    const cls = v.state === "ON" ? "state-ok" : "state-bad";
    const fillCls = pct >= 90 ? "crit" : pct >= 60 ? "hot" : "";
    return `<tr>
      <td>${ch}${v.essential ? "*" : ""}</td><td>${esc(v.load)}</td>
      <td class="${cls}">${v.state}</td>
      <td>${v.current_a.toFixed(1)} / ${v.rated_a.toFixed(0)}A</td>
      <td><div class="i2t-wrap"><div class="i2t-bar">
        <div class="i2t-fill ${fillCls}" style="width:${pct}%"></div>
      </div><span class="i2t-pct">${v.i2t_pct.toFixed(0)}%</span></div></td>
    </tr>`;
  }).join("");
}

/* ---- Bus transfer tile ---- */
const BUS_STATE_CLS = { ON_MAIN: "state-ok", ON_ALT: "state-warn",
                        XFER_IN_PROG: "state-warn", XFER_FAIL: "state-bad" };
function renderBus(tile) {
  if (!tile) return;
  const v = tile.channels.ESS_BUS;
  if (!v) return;
  $("#bus-mimic").innerHTML = `
    <div class="bus-row"><span class="lbl">STATE</span>
      <span class="bus-state ${BUS_STATE_CLS[v.state] || ""}">${v.state}</span></div>
    <div class="bus-row"><span class="lbl">MAIN SRC</span><span>${v.v_main.toFixed(1)} V</span></div>
    <div class="bus-row"><span class="lbl">ALTN SRC</span><span>${v.v_alt.toFixed(1)} V</span></div>
    <div class="bus-row"><span class="lbl">ESS BUS</span><span>${v.v_bus.toFixed(1)} V</span></div>`;
}

/* ---- Harness tile ---- */
function wireStatus(v) {
  if (v.r_loop_mohm === null) return ["OPEN", "w-fault"];
  if (v.ins_pwr_mohm < 0.1) return ["SHT→PWR", "w-warn"];
  if (v.ins_gnd_mohm < 0.1) return ["SHT→GND", "w-fault"];
  if (v.r_loop_mohm > 500) return ["HIGH R", "w-fault"];
  return ["OK", "w-ok"];
}
function renderHarness(tile) {
  if (!tile) return;
  const wires = Object.entries(tile.channels).sort(([a], [b]) => a.localeCompare(b));
  $("#wire-grid").innerHTML = wires.map(([id, v]) => {
    const [stat, cls] = wireStatus(v);
    const r = v.r_loop_mohm === null ? "—" : `${v.r_loop_mohm.toFixed(0)} mΩ`;
    return `<div class="wire ${cls}">
      <div class="wid">${id}</div><div class="wstat">${stat}</div>
      <div class="wres">${v.from}→${v.to}</div><div class="wres">${r}</div></div>`;
  }).join("");
}

/* ---- History ---- */
function renderHistory(entries) {
  $("#history-table tbody").innerHTML = entries.map((e) => `
    <tr><td>${esc(e.t_wall.replace("T", " ").slice(0, 23))}</td>
    <td>${e.event}</td>
    <td class="sev-${e.severity}">${e.severity}</td>
    <td>${esc(e.message)}</td><td>${e.source} · SIM</td></tr>`).join("");
}

/* ---- Poll loop ---- */
async function refresh() {
  try {
    const [state, history] = await Promise.all([
      fetch("/api/state").then((r) => r.json()),
      fetch("/api/history?limit=100").then((r) => r.json()),
    ]);
    renderCas(state.messages);
    renderMasters(state.masters);
    renderSspc(state.telemetry.sspc);
    renderBus(state.telemetry.bus_xfr);
    renderHarness(state.telemetry.harness);
    renderHistory(history);
  } catch (e) { /* server briefly unreachable; next poll retries */ }
}

$("#master-warning").addEventListener("click", () => post("/api/ack"));
$("#master-caution").addEventListener("click", () => post("/api/ack"));
$("#btn-reset").addEventListener("click", () => post("/api/reset"));
$("#inject-buttons").addEventListener("click", (ev) => {
  const scn = ev.target.dataset && ev.target.dataset.scn;
  if (scn) post("/api/inject", { scenario: scn });
});

refresh();
setInterval(refresh, 500);
