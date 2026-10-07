const TV_EXCHANGE={NVDA:'NASDAQ:NVDA',AAPL:'NASDAQ:AAPL',MSFT:'NASDAQ:MSFT',SPY:'AMEX:SPY'};
let TICKER='NVDA';
function tvURL(t){const s=encodeURIComponent(TV_EXCHANGE[t]||('NASDAQ:'+t));
 return `https://www.tradingview.com/widgetembed/?symbol=${s}&interval=D&theme=dark&style=1&timezone=America%2FNew_York&withdateranges=1&hidesidetoolbar=0`;}
function drawChart(ch){
 const cv=$('chart'),ctx=cv.getContext('2d');
 ctx.clearRect(0,0,cv.width,cv.height);
 const prices=(ch.prices||{})[TICKER];
 if(!prices||!prices.length){$('chartnote').textContent='No price data — run scripts/strategy_backtest.py';return;}
 const vars=(ch.top||[]).filter(v=>v.ticker===TICKER).slice(0,3);
 const series=[{label:TICKER+' price',color:'#888',pts:prices.map(p=>({d:p.date,v:p.close}))}];
 const colors=['#0969da','#1a7f37','#cf222e'];
 vars.forEach((v,i)=>{const pts=[];
  (v.is_curve||[]).forEach(p=>pts.push({d:p.date,v:p.equity}));
  (v.oos_curve||[]).forEach(p=>pts.push({d:p.date,v:p.equity}));
  if(pts.length)series.push({label:v.variant,color:colors[i%3],pts});});
 let lo=Infinity,hi=-Infinity;
 series.forEach(s=>s.pts.forEach(p=>{lo=Math.min(lo,p.v);hi=Math.max(hi,p.v);}));
 if(!(hi>lo))hi=lo+1;
 const X=i=>40+i*(cv.width-60)/Math.max(1,series[0].pts.length-1);
 const Y=v=>cv.height-30-(v-lo)*(cv.height-60)/(hi-lo);
 ctx.strokeStyle='#8886';ctx.beginPath();ctx.moveTo(40,10);ctx.lineTo(40,cv.height-30);ctx.lineTo(cv.width-10,cv.height-30);ctx.stroke();
 ctx.fillStyle='#888';ctx.font='12px sans-serif';
 ctx.fillText((hi*100).toFixed(0)+'%',4,20);ctx.fillText((lo*100).toFixed(0)+'%',4,cv.height-30);
 series.forEach((s,si)=>{ctx.strokeStyle=s.color;ctx.lineWidth=si?2:3;ctx.beginPath();
  s.pts.forEach((p,i)=>{const x=X(Math.round(i*(series[0].pts.length-1)/Math.max(1,s.pts.length-1)));i?ctx.lineTo(x,Y(p.v)):ctx.moveTo(x,Y(p.v));});
  ctx.stroke();});
 $('chartnote').textContent=series.map(s=>s.label).join('  ·  ');
 $('tv').src=tvURL(TICKER);
}
async function load(){
 try{
  const ch=await api('/api/charts');
  $('runid').textContent=ch.run_id?('run '+ch.run_id):'';
  $('bt').innerHTML=ch.top&&ch.top.length?'<table><tr><th>Ticker</th><th>Variant</th><th>In-sample</th><th>OOS</th><th></th></tr>'+
   ch.top.slice(0,10).map(x=>`<tr><td><b>${esc(x.ticker)}</b></td><td>${esc(x.variant)}</td><td>${(x.is_return*100).toFixed(1)}%</td><td>${(x.oos_return*100).toFixed(1)}%</td><td>${x.incubation_pass?'PASS':'—'}</td></tr>`).join('')+'</table>':
   '<span class="empty">No backtests yet.</span>';
  if(ch.prices&&Object.keys(ch.prices).length){
   if(!ch.prices[TICKER])TICKER=Object.keys(ch.prices)[0];
   $('tickers').innerHTML=Object.keys(ch.prices).map(t=>
    `<button class="${t===TICKER?'on':''}" data-t="${esc(t)}">${esc(t)}</button>`).join(' ');
   document.querySelectorAll('#tickers button').forEach(b=>b.onclick=()=>{TICKER=b.dataset.t;load();});
   drawChart(ch);
  }
 }catch(e){showError('bt',e);}
}
load();
