let DATA = [];
async function load() {
  try {
    DATA = await api("/api/desk");
    const sel = $("cand");
    sel.innerHTML = DATA.map((t, i) => {
      const c = t.candidate || {};
      return `<option value="${i}">${esc(c.symbol || "?")} · ${esc(String(c.candidate_id || "").slice(-12))} · ${esc(c.status || "")}</option>`;
    }).join("");
    if (!DATA.length) {
      ["hero", "cols", "cbars", "chips", "ctimeline", "crisk"].forEach((id) => { $(id).innerHTML = ""; });
      $("hero").innerHTML = '<span class="empty">No desk analyses yet — the next pipeline run appears here.</span>';
      return;
    }
    sel.onchange = render;
    render();
  } catch (e) { showError("hero", e); }
}
function opinionBlock(o, cls, title) {
  if (!o) return `<div class="case ${cls}"><h3>${title}</h3><span class="empty">No argument recorded.</span></div>`;
  let detail = {};
  try { detail = JSON.parse(o.evidence || "{}"); } catch (_) { detail = {}; }
  const points = detail.thesis ? `<p><b>Thesis:</b> ${esc(detail.thesis)}</p>` : "";
  const against = detail.invalidation ? `<p><b>Invalidation:</b> ${esc(detail.invalidation)}</p>` : "";
  const ev = Array.isArray(detail.evidence) && detail.evidence.length
    ? `<ul>${detail.evidence.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  return `<div class="case ${cls}"><h3>${title} — ${esc(o.confidence)}%</h3>` +
    `<p>${esc(o.verdict)}</p>${points}${against}${ev}</div>`;
}
function render() {
  const t = DATA[$("cand").value || 0];
  if (!t || !t.candidate) return;
  const c = t.candidate;
  const ops = t.opinions || [];
  const get = (a) => ops.find((o) => o.agent === a);
  const j = get("judge");
  const verdict = j ? j.verdict : "—";
  const vcls = verdict.includes("CALL") ? "v-call" : verdict.includes("PUT") ? "v-put" : "v-none";
  const rk = (t.risk_decisions || []).slice(-1)[0] || null;
  $("hero").innerHTML =
    `<div class="verdict-hero"><span class="verdict-badge ${vcls}">${esc(verdict)}</span>` +
    `<span><span class="verdict-conf">${esc(j ? j.confidence : "—")}%</span><br><small>judge confidence · agreement ${esc(j ? (JSON.parse(j.evidence || "{}").agreement ?? "—") : "—")}%</small></span>` +
    `<span><b>${esc(c.symbol)}</b> · ${esc(c.status)}<br><small>Risk: ` +
    (rk ? (rk.approved ? '<span class="risk-pass">PASS</span>' : '<span class="risk-fail">FAIL</span>') : "NO RISK RUN") +
    `</small></span></div>`;
  $("cols").innerHTML =
    opinionBlock(get("bull"), "bull", "🐂 Bull case") +
    opinionBlock(get("bear"), "bear", "🐻 Bear case");
  const red = get("red");
  if (red && Number(red.confidence) >= 50) {
    let thesis = "";
    try { thesis = JSON.parse(red.evidence || "{}").thesis || ""; } catch (_) { thesis = ""; }
    $("redcard").hidden = false;
    $("redbox").innerHTML = `<h3>🚩 Red team objects (${esc(red.confidence)}%)</h3><p>${esc(thesis || red.verdict)}</p>`;
  } else {
    $("redcard").hidden = true;
    $("redbox").innerHTML = "";
  }
  const chips = ["market", "news", "macro"].map(get).filter(Boolean).map((o) =>
    `<span class="chip">${esc(o.agent)}: ${esc(o.verdict)} ${esc(o.confidence)}%</span>`).join("");
  $("chips").innerHTML = chips || '<span class="empty">No signal views recorded.</span>';
  $("cbars").innerHTML = ops.filter((o) => o.agent !== "judge")
    .map((o) => bar(o.confidence, o.agent + " · " + o.verdict)).join("");
  paintBars($("cbars"));
  const ev = [];
  let source = "";
  try { source = JSON.parse(c.payload || "{}").source || ""; } catch (_) { source = ""; }
  ev.push(`<b>Discovered</b> ${esc(c.created_at || "")} (${esc(c.side || "")} · ${esc(source)})`);
  ops.forEach((o) => {
    let detail = {};
    try { detail = JSON.parse(o.evidence || "{}"); } catch (_) { detail = {}; }
    const what = detail.thesis || detail.summary ||
      (Array.isArray(detail.evidence) ? detail.evidence.join("; ") : "");
    ev.push(`<b>${esc(o.agent)}</b> → ${esc(o.verdict)} ${esc(o.confidence)}%${what ? " — " + esc(what) : ""}`);
  });
  if (rk) {
    let failed = [];
    try { failed = JSON.parse(rk.failed_rules || "[]"); } catch (_) { failed = []; }
    ev.push(`<b>Risk ${rk.approved ? "PASS" : "FAIL"}</b>${failed.length ? " — failed: " + esc(failed.join(", ")) : " — all rules passed"}`);
  }
  $("ctimeline").innerHTML = ev.map((x) => `<li>${x}</li>`).join("");
  $("crisk").innerHTML = rk ? `<small>${esc(rk.decision_id)} · ${esc(rk.created_at)}</small>`
    : "<span class='empty'>Advisory run — execution stayed with the gated paper flow.</span>";
}
load();
setInterval(load, 15000);
