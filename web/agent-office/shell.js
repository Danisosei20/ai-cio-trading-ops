"use strict";

// Desk views map to the standalone read-only pages served by agent_office.py.
// Overview is the trading desk overview (/trading), not an order path.
const VIEW_SRC = {
  office: "/",
  market: "/market",
  pipeline: "/pipeline",
  overview: "/trading",
};

const VIEW_TITLE = {
  office: "Office",
  market: "Market",
  pipeline: "Pipeline",
  overview: "Overview",
};

const DEFAULT_VIEW = "office";
const SAFETY_UNKNOWN = "SAFETY UNKNOWN";
const UNKNOWN_NOTE = "status not verified";

let frame;
let titleEl;
let navButtons = [];
let badgeEl;
let noteEl;

function isKnownView(view) {
  return Object.prototype.hasOwnProperty.call(VIEW_SRC, view);
}

// Switch the desk view in place: only the iframe src changes. The shell
// document itself is never reloaded and the URL is updated without navigation.
function applyView(view) {
  const active = isKnownView(view) ? view : DEFAULT_VIEW;
  frame.src = VIEW_SRC[active];
  frame.title = VIEW_TITLE[active] + " desk view";
  titleEl.textContent = VIEW_TITLE[active];
  navButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.view === active));
  });
  history.replaceState(null, "", "?view=" + active);
}

function showUnknown() {
  badgeEl.textContent = SAFETY_UNKNOWN;
  badgeEl.className = "safety-badge unknown";
  noteEl.textContent = UNKNOWN_NOTE;
}

async function refreshSafety() {
  try {
    const response = await fetch("/api/safety", {
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) {
      showUnknown();
      return;
    }
    const payload = await response.json();
    const entries = String(payload.entries || "").toUpperCase();

    // Fail closed: trust the badge only when entries is a known state and any
    // status field that is present is explicitly ok. The viewer's safety
    // endpoint has no top-level status, so a missing status is acceptable.
    const status = payload.status;
    const statusOk = status === undefined || String(status).toLowerCase() === "ok";
    if (!statusOk || (entries !== "PAUSED" && entries !== "ACTIVE")) {
      showUnknown();
      return;
    }

    const paused = entries === "PAUSED";
    badgeEl.textContent = paused ? "ENTRIES PAUSED" : "ENTRIES ACTIVE";
    badgeEl.className = "safety-badge " + (paused ? "paused" : "active");
    const mode = payload.mode || "unknown";
    const account = payload.account || "unknown";
    noteEl.textContent = "mode " + mode + " · account " + account;
  } catch (error) {
    showUnknown();
  }
}

function init() {
  frame = document.getElementById("shell-view");
  titleEl = document.getElementById("shell-title");
  badgeEl = document.getElementById("shell-safety-badge");
  noteEl = document.getElementById("shell-safety-note");
  navButtons = Array.from(
    document.querySelectorAll("#shell-nav button[data-view]")
  );

  const requested = new URLSearchParams(window.location.search).get("view");
  applyView(requested);

  navButtons.forEach((button, index) => {
    button.addEventListener("click", () => applyView(button.dataset.view));
    // Native buttons already handle Enter/Space; arrows move across the nav.
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
        return;
      }
      event.preventDefault();
      const step = event.key === "ArrowRight" ? 1 : -1;
      const next = navButtons[(index + step + navButtons.length) % navButtons.length];
      next.focus();
      applyView(next.dataset.view);
    });
  });

  refreshSafety();
  setInterval(() => {
    if (!document.hidden) {
      refreshSafety();
    }
  }, 30000);
}

document.addEventListener("DOMContentLoaded", init);
