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
    // chat and invite fields empty themselves once sent (no inline handlers: see the CSP)
    if (el instanceof HTMLFormElement && el.hasAttribute("data-reset-on-success") && e.detail.successful) el.reset();
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

  // Panels are re-rendered after every action; foldouts marked data-keep-open stay as the
  // player left them.
  const openFolds = new Set();
  document.addEventListener("toggle", (e) => {
    const key = e.target instanceof HTMLDetailsElement ? e.target.dataset.keepOpen : undefined;
    if (!key) return;
    if (e.target.open) openFolds.add(key); else openFolds.delete(key);
  }, true);
  document.addEventListener("htmx:oobAfterSwap", () => {
    document.querySelectorAll("details[data-keep-open]").forEach((d) => {
      if (openFolds.has(d.dataset.keepOpen)) d.open = true;
    });
  });

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

  // Animations for what happens to you: log lines marked data-fx (app/web/effects.py) shake the
  // foe, flash the status bar, float damage numbers or show a banner. Effects are classes on
  // the .game container for a moment, so they survive the panels being re-rendered meanwhile.
  // Nothing moves for players who asked their system for reduced motion.
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  const timers = {};
  const flash = (name, ms) => {
    const game = document.querySelector(".game");
    if (!game) return;
    game.classList.remove("fx-" + name);
    void game.offsetWidth; // restart the animation when the same effect comes twice in a row
    game.classList.add("fx-" + name);
    clearTimeout(timers[name]);
    timers[name] = setTimeout(() => game.classList.remove("fx-" + name), ms);
  };
  const float = (text, kind, selectors) => {
    const anchor = selectors.map((s) => document.querySelector(s)).find((el) => el);
    if (!anchor) return;
    const box = anchor.getBoundingClientRect();
    const el = document.createElement("div");
    el.className = "fx-float fx-float-" + kind;
    el.textContent = text;
    el.style.left = box.left + box.width / 2 + window.scrollX + "px";
    el.style.top = box.top + window.scrollY + "px";
    document.body.appendChild(el);
    el.addEventListener("animationend", () => el.remove());
  };
  const banner = (text, kind) => {
    document.querySelectorAll(".fx-banner").forEach((old) => old.remove());
    const el = document.createElement("div");
    el.className = "fx-banner fx-banner-" + kind;
    el.textContent = text;
    document.body.appendChild(el);
    el.addEventListener("animationend", () => el.remove());
  };
  const FOE = [".foe .sprite", ".room li.fighting .sprite", ".room .scene"];
  const HP = ["#hp-bar", "#status"];
  const PLAY = {
    strike: (n) => { flash("strike", 400); if (n) float("-" + n, "damage", FOE); },
    hurt: (n) => { flash("hurt", 450); if (n) float("-" + n, "hurt", HP); },
    crushed: (n) => { flash("crushed", 600); if (n) float("-" + n + "!", "crushed", HP); },
    ambush: () => flash("crushed", 600),
    drain: (n) => { flash("drain", 500); if (n) float("+" + n, "drain", FOE); },
    heal: (n) => { flash("heal", 600); if (n) float("+" + n, "heal", HP); },
    gold: (n) => { if (n) float("+" + n + " Gold", "gold", [".status .gold", "#status"]); },
    kill: () => flash("kill", 700),
    death: () => flash("death", 2500),
    craft: () => flash("craft", 700),
    level: (n, text) => { flash("level", 1200); banner(text, "level"); },
    achievement: (n, text) => banner(text.split(" – ")[0], "achievement"),
    quest: (n, text) => { flash("quest", 900); banner(text.split(/[.!]/)[0], "quest"); },
  };
  const play = (li) => {
    const effect = PLAY[li.dataset.fx];
    if (!effect || calm.matches) return;
    effect(parseInt(li.dataset.amount || "0", 10), li.textContent.trim());
  };
  document.addEventListener("DOMContentLoaded", () => {
    const el = log();
    if (!el) return;
    new MutationObserver((records) => {
      records.forEach((r) => r.addedNodes.forEach((node) => {
        if (node instanceof HTMLLIElement && node.dataset.fx) play(node);
      }));
    }).observe(el, { childList: true });
  });

  // Plain forms that need a confirmation (e.g. deleting a character).
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form instanceof HTMLFormElement && form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      e.preventDefault();
    }
  });
})();
