// Door stickers: QR text "aegis-place:B1/2/215", read by the Android app (parsePlace in Radio.kt).
const PART=/^[\p{L}\p{N} ._-]{0,40}$/u;
function draw(text){
  const code=new PairQRCode(-1,1);code.addData(text);code.make();
  const size=code.getModuleCount(),scale=5,canvas=document.createElement("canvas");
  canvas.width=canvas.height=(size+8)*scale;
  const ctx=canvas.getContext("2d");ctx.fillStyle="white";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.fillStyle="black";
  for(let y=0;y<size;y++)for(let x=0;x<size;x++)if(code.isDark(y,x))ctx.fillRect((x+4)*scale,(y+4)*scale,scale,scale);
  return canvas;
}
function make(){
  const grid=document.getElementById("grid");grid.replaceChildren();
  for(const line of document.getElementById("rooms").value.split("\n")){
    const parts=line.split("/").map(p=>p.trim());
    if(!parts[0]||parts.length>3||!parts.every(p=>PART.test(p)))continue;
    const [building,floor="",room=""]=parts, figure=document.createElement("figure"), caption=document.createElement("figcaption");
    caption.textContent=[building,floor&&"piętro "+floor,room&&"sala "+room].filter(Boolean).join(" · ");
    const hint=document.createElement("small");hint.textContent="AEGIS Mesh · miejsce pomiaru";caption.append(hint);
    figure.append(draw("aegis-place:"+parts.join("/")),caption);grid.append(figure);
  }
}
document.getElementById("make").onclick=make;
document.getElementById("print").onclick=()=>window.print();
make();
