/* AEGIS MESH panel: 5 screens, each with one purpose.
 * Data from scanned hosts (titles, headers, banners) is untrusted, so the DOM is always built
 * with textContent (helper h), never innerHTML. Buttons the user has no right to are not rendered. */
"use strict";

const ROLES = ["czytelnik", "analityk", "edytor", "admin"];
const SCREENS = [
  ["pulpit", "Pulpit", "czytelnik"], ["cele", "Cele", "czytelnik"], ["hosty", "Hosty", "czytelnik"],
  ["sygnatury", "Sygnatury", "czytelnik"], ["ustawienia", "Ustawienia", "admin"],
];
const STATUS = ["nowe", "potwierdzone", "w-trakcie", "rozwiązane", "fałszywy-alarm", "zaakceptowane-ryzyko"];
const KIND_HELP = {
  header: "nagłówek odpowiedzi, np. X-Powered-By", cookie: "nazwa ciasteczka", html: "fragment kodu strony",
  title: "tytuł strony", favicon: "hash favicony (mmh3 lub md5)", path: "odpowiedź na ścieżkę, np. /admin",
  service: "baner usługi z nmapa", tls: "certyfikat TLS (CN, wystawca, SAN)",
};
const STATE = { active: "aktywny", unstable: "niestabilny", inactive: "nieaktywny" };
const CHANGE = {
  new_host: "nowy host", new_service: "nowa usługa", gone_service: "usługa zniknęła", changed_service: "zmiana",
  unstable_host: "niestabilny", gone_host: "nieaktywny", host_back: "wrócił", daypart: "pora dnia",
};

let me = null;
let timer = null;

// ---- helpers ---------------------------------------------------------------------
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "value") el.value = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}
const $ = (sel) => document.querySelector(sel);
const can = (role) => !!me && ROLES.indexOf(me.role) >= ROLES.indexOf(role);
const token = () => sessionStorage.getItem("aegis-token");
const prio = (p) => h("span", { class: `prio p${p || 0}`, title: `Priorytet ${p}` }, `P${p || 0}`);
const tags = (list, onclick) => (list || []).map((t) => onclick
  ? h("button", { class: "tag", type: "button", onclick: () => onclick(t) }, t) : h("span", { class: "tag" }, t));
const tech = (list) => (list || []).map((t) => (t.version ? `${t.name} ${t.version}` : t.name)).join(", ");

function when(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  const year = d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {};
  return d.toLocaleString("pl-PL", { day: "numeric", month: "short", ...year, hour: "2-digit", minute: "2-digit" });
}
function seconds(s) {
  if (s === null || s === undefined) return "–";
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}
function toast(text, error) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.toggle("error", !!error);
  el.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => el.classList.remove("show"), 3500);
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", ...(token() ? { Authorization: `Bearer ${token()}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401 && path !== "/api/auth/login") {
    sessionStorage.removeItem("aegis-token");
    me = null;
    showLogin("Sesja wygasła. Zaloguj się ponownie.");
    throw new Error("401");
  }
  const type = response.headers.get("content-type") || "";
  const data = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === "object" ? data.detail : data;
    const text = Array.isArray(detail) ? detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ") : detail;
    throw new Error(text || `Błąd ${response.status}`);
  }
  return data;
}
async function act(button, work, done) {
  if (button) button.disabled = true;
  try {
    const result = await work();
    if (done) toast(typeof done === "function" ? done(result) : done);
    return result;
  } catch (error) {
    if (error.message !== "401") toast(error.message, true);
    return undefined;
  } finally {
    if (button) button.disabled = false;
  }
}
async function download(path, filename) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${token()}` } });
  if (!response.ok) return toast(`Nie udało się pobrać (${response.status}).`, true);
  const url = URL.createObjectURL(await response.blob());
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Nie udało się odczytać pliku."));
    reader.readAsText(file, "utf-8");
  });
}
function filePicker(accept, onfile) {
  const input = h("input", { type: "file", accept, hidden: true, onchange: () => input.files[0] && onfile(input.files[0]) });
  return input;
}

// ---- login and layout --------------------------------------------------------------
function showLogin(message) {
  clearInterval(timer);
  $("#top").hidden = true;
  const user = h("input", { id: "u", autocomplete: "username", required: true, autofocus: true });
  const pass = h("input", { id: "p", type: "password", autocomplete: "current-password", required: true });
  const err = h("p", { class: "error small", role: "alert" }, message || "");
  const form = h("form", {
    class: "card login",
    onsubmit: async (event) => {
      event.preventDefault();
      const button = form.querySelector("button");
      button.disabled = true;
      try {
        const result = await api("POST", "/api/auth/login", { username: user.value, password: pass.value });
        sessionStorage.setItem("aegis-token", result.token);
        await start();
      } catch (error) {
        err.textContent = error.message;
        pass.value = "";
        pass.focus();
      } finally {
        button.disabled = false;
      }
    },
  },
  h("h1", {}, h("span", { class: "mark", "aria-hidden": "true" }), "AEGIS MESH"),
  h("div", { class: "field" }, h("label", { for: "u" }, "Login"), user),
  h("div", { class: "field" }, h("label", { for: "p" }, "Hasło"), pass),
  err,
  h("button", { class: "primary", type: "submit" }, "Zaloguj"),
  h("p", { class: "muted small" }, "Konta zakłada administrator. Po 5 błędnych próbach konto blokuje się na 15 minut."));
  $("#view").replaceChildren(form);
}

function layout() {
  $("#top").hidden = false;
  $("#nav").replaceChildren(...SCREENS.filter(([, , role]) => can(role))
    .map(([id, label]) => h("a", { href: `#${id}`, "data-id": id }, label)));
  $("#who").replaceChildren(me.username, h("span", { class: "role" }, me.role));
}

async function start() {
  if (!token()) return showLogin();
  try {
    me = await api("GET", "/api/auth/me");
  } catch {
    return;
  }
  layout();
  route();
}

