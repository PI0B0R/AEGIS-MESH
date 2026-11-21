"use strict";
const el=id=>document.getElementById(id);
const tag=(name,text)=>Object.assign(document.createElement(name),{textContent:text});
const clock=at=>new Date(at).toLocaleString("pl-PL");
let state,selected,loading=false;
function title(c){
  const newPoint=c.reason.startsWith("new_bssid");
  if(newPoint && c.security==="OPEN")return "Nowy punkt rozgłasza "+c.ssid+" jako sieć otwartą";
  return (newPoint ? "Nowy BSSID dla " : "Zmiana zabezpieczeń ")+c.ssid+" · "+c.security;
}
function render(){
  const c=state.cases.find(x=>x.id===selected);
  el("plan").replaceChildren(...["A","B","D","C"].map(z=>{
    const box=tag("article","");
    if(c?.zones.includes(z))box.className="chosen";
    const sensors=state.sensors.filter(s=>s.zone===z && s.online);
    box.append(tag("strong","Strefa "+z),tag("small",sensors.length ? sensors.map(s=>s.sensor).join(", ") : "Brak aktualnych danych"));
    if(c?.strength[z]!=null)box.append(tag("small","Około "+c.strength[z]+" dBm"));
    return box;
  }));
  el("suggestion").textContent=c?.zones.length ? "Zacznij oględziny w strefie "+c.zones.join(" lub ")+"."
    : c ? "Brak rozstrzygającego porównania sygnału między strefami." : "Wybierz sprawę, aby zobaczyć wskazanie strefy.";
  el("evidence").replaceChildren();el("log").replaceChildren();
  el("review").hidden=!c || c.closed || !state.canReview;
  if(!c){el("evidence").append(tag("p","Brak wykrytych rozbieżności."));return}
  el("evidence").append(tag("h3",title(c)),tag("p",c.bssid+" · "+(c.closed?"Sprawa zamknięta":"Sprawa otwarta")),
    tag("p","Oczekiwano "+c.expected+". Zaobserwowano "+c.security+"."),
    tag("p","Czujniki: "+c.sensors.join(", ")+" · ostatnia obserwacja "+clock(c.at)));
  if(c.log.length)el("log").append(tag("h3","Dziennik oględzin"),...c.log.map(e=>tag("p",clock(e.at)+" · "+e.outcome+(e.note?" · "+e.note:""))));
}
async function load(){
  if(loading)return;
  loading=true;
  try{
    const res=await fetch("/api/room",{cache:"no-store"});if(!res.ok)throw Error("Brak danych serwera.");
    state=await res.json();
    const next=state.cases.some(c=>c.id===selected)?selected:state.cases[0]?.id;
    if(next!==selected)el("note").value="";
    selected=next;
    el("choice").replaceChildren(...state.cases.map(c=>new Option(title(c)+(c.closed?" · zamknięta":""),c.id)));
    el("choice").disabled=!selected;if(selected)el("choice").value=selected;
    if(!el("outcome").options.length)el("outcome").replaceChildren(...state.outcomes.map(x=>new Option(x,x)));
    el("simulate").hidden=!state.demo || !state.canReview;
    if(state.demo)el("mode").textContent="Pokaz dla jury · symulowane raporty Wi-Fi w osobnej bazie";
    el("sensors").replaceChildren(...state.sensors.map(s=>tag("p",s.sensor+" · "+(s.zone?"strefa "+s.zone:"strefa nieustalona")+" · "+s.ssid+" · "+(s.online?"ostatni raport ":"brak aktualnych danych · ostatni raport ")+clock(s.at)+" · ostatnia zmiana "+clock(s.changedAt))));
    if(!state.sensors.length)el("sensors").append(tag("p","Uruchom odczyty Wi-Fi i wybierz strefę stanowiska."));
    el("changes").replaceChildren(...state.changes.map(r=>tag("li",clock(r.at)+" · "+r.ssid+" · "+r.bssid)));
    el("inventory").replaceChildren(...state.inventory.map(r=>tag("li",r.ssid+" · "+r.bssid+" · "+r.security+(r.zone?" · strefa "+r.zone:""))));
    render();
  }catch(e){el("message").textContent=e.message}finally{loading=false}
}
async function action(path,data,button){
  button.disabled=true;
  try{
    const res=await fetch("/api/room/"+path,{method:"POST",headers:{"Content-Type":"application/json","X-Aegis-Session":"1"},body:JSON.stringify(data)});
    const result=await res.json();if(!res.ok)throw Error(result.error);
    el("message").textContent=path==="review"?"Zapisano oględziny.":"Zapisano symulowane raporty.";
    await load();
  }catch(e){el("message").textContent=e.message}finally{button.disabled=false}
}
el("review").onsubmit=e=>{e.preventDefault();action("review",{incident:selected,outcome:el("outcome").value,note:el("note").value},e.submitter)};
el("choice").onchange=()=>{selected=el("choice").value;el("note").value="";render()};
el("simulate").onclick=e=>action("simulate",{},e.currentTarget);
load();setInterval(()=>{if(!document.hidden)load()},5000);
