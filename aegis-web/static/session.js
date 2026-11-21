"use strict";

let series = null;
let ssidRequest = 0;
const periodMs = 30000;
fetch("/api/capabilities", {cache:"no-store"}).then(r => r.json()).then(info => {
  $("radioMode").disabled = !info.localWifi;
}).catch(() => { $("radioMode").disabled = true; });

async function detectSsid() {
  const ticket = ++ssidRequest;
  const currentDraft = draft;
  const input = $("sessionSsid");
  if (!currentDraft.manualSsid) input.value = "";
  $("connectedNetworks").replaceChildren();
  $("detectSsid").disabled = true;
  $("ssidStatus").textContent = "Odczytuję nazwę połączonej sieci Wi-Fi…";
  ready();
  try {
    const response = await fetch("/api/wifi/connection", {
      headers: {"X-Aegis-Session": "1"}, cache: "no-store", signal: AbortSignal.timeout(10000)
    });
    const data = await response.json();
    if (ticket !== ssidRequest || draft !== currentDraft) return;
    if (!response.ok) throw Error(data.error);
    const names = data.ssids;
    if (names.length === 1) {
      input.value = names[0];
      currentDraft.manualSsid = false;
      $("ssidStatus").textContent = "Nazwa z karty Wi-Fi. Możesz ją zmienić przed rozpoczęciem serii.";
    } else if (names.length) {
      $("connectedNetworks").replaceChildren(...names.map(name => new Option(name, name)));
      $("ssidStatus").textContent = "Wykryto kilka połączonych sieci Wi-Fi. Wybierz nazwę z listy.";
    } else {
      $("ssidStatus").textContent = "Brak nazwy połączonej sieci Wi-Fi. Przy Ethernecie wpisz ręcznie SSID widoczny dla włączonej karty Wi-Fi.";
    }
  } catch (error) {
    if (ticket === ssidRequest && draft === currentDraft)
      $("ssidStatus").textContent = "Nie udało się odczytać SSID z Windows. Wpisz nazwę ręcznie lub wybierz serię testów łączności.";
  } finally {
    if (ticket === ssidRequest) {
      $("detectSsid").disabled = false;
      ready();
    }
  }
}

function changeMode() {
  ++ssidRequest;
  const mode = $("mode").value;
  const single = mode === "single";
  if(!single && draft.automaticAttempt){
    draft.geoAttempt=null;
    draft.automaticAttempt=false;
    $("geo").disabled=false;
    $("locationStatus").textContent="Wskaż stanowisko, użyj sparowanego telefonu lub rozpocznij serię bez lokalizacji.";
  }
  $("ssidField").hidden = mode !== "wifi";
  $("singleResult").hidden = !single;
  $("retry").hidden = !single;
  $("save").textContent = single ? "Zapisz na mapie" : "Rozpocznij serię";
  const locationHelp = " Wskaż stałe stanowisko, sparuj telefon lub zapisz wyniki bez lokalizacji.";
  $("modeHelp").textContent = single ? "Mierzymy czas odpowiedzi serwera z tej przeglądarki."
    : (mode === "wifi" ? "Co 30 s serwer odczytuje raport Windows dla Twojej sieci. System może zwrócić poprzedni skan."
      : "Co 30 s przeglądarka mierzy czas odpowiedzi serwera.") + locationHelp + " Zostaw kartę otwartą.";
  $("detectSsid").disabled = false;
  if (mode === "wifi" && !draft.manualSsid) detectSsid();
  if (single) probe();
  ready();
}
$("mode").onchange = changeMode;
$("detectSsid").onclick = detectSsid;
$("sessionSsid").oninput = () => {
  ++ssidRequest;
  draft.manualSsid = true;
  $("detectSsid").disabled = false;
  $("ssidStatus").textContent = "";
  ready();
};

function finishSeries(s) {
  clearTimeout(s.timer);
  if(s.phoneSession && phoneSession===s.phoneSession)disconnectPhone();
  series = null;
  updatePhoneBadge();
  $("stopSession").hidden = true;
  $("confirmStation").hidden = true;
  $("measure").disabled = false;
  $("sessionStatus").textContent = "Seria zakończona. Zapisane odczyty: " + s.saved + ".";
}
function stopSeries() {
  if (!series) return;
  series.stopped = true;
  clearTimeout(series.timer);
  if (!series.busy) return finishSeries(series);
  $("stopSession").disabled = true;
  $("sessionStatus").textContent = "Kończenie bieżącego pomiaru…";
}
$("stopSession").onclick = stopSeries;
window.addEventListener("pagehide", stopSeries);
$("confirmStation").onclick = () => {
  if (!series) return;
  series.verify = false;
  $("confirmStation").hidden = true;
  $("sessionStatus").textContent = "Potwierdzono stanowisko. Kolejne pomiary w tej serii użyją jego pinezki.";
};