function route() {
  if (!me) return;
  clearInterval(timer);
  const [screen, ...rest] = (location.hash.slice(1) || "pulpit").split("/");
  const param = decodeURIComponent(rest.join("/"));
  document.querySelectorAll("#nav a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.id === screen));
  if (document.querySelector('#nav a[aria-current]')) document.querySelector('#nav a[aria-current]').setAttribute("aria-current", "page");
  const view = $("#view");
  const screens = { pulpit: dashboard, cele: targets, hosty: param ? hostCard : hosts, sygnatury: signatures,
    ustawienia: settings, konto: account };
  const allowed = SCREENS.find(([id]) => id === screen);
  if (!screens[screen] || (allowed && !can(allowed[2]))) return (location.hash = "#pulpit");
  view.replaceChildren(h("p", { class: "muted" }, "Ładowanie…"));
  screens[screen](view, param).catch((error) => {
    if (error.message !== "401") view.replaceChildren(h("p", { class: "error" }, error.message));
  });
  view.focus({ preventScroll: true });
}

// ---- 1. Pulpit ------------------------------------------------------------------------
async function dashboard(view) {
  const [summary, news, history] = await Promise.all([
    api("GET", "/api/summary"), api("GET", "/api/changes?limit=200"), api("GET", "/api/changes/history?limit=20"),
  ]);
  const last = summary.last_scan;
  const scanBox = h("div", { class: "card" });
  const renderScan = (run) => {
    const running = run && ["queued", "running"].includes(run.status);
    scanBox.replaceChildren(
      h("div", { class: "row" },
        h("div", {},
          h("h3", {}, running ? `Skan #${run.id} w toku` : "Skany"),
          running
            ? h("div", {}, h("div", { class: "bar" }, h("span", {})),
              h("p", { class: "muted small" }, `${run.progress}% · ${run.hosts_up} aktywnych · szacunek ${seconds(run.estimate_seconds)}`))
            : h("p", { class: "muted" },
              last ? `Ostatni: ${when(last.finished_at)} (${seconds(last.active_seconds)}, ${last.hosts_up} hostów). ` : "Jeszcze nie było skanu. ",
              summary.next_scan ? `Następny: ${when(summary.next_scan.at)} (${summary.next_scan.name}).` : "Brak harmonogramu.")),
        can("edytor") ? h("div", { class: "shrink actions" },
          running ? h("button", { type: "button", onclick: (e) => act(e.target, () => api("POST", `/api/scans/${run.id}/pause`), "Skan wstrzymany.").then(route) }, "Wstrzymaj")
            : summary.paused_run ? h("button", { type: "button", onclick: (e) => act(e.target, () => api("POST", `/api/scans/${summary.paused_run}/resume`), "Wznowiono skan.").then(route) }, `Wznów skan #${summary.paused_run}`) : null,
          running ? null : h("button", { class: "primary", type: "button",
            onclick: (e) => act(e.target, () => api("POST", "/api/scans"), "Skan uruchomiony.").then(route) }, "Skanuj teraz")) : null));
    if (running) scanBox.querySelector(".bar span").style.width = `${run.progress}%`;
  };
  renderScan(summary.active_run ? await api("GET", `/api/scans/${summary.active_run}`) : null);
  if (summary.active_run) {
    timer = setInterval(async () => {
      const run = await api("GET", `/api/scans/${summary.active_run}`).catch(() => null);
      if (run && !["queued", "running"].includes(run.status)) return route();
      if (run) renderScan(run);
    }, 3000);
  }

  const list = h("ul", { class: "news" });
  let shown = 10;
  const more = h("button", { class: "link", type: "button", onclick: () => { shown = news.changes.length; renderNews(); } });
  const renderNews = () => {
    list.replaceChildren(...news.changes.slice(0, shown).map((c) => h("li", {},
      prio(c.priority),
      h("div", {}, h("a", { href: `#hosty/${encodeURIComponent(c.ip)}` }, c.message),
        h("div", { class: "muted small" }, CHANGE[c.kind] || c.kind)),
      h("span", { class: "when" }, when(c.created_at)))));
    if (!news.changes.length) list.replaceChildren(h("li", { class: "empty" }, "Brak zmian po ostatnim skanie."));
    more.textContent = `pokaż więcej (${news.changes.length - shown})`;
    more.hidden = shown >= news.changes.length;
  };
  renderNews();

  view.replaceChildren(
    h("div", { class: "head" }, h("div", {}, h("h1", {}, "Pulpit"), h("p", { class: "muted" }, "Najważniejsze od ostatniego skanu."))),
    h("div", { class: "grid tiles" },
      h("a", { class: "tile", href: "#hosty" }, h("div", { class: "n" }, summary.active_hosts), h("div", { class: "l" }, "aktywne hosty"),
        h("div", { class: "s" }, `${summary.unstable_hosts} niestabilnych · ${summary.inactive_hosts} nieaktywnych`)),
      h("a", { class: "tile", href: "#pulpit", onclick: (e) => { e.preventDefault(); $("#co-nowego").scrollIntoView({ behavior: "smooth" }); } },
        h("div", { class: "n" }, summary.changes_since_last_scan),
        h("div", { class: "l" }, "zmiany od ostatniego skanu")),
      h("a", { class: `tile ${summary.high_priority ? "hot" : ""}`, href: "#hosty" }, h("div", { class: "n" }, summary.high_priority),
        h("div", { class: "l" }, "wysoki priorytet (4–5)"), h("div", { class: "s" }, "bez rozwiązanych i fałszywych alarmów"))),
    scanBox,
    h("div", { class: "grid two" },
      h("section", { class: "card", id: "co-nowego" }, h("h2", {}, "Co nowego"), list, more),
      h("section", { class: "card chart" }, h("h2", {}, "Zmiany w kolejnych skanach"), chart(history.runs))));
}

function chart(runs) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 400 160");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Liczba zmian w każdym skanie");
  if (!runs.length) {
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", "200"); t.setAttribute("y", "80"); t.setAttribute("text-anchor", "middle");
    t.textContent = "Brak skanów";
    svg.append(t);
    return svg;
  }
  const totals = runs.map((r) => Object.values(r.changes).reduce((a, b) => a + b, 0));
  const max = Math.max(1, ...totals);
  const w = 380 / runs.length;
  runs.forEach((run, i) => {
    const height = (totals[i] / max) * 120;
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", 10 + i * w + w * 0.15); rect.setAttribute("width", w * 0.7);
    rect.setAttribute("y", 135 - height); rect.setAttribute("height", Math.max(1, height)); rect.setAttribute("rx", "2");
    rect.setAttribute("class", `b ${i === runs.length - 1 ? "last" : ""}`);
    const title = document.createElementNS(ns, "title");
    title.textContent = `Skan #${run.run_id} (${when(run.finished_at)}): ${totals[i]} zmian, ${run.hosts_up} hostów`;
    rect.append(title);
    svg.append(rect);
    if (runs.length <= 12 || i % Math.ceil(runs.length / 12) === 0) {
      const label = document.createElementNS(ns, "text");
      label.setAttribute("x", 10 + i * w + w / 2); label.setAttribute("y", "152"); label.setAttribute("text-anchor", "middle");
      label.textContent = `#${run.run_id}`;
      svg.append(label);
    }
  });
  return svg;
}

