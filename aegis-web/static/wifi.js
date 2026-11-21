"use strict";
const scanLayer = L.layerGroup().addTo(map);
const scanPoints = new Map();
let lastReadings = "";
let wifiReadings = [];
let wifiScans = [];
const signalTypes = {
  own_hotspot: ["Własny hotspot", "Użytkownik potwierdził ten SSID i BSSID. Odczyt oznacza widoczność znanego hotspotu w zasięgu laptopa."],
  inventory: ["Punkt z inwentarza", "Nazwa i identyfikator punktu zgadzają się z bieżącym inwentarzem."],
  possible_hotspot: ["Możliwy hotspot", "Nazwa sieci sugeruje hotspot telefonu. Typ urządzenia wymaga potwierdzenia."],
  unknown: ["Typ nieustalony", "Brakuje danych pozwalających ustalić rodzaj urządzenia."]
};
let signals = new Map();
let canClassify = false;
let signalListVersion = "";

function showSignal(){
  const reading=signals.get($("signalChoice").value);
  $("signalEvidence").textContent=reading ? signalTypes[reading.category][1] : "Brak zapisanych odczytów Wi-Fi.";
  if(reading?.possibleRotation)$("signalEvidence").textContent+=" Nowy BSSID przy nazwie własnego hotspotu. Sprawdź identyfikator na telefonie przed potwierdzeniem.";
  $("markHotspot").hidden=!canClassify || !reading;
  $("markHotspot").textContent=reading?.category==="own_hotspot" ? "Cofnij oznaczenie hotspotu" : "Potwierdzam, że to mój hotspot";
}
function listSignals(readings){
  const distinct=new Map();
  for(const row of readings){
    const id=JSON.stringify([row.ssid,row.bssid]);
    if(!distinct.has(id))distinct.set(id,row);
  }
  const rank=Object.keys(signalTypes);
  const sorted=[...distinct].sort(([,a],[,b])=>rank.indexOf(a.category)-rank.indexOf(b.category)
    || a.ssid.localeCompare(b.ssid,"pl") || a.bssid.localeCompare(b.bssid));
  signals=new Map(sorted);
  const version=JSON.stringify(sorted.map(([key,r])=>[key,r.category,r.possibleRotation]));
  if(version!==signalListVersion){
    const select=$("signalChoice"), previous=select.value;
    select.replaceChildren(...sorted.map(([key,r])=>new Option(signalTypes[r.category][0]+" · "+r.ssid+" · "+r.bssid,key)));
    if(signals.has(previous))select.value=previous;
    select.disabled=!sorted.length;
    signalListVersion=version;
  }
  showSignal();
}
$("signalChoice").onchange=()=>{$("signalStatus").textContent="";showSignal()};
$("markHotspot").onclick=async ()=>{
  const reading=signals.get($("signalChoice").value);
  if(!reading)return;
  const button=$("markHotspot");
  button.disabled=true;
  try{
    const response=await fetch("/api/wifi/own-hotspot",{method:"POST",
      headers:{"Content-Type":"application/json","X-Aegis-Session":"1"},
      body:JSON.stringify({ssid:reading.ssid,bssid:reading.bssid,confirmed:reading.category!=="own_hotspot"})});
    if(!response.ok)throw Error((await response.json()).error);
    await loadWifi();
    $("signalStatus").textContent="Zapisano oznaczenie dla "+reading.ssid+" · "+reading.bssid+".";
  }catch(error){$("signalStatus").textContent=error.message}
  finally{button.disabled=false}
};

function readingStatus(reading) {
  if(reading.reason==="new_bssid")return "Nowy BSSID dla znanej sieci";
  if (reading.mismatch) return "Rozbieżność · oczekiwano " + reading.expected;
  if (!reading.expected) return "Sieć poza inwentarzem";
  if (reading.security === "UNKNOWN") return "Zabezpieczenia nierozpoznane";
  return "Zabezpieczenia zgodne z inwentarzem";
}

