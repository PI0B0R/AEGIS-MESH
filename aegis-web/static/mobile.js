"use strict";
const byId=id=>document.getElementById(id);
const map=L.map("fieldMap",{zoomControl:false,attributionControl:true}).fitBounds([[49.0,14.1],[54.85,24.1]]);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
L.control.zoom({position:"topleft"}).addTo(map);
let fixPin,radius,stationPin,device,radioState,casesLive=[],sampleCase=null,polling=false,hasReport=false;
const timeAt=t=>new Date(t).toLocaleTimeString("pl-PL");
const element=(name,txt)=>Object.assign(document.createElement(name),{textContent:txt});
function tab(name){
  document.querySelectorAll("[data-screen]").forEach(x=>x.hidden=x.dataset.screen!==name);
  document.querySelectorAll("[data-tab]").forEach(x=>{if(x.dataset.tab===name)x.setAttribute("aria-current","page");else x.removeAttribute("aria-current")});
  if(name==="map")map.invalidateSize();
  if(name!=="alerts" && stream)stopCamera();
  window.scrollTo(0,0);
}
document.querySelectorAll("[data-tab]").forEach(b=>b.onclick=()=>tab(b.dataset.tab));
byId("home").onclick=e=>{e.preventDefault();tab("map")};
function markDevice(p){
  const center=[p.coords.latitude,p.coords.longitude],accuracy=p.coords.accuracy;
  const first=!device;device=p;
  if(!fixPin){
    radius=L.circle(center,{radius:accuracy,color:"#216fa0",weight:1,fillOpacity:.12}).addTo(map);
    fixPin=L.circleMarker(center,{radius:7,color:"white",weight:3,fillColor:"#1177b9",fillOpacity:1}).bindTooltip("Telefon").addTo(map);
  }else{fixPin.setLatLng(center);radius.setLatLng(center).setRadius(accuracy)}
  byId("recenter").disabled=false;byId("accuracy").textContent="±"+Math.ceil(accuracy)+" m";
  if(first)centerDevice();
}
function centerDevice(){if(radius)map.fitBounds(radius.getBounds(),{maxZoom:18,padding:[12,12],animate:false})}
byId("recenter").onclick=centerDevice;
function stopWatch(){
  generation++;if(watch!==undefined)navigator.geolocation.clearWatch(watch);watch=undefined;
  start.hidden=false;start.disabled=false;stop.hidden=true;byId("fixBadge").textContent="GPS wyłączony";
}
start.onclick=async()=>{
  start.disabled=true;
  try{
    await pair();if(!navigator.geolocation)throw Error("Ta przeglądarka nie udostępnia lokalizacji.");
    const run=++generation;
    statusLine.textContent="Oczekiwanie na pozycję urządzenia…";byId("fixBadge").textContent="Szukanie GPS";
    start.hidden=true;stop.hidden=false;
    watch=navigator.geolocation.watchPosition(async p=>{
      if(run!==generation)return;
      markDevice(p);
      const text=p.coords.latitude.toFixed(5)+"°, "+p.coords.longitude.toFixed(5)+"° · "+timeAt(p.timestamp);
      statusLine.textContent=text;
      try{
        await send("/fix",{device:"mobile",lat:p.coords.latitude,lng:p.coords.longitude,accuracy:p.coords.accuracy,timestamp:p.timestamp/1000});
        if(run===generation && device===p){byId("fixBadge").textContent="GPS aktywny";statusLine.textContent=text+" · przyjęto w sesji laptopa";}
      }catch(e){if(run===generation && device===p){byId("fixBadge").textContent="Odczyt orientacyjny";statusLine.textContent=text+" · "+e.message}}
    },e=>{if(run!==generation)return;statusLine.textContent=e.code===1?"Brak zgody na lokalizację. Włącz ją w ustawieniach strony.":"Brak świeżej pozycji telefonu.";if(e.code===1)stopWatch()},
    {enableHighAccuracy:true,maximumAge:0,timeout:12000});
  }catch(e){stopWatch();statusLine.textContent=e.message}
};
stop.onclick=async()=>{stopWatch();try{await send("/detach",{});statusLine.textContent="Zakończono udostępnianie pozycji."}catch(e){statusLine.textContent=e.message}};
function renderRadio(r){
  radioState=r;
  byId("radioStart").hidden=r.active;byId("radioStop").hidden=!r.active;
  for(const item of ["network","zone","anchor"])byId(item).disabled=r.active;
  if(r.active){byId("network").value=r.ssid;byId("zone").value=r.zone}
  const state = r.active ? "Laptop odczytuje " + r.ssid + " co około 30 s." : "Odczyty tej sesji są zatrzymane.";
  let last = "";
  if (r.at) {
    const when = new Date(r.at).toLocaleString("pl-PL");
    last = "Ostatni raport z laptopa (" + when + "). " + (r.count === 0
      ? "Windows nie zgłosił żadnego punktu sieci " + r.ssid + "."
      : "Liczba punktów sieci " + r.ssid + " w raporcie: " + r.count + ".");
  } else if (r.active && !r.error) {
    last = "Oczekiwanie na pierwszy raport Windows.";
  }
  byId("radioState").textContent = [state, r.error, last].filter(Boolean).join(" ");
  if(r.station){
    const center=[r.station.lat,r.station.lng];
    if(!stationPin)stationPin=L.circleMarker(center,{radius:8,color:"white",weight:2,fillColor:"#225237",fillOpacity:1}).addTo(map).bindTooltip("Stałe stanowisko laptopa");
    else stationPin.setLatLng(center);
  }else if(stationPin){stationPin.remove();stationPin=null}
}
async function radio(action){
  const btn=byId(action==="start"?"radioStart":"radioStop");btn.disabled=true;
  try{await pair();renderRadio(await send("/radio",{action,ssid:byId("network").value,zone:byId("zone").value,anchor:byId("anchor").checked}));byId("radioMessage").textContent=""}
  catch(e){byId("radioMessage").textContent=e.message}finally{btn.disabled=false}
}
byId("radioStart").onclick=()=>radio("start");byId("radioStop").onclick=()=>radio("stop");
function card(c){
  const box=element("article","");box.className="alarm";
  box.append(element("h3",c.ssid+" · "+c.security),element("p","BSSID "+c.bssid+" · oczekiwano "+c.expected),
    element("p",c.zones.length?"Zacznij oględziny w strefie "+c.zones.join(" lub ")+".":"Brak aktualnego wskazania strefy."));
  return box;
}
function updateCases(){
  byId("liveAlerts").replaceChildren(...casesLive.map(card));
  if(!casesLive.length)byId("liveAlerts").append(element("p","Brak otwartych alertów w raportach laptopa."));
  byId("alertCount").textContent=String(casesLive.length)+" otw.";
  const previous=field("incident").value;
  cases=sampleCase?[...casesLive,sampleCase]:casesLive;
  field("incident").replaceChildren(...cases.map(c=>new Option((c===sampleCase?"Próba · ":"")+c.ssid+" · "+c.security,c.id)));
  if(cases.some(c=>c.id===previous))field("incident").value=previous;
  display();
  const selected=cases.find(c=>c.id===field("incident").value);
  document.querySelectorAll("#indoor span").forEach(n=>n.classList.toggle("chosen",!!selected?.zones.includes(n.textContent)));
}
async function refreshMobile(){
  if(polling || !credential)return;
  polling=true;
  try{
    const result=await send("/mobile",{});renderRadio(result.radio);casesLive=result.cases;updateCases();
    const fix=result.apkFix;
    if(fix && watch===undefined){
      const timestamp=Date.parse(fix.location.at);
      markDevice({coords:{latitude:fix.lat,longitude:fix.lng,accuracy:fix.location.accuracy},timestamp});
      byId("fixBadge").textContent=Date.now()-timestamp<=15000?"GPS telefonu · APK":"Ostatnia pozycja APK";
      statusLine.textContent=new Date(timestamp).toLocaleString("pl-PL")+" · pozycja telefonu z odbiornika GPS";
    }
    byId("connection").textContent="● Laptop online";gate(false);
  }catch(e){byId("connection").textContent="Brak połączenia";byId("liveAlerts").textContent="Nie udało się odświeżyć alertów."}
  finally{polling=false}
}
byId("sample").onclick=async()=>{
  try{await pair();sampleCase=await send("/sample",{});sampleCase.id="sample-"+sampleCase.id;
    byId("sampleAlert").replaceChildren(card(sampleCase));updateCases();
    field("incident").value=sampleCase.id;field("incident").onchange();
  }catch(e){byId("sampleAlert").textContent=e.message}
};
byId("inspect").onclick=async()=>{
  try{await pair();await refreshMobile();byId("inspection").hidden=false;byId("inspect").hidden=true;updateCases()}
  catch(e){byId("liveAlerts").textContent=e.message}
};
async function connect(){
  try{await pair();await refreshMobile()}
  catch(e){gate(true,e.message)}
}
byId("join").onclick=connect;
if(invite || credential)connect();else gate(true);
setInterval(()=>{if(!document.hidden)refreshMobile();if(watch!==undefined && device && Date.now()-device.timestamp>15000)byId("fixBadge").textContent="Ostatni odczyt"},5000);
