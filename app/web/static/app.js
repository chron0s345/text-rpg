// Small glue around htmx; no game logic lives here.
(function () {
  "use strict";

  const log = () => document.getElementById("log");
  const scrollLog = () => { const el = log(); if (el) el.scrollTop = el.scrollHeight; };

  // Buttons are disabled while their request runs (no double clicks into the rate limit).
  document.addEventListener("htmx:beforeRequest", (e) => {
    const el = e.detail.elt;
    if (el instanceof HTMLButtonElement) el.disabled = true;
    if (el instanceof HTMLFormElement) el.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  });
  document.addEventListener("htmx:afterRequest", (e) => {
    const el = e.detail.elt;
    if (el instanceof HTMLButtonElement) el.disabled = false;
    if (el instanceof HTMLFormElement) el.querySelectorAll("button").forEach((b) => { b.disabled = false; });
  });

  // Newest log entries stay visible; keep the log short in long sessions.
  document.addEventListener("htmx:afterSwap", (e) => {
    const el = log();
    if (!el || !(e.detail.target === el || el.contains(e.detail.target))) return;
    while (el.children.length > 300) el.firstElementChild.remove();
    scrollLog();
  });
  document.addEventListener("htmx:sseMessage", (e) => {
    if (e.detail.type === "log") scrollLog();
  });
  document.addEventListener("DOMContentLoaded", scrollLog);

  // SSE control messages.
  let opened = false;
  document.addEventListener("htmx:sseOpen", () => {
    if (opened) {
      // reconnect: events may have been missed -> reload log and panels
      htmx.ajax("GET", "/ui/log", { target: "#log", swap: "innerHTML" });
      htmx.trigger("#refresher", "refresh");
    }
    opened = true;
  });
  document.addEventListener("htmx:sseBeforeMessage", (e) => {
    if (e.detail.type === "ended") { e.preventDefault(); window.location.href = "/select"; }
    if (e.detail.type === "reload") { e.preventDefault(); window.location.reload(); }
  });

  // Leaving the page (logout, character selection, reload): close the stream first, so the
  // aborted connection does not show up as an SSE error in the console.
  window.addEventListener("pagehide", () => {
    const el = document.querySelector("[sse-connect]");
    const internal = el && el["htmx-internal-data"];
    if (internal && internal.sseEventSource) internal.sseEventSource.close();
  });

  // Closing the tab in a fight kills the character after the grace period: warn.
  window.addEventListener("beforeunload", (e) => {
    const status = document.getElementById("status");
    if (status && status.dataset.state === "in_combat") { e.preventDefault(); e.returnValue = ""; }
  });

  // Plain forms that need a confirmation (e.g. deleting a character).
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form instanceof HTMLFormElement && form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      e.preventDefault();
    }
  });
})();
