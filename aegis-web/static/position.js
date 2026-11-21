"use strict";

let workingCity=null;
let workingRadius=25000;
function outsideCity(point){
  return workingCity && Number.isFinite(point?.lat) && Number.isFinite(point?.lng)
    && L.latLng(workingCity.lat,workingCity.lng).distanceTo([point.lat,point.lng])>workingRadius;
}

function placeText(place) {
  if (!place?.building) return "";
  return [place.building, place.floor && "piętro " + place.floor, place.room && "sala " + place.room].filter(Boolean).join(" · ");
}
function locationText(point) {
  if (!Number.isFinite(point.lat) || !Number.isFinite(point.lng)) {
    if(point.location?.source==="paired_phone_manual")return "Miejsce podane w telefonie: "+placeText(point.location.place)+(point.location.at?" · "+stamp({at:point.location.at}):"");
    if(point.location?.reason==="outside_city")return "Odrzucono pozycję poza obszarem "+(point.location.city || "pracy");
    return {phone_unavailable:"Brak świeżej pozycji telefonu",kinematic_jump:"Odrzucono skok pozycji telefonu",unverified_history:"Historyczna pozycja niezweryfikowana",outside_area:"Bez pinezki · odczyt poza potwierdzonym obszarem",
      uncertain_area:"Bez pinezki · zbyt duża niepewność odczytu",
      unconfirmed:"Bez pinezki · miejsce niepotwierdzone"}[point.location?.reason] || "Brak lokalizacji";
  }
  const info = point.location || {};
  const parts = [{phone:"Pozycja telefonu",paired_phone_gnss:"Pozycja GPS sparowanego telefonu",browser:"Pozycja z przeglądarki", manual:"Stanowisko wskazane ręcznie",
    csv:"Pozycja z pliku CSV"}[info.source] || "Źródło lokalizacji nieznane"];
  if (info.unverified) parts.unshift("Pozycja historyczna · niezweryfikowana");
  if (info.station) parts.push(info.station);
  if (info.place) parts.push(placeText(info.place));
  if (info.corrected) parts.push("korekta miejsca pomiaru");
  if (info.at) parts.push("pozycja z " + stamp({at:info.at}));
  if (Number.isFinite(info.accuracy)) {
    parts.push("szacowana dokładność " + Math.ceil(info.accuracy) + " m");
    if (info.accuracy > 50) parts.push("orientacyjna");
  }
  parts.push(point.lat.toFixed(5)+"°, "+point.lng.toFixed(5)+"°");
  if (info.conflict) parts.push(conflictText(info.conflict));
  return parts.join(" · ");
}

function conflictText(check) {
  return "Rozbieżność z urządzeniem: " + (check.meters / 1000).toLocaleString("pl-PL",{maximumFractionDigits:2}) + " km";
}

function browserPoint() {
  return new Promise((resolve,reject) => {
    if (!navigator.geolocation) return reject(Error("Brak dostępu do geolokalizacji."));
    navigator.geolocation.getCurrentPosition(pos => {
      const {latitude:lat, longitude:lng, accuracy} = pos.coords;
      if (![lat,lng,accuracy,pos.timestamp].every(Number.isFinite) || Math.abs(lat)>85 || accuracy<0)
        return reject(Error("Brak poprawnej pozycji urządzenia."));
      resolve({lat,lng,location:{source:"browser",at:new Date(pos.timestamp).toISOString(),accuracy}});
    }, reject, {maximumAge:0,enableHighAccuracy:true,timeout:10000});
  });
}

function checkStation(anchor, candidate) {
  const {conflict:previous, ...info} = anchor.location;
  const meters = L.latLng(anchor.lat,anchor.lng).distanceTo([candidate.lat,candidate.lng]);
  if (meters > 100 + candidate.location.accuracy)
    info.conflict = {meters:Math.round(meters),at:candidate.location.at,accuracy:candidate.location.accuracy};
  return {...anchor,location:info};
}