function reportDetails(scan){
  const box=node("details","");
  const label=scan.legacy ? "Zapis archiwalny" : "Skan";
  box.append(node("summary",label+" · "+scan.ssid+" · obserwacje: "+scan.points.length));
  if(scan.unchanged)box.append(node("p","Raport bez zmian od "+stamp({at:scan.last_changed_at})));
  for(const r of scan.points){
    const strength=r.signal_pct!=null ? " · "+r.signal_pct+"% (ok. "+r.rssi_dbm+" dBm)"
      : Number.isFinite(r.rssi_dbm) ? " · "+r.rssi_dbm+" dBm" : "";
    const channel=r.channel==null ? "" : " · kanał "+r.channel;
    const frequency=r.frequency_mhz==null ? "" : " · "+r.frequency_mhz+" MHz";
    box.append(node("div",(r.ssid || "Ukryty SSID")+" · "+r.bssid+strength+channel+frequency+" · "+r.security+" · "+signalTypes[r.category][0]+" · "+readingStatus(r)));
  }
  return box;
}
function scanHistory(group, brief=false){
  const box=node("div","","wifi-map-popup");
  for(const scan of brief ? group.slice(0,1) : group){
    const entry=node("article","");
    entry.append(node("strong",scan.sensor),node("div",stamp(scan)),node("div",locationText(scan)));
    if(brief)entry.append(node("div",scan.ssid+" · obserwacje: "+scan.points.length));
    else entry.append(reportDetails(scan));
    box.append(entry);
  }
  return box;
}
function updateScanLayer(readings,scans){
  const signature=JSON.stringify([readings,scans,$("unverifiedToggle").checked]);
  if(signature===lastReadings)return;
  lastReadings=signature;
  wifiReadings=readings;
  wifiScans=scans;
  scanLayer.clearLayers();
  scanPoints.clear();
  const reports=new Map(scans.map(scan=>[scan.id,{...scan,points:[]}]));
  for(const reading of readings){
    // Older files contain observations without a scan identity.
    const id=reading.scan_id || JSON.stringify([reading.sensor,reading.at,reading.ssid,
      reading.lat,reading.lng,reading.unverifiedPoint,reading.location]);
    if(!reports.has(id))reports.set(id,{...reading,legacy:true,points:[]});
    reports.get(id).points.push(reading);
  }
  const groups=new Map(), history=document.createDocumentFragment();
  for(const scan of [...reports.values()].sort((a,b)=>b.at.localeCompare(a.at))){
    scan.points.sort((a,b)=>(b.signal_pct ?? -1)-(a.signal_pct ?? -1) || a.bssid.localeCompare(b.bssid));
    const row=document.createElement("tr"), details=document.createElement("td");
    details.append(reportDetails(scan));
    row.append(node("td",stamp(scan)),details,node("td",scan.sensor),node("td",locationText(scan)));
    history.append(row);
    const p=visiblePoint(scan);
    if(!Number.isFinite(p.lat) || !Number.isFinite(p.lng))continue;
    const place=key(p);
    if(!groups.has(place))groups.set(place,[]);
    groups.get(place).push(p);
  }
  $("wifiRows").replaceChildren(history);
  for(const [place, group] of groups){
    const latest=group[0], title="Miejsce wykonania skanów · "+latest.sensor;
    const icon=L.divIcon({className:"wifi-map-pin",html:String(group.length),iconAnchor:[0,0],iconSize:[24,24]});
    const marker=L.marker([latest.lat,latest.lng],{icon,title,alt:title,zIndexOffset:100}).addTo(scanLayer)
      .bindTooltip(()=>scanHistory(group,true),{className:"record-tip"}).bindPopup(()=>scanHistory(group));
    scanPoints.set(place,{marker,group});
  }
  $("fitWifi").disabled=!scanPoints.size;
}
$("unverifiedToggle").onchange=()=>{renderRecords();updateScanLayer(wifiReadings,wifiScans)};

function revealScans() {
  $("wifiLayerToggle").checked = true;
  scanLayer.addTo(map);
}

$("wifiLayerToggle").onchange = event => {
  if (event.target.checked) scanLayer.addTo(map);
  else scanLayer.remove();
};
$("fitWifi").onclick = () => {
  const points = Array.from(scanPoints.values(), item => item.marker.getLatLng());
  if (!points.length) return;
  revealScans();
  map.fitBounds(L.latLngBounds(points), {maxZoom: 18, padding: [36, 36]});
};

