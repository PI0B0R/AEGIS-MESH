"use strict";
const $ = id => document.getElementById(id);
const fmt = new Intl.DateTimeFormat("pl-PL",{timeZone:"Europe/Warsaw",dateStyle:"short",timeStyle:"medium"});
const stamp = r => fmt.format(new Date(r.at));
const number = value => value.toLocaleString("pl-PL",{maximumFractionDigits:1});
const node = (tag,txt,cls) => {const e=document.createElement(tag);e.textContent=txt;if(cls)e.className=cls;return e};
let live=[], current="overview", choosing=false, draft=null, run=0;
const records=()=>[...live].sort((a,b)=>Date.parse(b.at)-Date.parse(a.at));
const key=r=>r.lat.toFixed(5)+","+r.lng.toFixed(5);
const map=L.map("map",{minZoom:3,maxZoom:21,preferCanvas:true,zoomSnap:0.25,zoomDelta:1});
showPoland(map);
const markerLayer=L.layerGroup().addTo(map);
const markerIndex=new Map();
let positionMarker;
const lastPositionKey="aegis.lastMeasurementPosition";

function drawMeasurementPosition(position,recenter=true){
  const anchor=[position.lat,position.lng];
  const label=locationText(position);
  if(!positionMarker){
    const icon=L.divIcon({className:"measurement-position",iconSize:[18,18],iconAnchor:[9,9]});
    positionMarker=L.marker(anchor,{icon,interactive:false,keyboard:false,zIndexOffset:200})
      .bindTooltip("",{permanent:true,direction:"bottom",offset:[0,17],className:"position-label"}).addTo(map);
  }
  const caption=node("span","");
  caption.append(node("strong",label),node("small",stamp(position)+" · "+position.detail));
  positionMarker.phone=position.location?.source==="phone";
  positionMarker.setOpacity(1).setLatLng(anchor).setTooltipContent(caption);
  if(recenter)map.setView(anchor,Math.max(17,map.getZoom()),{animate:false});
}

function focusMeasurement(point,at,detail,first=true){
  if(outsideCity(point) || !["manual","phone"].includes(point.location?.source))return;
  const position={lat:point.lat,lng:point.lng,location:point.location,at,detail};
  try{sessionStorage.setItem(lastPositionKey,JSON.stringify(position))}catch{}
  if(first && current!=="overview")view("overview");
  drawMeasurementPosition(position,first);
  if(first)$("map").scrollIntoView({block:"center",behavior:"instant"});
}

