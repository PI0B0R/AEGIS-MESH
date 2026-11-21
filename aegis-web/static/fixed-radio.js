"use strict";

(() => {
  const panel = $("fixedPanel");
  const ssid = $("fixedSsid");
  const start = $("fixedStart");
  const stop = $("fixedStop");
  const status = $("fixedStatus");
  let pending = false;

  async function update(action) {
    if (pending) return;
    pending = true;
    start.disabled = stop.disabled = true;
    try {
      const response = await fetch("/api/station-radio", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Aegis-Session": "1"},
        body: JSON.stringify({action, ssid: ssid.value})
      });
      if (!response.ok) {
        if (panel.hidden && action === "status") return;
        throw new Error((await response.json()).error || "Nie udało się odczytać stanu skanera.");
      }
      const radio = await response.json();
      panel.hidden = false;
      ssid.disabled = radio.active;
      if (radio.active) ssid.value = radio.ssid;
      start.hidden = radio.active;
      stop.hidden = !radio.active;
      const message = radio.active
        ? "Laptop odczytuje raport co około 30 s, także po zamknięciu strony."
        : "Skaner zatrzymany.";
      const last = radio.at
        ? " Ostatni raport " + stamp(radio) + ". Liczba punktów sieci " + radio.ssid + " w raporcie: " + radio.count + "."
        : "";
      status.textContent = [message + last, radio.error].filter(Boolean).join(" ");
    } catch (error) {
      status.textContent = error.message;
    } finally {
      start.disabled = stop.disabled = pending = false;
    }
  }

  start.onclick = () => update("start");
  stop.onclick = () => update("stop");
  panel.addEventListener("toggle", () => {
    if (panel.open) update("status");
  });
  update("status");
  setInterval(() => {
    if (panel.open && !document.hidden) update("status");
  }, 5000);
})();
