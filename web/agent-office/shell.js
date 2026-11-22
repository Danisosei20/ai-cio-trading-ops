"use strict";

// Desk views map to the standalone read-only pages served by agent_office.py.
// Overview is the trading desk overview (/trading), not an order path.
const VIEW_SRC = {
  office: "/",
  market: "/market",
  pipeline: "/pipeline",
  overview: "/trading",
  options: "/options",
};

const VIEW_TITLE = {
  office: "Office",
  market: "Market",
  pipeline: "Pipeline",
  overview: "Overview",
  options: "Options",
};

const DEFAULT_VIEW = "office";

let frame;
let titleEl;
let navButtons = [];
let badgeEl;
let noteEl;
// The HTML already loads the office view (src="/"), so start it as active
// and avoid reloading the iframe when that view is selected at startup.
let activeView = DEFAULT_VIEW;

function isKnownView(view) {
  return Object.prototype.hasOwnProperty.call(VIEW_SRC, view);
}

// Switch the desk view in place: only the iframe src changes. The shell
// document itself is never reloaded and the URL is updated without navigation.
function applyView(view) {
  const active = isKnownView(view) ? view : DEFAULT_VIEW;
  if (active !== activeView) {
    activeView = active;
    frame.src = VIEW_SRC[active];
  }
  frame.title = VIEW_TITLE[active] + " desk view";
  titleEl.textContent = VIEW_TITLE[active];
  navButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.view === active));
  });
  history.replaceState(null, "", "?view=" + active);
}

function applySafety(result) {
  badgeEl.textContent = result.text;
  badgeEl.className = result.cls;
  noteEl.textContent = result.note;
}

// Fail-closed contract lives in shell_safety.js. classifySafety() yields
// SAFETY UNKNOWN / "status not verified" unless entries is PAUSED or ACTIVE
// and any present status is ok; a null payload is always unknown.
async function refreshSafety() {
  try {
    const response = await fetch("/api/safety", {
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) {
      applySafety(classifySafety(null));
      return;
    }
    const payload = await response.json();
    applySafety(classifySafety(payload));
  } catch (error) {
    applySafety(classifySafety(null));
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