function restoreMeasurementPosition(){
  try{
    const p=JSON.parse(sessionStorage.getItem(lastPositionKey));
    if(p?.location?.source==="manual" && Number.isFinite(p.lat) && Number.isFinite(p.lng) && Number.isFinite(Date.parse(p.at)) && typeof p.detail==="string")
      drawMeasurementPosition(p);
  }catch{}
}
function notify(message){$("notice").textContent=message}
function popup(group,brief=false){
  const box=node("div","");
  for(const r of brief?group.slice(0,1):group){
    const block=node("article","");block.append(node("strong",r.name),node("div",stamp(r)),node("div","Test przeglądarki · "+number(r.ms)+" ms"),node("div",locationText(r)));box.append(block);
  }
  if(brief && group.length>1)box.append(node("small","Liczba pomiarów: "+group.length+" · kliknij po historię"));
  return box;
}
function visiblePoint(row){
  if($("unverifiedToggle").checked && row.unverifiedPoint){
    const p=row.unverifiedPoint;
    return {...row,...p,location:{...p.location,unverified:true}};
  }
  return row;
}
function renderRecords(){
  const items=records(), groups=new Map();
  items.map(visiblePoint).filter(r=>Number.isFinite(r.lat) && Number.isFinite(r.lng)).forEach(r=>{const k=key(r);if(!groups.has(k))groups.set(k,[]);groups.get(k).push(r)});
  $("pointCount").textContent=groups.size;$("measureCount").textContent=items.length;
  markerLayer.clearLayers();markerIndex.clear();
  for(const [k,group] of groups){
    const r=group[0],icon=L.divIcon({className:"",html:'<div class="pin real"></div>',iconSize:[26,26],iconAnchor:[7,26]});
    const marker=L.marker([r.lat,r.lng],{icon,title:r.name,alt:r.name,keyboard:true}).addTo(markerLayer);
    marker.bindTooltip(popup(group,true),{direction:"top",offset:[5,-25],className:"record-tip"}).bindPopup(popup(group),{offset:[6,-19]});
    markerIndex.set(k,marker);
  }
  renderRows();
}
function renderRows(){
  let items=records();
  if(current==="overview")items=items.slice(0,4);
  const rows=$("rows");rows.replaceChildren();
  items.forEach(r=>{
    const tr=document.createElement("tr"), first=document.createElement("td"), btn=node("button",r.name);
    if(r.location?.reason==="outside_city" && r.unverifiedPoint){
      btn.textContent="Skoryguj miejsce";
      btn.onclick=()=>openCorrections(r.at);
    }else if(r.unverifiedPoint){
      btn.textContent="Pozycja historyczna";
      btn.disabled=!$("unverifiedToggle").checked;
      btn.onclick=()=>{view("overview");const p=visiblePoint(r);map.setView([p.lat,p.lng],16);markerIndex.get(key(p))?.openPopup()};
    }else if(r.lat===null){
      btn.textContent="Dodaj miejsce na mapie";
      btn.onclick=()=>locateSavedMeasurement(r);
    }else{
      btn.onclick=()=>{
        view("overview");map.setView([r.lat,r.lng],17);markerIndex.get(key(r))?.openPopup();$("map").scrollIntoView({block:"center"});
      };
    }
    first.append(btn);tr.append(first,node("td",stamp(r)),node("td",locationText(r)),node("td",number(r.ms)+" ms"));rows.append(tr);
  });
  if(!items.length){const td=node("td","Brak zapisanych pomiarów. Wybierz „Zmierz tutaj”, aby wykonać pierwszy test.");td.colSpan=4;const tr=document.createElement("tr");tr.append(td);rows.append(tr)}
}
function view(name){
  current=["overview","alerts","measurements"].includes(name)?name:"overview";
  history.replaceState(null,"","#"+current);
  $("overview").hidden=current!=="overview";$("history").hidden=current==="alerts";$("alertPage").hidden=current!=="alerts";
  $("title").textContent={overview:"Pomiary sieci",measurements:"Historia pomiarów",alerts:"Alerty sieci"}[current];
  $("subtitle").textContent={overview:"Wyniki testów łączności i miejsca ich wykonania.",measurements:"Zapisane wyniki z czasem i miejscem wykonania pomiaru.",alerts:"Porównaj obserwacje z inwentarzem i sprawdź dowody na mapie."}[current];
  $("historyTitle").textContent=current==="overview"?"Ostatnie pomiary":"Wszystkie pomiary";
  $("allRows").hidden=current!=="overview";
  $("wifiHistory").hidden=current!=="measurements";
  document.querySelectorAll("[data-view]").forEach(a=>a.classList.toggle("active",a.dataset.view===current));
  if(current==="overview")map.invalidateSize();
  renderRows();
}
async function refresh(){
  try{const res=await fetch("/api/measurements",{cache:"no-store"});if(!res.ok)throw Error();const next=await res.json();
    if(JSON.stringify(live)!==JSON.stringify(next)){live=next;renderRecords()}
  }catch{if(!choosing)notify("Brak połączenia z serwerem pomiarów. Wyświetlamy ostatnie dostępne dane.")}
}
function ready(){
  const mode=$("mode").value;
  $("save").disabled=Boolean(draft?.candidate || draft?.locating || (draft?.existing && !draft.point)) || (mode==="single" && typeof draft?.ms!=="number")
    || (mode==="wifi" && !$("sessionSsid").value.trim());
  if(mode==="single")$("save").textContent=(draft?.point || (typeof phoneSession!=="undefined" && phoneSession && !draft?.existing)) ? "Zapisz pomiar" : (draft?.existing ? "Zapisz miejsce" : "Zapisz bez lokalizacji");
}
async function timedProbe(){
  const start=performance.now();
  const response=await fetch("/api/probe",{cache:"no-store",signal:AbortSignal.timeout(10000)});
  if(!response.ok)throw Error("Serwer nie odpowiedział.");
  await response.json();
  return Math.round((performance.now()-start)*10)/10;
}
async function probe(){
  const ticket=++run;draft.ms=null;ready();$("probeResult").textContent="Pomiar trwa…";$("retry").disabled=true;
  try{
    const ms=await timedProbe();if(ticket!==run)return;
    draft.ms=ms;draft.at=new Date().toISOString();
    $("probeResult").textContent=number(draft.ms)+" ms";
    await areaReady;
    if(ticket!==run || !$("formDialog").open || $("mode").value!=="single" || draft.existing)return;
    if(draft.point){showPointPreview(draft.point);return}
    if(draft.geoAttempt || draft.candidate || draft.skipLocation)return;
    $("previewMap").hidden=true;
    if(phoneSession){
      const session=phoneSession;
      const fix=await phoneRequest("status",{session}).catch(()=>null);
      if(ticket!==run || session!==phoneSession || !$("formDialog").open || $("mode").value!=="single" ||
          draft.point || draft.geoAttempt || draft.skipLocation)return;
      if(fix?.location?.source==="phone" && !outsideCity(fix) && Math.abs(Date.parse(draft.at)-Date.parse(fix.location.at))<=15000){
        showPointPreview(fix);
        $("locationStatus").textContent=locationText(fix);
      }else $("locationStatus").textContent="Pierwszy pomiar gotowy. Brak świeżej pozycji telefonu dla tego pomiaru.";
    }else{
      locateDraft(true);
    }
  }catch{if(ticket===run)$("probeResult").textContent="Serwer nie odpowiedział"}
  finally{if(ticket===run){$("retry").disabled=false;ready()}}
}
function setPoint(point){
  draft.point=point;
  draft.candidate=null;
  $("reviewPlace").hidden=true;
  $("keepStation").hidden=$("useCandidate").hidden=true;
  $("geo").disabled=false;
  const manual=point.location.source==="manual";
  $("stationOptions").hidden=!manual;
  $("stationLabel").value=point.location.station || "";
  $("locationStatus").textContent=locationText(point);
  if(!outsideCity(point))showPointPreview(point);
  ready();
}
const cityList=fetch("/miasta.json").then(response=>response.ok ? response.json() : []).catch(()=>[]);
function reviewCandidate(candidate){
  draft.candidate=candidate;
  $("keepStation").hidden=$("useCandidate").hidden=true;
  $("reviewPlace").hidden=false;
  $("locationStatus").textContent=locationText(candidate)+". Sprawdź pinezkę przed zatwierdzeniem stanowiska.";
  showPointPreview(candidate);
  ready();
}
const pointMaps=new Map();
function showPointPreview(point,id="previewMap"){
  $(id).hidden=false;
  if(!pointMaps.has(id)){
    const detailMap=L.map(id,{scrollWheelZoom:false});
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{
      maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(detailMap);
    pointMaps.set(id,{map:detailMap,markers:L.layerGroup().addTo(detailMap)});
  }
  const detail=pointMaps.get(id), center=[point.lat,point.lng];
  detail.markers.clearLayers();
  detail.map.invalidateSize().setView(center,18,{animate:false});
  if(Number.isFinite(point.location.accuracy)){
    const radius=L.circle(center,{radius:point.location.accuracy,interactive:false}).addTo(detail.markers);
    if(point.location.accuracy>25)detail.map.fitBounds(radius.getBounds(),{maxZoom:18,animate:false,padding:[8,8]});
  }
  L.circleMarker(center,{radius:6,fillOpacity:1,interactive:false}).addTo(detail.markers);
}

let whereRequest=0;
async function locateNow(){
  const request=++whereRequest;
  const dialog=$("whereDialog");
  if(!dialog.open)dialog.showModal();
  $("whereMap").hidden=$("whereRaw").hidden=true;
  $("whereRaw").onclick=null;
  $("whereStatus").textContent="Ustalam pozycję urządzenia…";
  const session=phoneSession;
  try{
    const point=session ? await phoneRequest("status",{session}) : await browserPoint();
    await areaReady;
    if(request!==whereRequest || !dialog.open)return;
    if(session && (session!==phoneSession || point.location?.source!=="phone" ||
        Math.abs(Date.now()-Date.parse(point.location.at))>15000)){
      $("whereStatus").textContent="Brak świeżej pozycji sparowanego telefonu. Otwórz na nim stronę lokalizacji i ponów odczyt.";
      return;
    }
    if(outsideCity(point)){
      $("whereStatus").textContent="Urządzenie wskazuje punkt poza obszarem "+workingCity.name+". Twoja bieżąca pozycja wymaga potwierdzenia.";
      $("whereRaw").hidden=false;
      $("whereRaw").onclick=()=>{
        $("whereStatus").textContent="Niepotwierdzony odczyt poza obszarem pracy · "+locationText(point);
        showPointPreview(point,"whereMap");
        $("whereRaw").hidden=true;
      };
      return;
    }
    $("whereStatus").textContent=locationText(point);
    showPointPreview(point,"whereMap");
  }catch(error){
    if(request!==whereRequest || !dialog.open)return;
    $("whereStatus").textContent=error.code===1 ? "Brak zgody na lokalizację. Włącz dostęp dla tej strony w ustawieniach przeglądarki i odśwież pozycję."
      : "Nie udało się pobrać pozycji urządzenia. Sprawdź ustawienia lokalizacji lub użyj sparowanego telefonu.";
  }
}
$("locateNow").onclick=$("whereRetry").onclick=locateNow;
$("whereDialog").addEventListener("close",()=>{whereRequest++});
$("acceptPlace").onclick=()=>{
  const {lat,lng}=draft.candidate;
  setPoint({lat,lng,location:{source:"manual",at:new Date().toISOString()}});
};
$("skipPlace").onclick=()=>{
  draft.skipLocation=true;$("previewMap").hidden=true;
  draft.autoLocation=false;draft.point=null;draft.candidate=null;
  $("reviewPlace").hidden=$("stationOptions").hidden=true;$("geo").disabled=false;
  $("locationStatus").textContent="Pomiar bez lokalizacji.";ready();
};
function resetPlaceControls(){
  $("previewMap").hidden=true;
  $("reviewPlace").hidden=true;
  $("keepStation").hidden=$("useCandidate").hidden=$("stationOptions").hidden=true;
  $("verifyStation").checked=false;
  $("reuseStation").hidden=true;
  try{
    const saved=JSON.parse(localStorage.getItem("aegis.manualStation"));
    if(saved?.location?.source==="manual" && Number.isFinite(saved.lat) && Number.isFinite(saved.lng) && !outsideCity(saved)){
      $("reuseStation").textContent="Użyj stanowiska: "+(saved.location.station || "ostatnia pinezka");
      $("reuseStation").hidden=false;
      $("reuseStation").onclick=()=>{
        draft.geoAttempt=null;draft.locating=false;draft.autoLocation=false;$("geo").disabled=false;
        setPoint(saved);
      };
    }
  }catch{}
}
function rememberStation(point){
  if(point?.location?.source!=="manual")return;
  const {conflict,...location}=point.location;
  try{localStorage.setItem("aegis.manualStation",JSON.stringify({...point,location}))}catch{}
}
$("stationLabel").oninput=()=>{draft.point.location.station=$("stationLabel").value.trim()};
function measurementId(){
  return globalThis.crypto?.randomUUID?.() || "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g,c=>(c==="x"?Math.floor(Math.random()*16):8+Math.floor(Math.random()*4)).toString(16));
}
function start(){
  choosing=false;$("map").classList.remove("pick-mode");notify("");
  draft={id:measurementId()};
  resetPlaceControls();
  $("locationStatus").textContent="Na komputerze możesz wskazać budynek ręcznie.";$("saveStatus").textContent="";$("geo").disabled=false;
  $("mode").disabled=false;$("mode").value="single";$("formDialog").showModal();changeMode();
}
function locateSavedMeasurement(record){
  ++run;
  draft={id:record.id,at:record.at,ms:record.ms,existing:true};
  resetPlaceControls();
  $("mode").value="single";$("mode").disabled=true;$("singleResult").hidden=false;
  $("modeHelp").textContent="Wskaż miejsce tego zapisanego pomiaru.";
  $("probeResult").textContent=number(record.ms)+" ms · "+stamp(record);
  $("ssidField").hidden=true;$("retry").hidden=true;$("saveStatus").textContent="";
  $("locationStatus").textContent="Wybierz miejsce na mapie.";$("geo").disabled=false;
  ready();chooseMapPoint();
}
$("measure").onclick=start;$("retry").onclick=probe;
$("geo").onclick=()=>locateDraft(false);
async function locateDraft(automatic){
  const pending=draft;
  const anchor=draft.point?.location.source==="manual" ? draft.point : null;
  pending.locating=!automatic;pending.autoLocation=false;pending.skipLocation=false;
  if(!anchor)pending.point=null;
  $("geo").disabled=true;$("locationStatus").textContent="Ustalamy pozycję urządzenia…";ready();
  const attempt=Symbol();draft.geoAttempt=attempt;draft.automaticAttempt=automatic;
  try{
    const candidate=await browserPoint();
    if(pending!==draft || draft.geoAttempt!==attempt || !$("formDialog").open || (automatic && $("mode").value!=="single"))return;
    await areaReady;
    if(pending!==draft || draft.geoAttempt!==attempt || !$("formDialog").open || (automatic && $("mode").value!=="single"))return;
    draft.browserHint=candidate;
    if(outsideCity(candidate)){
      if(!anchor)$("previewMap").hidden=true;
      $("locationStatus").textContent="Odczyt przeglądarki wskazuje inne miasto niż wybrany obszar ("+workingCity.name+"). "+(anchor?"Zachowano stanowisko.":"Wskaż stanowisko na mapie lub zapisz wynik bez lokalizacji.");
      return;
    }
    if(!anchor){reviewCandidate(candidate);return}
    const point=checkStation(anchor,candidate);
    setPoint(point);
    if(point.location.conflict){
      draft.candidate=candidate;
      $("keepStation").hidden=$("useCandidate").hidden=false;
      $("locationStatus").textContent=locationText(point)+". Którego miejsca użyć?";
    }
  }catch{
    if(pending!==draft || draft.geoAttempt!==attempt || !$("formDialog").open || (automatic && $("mode").value!=="single"))return;
    $("locationStatus").textContent=anchor ? "Brak odczytu urządzenia. Zachowano stanowisko." : "Brak pozycji urządzenia. Możesz wskazać miejsce lub wykonać pomiar bez lokalizacji.";
  }finally{
    if(pending===draft && draft.geoAttempt===attempt){draft.geoAttempt=null;draft.automaticAttempt=false;draft.locating=false;$("geo").disabled=Boolean(draft.candidate);ready()}
  }
}
$("keepStation").onclick=()=>setPoint(draft.point);
$("useCandidate").onclick=()=>reviewCandidate(draft.candidate);
function chooseMapPoint(){
  choosing=true;draft.geoAttempt=null;draft.locating=false;$("geo").disabled=false;ready();$("formDialog").close();view("overview");$("map").classList.add("pick-mode");
  if(workingCity)map.setView([workingCity.lat,workingCity.lng],13,{animate:false});
  notify("Wskaż na mapie miejsce wykonania pomiaru.");const cancel=node("button","Anuluj");
  const select=document.createElement("select");
  select.setAttribute("aria-label","Przybliż mapę do miasta");
  select.add(new Option("Przybliż do miasta…",""));
  cityList.then(cities=>{
    for(const city of [...cities].sort((a,b)=>a.name.localeCompare(b.name,"pl"))) {
      select.add(new Option(city.name,city.lat+","+city.lng));
    }
  });
  select.onchange=()=>{if(select.value)map.setView(select.value.split(",").map(Number),13,{animate:false})};
  $("notice").append(select);
  cancel.onclick=()=>{choosing=false;notify("");$("map").classList.remove("pick-mode");$("formDialog").showModal()};$("notice").append(cancel);
  $("map").scrollIntoView({block:"center"});
}
$("pick").onclick=chooseMapPoint;
// Capture the selection before markers or alert overlays handle the click.
$("map").addEventListener("click",e=>{
  if(!choosing || e.target.closest(".leaflet-control"))return;
  e.stopImmediatePropagation();const pos=map.mouseEventToLatLng(e).wrap();
  if(outsideCity(pos)){map.setView([workingCity.lat,workingCity.lng],13);return}
  draft.autoLocation=false;setPoint({lat:pos.lat,lng:pos.lng,location:{source:"manual",at:new Date().toISOString()}});choosing=false;
  $("map").classList.remove("pick-mode");$("map").closest("dialog[open]")?.close();notify("");$("formDialog").showModal();
},true);
$("measurementForm").onsubmit=async e=>{
  e.preventDefault();if(!draft || draft.candidate || draft.locating || (draft.existing && !draft.point))return;
  if($("mode").value!=="single"){startSeries();return}
  if(typeof draft.ms!=="number")return;
  const pending=draft;const data={...draft.point,id:draft.id,ms:draft.ms,at:draft.at,browserHint:draft.browserHint,phoneSession:draft.existing ? null : phoneSession};
  $("save").disabled=true;$("saveStatus").textContent="Zapisywanie…";
  try{
    const path=draft.existing?"/api/measurements/location":"/api/measurements";
    const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data),signal:AbortSignal.timeout(10000)});
    if(!r.ok)throw Error();const item=await r.json();live=[item,...live.filter(x=>x.id!==item.id)];renderRecords();
    if(pending!==draft)return;$("formDialog").close();
    if(item.lat===null){
      view("measurements");
      notify("Zapisano wynik bez lokalizacji. Wybierz „Dodaj miejsce na mapie” przy pomiarze.");
      return;
    }
    notify("Zapisano pomiar na mapie.");
    rememberStation(item);
    focusMeasurement(item,item.at,number(item.ms)+" ms");
  }catch{if(pending===draft){$("saveStatus").textContent="Zapis nie powiódł się. Spróbuj ponownie.";ready()}}
};
$("allRows").onclick=()=>view("measurements");
document.querySelectorAll(".close").forEach(b=>b.onclick=()=>b.closest("dialog").close());
function openRoute(){
  const name=location.hash.slice(1);
  view(name);
  if(name==="nowy-pomiar")start();
}
window.addEventListener("hashchange",openRoute);
document.addEventListener("DOMContentLoaded",openRoute,{once:true});

renderRecords();addCampusMap(map,notify);refresh();
setInterval(()=>{if(!document.hidden)refresh()},8000);
