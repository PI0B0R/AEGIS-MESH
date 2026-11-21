"use strict";
// "Dodaj telefon": one QR, refreshed automatically. Works only in the panel opened on the laptop itself.
(()=>{
  const APK="https://github.com/PI0B0R/AEGIS-MESH/releases/download/apk-latest/aegis-mesh.apk";
  const card=document.getElementById("quickPhone"), status=document.getElementById("quickStatus");
  const qr=document.getElementById("quickQR"), again=document.getElementById("quickNew");
  let shown="", timer=null;
  function draw(canvas,text,scale){
    const code=new PairQRCode(-1,1);code.addData(text);code.make();
    const size=code.getModuleCount();canvas.width=canvas.height=(size+8)*scale;
    const ctx=canvas.getContext("2d");ctx.fillStyle="#fff";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.fillStyle="#0a0a0a";
    for(let y=0;y<size;y++)for(let x=0;x<size;x++)if(code.isDark(y,x))ctx.fillRect((x+4)*scale,(y+4)*scale,scale,scale);
  }
  async function ask(renew){
    const response=await fetch("/api/phone/quick",{method:"POST",headers:{"Content-Type":"application/json","X-Aegis-Session":"1"},body:JSON.stringify({new:!!renew})});
    const data=await response.json();
    if(response.status===403){card.hidden=true;clearInterval(timer);return}
    card.hidden=false;
    if(!response.ok)throw Error(data.error||"Nie udało się przygotować kodu.");
    return data;
  }
  async function refresh(renew){
    try{
      const data=await ask(renew);
      if(!data)return;
      card.dataset.state=data.paired?"paired":"waiting";
      qr.closest("figure").hidden=!!data.paired;
      again.hidden=!data.paired;
      if(data.qr && data.qr!==shown){shown=data.qr;draw(qr,data.qr,5)}
      status.textContent=data.paired
        ? "✓ Telefon połączony"+(data.seen!=null?" · ostatni raport "+data.seen+" s temu":" · czekam na pierwszy raport")
        : "Czekam na telefon · laptop "+data.address+(data.secure?" · połączenie szyfrowane":" · HTTP w sieci lokalnej (brak OpenSSL)");
    }catch(error){status.textContent=error.message}
  }
  again.onclick=()=>{shown="";refresh(true)};
  draw(document.getElementById("apkQR"),APK,4);
  refresh(false);
  timer=setInterval(()=>refresh(false),3000);
})();
