"use strict";
const field=id=>document.getElementById(id);
const bearings={A:-45,B:45,C:135,D:-135};
let cases=[],stream,heading=null,baseline=null,lastHeading=0,cameraRun=0;
function orientation(e){
  if(![e.alpha,e.beta,e.gamma].every(Number.isFinite))return;
  const [a,b,g]=[e.alpha,e.beta,e.gamma].map(v=>v*Math.PI/180);
  const x=-Math.cos(a)*Math.sin(g)-Math.sin(a)*Math.sin(b)*Math.cos(g);
  const y=-Math.sin(a)*Math.sin(g)+Math.cos(a)*Math.sin(b)*Math.cos(g);
  if(Math.hypot(x,y)<.35){heading=null;field("direction").textContent="Trzymaj kamerę poziomo";return}
  heading=Math.atan2(x,y)*180/Math.PI;lastHeading=performance.now();
  field("calibrate").disabled=!stream;
  if(baseline===null)return;
  const z=field("target").value;
  if(!(z in bearings))return;
  const offset=((bearings[z]-heading+baseline+540)%360)-180;
  const label=Math.abs(offset)<12 ? "↑ Przed Tobą" : (offset>0?"→ W prawo ":"← W lewo ")+Math.round(Math.abs(offset))+"°";
  field("direction").textContent=label+" · strefa "+z;
}
function stopCamera(){
  cameraRun++;
  window.removeEventListener("deviceorientation",orientation);
  stream?.getTracks().forEach(t=>t.stop());stream=null;heading=baseline=null;
  field("video").srcObject=null;field("calibrate").disabled=true;
  field("scene").hidden=field("endCamera").hidden=true;
  field("camera").disabled=!field("target").value;
}
function display(){
  const c=cases.find(x=>x.id===field("incident").value);
  field("finding").textContent=c ? c.ssid+" · "+c.bssid+" · oczekiwano "+c.expected+", odebrano "+c.security+". "+(c.zones.length?"Sprawdź strefę "+c.zones.join(" lub ")+".":"Brak aktualnego wskazania strefy.") : "Brak otwartych spraw.";
  document.querySelectorAll("#indoor span").forEach(n=>n.classList.toggle("chosen",!!c?.zones.includes(n.textContent)));
  const previous=field("target").value;
  field("target").replaceChildren(...(c?.zones || []).map(z=>new Option(z,z)));
  if(c?.zones.includes(previous))field("target").value=previous;
  field("targetLabel").hidden=!c?.zones.length;
  if(!field("target").value)stopCamera();
  field("camera").disabled=!!stream || !field("target").value;
}
field("incident").onchange=()=>{stopCamera();display()};
field("target").onchange=()=>{baseline=null;field("direction").textContent="Skalibruj widok"};
field("camera").onclick=async()=>{
  const run=++cameraRun;field("camera").disabled=true;
  try{
    const orientationAPI=window.DeviceOrientationEvent;
    if(!orientationAPI || !navigator.mediaDevices?.getUserMedia)throw Error("Ten telefon nie udostępnia kamery lub orientacji. Skorzystaj ze wskazania strefy.");
    if(orientationAPI.requestPermission && await orientationAPI.requestPermission()!=="granted")throw Error("Brak zgody na orientację.");
    const media=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"}},audio:false});
    if(run!==cameraRun || document.hidden){media.getTracks().forEach(t=>t.stop());return}
    stream=media;field("video").srcObject=stream;
    field("scene").hidden=field("endCamera").hidden=false;field("direction").textContent="Skalibruj widok";
    field("arStatus").textContent="Oczekiwanie na odczyty orientacji. Przy braku odczytów użyj planu stref.";
    window.addEventListener("deviceorientation",orientation);
  }catch(e){stopCamera();field("arStatus").textContent=e.message}
};
field("calibrate").onclick=()=>{
  if(heading===null || performance.now()-lastHeading>2000){field("arStatus").textContent="Porusz telefonem i poczekaj na aktualny odczyt.";return}
  baseline=heading;field("arStatus").textContent="Skalibrowano względem ściany A–B.";
  field("direction").textContent="Strefa "+field("target").value+" · "+(bearings[field("target").value]>0?"w prawo ":"w lewo ")+Math.abs(bearings[field("target").value])+"°";
};
field("endCamera").onclick=stopCamera;
document.addEventListener("visibilitychange",()=>{if(document.hidden)stopCamera()});
window.addEventListener("pagehide",stopCamera);
