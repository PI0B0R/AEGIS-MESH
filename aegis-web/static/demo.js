"use strict";
const $ = id => document.getElementById(id);
const format = "aegis-discovery-1";
const serviceId = row => row.scheme + "://" + row.host + ":" + row.port;
const ms = value => value.toLocaleString("pl-PL") + " ms";
let report;

function validRow(row) {
  return row && typeof row.host === "string" && row.host.length > 0 &&
    ["http","https"].includes(row.scheme) && Number.isInteger(row.port) && row.port > 0 && row.port <= 65535 &&
    Number.isFinite(row.ms) && row.ms >= 0 && Number.isFinite(Date.parse(row.at)) &&
    ["title","server","tls"].every(key => typeof row[key] === "string") &&
    Array.isArray(row.cookies) && row.cookies.every(name => typeof name === "string") &&
    ((row.status === "timeout" && row.http === null) ||
      (row.status === "response" && Number.isInteger(row.http) && row.http >= 100 && row.http <= 599));
}
function validate(data) {
  if (data?.format !== format || !Array.isArray(data.runs) || data.runs.length !== 2 ||
    !data.runs.every(run => Number.isFinite(run?.durationMs) && run.durationMs >= 0 &&
      Array.isArray(run.services) && run.services.every(validRow) &&
      new Set(run.services.map(serviceId)).size === run.services.length))
    throw Error("Oczekiwano raportu JSON w formacie eksportu AEGIS z dwoma przebiegami.");
}
function outcome(row) {
  if (!row) return "Brak wpisu";
  return row.status === "response" ? "HTTP " + row.http : "Przekroczony czas";
}
function diff(first, second) {
  if (!first) return second.status === "response" ? "Nowo zaobserwowana usługa" : "Pierwsza próba bez odpowiedzi";
  if (!second) return "Brak obserwacji w drugim przebiegu";
  if (second.status === "timeout") return "Dostępność niepotwierdzona";
  if (first.status === "timeout") return "Usługa odpowiedziała ponownie";
  const fields = {http:"kod HTTP", title:"tytuł", server:"serwer", tls:"TLS"};
  const changed = Object.keys(fields).filter(key => first[key] !== second[key]).map(key => fields[key]);
  if (JSON.stringify([...first.cookies].sort()) !== JSON.stringify([...second.cookies].sort())) changed.push("ciasteczka");
  return changed.length ? "Zmieniono " + changed.join(", ") : "Bez zmian";
}
function addRow(body, values) {
  const row = body.insertRow();
  for (const value of values) row.insertCell().textContent = value;
}
function show(data) {
  validate(data);
  const [before, after] = data.runs.map(run => new Map(run.services.map(row => [serviceId(row),row])));
  const keys = new Set([...before.keys(), ...after.keys()]);
  $("changes").replaceChildren();
  $("evidence").replaceChildren();
  for (const key of keys) {
    const first = before.get(key), second = after.get(key);
    addRow($("changes"), [key, outcome(first), outcome(second), diff(first, second)]);
  }
  data.runs.forEach((run,index) => {
    for (const row of run.services) addRow($("evidence"), [index + 1,serviceId(row),ms(row.ms),
      row.title || "—",row.server || "—",row.cookies.join(", ") || "—",row.tls || "—"]);
  });
  $("summary").textContent = "Usługi w porównaniu: " + keys.size + ". Czas pierwszego przebiegu: " +
    ms(data.runs[0].durationMs) + ". Czas drugiego: " + ms(data.runs[1].durationMs) + ".";
  if (["changed","timeout","stable"].includes(data.scenario)) $("scenario").value=data.scenario;
  report=data;
  $("download").disabled=false;
}
async function runReport(start) {
  for (const id of ["runDemo","scenario","importFile"]) $(id).disabled=true;
  try {
    const options = start ? {method:"POST", headers:{"Content-Type":"application/json","X-Aegis-Demo":"1"},
      body:JSON.stringify({scenario:$("scenario").value})} : {cache:"no-store"};
    if (start) $("progress").textContent="Trwają próby lokalnych usług…";
    let data;
    if(window.mobileTransport){
      data=await window.mobileTransport(start?"/service-test":"/service-report",start?{scenario:$("scenario").value}:{});
    }else{
      const response=await fetch("/api/demo",{...options,signal:AbortSignal.timeout(15000)});
      if (!response.ok) throw Error("Nie udało się pobrać wyników z serwera pokazu.");
      data=await response.json();
    }
    if (data) {
      show(data);
      $("progress").textContent=start ? "Zakończono oba przebiegi." : "Ostatni zapisany pokaz.";
    }
  } catch(error) { $("progress").textContent=error.message; }
  finally { for (const id of ["runDemo","scenario","importFile"]) $(id).disabled=false; }
}
$("runDemo").onclick=()=>runReport(true);
$("importFile").onchange=async event=>{
  const file=event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 5*1024*1024) throw Error("Wybierz plik do 5 MB.");
    show(JSON.parse(await file.text()));
    $("progress").textContent="Raport z pliku: " + file.name;
  } catch(error) { $("progress").textContent=error.message; }
  finally { $("importFile").value=""; }
};
$("download").onclick=()=>{
  const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:"application/json"}));
  const link=document.createElement("a");
  link.href=url; link.download="aegis-discovery.json"; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};
if(!window.mobileTransport)runReport(false);
