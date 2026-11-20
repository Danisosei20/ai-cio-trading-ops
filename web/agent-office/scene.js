/* Nexus: an illustrative AI office. Rendering never executes tasks or fabricates telemetry. */
window.OfficeScene = class {
 constructor(canvas, select) {
  this.canvas=canvas;this.c=canvas.getContext('2d');this.select=select;
  this.zoom=1.08;this.time=0;this.selected=0;this.hit=[];
  this.desks=[[2,1.7],[5.8,1.7],[9.6,1.7],[5.8,5.8],[9.6,5.8]];
  this.colors=['#69e9ed','#b6a1ff','#ffc985','#83f1bc','#f8a7d1'];
  this.names=['Screen','Research','News','Verify','Report'];
  this.people=this.desks.map(([x,y],i)=>({x:x+1,y:y+2,path:[],goal:'',state:'idle',changed:0,i}));
  canvas.addEventListener('click',e=>{
   const r=canvas.getBoundingClientRect(),scale=Math.min(r.width/1440,r.height/1000);
   const px=(e.clientX-r.left-(r.width-1440*scale)/2)/scale;
   const py=(e.clientY-r.top-(r.height-1000*scale)/2)/scale;
   const nearest=this.hit.map(p=>({...p,d:Math.hypot(px-p.x,py-p.y)})).sort((a,b)=>a.d-b.d)[0];
   if(nearest?.d<55)select(nearest.i);
  });
 }
 iso(x,y,z=0){return [680+(x-y)*47,210+(x+y)*24-z]}
 polygon(points,color){const c=this.c;c.fillStyle=color;c.beginPath();points.forEach((p,i)=>i?c.lineTo(...p):c.moveTo(...p));c.closePath();c.fill()}
 line(points,color,width=2){const c=this.c;c.strokeStyle=color;c.lineWidth=width;c.beginPath();points.forEach((p,i)=>i?c.lineTo(...p):c.moveTo(...p));c.stroke()}
 box(x,y,w,d,z,h,top='#263e50',left='#152637',right='#1b3042'){
  const a=this.iso(x,y,z+h),b=this.iso(x+w,y,z+h),e=this.iso(x,y+d,z+h),f=this.iso(x+w,y+d,z+h);
  this.polygon([e,f,this.iso(x+w,y+d,z),this.iso(x,y+d,z)],left);
  this.polygon([b,f,this.iso(x+w,y+d,z),this.iso(x+w,y,z)],right);
  this.polygon([a,b,f,e],top);
 }
 label(s,x,y,color='#a4c5d1',size=14,align='center'){
  this.c.font=`${size}px "Avenir Next",sans-serif`;this.c.textAlign=align;this.c.fillStyle=color;this.c.fillText(s,x,y);
 }
 ellipse(x,y,rx,ry,color,width=1,rotation=0){const c=this.c;c.strokeStyle=color;c.lineWidth=width;c.beginPath();c.ellipse(x,y,rx,ry,rotation,0,Math.PI*2);c.stroke()}
 glow(x,y,r,color){const c=this.c,g=c.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,color);g.addColorStop(1,'#00000000');c.fillStyle=g;c.fillRect(x-r,y-r,r*2,r*2)}
 light(points,color,width=2){const c=this.c;c.save();c.shadowColor=color;c.shadowBlur=9;this.line(points,color,width);c.restore()}
 floorRing(x,y,r,color,z=2){const [px,py]=this.iso(x,y,z);this.ellipse(px,py,r,r*.42,color,2)}

 architecture(){
  const c=this.c;
  // Floating alloy deck. Its illuminated perimeter reads as one physical room.
  this.box(-.25,-.25,13.65,10.55,-33,33,'#1c3042','#102033','#13263b');
  for(let x=0;x<13;x++)for(let y=0;y<10;y++)this.box(x,y,.985,.985,0,1,(x+y)%2?'#1c2d3e':'#203246','#142535','#142535');
  this.light([this.iso(-.25,10.3,-10),this.iso(13.4,10.3,-10),this.iso(13.4,-.25,-10)],'#3e8d9e',2);
  this.line([this.iso(-.25,10.3,-23),this.iso(13.4,10.3,-23),this.iso(13.4,-.25,-23)],'#27445f',2);
  this.box(-.25,-.25,13.65,.2,0,166,'#385064','#142438','#1b3044');
  this.box(-.25,-.25,.2,10.55,0,166,'#3b5267','#192b40','#233b50');
  this.light([this.iso(0,10,160),this.iso(0,0,160),this.iso(13,0,160)],'#6894b2',2);
  this.light([this.iso(0,10,5),this.iso(0,0,5),this.iso(13,0,5)],'#3e9fba',2);
  for(let i=1;i<13;i+=2)this.line([this.iso(i,0,10),this.iso(i,0,157)],'#35546a',1);
  // Rear observation windows with distant orbital structures, no market-data claims.
  [1,5.1,9.2].forEach((x,j)=>{
   const pts=[this.iso(x,0,143),this.iso(x+2.9,0,143),this.iso(x+2.9,0,43),this.iso(x,0,43)];
   this.polygon(pts,'#091827');this.line([...pts,pts[0]],'#446c82',2);
   c.save();c.beginPath();pts.forEach((p,i)=>i?c.lineTo(...p):c.moveTo(...p));c.closePath();c.clip();
   const [px,py]=this.iso(x,0,143);this.glow(px+95,py+65,130,j===1?'#7867c43b':'#4191ab3b');
   for(let k=0;k<22;k++){const sx=px+(k*47+j*13)%150,sy=py+(k*31)%175;c.fillStyle=k%4?'#6a93a0':'#bdd5e1';c.fillRect(sx,sy,k%4?1:2,1)}
   for(let k=0;k<8;k++){const height=24+(k*23+j*9)%57;c.fillStyle=k%2?'#1c3548':'#213d51';c.fillRect(px+k*20,py+143-height,13,height+30);this.line([[px+k*20+4,py+146-height],[px+k*20+4,py+139]],'#608294',1)}
   this.ellipse(px+90,py+45,36,13,'#8bc5ce66',1,-.4);c.restore();
   this.light([pts[2],pts[3]],'#5592a4',2);
  });
  // Wall-mounted network map: abstract decorative topology, not a live data chart.
  const map=[this.iso(0,1.15,137),this.iso(0,4.2,137),this.iso(0,4.2,52),this.iso(0,1.15,52)];
  this.polygon(map,'#0d2637');this.line([...map,map[0]],'#48728b',2);
  const nodes=Array.from({length:9},(_,i)=>this.iso(-.03,1.5+(i%3)*.9,70+Math.floor(i/3)*26));
  nodes.forEach((p,i)=>{if(i<6)this.line([p,nodes[i+3]],'#356278',1);if(i%3<2)this.line([p,nodes[i+1]],'#356278',1);c.fillStyle=i%3?'#6ab1c0':'#b1a9ec';c.beginPath();c.arc(...p,3,0,Math.PI*2);c.fill()});
  const [lx,ly]=this.iso(0,6.2,112);this.label('N E X U S',lx,ly,'#8baac4',23);this.label('Agent intelligence',lx,ly+23,'#6d93a8',13);
  // Recessed floor conduits connect stations without depicting fake task progress.
  [4.7,8.7].forEach(x=>this.line([this.iso(x,.4,2),this.iso(x,9.5,2)],'#3c647a',2));
  [4.5,9.1].forEach(y=>this.line([this.iso(.7,y,2),this.iso(12.5,y,2)],'#3c647a',2));
  [1,3,5,7,9,11].forEach(x=>this.light([this.iso(x,9.8,2),this.iso(x+.45,9.8,2)],'#779fac',2));
 }

 terminal(x,y,i,state){
  const c=this.c,color=state==='blocked'?'#ffa6af':this.colors[i];
  this.box(x-.15,y-.1,2.7,1.5,0,7,'#2a4558','#152638','#1c3448');
  this.box(x+.85,y+.35,.55,.55,7,32,'#3b5367','#1d3547','#2a4255');
  this.box(x-.1,y-.05,2.6,1.4,39,7,'#385366','#243b4d','#2b4357');
  this.light([this.iso(x-.1,y+1.35,42),this.iso(x+2.5,y+1.35,42),this.iso(x+2.5,y-.05,42)],color+'a0',2);
  const base=this.iso(x+1.1,y+.65,47);
  this.ellipse(...base,24,8,color+'80',1);
  // Projected glass panes are tilted into the room's actual perspective.
  const a=this.iso(x+.15,y+.25,120),b=this.iso(x+2.05,y+.25,120),d=this.iso(x+.15,y+.25,58),e=this.iso(x+2.05,y+.25,58);
  this.polygon([a,b,e,d],'#12394cbb');this.line([a,b,e,d,a],color+'b0',1.5);
  c.save();c.globalAlpha=.12;this.polygon([base,a,b],color);c.restore();
  for(let k=0;k<5;k++)this.line([this.iso(x+.3,y+.24,107-k*8),this.iso(x+.8+(k+i)%3*.35,y+.24,107-k*8)],color+(k===0?'dd':'70'),k===0?2:1);
  this.light([this.iso(x+1.55,y+.23,67),this.iso(x+1.55,y+.23,80),this.iso(x+1.8,y+.23,80),this.iso(x+1.8,y+.23,97)],color+'bb',1);
  // Secondary vertical pane and a touch-sensitive desk surface.
  const side=[this.iso(x+2.25,y+.3,93),this.iso(x+2.25,y+1.1,93),this.iso(x+2.25,y+1.1,56),this.iso(x+2.25,y+.3,56)];
  this.polygon(side,'#183747aa');this.line([...side,side[0]],color+'66',1);
  for(let k=0;k<4;k++)this.line([this.iso(x+.5,y+.9+k*.08,48),this.iso(x+1.65,y+.9+k*.08,48)],color+'66',1);
  if(state==='working'){
   const scan=(this.time*.35+i)%1;
   this.light([this.iso(x+.2,y+.22,62+scan*53),this.iso(x+2,y+.22,62+scan*53)],color,1);
  }
  const [tx,ty]=this.iso(x+1.15,y-.15,139);this.label(this.names[i],tx,ty,color,16);
  this.floorRing(x+1,y+2,26,color+'66');
 }

 core(){
  const c=this.c;
  this.box(1.8,7.3,3.1,2.25,0,10,'#2b4358','#14283a','#1c3249');
  const [x,y]=this.iso(3.35,8.35,12);
  this.ellipse(x,y,72,31,'#3b647a',2);this.ellipse(x,y,57,24,'#7e9ed1',2);
  this.ellipse(x,y,35,15,'#80e5e1',2);
  const cy=y-117+Math.sin(this.time*.8)*4;
  this.glow(x,cy,85,'#4d8cd72b');
  // Volumetric projection cone and latitude/longitude shell.
  const gradient=c.createLinearGradient(0,cy,0,y);gradient.addColorStop(0,'#7dc9ee20');gradient.addColorStop(1,'#4aa0c404');
  this.polygon([[x-42,cy],[x+42,cy],[x+30,y],[x-30,y]],gradient);
  this.ellipse(x,cy,43,43,'#9fdce8bb',1);
  for(let k=-2;k<=2;k++){const offset=k*13,rx=Math.sqrt(43*43-offset*offset);this.ellipse(x,cy+offset,rx,rx*.23,'#73c4db77',1)}
  for(let k=0;k<4;k++)this.ellipse(x,cy,Math.abs(Math.cos(this.time*.25+k*.8))*40+1,43,'#8db0e188',1);
  this.ellipse(x,cy,65,21,'#aaa8efcc',2,-.38);
  const t=this.time*.6;const ox=Math.cos(t)*65,oy=Math.sin(t)*21;
  const rx=ox*Math.cos(-.38)-oy*Math.sin(-.38),ry=ox*Math.sin(-.38)+oy*Math.cos(-.38);
  this.glow(x+rx,cy+ry,12,'#b5caff66');c.fillStyle='#dfedff';c.beginPath();c.arc(x+rx,cy+ry,3,0,Math.PI*2);c.fill();
  for(let k=0;k<3;k++){const height=(this.time*15+k*32)%95;this.line([[x-20+k*20,y-height],[x-20+k*20,y-height-8]],'#76b3ce55',1)}
  this.label('Intelligence core',x,y+57,'#a0bed6',15);
  this.label('Ambient projection',x,y+76,'#7193a9',12);
 }
 rack(x,y){
  this.box(x,y,.8,1.6,0,84,'#375066','#172a3c','#253e51');
  for(let j=0;j<6;j++){
   this.box(x+.81,y+.12,.03,1.32,9+j*11,7,'#263d50','#182d3d','#0f2232');
   this.light([this.iso(x+.86,y+.24,12+j*11),this.iso(x+.86,y+.42,12+j*11)],j%3?'#62b8c5':'#aaa2eb',2);
  }
 }
 beacon(x,y,color){
  this.box(x,y,.45,.45,0,7,'#435e70','#213b4c','#2b4559');
  const [px,py]=this.iso(x+.22,y+.22,8);
  this.polygon([[px-7,py],[px-7,py-50],[px,py-58],[px+7,py-50],[px+7,py]],'#2a506577');
  this.light([[px,py-5],[px,py-50]],color+'a0',2);
 }
 archive(){
  this.box(11.8,8.3,1,1.1,0,32,'#3d5367','#1a3044','#2a4058');
  const [x,y]=this.iso(12.3,8.8,33);
  this.ellipse(x,y,23,9,'#79b6cc',2);
  const z=28+Math.sin(this.time)*3;
  this.polygon([[x,y-z-20],[x+17,y-z-10],[x,y-z],[x-17,y-z-10]],'#9dd7e288');
  this.line([[x-17,y-z-10],[x-17,y-z+10],[x,y-z+20],[x+17,y-z+10],[x+17,y-z-10]],'#8fcad9',1.5);
  this.line([[x,y-z],[x,y-z+20]],'#8fcad9',1);
 }

 blocked(x,y){return x<.8||x>12.8||y<.8||y>9.5||this.desks.some(([a,b])=>x>a-.3&&x<a+2.8&&y>b-.2&&y<b+1.5)||(x<1.7&&y>4.5)||(x>1.5&&x<4.8&&y>7.1)||(x>11.5&&y>8)}
 route(sx,sy,tx,ty){
  const key=(x,y)=>x+','+y,goal=[Math.round(tx*2),Math.round(ty*2)],start=[Math.round(sx*2),Math.round(sy*2)];
  const queue=[start],parents=new Map([[key(...start),null]]);let found=false;
  for(let q=0;q<queue.length;q++){
   const [x,y]=queue[q];if(x===goal[0]&&y===goal[1]){found=true;break}
   for(const [dx,dy] of [[1,0],[-1,0],[0,1],[0,-1]]){const nx=x+dx,ny=y+dy,k=key(nx,ny);if(!parents.has(k)&&!this.blocked(nx/2,ny/2)){parents.set(k,[x,y]);queue.push([nx,ny])}}
  }
  if(!found)return [];const path=[];let cur=goal;while(cur){path.push([cur[0]/2,cur[1]/2]);cur=parents.get(key(...cur))}return path.reverse().slice(1);
 }
 peopleStep(dt,data){
  this.people.forEach(p=>{
   const i=p.i,onDuty=data.mode==='live'||data.mode==='demo';
   const state=onDuty?(data.tasks[this.names[i].toLowerCase()]||'idle'):'idle';
   if(p.state!==state){p.state=state;p.changed=this.time;p.goal=''}
   let target;
   if(state==='working'||state==='blocked')target=[this.desks[i][0]+1,this.desks[i][1]+2];
   else if(state==='complete'&&this.time-p.changed<8)target=[11,8.8];
   else if(onDuty)target=[this.desks[i][0]+1,this.desks[i][1]+2];
   else{const phase=Math.floor(this.time/16+i)%3;target=phase===0?[2+i*.5,6.5]:phase===1?[this.desks[i][0]+1,this.desks[i][1]+2]:[5+i*.6,9.2]}
   target=target.map(v=>Math.round(v*2)/2);const goal=target.join(',');
   if(goal!==p.goal){p.goal=goal;p.path=this.route(p.x,p.y,...target)}p.walking=p.path.length>0;
   if(p.path.length&&dt){const [x,y]=p.path[0],dx=x-p.x,dy=y-p.y,d=Math.hypot(dx,dy),step=dt*1.1;p.facing=dx-dy>=0?1:-1;if(d<=step){p.x=x;p.y=y;p.path.shift()}else{p.x+=dx/d*step;p.y+=dy/d*step}}
  });
 }
 person(p){
  const c=this.c,i=p.i,[x,y]=this.iso(p.x,p.y),color=p.state==='blocked'?'#ffa6af':this.colors[i];
  const work=p.state==='working'&&!p.walking,step=p.walking?Math.sin(this.time*9+i)*5:0;
  this.ellipse(x,y+2,21,8,color+'44',1);
  if(i===this.selected){this.ellipse(x,y+2,27,11,color,1.5);this.line([[x-32,y+2],[x-27,y+2]],color,2);this.line([[x+27,y+2],[x+32,y+2]],color,2)}
  c.save();c.translate(Math.round(x),Math.round(y));c.scale(1.2,1.2);
  // Articulated alloy legs and softly bevelled torso.
  c.fillStyle='#7996a6';c.fillRect(-11,-23,7,17+step);c.fillRect(4,-23,7,17-step);
  c.fillStyle='#aec2cc';c.fillRect(-13,-7+step,11,6);c.fillRect(3,-7-step,11,6);
  c.fillStyle='#283e50';c.beginPath();c.roundRect(-15,-48,30,26,6);c.fill();
  this.polygon([[-14,-45],[-8,-49],[10,-49],[15,-43],[10,-24],[-10,-24]],'#809ba9');
  this.line([[-10,-44],[-10,-29]],'#bdd4db',2);
  c.fillStyle='#253d4d';c.beginPath();c.roundRect(-6,-43,12,13,3);c.fill();
  c.fillStyle=color;c.fillRect(-3,-39,6,4);
  const arm=work?Math.sin(this.time*8+i)*4:step*.5;
  this.line([[-16,-42],[-20,-31],[-18,-22+arm]],'#809eae',6);
  this.line([[16,-42],[21,-33],[18,-22-arm]],'#809eae',6);
  c.fillStyle='#b0c9d3';c.fillRect(-21,-24+arm,7,7);c.fillRect(15,-24-arm,7,7);
  // Each AI has a different face display and antenna profile.
  c.fillStyle='#9eb6c2';c.beginPath();c.roundRect(-19,-78,38,28,[10,10,7,7]);c.fill();
  c.fillStyle='#142f40';c.beginPath();c.roundRect(-15,-73,30,18,5);c.fill();
  c.save();c.shadowColor=color;c.shadowBlur=7;c.fillStyle=color;
  if(i===0){c.fillRect(-10,-66,7,3);c.fillRect(3,-66,7,3)}
  else if(i===1){this.line([[-10,-64],[-5,-68],[0,-63],[5,-68],[10,-64]],color,2)}
  else if(i===2){c.beginPath();c.arc(0,-64,5,0,Math.PI*2);c.fill()}
  else if(i===3){this.line([[-9,-64],[-3,-59],[8,-69]],color,2)}
  else {c.fillRect(-9,-68,18,2);c.fillRect(-9,-63,12,2)}
  c.restore();
  this.line([[0,-79],[0,-86]],'#91abbc',2);c.fillStyle=color;c.beginPath();c.arc(0,-87,2,0,Math.PI*2);c.fill();
  c.fillStyle='#486376';c.fillRect(-22,-70,4,13);c.fillRect(18,-70,4,13);
  if(work){this.polygon([[17,-33],[39,-41],[39,-20],[17,-12]],color+'25');this.line([[18,-32],[38,-39],[38,-21],[18,-14]],color+'aa',1)}
  if(p.state==='complete'&&p.walking){this.polygon([[19,-35],[32,-42],[45,-35],[32,-28]],color+'88');this.line([[19,-35],[19,-21],[32,-14],[45,-21],[45,-35]],color,1)}
  c.restore();
  this.hit.push({x:720+(x-720)*this.zoom,y:500+(y-500)*this.zoom-42*this.zoom,i});
 }
 bubble(p){
  let s='';
  if(p.walking&&p.state==='complete')s='Transferring result';
  else if(p.state==='blocked')s='Needs attention';
  else if(p.state==='working'&&!p.walking)s=['Screening candidates','Building the thesis','Reading headlines','Checking inputs','Writing summary'][p.i];
  else if(p.state==='complete'&&this.time-p.changed<5)s='Task complete';
  else if(p.i===this.selected)s=this.names[p.i];
  if(!s)return;
  const c=this.c,[x,y]=this.iso(p.x,p.y,128),color=p.state==='blocked'?'#ffa6af':this.colors[p.i];
  c.font='17px "Avenir Next",sans-serif';const w=c.measureText(s).width+24;
  c.fillStyle='#0a1b2bef';c.strokeStyle=color+'88';c.lineWidth=1;c.beginPath();c.roundRect(x-w/2,y-24,w,34,5);c.fill();c.stroke();
  this.line([[x,y+10],[x,y+18]],color+'99',1);this.label(s,x,y,color,17);
 }
 draw(dt,data,selected){
  this.time+=dt;this.selected=selected;const c=this.c;this.hit=[];c.clearRect(0,0,1440,1000);
  // Restrained ambient field gives the station depth without obscuring task state.
  this.glow(750,540,530,'#25446c24');
  for(let k=0;k<32;k++){c.fillStyle=k%3?'#34506a':'#52718b';c.fillRect(80+(k*193)%1290,90+(k*131)%800,1,1)}
  c.save();c.translate(720,500);c.scale(this.zoom,this.zoom);c.translate(-720,-500);
  this.architecture();this.peopleStep(dt,data);
  const objects=[{depth:7.5,draw:()=>this.rack(.35,5)},{depth:12,draw:()=>this.core()},{depth:9.8,draw:()=>this.rack(.2,8)},{depth:21.4,draw:()=>this.archive()}];
  this.desks.forEach(([x,y],i)=>objects.push({depth:x+y+2,draw:()=>this.terminal(x,y,i,data.tasks[this.names[i].toLowerCase()])}));
  [[.55,.65],[12.4,.65],[12.6,9.5]].forEach(([x,y])=>objects.push({depth:x+y,draw:()=>this.beacon(x,y,'#83bdd6')}));
  this.people.forEach(p=>objects.push({depth:p.x+p.y,draw:()=>this.person(p)}));
  objects.sort((a,b)=>a.depth-b.depth).forEach(o=>o.draw());this.people.forEach(p=>this.bubble(p));
  c.restore();
 }
};