function showObservation(incident, observation) {
  $("wifiDialog").close();
  view("overview");
  revealScans();
  const point = [observation.lat, observation.lng];
  map.setView(point, 18);
  const text = node("div", "");
  text.append(node("strong", "Miejsce wykonania skanu"), node("div", observation.sensor),
    node("div", stamp(observation)), node("div", incident.ssid + " · " + incident.security),
    node("div", "Oczekiwano: " + observation.expected));
  L.popup().setLatLng(point).setContent(text).openOn(map);
  $("map").scrollIntoView({block: "center"});
}

function showWifiCase(incident) {
  $("wifiTitle").textContent = (incident.reason==="new_bssid" ? "Nowy BSSID: " : "Niezgodne zabezpieczenia: ") + incident.ssid;
  const body = $("wifiEvidence");
  body.replaceChildren(node("p", "BSSID: " + incident.bssid));
  for (const observation of incident.observations) {
    const card = node("article", "", "detailbox wifi-reading");
    card.append(node("strong", observation.sensor), node("div", stamp(observation)),
      node("div", "Z inwentarza: " + observation.expected),
      node("div", "Ze skanu: " + incident.security),node("div",locationText(observation)));
    const button = node("button", "Pokaż miejsce skanu");
    button.onclick = () => showObservation(incident, observation);
    button.hidden = !Number.isFinite(observation.lat) || !Number.isFinite(observation.lng);
    card.append(button);
    body.append(card);
  }
  $("wifiDialog").showModal();
}

function wifiCard(incident) {
  const card = node("button", "", "alert-card");
  card.append(node("span", "Z obserwacji Wi-Fi", "pill"), node("h3", incident.ssid + " · " + incident.security),
    node("small", "Obserwacje: " + incident.observations.length + " · " + stamp(incident.observations[0])));
  card.onclick = () => showWifiCase(incident);
  return card;
}

async function loadWifi() {
  const response = await fetch("/api/wifi", {cache: "no-store"});
  if (!response.ok) throw Error("Nie udało się odczytać listy spraw.");
  const state = await response.json();
  canClassify = state.canClassify;
  listSignals(state.readings);
  updateScanLayer(state.readings,state.scans || []);
  $("wifiTotals").textContent = "Punkty: " + state.inventory + " · Odczyty: " + state.observations;
  $("wifiCount").textContent = state.cases.length;
  $("wifiCases").replaceChildren(...state.cases.map(wifiCard));
  if (!state.cases.length) {
    $("wifiCases").append(node("p", "Brak wykrytych rozbieżności w zaimportowanych skanach.", "aside-foot"));
  }
  $("wifiPreview").replaceChildren(...state.cases.slice(0, 2).map(wifiCard));
  if (!state.cases.length) $("wifiPreview").append(node("p", "Brak zarejestrowanych rozbieżności Wi-Fi.", "aside-foot"));
}

for (const form of document.querySelectorAll("[data-import]")) {
  form.onsubmit = async event => {
    event.preventDefault();
    const file = form.querySelector("input").files[0];
    if (!file) return;
    const status = form.querySelector("[role=status]");
    if (file.size > 1048576) {
      status.textContent = "Wybierz plik CSV do 1 MB.";
      return;
    }
    const submit = form.querySelector("button");
    submit.disabled = true;
    status.textContent = "Wczytywanie pliku…";
    try {
      const response = await fetch("/api/wifi/" + form.dataset.import, {
        method: "POST", headers: {"Content-Type": "text/csv", "X-Aegis-Import": "1"}, body: file
      });
      const result = await response.json();
      if (!response.ok) throw Error(result.error || "Błąd importu.");
      status.textContent = form.dataset.import === "inventory"
        ? "Zapisano inwentarz. Liczba zatwierdzonych punktów: " + result.points + "."
        : "Nowe odczyty: " + result.added + ". Pominięte duplikaty: " + result.duplicates + ". Nowe sprawy: " + result.new_cases + ".";
      await loadWifi();
    } catch (error) {
      status.textContent = error.message || "Nie udało się połączyć z serwerem.";
    } finally {
      submit.disabled = false;
    }
  };
}
function refreshWifi() {
  loadWifi().catch(() => { $("wifiTotals").textContent = "Błąd odczytu danych"; });
}
refreshWifi();
window.addEventListener("hashchange", () => { if (current === "alerts") refreshWifi(); });
setInterval(() => { if (!document.hidden) refreshWifi(); }, 15000);
