"use strict";
const SAFETY_UNKNOWN = "SAFETY UNKNOWN";
const UNKNOWN_NOTE = "status not verified";
const VALID_ENTRIES = ["PAUSED", "ACTIVE"];

function classifySafety(payload) {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    return { text: SAFETY_UNKNOWN, cls: "safety-badge unknown", note: UNKNOWN_NOTE };
  }
  const status = payload.status;
  const statusOk = status === undefined || (typeof status === "string" && status.toLowerCase() === "ok");
  // No array/number coercion: only a real string may be trusted.
  const entries = typeof payload.entries === "string" ? payload.entries.toUpperCase() : "";
  if (!statusOk || !VALID_ENTRIES.includes(entries)) {
    return { text: SAFETY_UNKNOWN, cls: "safety-badge unknown", note: UNKNOWN_NOTE };
  }
  const paused = entries === "PAUSED";
  return {
    text: paused ? "ENTRIES PAUSED" : "ENTRIES ACTIVE",
    cls: "safety-badge " + (paused ? "paused" : "active"),
    note: "mode " + (payload.mode || "unknown") + " · account " + (payload.account || "unknown"),
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { classifySafety };
}
