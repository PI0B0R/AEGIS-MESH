"use strict";

const areaMessage=$("areaMessage");
let pickingCorrection=false, correctionPoint=null, correctionKeys=[];

async function areaRequest(path,data){
  const response=await fetch(path,data ? {method:"POST",headers:{"Content-Type":"application/json","X-Aegis-Area":"1"},body:JSON.stringify(data)} : {cache:"no-store"});
  const result=await response.json();
  if(!response.ok)throw Error(result.error || "Nie udało się wczytać obszaru.");
  return result;
}
function applyArea(config){
  workingCity=config.city;
  workingRadius=config.radius;
  $("areaCity").value=workingCity?.name || "";
  if(positionMarker){positionMarker.remove();positionMarker=null}
  if(workingCity)map.setView([workingCity.lat,workingCity.lng],12,{animate:false});
  else showPoland(map);
  areaMessage.textContent=workingCity
    ? "Widok okolicy "+workingCity.name+" (25 km). Znacznik Wi-Fi oznacza miejsce wykonania skanu laptopem."
    : "Widok wszystkich zapisanych miejsc skanowania.";
  $("correctArea").hidden=!workingCity;
}
const areaReady=Promise.all([areaRequest("/api/work-area"),cityList]).then(([config,cities])=>{
  $("areaCity").replaceChildren(new Option("Wszystkie miejsca",""),...cities.map(c=>new Option(c.name,c.name)).sort((a,b)=>a.text.localeCompare(b.text,"pl")));
  applyArea(config);
  if(!workingCity)restoreMeasurementPosition();
  $("areaCity").disabled=false;
}).catch(()=>{areaMessage.textContent="Nie udało się wczytać obszaru pracy. Odśwież stronę.";});

$("areaCity").onchange=async ()=>{
  const select=$("areaCity");
  if(series || choosing || pickingCorrection){
    select.value=workingCity?.name || "";
    areaMessage.textContent="Zakończ bieżącą serię lub wybór miejsca, aby zmienić obszar.";
    return;
  }
  select.disabled=true;
  try{
    applyArea(await areaRequest("/api/work-area",{city:select.value || null}));
    await Promise.all([refresh(),loadWifi()]);
  }catch(error){select.value=workingCity?.name || "";areaMessage.textContent=error.message}
  finally{select.disabled=false}
};
function localMinute(date){
  return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
}
function prepareCorrection(){
  correctionPoint=null;
  $("correctPoint").textContent="";
  $("correctSave").disabled=true;
  const from=Date.parse($("correctFrom").value),end=Date.parse($("correctTo").value)+60000;
  const inRange=row=>row.unverifiedPoint && row.location?.reason==="outside_city" && Date.parse(row.at)>=from && Date.parse(row.at)<end;
  const measurements=live.filter(inRange),readings=[...wifiReadings.filter(r=>!r.scan_id),...wifiScans].filter(inRange);
  correctionKeys=[...measurements,...readings].map(row=>row.recordKey);
  $("correctSummary").textContent="Pomiary łączności: "+measurements.length+" · odczyty Wi-Fi: "+readings.length+".";
  $("correctPick").disabled=!correctionKeys.length || correctionKeys.length>5000;
  if(correctionKeys.length>5000)$("correctSummary").textContent+=" Wybierz krótszy przedział (do 5000 wpisów).";
}
function openCorrections(at){
  if(!workingCity)return;
  if(series || choosing){notify("Zakończ serię lub wybór miejsca przed korektą historii.");return}
  const date=at ? new Date(at) : new Date();
  const day=localMinute(date).slice(0,10);
  $("correctFrom").value=at?localMinute(date):day+"T00:00";
  $("correctTo").value=at?localMinute(date):day+"T23:59";
  prepareCorrection();
  $("correctionDialog").showModal();
}
$("correctArea").onclick=()=>openCorrections();
$("correctFrom").oninput=$("correctTo").oninput=prepareCorrection;
function endPick(){
  pickingCorrection=false;
  $("map").classList.remove("pick-mode");$("notice").replaceChildren();
  $("correctionDialog").showModal();
}
$("correctPick").onclick=()=>{
  $("correctionDialog").close();view("overview");pickingCorrection=true;
  map.setView([workingCity.lat,workingCity.lng],13,{animate:false});
  $("map").classList.add("pick-mode");
  notify("Kliknij miejsce, w którym był laptop podczas wybranych pomiarów.");
  const cancel=node("button","Anuluj");cancel.onclick=endPick;$("notice").append(cancel);
  $("map").scrollIntoView({block:"center"});
};
$("map").addEventListener("click",event=>{
  if(!pickingCorrection || event.target.closest(".leaflet-control"))return;
  event.stopImmediatePropagation();
  const {lat,lng}=map.mouseEventToLatLng(event).wrap();
  if(outsideCity({lat,lng})){map.setView([workingCity.lat,workingCity.lng],13);return}
  correctionPoint={lat,lng,location:{source:"manual",at:new Date().toISOString(),station:"Stanowisko · "+workingCity.name}};
  $("correctPoint").textContent="Wybrane stanowisko: "+lat.toFixed(5)+"°, "+lng.toFixed(5)+"°. Sprawdź przedział czasu przed zapisaniem.";
  $("correctSave").disabled=false;
  $("map").closest("dialog[open]")?.close();
  endPick();
},true);
$("correctSave").onclick=async ()=>{
  if(!correctionPoint)return;
  const point=correctionPoint;
  $("correctSave").disabled=true;
  try{
    const result=await areaRequest("/api/location-corrections",{...point,city:workingCity.name,keys:correctionKeys});
    rememberStation(point);
    $("correctionDialog").close();
    await Promise.all([refresh(),loadWifi()]);
    view("overview");map.setView([point.lat,point.lng],17);
    areaMessage.textContent="Skorygowano "+result.count+" wpisów. Pinezka oznacza stanowisko laptopa.";
  }catch(error){$("correctPoint").textContent=error.message;$("correctSave").disabled=false}
};
