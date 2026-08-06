/* Bounded same-origin refresh for explicitly opted-in read-only components. */
(function () {
  "use strict";
  const minimum = 15, maximum = 300;
  const root = document.querySelector("[data-auto-refresh]");
  if (!root) return;
  const seconds = Number(root.dataset.refreshInterval || "0");
  if (!Number.isFinite(seconds) || seconds < minimum || seconds > maximum) return;
  const endpoint = root.dataset.refreshEndpoint;
  if (!endpoint || !endpoint.startsWith("/api/")) return;
  let timer = null, inFlight = false, controller = null;
  const status = root.querySelector("[data-refresh-status]");
  const poll = function () {
    if (document.hidden || inFlight) return;
    inFlight = true; if (controller) controller.abort(); controller = new AbortController();
    const attempted = new Date().toISOString(); root.dataset.lastAttempted = attempted;
    fetch(endpoint, { method: "GET", credentials: "same-origin", signal: controller.signal, headers: { Accept: "application/json" } })
      .then(function (response) { if (!response.ok) throw new Error("REFRESH_FAILED"); return response.json(); })
      .then(function (payload) { if (!payload || typeof payload !== "object") throw new Error("SCHEMA_MISMATCH"); root.dataset.lastSuccessful = new Date().toISOString(); if (status) status.textContent = "Auto-refresh: ON; last validated update recorded."; })
      .catch(function (error) { if (status) status.textContent = error && error.message === "SCHEMA_MISMATCH" ? "Schema mismatch; existing data retained." : "Refresh failed; existing data retained."; })
      .finally(function () { inFlight = false; });
  };
  const schedule = function () { if (timer) clearInterval(timer); timer = setInterval(poll, seconds * 1000); };
  document.addEventListener("visibilitychange", function () { if (document.hidden) { if (timer) clearInterval(timer); timer = null; } else schedule(); });
  schedule();
}());