// ---- 2. Cele --------------------------------------------------------------------------
async function targets(view) {
  const [data, schedules, templates, settings] = await Promise.all([
    api("GET", "/api/targets"), api("GET", "/api/schedules"), api("GET", "/api/schedules/templates"),
    can("edytor") ? api("GET", "/api/settings") : null,
  ]);
  const parts = [h("div", { class: "head" }, h("div", {}, h("h1", {}, "Cele"),
    h("p", { class: "muted" }, `Co skanować i kiedy. Razem ${data.addresses} adresów.`)))];

  if (can("edytor")) {
    const text = h("textarea", { id: "cele-text", placeholder: "10.0.0.0/24\n192.168.1.10-40\nexample.com\n\n…albo upuść tu plik CSV / JSON",
      "aria-label": "Adresy do skanowania" });
    const note = h("input", { id: "cele-note", placeholder: "np. sieć wydziału" });
    let pickedName = null;
    const drop = h("div", { class: "drop" }, text);
    const takeFile = async (file) => {
      text.value = await readFile(file);
      pickedName = file.name;
      toast(`Wczytano ${file.name}. Sprawdź i kliknij „Dodaj”.`);
    };
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) takeFile(e.dataTransfer.files[0]); });
    const picker = filePicker(".csv,.json,.txt", takeFile);
    parts.push(h("section", { class: "card" },
      h("h2", {}, "Wklej adresy albo upuść plik CSV/JSON"),
      h("p", { class: "muted small" }, "IP, zakresy CIDR, zakresy a–b i domeny. System sam rozpozna format."),
      drop,
      h("div", { class: "row" },
        h("div", {}, h("label", { for: "cele-note" }, "Notatka (opcjonalnie, szyfrowana)"), note),
        h("div", { class: "shrink actions" }, picker,
          h("button", { type: "button", onclick: () => picker.click() }, "Wybierz plik"),
          h("button", { class: "primary", type: "button", onclick: async (e) => {
            const body = pickedName && /\.(csv|json)$/i.test(pickedName)
              ? ["/api/targets/import", { content: text.value, filename: pickedName }]
              : ["/api/targets", { text: text.value, note: note.value || null }];
            const result = await act(e.target, () => api("POST", ...body));
            if (result) { toast(`Dodano ${result.added.length} celów (${result.addresses} adresów), duplikaty: ${result.duplicates}.`); route(); }
          } }, "Dodaj")))));
  }

  const rows = data.targets.map((t) => h("tr", {},
    h("td", { class: "mono" }, t.value), h("td", {}, t.kind), h("td", {}, t.size), h("td", {}, t.note || ""),
    h("td", {}, t.enabled ? "tak" : "wyłączony"),
    can("edytor") ? h("td", {}, h("div", { class: "actions tight" },
      h("button", { type: "button", onclick: (e) => act(e.target, () => api("PATCH", `/api/targets/${t.id}`, { enabled: !t.enabled })).then(route) },
        t.enabled ? "Wyłącz" : "Włącz"),
      h("button", { type: "button", onclick: (e) => act(e.target, () => api("DELETE", `/api/targets/${t.id}`), "Usunięto.").then(route) }, "Usuń"))) : null));
  parts.push(h("section", { class: "card" }, h("h2", {}, "Lista celów"),
    rows.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Cel"), h("th", {}, "Rodzaj"), h("th", {}, "Adresy"), h("th", {}, "Notatka"),
        h("th", {}, "Aktywny"), can("edytor") ? h("th", {}, "") : null)),
      h("tbody", {}, rows))) : h("p", { class: "empty" }, "Brak celów.")));

  const scheduleRows = schedules.schedules.map((s) => h("li", {},
    h("span", { class: "prio p1" }, s.window_start === s.window_end ? "24h" : "okno"),
    h("div", {}, h("strong", {}, s.name), h("div", { class: "muted small" },
      `${s.frequency}, ${s.window_start === s.window_end ? "bez okna" : `okno ${s.window_start}–${s.window_end}`}, ostatni start ${when(s.last_started_at)}`)),
    can("edytor") ? h("button", { type: "button", onclick: (e) => act(e.target, () => api("DELETE", `/api/schedules/${s.id}`), "Usunięto harmonogram.").then(route) }, "Usuń") : h("span")));
  parts.push(h("section", { class: "card" }, h("h2", {}, "Harmonogram"),
    can("edytor") ? h("div", { class: "actions" }, Object.entries(templates).map(([key, t]) =>
      h("button", { type: "button", onclick: (e) => act(e.target, () => api("POST", "/api/schedules", { template: key }), `Dodano: ${t.name}.`).then(route) },
        `+ ${t.name}`))) : null,
    h("ul", { class: "news" }, scheduleRows.length ? scheduleRows : h("li", { class: "empty" }, "Brak harmonogramu: skany tylko ręcznie."))));

  if (settings) {
    const fields = [["rate", "Pakiety na sekundę (nmap --max-rate)"], ["threads", "Podgrupy równolegle"], ["ports", "Dozwolone porty"],
      ["http_rate", "Zapytania HTTP na sekundę"], ["host_timeout", "Limit czasu na host (s)"], ["max_retries", "Powtórzenia nmapa"],
      ["chunk_seconds", "Docelowy czas podgrupy (s)"], ["unstable_after", "Nieaktywny po tylu skanach bez odpowiedzi"]];
    const inputs = Object.fromEntries(fields.map(([key]) => [key, h("input", { id: `s-${key}`, value: settings[key] })]));
    parts.push(h("details", { class: "card" }, h("summary", {}, "Zaawansowane: ustawienia skanera"),
      h("div", { class: "grid" }, h("div", { class: "row" }, fields.map(([key, label]) =>
        h("div", { class: "field" }, h("label", { for: `s-${key}` }, label), inputs[key])))),
      h("p", { class: "muted small" }, `Zmierzona szybkość: ${settings.measured_rate ?? "jeszcze nie"} hostów/s na proces.`),
      h("button", { class: "primary", type: "button", onclick: (e) => {
        const body = {};
        for (const [key] of fields) body[key] = key === "ports" ? inputs[key].value : Number(inputs[key].value);
        act(e.target, () => api("PATCH", "/api/settings", body), "Zapisano ustawienia.");
      } }, "Zapisz")));
  }
  view.replaceChildren(...parts);
}

