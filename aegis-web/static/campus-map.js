"use strict";
function showPoland(map){
  map.fitBounds([[48.95,14.05],[54.90,24.20]],{padding:[16,16],animate:false});
}
function addCampusMap(map, notify){
  const container=map.getContainer();
  const home=container.parentElement;
  const next=container.nextSibling;
  const enlarge=document.getElementById("expandMap");
  const dialog=document.createElement("dialog");
  dialog.className="large-map";
  const title=node("h2",container.closest("section").querySelector("h2").textContent);
  title.id="largeMapTitle";
  dialog.setAttribute("aria-labelledby",title.id);
  const close=node("button","Wróć do panelu");
  close.type="button";
  const header=document.createElement("header");
  header.append(title,close);
  dialog.append(header);
  document.body.append(dialog);
  enlarge.onclick=()=>{
    dialog.append(container);
    dialog.showModal();
    map.invalidateSize({animate:false});
  };
  close.onclick=()=>dialog.close();
  dialog.addEventListener("close",()=>{
    home.insertBefore(container,next);
    map.invalidateSize({animate:false});
  });
  L.control.scale({imperial:false}).addTo(map);
  const countryControl=L.control({position:"topleft"});
  countryControl.onAdd=()=>{
    const button=node("button","Cała Polska");
    button.type="button";
    L.DomEvent.disableClickPropagation(button);
    button.onclick=()=>{
      map.closePopup();
      showPoland(map);
    };
    return button;
  };
  countryControl.addTo(map);
  map.attributionControl.setPrefix(false);
  map.attributionControl.addAttribution('© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>');
  map.createPane("names");map.getPane("names").style.zIndex=440;map.getPane("names").style.pointerEvents="none";
  const details=L.layerGroup();
  map.createPane("campusGeometry").style.zIndex=350;
  const countryPane=map.createPane("countryOverview");
  countryPane.style.zIndex=190;
  countryPane.style.pointerEvents="none";
  const countryOutline=L.layerGroup();
  fetch("/polska.geojson").then(response=>{
    if(!response.ok)throw Error();
    return response.json();
  }).then(feature=>{
    L.geoJSON(feature,{pane:"countryOverview",interactive:false,style:{color:"#417365",weight:2,fillColor:"#bcd5c6",fillOpacity:0.75}}).addTo(countryOutline);
  }).catch(()=>{/* The online map also includes the country boundary. */});
  const online=L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxNativeZoom:19,maxZoom:map.getMaxZoom()});
  const localBounds=L.latLngBounds([50.063,19.912],[50.069,19.923]);
  function updateBase(){
    if(map.getZoom()>=16)details.addTo(map);else details.remove();
    if(map.getZoom()<=8)countryOutline.addTo(map);else countryOutline.remove();
    if(!localBounds.contains(map.getBounds()) || map.getZoom()<16){if(!map.hasLayer(online))online.addTo(map)}
    else if(map.hasLayer(online))map.removeLayer(online);
  }
  map.on("moveend zoomend",updateBase);
  const styles={building:{color:"#aeb8ad",weight:1,fillColor:"#d3d8cf"},road:{color:"#fffef9",weight:5},path:{color:"#fffdf3",weight:2},green:{color:"#c0d7ab",weight:0.5,fillColor:"#c4d9af"},parking:{color:"#c8d0c3",weight:0.5,fillColor:"#e0e5da"}};
  async function loadGeometry(){
    try{
      const response=await fetch("/campus.json");if(!response.ok)throw Error();
      const geo=await response.json();
      L.geoJSON(geo,{pane:"campusGeometry",interactive:false,style:f=>({...styles[f.properties.kind],opacity:1,fillOpacity:1})}).addTo(details);
      const namedRoads=new Set();
      for(const f of geo.features){
        const p=f.properties;
        let position,label;
        if(p.kind==="building" && /^[ABCDU]-\d+$/.test(p.ref)){
          const ring=f.geometry.coordinates[0];
          position=L.latLngBounds(ring.map(([lon,lat])=>[lat,lon])).getCenter();label=p.ref;
        }else if(p.kind==="road" && p.name && !namedRoads.has(p.name) && f.geometry.coordinates.length>=4){
          const mid=f.geometry.coordinates[Math.floor(f.geometry.coordinates.length/2)];
          position=[mid[1],mid[0]];label=p.name;namedRoads.add(label);
        }else continue;
        L.tooltip({permanent:true,direction:"center",className:"maplabel",pane:"names"}).setLatLng(position).setContent(node("span",label)).addTo(details);
      }
    }catch{online.addTo(map);notify("Lokalny podkład jest niedostępny. Wczytujemy mapę sieciową.")}
    updateBase();
  }
  return loadGeometry();
}
