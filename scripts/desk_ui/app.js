/* AI Trading Desk — shared frontend helpers. No dependencies. */
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
async function api(path, opts) {
  const r = await fetch(path, {cache: "no-store", ...(opts || {})});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body && body.error) || ("HTTP " + r.status));
  return body;
}
function bar(pct, label) {
  pct = Math.max(0, Math.min(100, Number(pct) || 0));
  return `<div class="brow"><span class="blabel">${esc(label)}</span>` +
    `<span class="bar bar-inline"><i data-w="${pct}"></i></span> ${pct}%</div>`;
}
function paintBars(root) {
  (root || document).querySelectorAll("i[data-w]").forEach((el) => {
    el.style.width = el.dataset.w + "%";
  });
}
function pill(text, cls) {
  return `<span class="pill ${cls || ""}">${esc(text)}</span>`;
}
function setActiveNav() {
  const page = (location.pathname.split("/").pop() || "index.html").replace(".html", "");
  document.querySelectorAll("nav.top a[data-page]").forEach((a) => {
    if (a.dataset.page === page || (page === "" && a.dataset.page === "index")) a.classList.add("active");
  });
}
function showError(id, err) {
  const el = $(id);
  if (el) el.innerHTML = `<span class="error">Failed to load: ${esc(err.message || err)}</span>`;
}
document.addEventListener("DOMContentLoaded", setActiveNav);
