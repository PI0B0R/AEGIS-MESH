"use strict";
let invite=location.hash.slice(1),credential,pairing,watch,generation=0;
const pinEntry=document.querySelector("#pinEntry"), pinInput=document.querySelector("#phonePin");
const start=document.querySelector("#start"), stop=document.querySelector("#stop"), statusLine=document.querySelector("#status");
const storageKey="aegis.mobile.credential";
try{credential=invite ? null : sessionStorage.getItem(storageKey)}catch{}
if(/^apk=[A-Za-z0-9_-]{43}$/.test(invite)){
  credential=invite.slice(4);
  try{sessionStorage.setItem(storageKey,credential)}catch{}
  history.replaceState(null,"",location.pathname);
  invite=null;
}
if(invite)history.replaceState(null,"",location.pathname);
function gate(show,message=""){
  document.querySelector("#pairGate").hidden=!show;
  document.querySelector("#joinStatus").textContent=message;
  if(show)document.querySelector("#connection").textContent="Do sparowania";
}
async function send(path,data){
  const headers={"Content-Type":"application/json"};
  if(credential)headers.Authorization="Bearer "+credential;
  const response=await fetch(path,{method:"POST",headers,body:JSON.stringify(data),signal:AbortSignal.timeout(18000)});
  const result=await response.json();
  if(!response.ok){
    if(result.error==="Sesja telefonu wygasła."){
      credential=null;try{sessionStorage.removeItem(storageKey)}catch{}
      gate(true,"Sesja wygasła. Wygeneruj nowe zaproszenie na laptopie.");
    }
    throw Error(result.error || "Nie udało się połączyć z laptopem.");
  }
  return result;
}
async function pair(){
  if(credential)return;
  if(!invite && !/^[0-9]{8}$/.test(pinInput.value))throw Error("Wpisz osiem cyfr z panelu laptopa.");
  if(!pairing)pairing=send("/exchange",invite ? {token:invite} : {pin:pinInput.value});
  try{
    credential=(await pairing).key;invite=null;pinInput.value="";
    try{sessionStorage.setItem(storageKey,credential)}catch{}
    gate(false);
  }catch(e){invite=null;gate(true,e.message);throw e}finally{pairing=null}
}
window.mobileTransport=send;