async function locationFor(s) {
  if (!s.verify){
    if(!s.initialHint)return s.point;
    const hint=s.initialHint;
    s.initialHint=null;
    return {...s.point,browserHint:hint};
  }
  try {
    const current = await browserPoint();
    return {...checkStation(s.point,current),browserHint:current};
  } catch {
    return s.point;
  }
}
async function tick(s) {
  if (s.stopped) return;
  s.busy = true;
  const start = performance.now();
  let detail;
  try {
    const locating=locationFor(s);
    let path, payload;
    if (s.mode === "wifi") {
      const point=await locating;
      if(s.stopped)return;
      path = "/api/wifi/read-local";
      payload = {...point, ssid:s.ssid,zone:s.zone,session:s.id,phoneSession:s.phoneSession};
    } else {
      const ms = await timedProbe();
      if (s.stopped) return;
      const at=new Date().toISOString();
      const fix=s.point || (s.phoneSession && s.phoneSession===phoneSession && phoneState.location?.source==="phone"
        && Math.abs(Date.parse(at)-Date.parse(phoneState.location.at))<=15000 ? phoneState : null);
      if(!s.focused && fix && !outsideCity(fix)){
        if(current!=="overview")view("overview");
        drawMeasurementPosition({...fix,at,detail:number(ms)+" ms · zapisywanie"});
        $("map").scrollIntoView({block:"center"});
        s.preview=true;
        s.focused=true;
      }
      const point=await locating;
      if(s.stopped)return;
      path = "/api/measurements";
      payload = {...point, phoneSession:s.phoneSession, ms, at, id:measurementId()};
    }
    const response = await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Aegis-Session":"1"},
      body:JSON.stringify(payload),signal:AbortSignal.timeout(15000)});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || "Błąd zapisu.");
    if (s.mode === "wifi") {
      s.saved++;
      detail = result.added ? "Punkty dostępowe w odczycie: " + result.added : "Sieć niewidoczna w raporcie Windows.";
      if(result.unchanged)detail+=" · raport bez zmian";
      refreshWifi();
    } else {
      s.saved++;
      detail = "Odpowiedź: " + number(result.ms) + " ms";
      refresh();
    }
    if (result.excludedCity || result.location?.reason==="outside_city") {
      detail += " · poza obszarem "+(result.excludedCity || result.location.city)+" · bez pinezki";
    } else if (Number.isFinite(result.lat) && Number.isFinite(result.lng)) {
      focusMeasurement(result,result.at || new Date().toISOString(),detail,!s.focused);
      s.focused=true;
      s.preview=false;
      if (result.location.conflict) {
        detail += " · " + conflictText(result.location.conflict) + ". Zachowano stanowisko.";
        $("confirmStation").hidden = !s.verify;
      } else $("confirmStation").hidden = true;
    } else detail += " · " + locationText(result);
  } catch (error) {
    detail = error.message || "Błąd odczytu.";
  } finally {
    if(s.preview){
      positionMarker?.remove();positionMarker=null;
      s.preview=false;s.focused=false;
    }
    s.busy = false;
    if (s.stopped) finishSeries(s);
    else {
      $("sessionStatus").textContent = "Seria aktywna · " + new Date().toLocaleTimeString("pl-PL") + " · " + detail + " · Zapisano: " + s.saved;
      s.timer = setTimeout(() => tick(s), Math.max(1000,periodMs-(performance.now()-start)));
    }
  }
}
function startSeries() {
  if (series || draft.candidate || draft.locating) return;
  const ssid = $("sessionSsid").value;
  if ($("mode").value === "wifi" && (!ssid.trim() || new TextEncoder().encode(ssid).length > 32)) {
    $("saveStatus").textContent = "Podaj nazwę sieci o długości 1–32 bajtów.";
    return;
  }
  rememberStation(draft.point);
  const fixed = draft.point?.location.source === "manual";
  const point = fixed ? {...draft.point,location:{...draft.point.location}} : null;
  if (point) delete point.location.conflict;
  series = {mode:$("mode").value, point, id:measurementId(),initialHint:draft.browserHint,phoneSession:fixed ? null : phoneSession,
    verify:fixed && $("verifyStation").checked,
    ssid, zone:$("sensorZone").value, saved:0, stopped:false, busy:false};
  $("formDialog").close();
  $("sessionBar").hidden = false;
  $("stopSession").disabled = false;
  $("stopSession").hidden = false;
  $("measure").disabled = true;
  $("sessionStatus").textContent = "Seria aktywna · pierwszy odczyt…";
  updatePhoneBadge();
  tick(series);
}