// ---- 3. Hosty -------------------------------------------------------------------------
async function hosts(view) {
  const data = await api("GET", "/api/inventory");
  const search = h("input", { type: "search", id: "szukaj", placeholder: "Szukaj: IP, technologia, tytuł…", "aria-label": "Szukaj hostów" });
  const stateSel = h("select", { "aria-label": "Stan" }, h("option", { value: "" }, "wszystkie stany"),
    Object.entries(STATE).map(([k, v]) => h("option", { value: k }, v)));
  const chosen = new Set();
  const chips = h("div", { class: "chips", "aria-label": "Filtruj po tagach" });
  const body = h("tbody", {});
  const count = h("span", { class: "muted" });
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const rows = data.hosts.filter((r) => (!stateSel.value || r.state === stateSel.value)
      && [...chosen].every((t) => r.tags.includes(t))
      && (!q || JSON.stringify(r).toLowerCase().includes(q)));
    count.textContent = `${rows.length} z ${data.total}`;
    body.replaceChildren(...rows.map((r) => h("tr", { class: "click", tabindex: "0",
      onclick: () => (location.hash = `#hosty/${encodeURIComponent(r.ip)}`),
      onkeydown: (e) => e.key === "Enter" && (location.hash = `#hosty/${encodeURIComponent(r.ip)}`) },
    h("td", {}, prio(r.priority)),
    h("td", { class: "mono" }, r.ip, r.edited ? h("span", { class: "badge" }, "edytowane") : null),
    h("td", {}, r.top || h("span", { class: "muted" }, "brak otwartych usług"), h("div", {}, tags(r.tags))),
    h("td", { class: "hide-sm small" }, r.technologies.join(", ")),
    h("td", { class: "hide-sm small mono" }, r.services.join(" ")),
    h("td", {}, STATE[r.state] || r.state, r.daypart ? h("div", { class: "muted small" }, r.daypart) : null))));
    if (!rows.length) body.replaceChildren(h("tr", {}, h("td", { colspan: 6, class: "empty" }, "Nic nie pasuje.")));
    chips.replaceChildren(...data.tags.map((t) => h("button", { type: "button", class: "tag", "aria-pressed": chosen.has(t) ? "true" : "false",
      onclick: () => { chosen.has(t) ? chosen.delete(t) : chosen.add(t); draw(); } }, t)));
  };
  search.addEventListener("input", draw);
  stateSel.addEventListener("change", draw);
  draw();
  view.replaceChildren(
    h("div", { class: "head" }, h("div", {}, h("h1", {}, "Hosty"), h("p", { class: "muted" }, "Od najważniejszych. Kliknij wiersz, żeby otworzyć kartę hosta.")),
      h("div", { class: "actions" }, can("czytelnik") ? h("button", { type: "button", onclick: () => download("/api/export/findings?format=csv", "aegis-znaleziska.csv") }, "Eksport CSV") : null,
        h("button", { type: "button", onclick: () => download("/api/export/findings?format=json", "aegis-znaleziska.json") }, "Eksport JSON"))),
    h("section", { class: "card" },
      h("div", { class: "row" }, h("div", {}, search), h("div", { class: "shrink" }, stateSel), h("div", { class: "shrink" }, count)),
      chips,
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Prio"), h("th", {}, "Host"), h("th", {}, "Dlaczego ważne"),
          h("th", { class: "hide-sm" }, "Technologie"), h("th", { class: "hide-sm" }, "Usługi"), h("th", {}, "Stan"))), body))));
  search.focus();
}

