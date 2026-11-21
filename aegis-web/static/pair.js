"use strict";
let phoneSession=null, phoneState={};
const phoneRequest=async(action,data)=>{
  const response=await fetch("/api/phone/"+action,{method:"POST",headers:{"Content-Type":"application/json","X-Aegis-Session":"1"},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok)throw Error(result.error || "Błąd połączenia z telefonem.");
  return result;
};
function updatePhoneBadge(){
  const manual=series?.point?.location?.source==="manual";
  const active=phoneState.location?.source==="phone";
  const badge=$("sourceBadge");
  badge.dataset.state=manual || active ? "active" : phoneState.remaining>0 ? "stale" : "";
  badge.textContent=manual ? "Źródło pozycji · stałe stanowisko "+(series.point.location.station || "")
    : active ? "Źródło pozycji · telefon · ±"+Math.ceil(phoneState.location.accuracy)+" m · wiek "+phoneState.age+" s"
    : phoneState.remaining>0 ? "Ostatnia pozycja telefonu · wygasa za "+phoneState.remaining+" s · nowe pomiary bez pinezki"
    : "Brak aktywnej lokalizacji · pomiary można zapisywać bez pinezki";
  if(!manual && !active && positionMarker?.phone){
    if(phoneState.remaining>0 && !phoneState.reason){
      positionMarker.setOpacity(.55).setTooltipContent("Ostatnia znana pozycja telefonu · "+phoneState.age+" s temu");
    }else{positionMarker.remove();positionMarker=null}
  }
}
async function pollPhone(){
  const session=phoneSession;
  if(!session)return;
  try{
    const state=await phoneRequest("status",{session});
    if(session!==phoneSession)return;
    phoneState=state;
    $("phoneStatus").textContent=state.reason==="kinematic_jump" ? "Odrzucono skok pozycji telefonu." : state.reason==="outside_city" ? "Telefon poza obszarem pracy." : state.paired ? "Telefon połączony." : "Oczekiwanie na telefon. Po wygaśnięciu linku ponów parowanie.";
    if(state.paired)$("phoneInvite").hidden=true;
  }catch{if(session!==phoneSession)return;phoneState={};$("phoneStatus").textContent="Brak połączenia z serwerem."}
  updatePhoneBadge();
}
function drawPairCode(url){
  const code=new PairQRCode(-1,1);
  code.addData(url);code.make();
  const size=code.getModuleCount(), canvas=$("pairQR"), scale=4;
  canvas.width=canvas.height=(size+8)*scale;
  const ctx=canvas.getContext("2d");
  ctx.fillStyle="white";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.fillStyle="black";
  for(let y=0;y<size;y++)for(let x=0;x<size;x++)if(code.isDark(y,x))ctx.fillRect((x+4)*scale,(y+4)*scale,scale,scale);
}
async function disconnectPhone(){
  const session=phoneSession;
  if(!session)return;
  try{
    await phoneRequest("stop",{session});
    if(session!==phoneSession)return;
    phoneSession=null;phoneState={};$("phoneInvite").hidden=$("stopPhone").hidden=true;
    $("pairPin").textContent="";$("phoneUrl").value="";$("phoneStatus").textContent="Zakończono parowanie.";updatePhoneBadge();
  }catch(e){$("phoneStatus").textContent=e.message}
}
$("stopPhone").onclick=disconnectPhone;
$("copyPhone").onclick=async()=>{try{await navigator.clipboard.writeText($("phoneUrl").value)}catch{$("phoneUrl").select()}};
$("pairPhone").onclick=async()=>{
  if(series){$("phoneStatus").textContent="Zakończ serię przed rozpoczęciem nowego parowania.";return}
  $("pairPhone").disabled=true;
  try{
    const result=await phoneRequest("start",{host:$("phoneHost").value,together:$("phoneTogether").checked});
    phoneSession=result.session;phoneState={};$("pairPin").textContent=result.pin;$("phoneUrl").value=result.url;$("phoneInvite").hidden=$("stopPhone").hidden=false;
    $("localCert").hidden=["online","cloudflare"].includes($("phoneHost").value) || $("phoneHost").value.startsWith("http://");drawPairCode(result.url);
    await pollPhone();
  }catch(e){$("phoneStatus").textContent=e.message}
  finally{$("pairPhone").disabled=false}
};
fetch("/api/capabilities").then(r=>r.json()).then(info=>{
  if(!info.onlinePairing && !info.phoneAddresses?.length)return;
  $("phoneHost").replaceChildren(...info.phoneAddresses.map(a=>new Option(a,a)));
  for(const addr of info.phoneAddresses)$("phoneHost").add(new Option("Android · test HTTP w zaufanej sieci "+addr,"http://"+addr));
  if(info.onlinePairing){$("phoneHost").add(new Option("Cloudflare · aplikacja na telefon","cloudflare"),0);$("phoneHost").add(new Option("Publiczny tunel HTTPS (ngrok)","online"));$("phoneHost").value="cloudflare"}
  $("phoneHost").onchange=()=>{$("tunnelHint").hidden=!["online","cloudflare"].includes($("phoneHost").value)};
  $("phoneHost").onchange();
  $("phonePanel").hidden=false;
}).catch(()=>{});
$("phonePanel").addEventListener("toggle",async ()=>{
  if(!$("phonePanel").open)return;
  try{
    const info=await phoneRequest("control",{});
    if(!info.session)return;
    phoneSession=info.session;$("phoneHost").value=info.mode;
    $("phoneHost").onchange();$("stopPhone").hidden=false;
    if(info.url){
      $("phoneUrl").value=info.url;$("pairPin").textContent=info.pin;
      $("localCert").hidden=["cloudflare","online"].includes(info.mode) || info.mode.startsWith("http://");
      $("phoneInvite").hidden=false;drawPairCode(info.url);
    }
    await pollPhone();
  }catch(e){$("phoneStatus").textContent=e.message}
});
setInterval(pollPhone,2000);