function kv(pairs) {
  return h("dl", { class: "kv" }, pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

function webBlock(web) {
  if (!web) return null;
  if (web.status === null || web.status === undefined) return h("p", { class: "muted small" }, `HTTP: brak odpowiedzi (${web.error || "?"})`);
  const tls = web.tls;
  return h("div", {},
    kv([["URL", h("span", { class: "mono" }, web.url)], ["Kod", web.status], ["Tytuł", web.title], ["Server", web.server],
      ["Przekierowania", (web.redirects || []).map((r) => `${r.from} → ${r.to} (${r.status}${r.followed ? "" : ", nie śledzone"})`).join("; ")],
      ["Favicona", web.favicon_mmh3 !== null && web.favicon_mmh3 !== undefined ? h("span", { class: "mono" }, `mmh3 ${web.favicon_mmh3}`) : null],
      ["Certyfikat", tls && !tls.error ? `${tls.subject}, wystawca ${tls.issuer}, ważny do ${when(tls.not_after)}${tls.self_signed ? ", self-signed" : ""}${tls.expired ? ", WYGASŁ" : ""}` : null]]),
    h("details", {}, h("summary", {}, `Nagłówki (${Object.keys(web.headers || {}).length}) i ciasteczka (${(web.cookies || []).length})`),
      h("table", { class: "small" }, h("tbody", {}, Object.entries(web.headers || {}).map(([k, v]) => h("tr", {}, h("td", { class: "mono" }, k), h("td", { class: "mono" }, v))))),
      (web.cookies || []).length ? h("table", { class: "small" }, h("thead", {}, h("tr", {}, h("th", {}, "Ciasteczko"), h("th", {}, "Secure"), h("th", {}, "HttpOnly"), h("th", {}, "SameSite"))),
        h("tbody", {}, web.cookies.map((c) => h("tr", {}, h("td", { class: "mono" }, c.name), h("td", {}, c.secure ? "tak" : "nie"),
          h("td", {}, c.httponly ? "tak" : "nie"), h("td", {}, c.samesite || "–"))))) : null,
      Object.keys(web.paths || {}).length ? h("p", { class: "small muted" }, "Sprawdzone ścieżki: ",
        Object.values(web.paths).map((p) => `${p.path} → ${p.status ?? "brak"}`).join(", ")) : null));
}

function findingBlock(f) {
  const box = h("div", { class: "card" });
  const view = () => box.replaceChildren(
    h("div", { class: "row" }, h("div", {}, h("h3", {}, prio(f.priority), " ", f.title,
      f.is_edited ? h("span", { class: "badge", title: `Edytowane ręcznie: ${f.edited.join(", ")}. Skan tego nie nadpisze.` }, "edytowane") : null,
      f.state !== "active" ? h("span", { class: "badge" }, "zniknęła") : null),
    h("div", {}, tags(f.tags)),
    h("p", { class: "muted small" }, `Status: ${f.status}. Technologie: ${tech(f.technologies) || "–"}.`),
    f.note ? h("p", {}, "Notatka: ", f.note) : null),
    can("analityk") ? h("div", { class: "shrink actions" }, h("button", { type: "button", onclick: edit }, "Edytuj"),
      h("button", { type: "button", onclick: (e) => act(e.target, () => api("POST", "/api/dojo/send", { finding_ids: [f.id] }),
        (r) => `Wysłano do DefectDojo (${r.sent}).`) }, "Do DefectDojo")) : null),
    f.reasons.length ? h("details", {}, h("summary", {}, `Dlaczego (${f.reasons.length})`),
      h("ul", { class: "small" }, f.reasons.map((r) => h("li", {}, `P${r.priority} ${r.message}`, r.evidence ? h("span", { class: "muted mono" }, ` — ${r.evidence}`) : null)))) : null);
  const edit = () => {
    const p = h("select", { id: `p-${f.id}` }, [1, 2, 3, 4, 5].map((n) => h("option", { value: n, selected: n === f.priority }, `P${n}`)));
    const t = h("input", { id: `t-${f.id}`, value: f.tags.join(", ") });
    const ti = h("input", { id: `ti-${f.id}`, value: f.title });
    const st = h("select", { id: `st-${f.id}` }, STATUS.map((s) => h("option", { value: s, selected: s === f.status }, s)));
    const n = h("textarea", { id: `n-${f.id}` });
    n.value = f.note || "";
    box.replaceChildren(h("h3", {}, `Edycja ${f.ip}:${f.port}`),
      h("p", { class: "muted small" }, "Zmienione pola dostaną znaczek „edytowane” i kolejne skany ich nie nadpiszą."),
      h("div", { class: "row" }, h("div", { class: "field shrink" }, h("label", { for: p.id }, "Priorytet"), p),
        h("div", { class: "field" }, h("label", { for: st.id }, "Status"), st),
        h("div", { class: "field" }, h("label", { for: t.id }, "Tagi (po przecinku)"), t)),
      h("div", { class: "field" }, h("label", { for: ti.id }, "Dlaczego ważne (jedno zdanie)"), ti),
      h("div", { class: "field" }, h("label", { for: n.id }, "Notatka (szyfrowana)"), n),
      h("div", { class: "actions" },
        h("button", { class: "primary", type: "button", onclick: async (e) => {
          const changes = {};
          if (Number(p.value) !== f.priority) changes.priority = Number(p.value);
          const newTags = t.value.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
          if (newTags.slice().sort().join() !== f.tags.slice().sort().join()) changes.tags = newTags;
          if (ti.value.trim() !== f.title) changes.title = ti.value.trim();
          if (st.value !== f.status) changes.status = st.value;
          if (n.value !== (f.note || "")) changes.note = n.value;
          if (!Object.keys(changes).length) return view();
          const updated = await act(e.target, () => api("PATCH", `/api/findings/${f.id}`, changes), "Zapisano.");
          if (updated) { f = updated; view(); }
        } }, "Zapisz"),
        h("button", { type: "button", onclick: view }, "Anuluj"),
        f.is_edited ? h("button", { type: "button", onclick: async (e) => {
          const updated = await act(e.target, () => api("POST", `/api/findings/${f.id}/reset`, {}), "Przywrócono wartości ze skanu.");
          if (updated) { f = updated; view(); }
        } }, "Przywróć wartości ze skanu") : null));
  };
  view();
  return box;
}

async function hostCard(view, ip) {
  const host = await api("GET", `/api/hosts/${encodeURIComponent(ip)}`);
  const byPort = Object.fromEntries(host.findings.map((f) => [`${f.port}/${f.proto}`, f]));
  view.replaceChildren(
    h("p", {}, h("a", { href: "#hosty" }, "← Hosty")),
    h("div", { class: "head" }, h("div", {}, h("h1", { class: "mono" }, host.ip),
      h("p", { class: "muted" }, `${STATE[host.state] || host.state}${host.missed ? `, bez odpowiedzi ${host.missed}×` : ""} · pierwszy raz ${when(host.first_seen)} · ostatnio ${when(host.last_seen)}`,
        host.hostname ? ` · ${host.hostname}` : "", host.daypart?.pattern ? ` · ${host.daypart.pattern}` : ""))),
    h("section", { class: "card" }, h("h2", {}, "Usługi"),
      host.services.length ? host.services.map((s) => h("div", { class: "svc" },
        h("h3", { class: "mono" }, `${s.port}/${s.proto} `, h("span", {}, [s.name, s.product, s.version, s.extrainfo].filter(Boolean).join(" · "))),
        webBlock(s.web),
        byPort[`${s.port}/${s.proto}`] ? findingBlock(byPort[`${s.port}/${s.proto}`]) : null))
        : h("p", { class: "empty" }, "Brak otwartych usług w ostatnim skanie."),
      host.findings.filter((f) => f.state !== "active").map(findingBlock)),
    h("div", { class: "grid two" },
      h("section", { class: "card" }, h("h2", {}, "Historia zmian"), h("ul", { class: "news" },
        host.changes.length ? host.changes.map((c) => h("li", {}, prio(c.priority), h("div", {}, c.message), h("span", { class: "when" }, when(c.created_at))))
          : h("li", { class: "empty" }, "Bez zmian."))),
      h("section", { class: "card" }, h("h2", {}, "Obecność w skanach"),
        h("div", { class: "dots" }, host.presence.map((p) => h("span", { class: `dot ${p.up ? "up" : ""}`,
          title: `Skan #${p.run_id}, godz. ${p.hour}: ${p.up ? "odpowiada" : "brak odpowiedzi"}` }))),
        h("p", { class: "muted small" }, "Pełny kwadrat: host odpowiadał. Pusty: brak odpowiedzi."))));
}

// ---- 4. Sygnatury ---------------------------------------------------------------------
async function signatures(view) {
  const data = await api("GET", "/api/signatures");
  const parts = [h("div", { class: "head" }, h("div", {}, h("h1", {}, "Sygnatury"),
    h("p", { class: "muted" }, "Wykrywanie technologii po nagłówkach, ciasteczkach, HTML, faviconie i ścieżkach. Tylko odczyt, bez prób logowania.")))];
  if (can("edytor")) {
    const f = {
      name: h("input", { id: "sg-name", placeholder: "np. Panel Moja-Aplikacja" }),
      kind: h("select", { id: "sg-kind" }, data.kinds.map((k) => h("option", { value: k }, `${k}: ${KIND_HELP[k] || ""}`))),
      field: h("input", { id: "sg-field", placeholder: "np. X-Powered-By albo /admin" }),
      pattern: h("input", { id: "sg-pattern", class: "mono", placeholder: "wyrażenie, np. Moja-App v([\\d.]+)" }),
      priority: h("select", { id: "sg-priority" }, [1, 2, 3, 4, 5].map((n) => h("option", { value: n, selected: n === 3 }, `P${n}`))),
      category: h("select", { id: "sg-category" }, data.categories.map((c) => h("option", { value: c }, c))),
      tags: h("input", { id: "sg-tags", placeholder: "po przecinku" }),
      message: h("input", { id: "sg-message", placeholder: "Jedno zdanie: dlaczego to ważne" }),
    };
    const fieldBox = h("div", { class: "field" }, h("label", { for: "sg-field" }, "Nagłówek / ścieżka"), f.field);
    const syncKind = () => { fieldBox.hidden = !["header", "path"].includes(f.kind.value); };
    f.kind.addEventListener("change", syncKind);
    syncKind();
    const results = h("div", {});
    const body = () => ({ name: f.name.value, kind: f.kind.value, field: f.field.value || null, pattern: f.pattern.value,
      priority: Number(f.priority.value), category: f.category.value, message: f.message.value,
      tags: f.tags.value.split(",").map((x) => x.trim()).filter(Boolean) });
    parts.push(h("section", { class: "card" }, h("h2", {}, "Nowa sygnatura"),
      h("div", { class: "row" }, h("div", { class: "field" }, h("label", { for: "sg-name" }, "Nazwa"), f.name),
        h("div", { class: "field" }, h("label", { for: "sg-kind" }, "Co sprawdzać"), f.kind), fieldBox),
      h("div", { class: "field" }, h("label", { for: "sg-pattern" }, "Wzorzec (grupa w nawiasach = wersja; wielkość liter bez znaczenia)"), f.pattern),
      h("div", { class: "row" }, h("div", { class: "field shrink" }, h("label", { for: "sg-priority" }, "Priorytet"), f.priority),
        h("div", { class: "field" }, h("label", { for: "sg-category" }, "Kategoria"), f.category),
        h("div", { class: "field" }, h("label", { for: "sg-tags" }, "Tagi"), f.tags)),
      h("div", { class: "field" }, h("label", { for: "sg-message" }, "Komunikat"), f.message),
      h("div", { class: "actions" },
        h("button", { type: "button", onclick: async (e) => {
          const r = await act(e.target, () => api("POST", "/api/signatures/test", body()));
          if (!r) return;
          results.replaceChildren(h("h3", {}, `Pasuje do ${r.count} usług`), h("ul", { class: "small" },
            r.matches.map((m) => h("li", {}, h("a", { href: `#hosty/${encodeURIComponent(m.ip)}`, class: "mono" }, `${m.ip}:${m.port}`),
              m.version ? ` wersja ${m.version}` : "", h("span", { class: "muted" }, ` — ${m.evidence}`)))));
        } }, "Sprawdź na istniejących hostach"),
        h("button", { class: "primary", type: "button", onclick: async (e) => {
          const r = await act(e.target, () => api("POST", "/api/signatures", body()));
          if (r) { toast(`Dodano ${r.signature.id}. Przeliczono ${r.reanalysed_findings} znalezisk.`); route(); }
        } }, "Zapisz i zastosuj")),
      results));
  }
  const row = (s, removable) => h("tr", {}, h("td", {}, prio(s.priority)), h("td", {}, s.name, h("div", { class: "muted small mono" }, s.id)),
    h("td", {}, s.kind, s.field ? h("div", { class: "mono small" }, s.field) : null), h("td", { class: "mono small" }, s.pattern),
    h("td", { class: "hide-sm small" }, s.message, h("div", {}, tags(s.tags))),
    removable && can("edytor") ? h("td", {}, h("button", { type: "button", onclick: (e) => act(e.target, () => api("DELETE", `/api/signatures/${s.id}`), "Usunięto.").then(route) }, "Usuń")) : h("td", {}));
  const table = (items, removable) => h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {},
    h("th", {}, "Prio"), h("th", {}, "Nazwa"), h("th", {}, "Rodzaj"), h("th", {}, "Wzorzec"), h("th", { class: "hide-sm" }, "Komunikat"), h("th", {}, ""))),
  h("tbody", {}, items.map((s) => row(s, removable)))));
  parts.push(h("section", { class: "card" }, h("h2", {}, `Własne (${data.custom.length})`),
    data.custom.length ? table(data.custom, true) : h("p", { class: "empty" }, "Brak własnych sygnatur.")));
  parts.push(h("details", { class: "card" }, h("summary", {}, `Wbudowane (${data.builtin.length})`), table(data.builtin, false)));
  view.replaceChildren(...parts);
}

// ---- 5. Ustawienia (admin) ---------------------------------------------------------------
async function settings(view) {
  const [users, s, audit] = await Promise.all([api("GET", "/api/users"), api("GET", "/api/settings"), api("GET", "/api/audit?limit=50")]);
  const name = h("input", { id: "nu-name", autocomplete: "off" });
  const pass = h("input", { id: "nu-pass", type: "password", autocomplete: "new-password" });
  const role = h("select", { id: "nu-role" }, users.roles.map((r) => h("option", { value: r, selected: r === "czytelnik" }, r)));
  const userRows = users.users.map((u) => {
    const sel = h("select", { "aria-label": `Rola ${u.username}`, onchange: (e) =>
      act(e.target, () => api("PATCH", `/api/users/${u.id}`, { role: e.target.value }), "Zmieniono rolę.").then(route) },
    users.roles.map((r) => h("option", { value: r, selected: r === u.role }, r)));
    const locked = u.locked_until && new Date(u.locked_until) > new Date();
    return h("tr", {}, h("td", {}, u.username), h("td", {}, sel),
      h("td", { class: "small" }, u.disabled ? "wyłączone" : locked ? "zablokowane" : "aktywne"),
      h("td", { class: "small hide-sm" }, when(u.last_login)),
      h("td", {}, h("div", { class: "actions tight" },
        locked ? h("button", { type: "button", onclick: (e) => act(e.target, () => api("PATCH", `/api/users/${u.id}`, { unlock: true }), "Odblokowano.").then(route) }, "Odblokuj") : null,
        h("button", { type: "button", onclick: (e) => act(e.target, () => api("PATCH", `/api/users/${u.id}`, { disabled: !u.disabled }), "Zapisano.").then(route) }, u.disabled ? "Włącz" : "Wyłącz"),
        h("button", { type: "button", onclick: (e) => {
          const pw = prompt(`Nowe hasło dla ${u.username} (min. 10 znaków):`);
          if (pw) act(e.target, () => api("PATCH", `/api/users/${u.id}`, { password: pw }), "Hasło zmienione.");
        } }, "Hasło"),
        h("button", { type: "button", onclick: (e) => confirm(`Usunąć konto ${u.username}?`) && act(e.target, () => api("DELETE", `/api/users/${u.id}`), "Usunięto.").then(route) }, "Usuń"))));
  });

  const dojo = {
    url: h("input", { id: "dj-url", value: s.dojo_url || "", placeholder: "http://dojo-uwsgi:8081" }),
    user: h("input", { id: "dj-user", value: s.dojo_user || "" }),
    password: h("input", { id: "dj-pass", type: "password", placeholder: s.dojo_password ? "zapisane (zostaw puste)" : "", autocomplete: "new-password" }),
    token: h("input", { id: "dj-token", type: "password", placeholder: s.dojo_token ? "zapisany (zostaw pusty)" : "", autocomplete: "off" }),
    product: h("input", { id: "dj-product", value: s.dojo_product || "" }),
    auto: h("input", { id: "dj-auto", type: "checkbox", checked: !!s.dojo_auto }),
    min: h("select", { id: "dj-min" }, [1, 2, 3, 4, 5].map((n) => h("option", { value: n, selected: n === s.dojo_min_priority }, `P${n} i wyżej`))),
  };
  const importFull = filePicker(".json", async (file) => {
    const data = JSON.parse(await readFile(file));
    act(null, () => api("POST", "/api/import/full", { data }), (r) => `Zaimportowano: ${Object.entries(r.imported).map(([k, v]) => `${k} ${v}`).join(", ")}.`);
  });
  const importEdits = filePicker(".csv,.json", async (file) => {
    const content = await readFile(file);
    act(null, () => api("POST", "/api/import/findings", { content, filename: file.name }),
      (r) => `Zaktualizowano ${r.updated} znalezisk, bez zmian ${r.unchanged}, nie znaleziono ${r.not_found.length}.`);
  });

  view.replaceChildren(
    h("div", { class: "head" }, h("div", {}, h("h1", {}, "Ustawienia"), h("p", { class: "muted" }, "Użytkownicy i role, DefectDojo, eksport i import. Widoczne tylko dla admina."))),
    h("section", { class: "card" }, h("h2", {}, "Użytkownicy i role"),
      h("p", { class: "muted small" }, "admin: wszystko · edytor: cele, sygnatury, skany · analityk: edycja znalezisk · czytelnik: tylko podgląd."),
      h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Login"), h("th", {}, "Rola"), h("th", {}, "Stan"),
        h("th", { class: "hide-sm" }, "Ostatnie logowanie"), h("th", {}, ""))), h("tbody", {}, userRows))),
      h("h3", {}, "Nowe konto"),
      h("div", { class: "row" }, h("div", {}, h("label", { for: "nu-name" }, "Login"), name),
        h("div", {}, h("label", { for: "nu-pass" }, "Hasło (min. 10 znaków)"), pass),
        h("div", { class: "shrink" }, h("label", { for: "nu-role" }, "Rola"), role),
        h("div", { class: "shrink" }, h("button", { class: "primary", type: "button", onclick: (e) =>
          act(e.target, () => api("POST", "/api/users", { username: name.value, password: pass.value, role: role.value }), "Konto założone.").then(route) }, "Załóż")))),
    h("section", { class: "card" }, h("h2", {}, "DefectDojo"),
      h("div", { class: "row" }, h("div", { class: "field" }, h("label", { for: "dj-url" }, "Adres"), dojo.url),
        h("div", { class: "field" }, h("label", { for: "dj-product" }, "Produkt"), dojo.product)),
      h("div", { class: "row" }, h("div", { class: "field" }, h("label", { for: "dj-user" }, "Użytkownik"), dojo.user),
        h("div", { class: "field" }, h("label", { for: "dj-pass" }, "Hasło"), dojo.password),
        h("div", { class: "field" }, h("label", { for: "dj-token" }, "albo token API"), dojo.token)),
      h("div", { class: "row" }, h("div", { class: "field shrink" }, h("label", { for: "dj-auto" }, dojo.auto, " Wysyłaj automatycznie po skanie")),
        h("div", { class: "field shrink" }, h("label", { for: "dj-min" }, "Od priorytetu"), dojo.min)),
      h("div", { class: "actions" },
        h("button", { class: "primary", type: "button", onclick: (e) => {
          const body = { dojo_url: dojo.url.value || null, dojo_user: dojo.user.value || null, dojo_product: dojo.product.value || null,
            dojo_auto: dojo.auto.checked, dojo_min_priority: Number(dojo.min.value) };
          if (dojo.password.value) body.dojo_password = dojo.password.value;
          if (dojo.token.value) body.dojo_token = dojo.token.value;
          Object.keys(body).forEach((k) => body[k] === null && delete body[k]);
          act(e.target, () => api("PATCH", "/api/settings", body), "Zapisano.");
        } }, "Zapisz"),
        h("button", { type: "button", onclick: (e) => act(e.target, () => api("POST", "/api/dojo/check"), "Połączenie z DefectDojo działa.") }, "Sprawdź połączenie"),
        h("button", { type: "button", onclick: (e) => act(e.target, () => api("POST", "/api/dojo/send", { min_priority: Number(dojo.min.value) }),
          (r) => r.sent ? `Wysłano ${r.sent} znalezisk (test #${r.test_id}).` : r.message) }, "Wyślij znaleziska teraz"))),
    h("section", { class: "card" }, h("h2", {}, "Eksport i import"),
      h("div", { class: "actions" },
        h("button", { type: "button", onclick: () => download("/api/export/full", "aegis-eksport.json") }, "Pełny eksport JSON"),
        h("button", { type: "button", onclick: () => download("/api/export/findings?format=csv", "aegis-znaleziska.csv") }, "Znaleziska CSV"),
        h("button", { type: "button", onclick: () => download("/api/export/findings?format=json", "aegis-znaleziska.json") }, "Znaleziska JSON")),
      h("div", { class: "actions" }, importFull, importEdits,
        h("button", { type: "button", onclick: () => importFull.click() }, "Import pełny (JSON)"),
        h("button", { type: "button", onclick: () => importEdits.click() }, "Import edycji znalezisk (CSV/JSON)")),
      h("p", { class: "muted small" }, "Import pełny dodaje cele, harmonogramy, sygnatury i znaleziska z edycjami. Istniejące dane zostają.")),
    h("details", { class: "card" }, h("summary", {}, "Dziennik zmian (ostatnie 50)"),
      h("div", { class: "table-wrap" }, h("table", { class: "small" }, h("tbody", {}, audit.entries.map((a) =>
        h("tr", {}, h("td", {}, when(a.at)), h("td", {}, a.username || "–"), h("td", { class: "mono" }, a.action), h("td", { class: "mono" }, a.target || "")))))))
  );
}

// ---- own account ------------------------------------------------------------------------
async function account(view) {
  const old = h("input", { id: "pw-old", type: "password", autocomplete: "current-password" });
  const neu = h("input", { id: "pw-new", type: "password", autocomplete: "new-password" });
  view.replaceChildren(h("div", { class: "head" }, h("div", {}, h("h1", {}, "Moje konto"), h("p", { class: "muted" }, `${me.username}, rola ${me.role}.`))),
    h("section", { class: "card" }, h("h2", {}, "Zmiana hasła"),
      h("div", { class: "row" }, h("div", {}, h("label", { for: "pw-old" }, "Obecne hasło"), old),
        h("div", {}, h("label", { for: "pw-new" }, "Nowe hasło (min. 10 znaków)"), neu),
        h("div", { class: "shrink" }, h("button", { class: "primary", type: "button", onclick: async (e) => {
          const r = await act(e.target, () => api("POST", "/api/auth/password", { old_password: old.value, new_password: neu.value }));
          if (r) { sessionStorage.removeItem("aegis-token"); me = null; showLogin(r.message); }
        } }, "Zmień")))));
}

// ---- start ------------------------------------------------------------------------------
window.addEventListener("hashchange", route);
document.addEventListener("DOMContentLoaded", () => {
  $("#logout").addEventListener("click", async () => {
    await api("POST", "/api/auth/logout").catch(() => {});
    sessionStorage.removeItem("aegis-token");
    me = null;
    showLogin("Wylogowano.");
  });
  start();
});
