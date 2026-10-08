async function tick(){
 try{
  const [h,a,r,g,l]=await Promise.all([api('/api/health'),api('/api/account'),api('/api/research'),api('/api/guard'),api('/api/lessons')]);
  const tg=await api('/api/target').catch(()=>({}));
  if(tg.pnl!==null&&tg.pnl!==undefined){const pv=parseFloat(tg.pnl),tv=parseFloat(tg.target)||100;
   const pct=Math.max(0,Math.min(100,(pv/tv)*100));
   $('target').innerHTML=`Daily goal: <b class='${pv>=0?'pos':'neg'}'>$${esc(tg.pnl)}</b> / $${esc(tg.target)} `+
    `<span class='bar bar-inline'><i data-w='${pct}'></i></span>${tg.halted?' · <b>HALTED</b> '+esc(tg.reason||''):''}`;
   paintBars($('target'));}else{$('target').innerHTML='<span class=\'empty\'>No P&L yet today.</span>';}
  $('pills').innerHTML=
   pill('LIVE '+(h.live_enabled?'ON':'OFF'),h.live_enabled?'bad':'ok')+' '+
   pill('PAPER '+(h.paper_auto?'AUTO':'OFF'),h.paper_auto?'info':'warn')+' '+
   pill(h.killed?'KILLED':'ARMED',h.killed?'bad':'ok')+' '+
   pill(h.mode,'info');
  $('clock').textContent=h.now_et+' · market '+(h.market_open?'OPEN':'CLOSED');
  $('today').innerHTML=a.ok?
   `<span class="kpi">$${esc(a.portfolio)}</span><br>Cash $${esc(a.cash)} · Buying power $${esc(a.buying_power)}`:
   '<span class="empty">broker unreachable</span>';
  if(a.ok&&a.equity_curve&&a.equity_curve.length>1){
   const pts=a.equity_curve.map(p=>p.v);
   const cv=$('spark'),ctx=cv.getContext('2d');
   ctx.clearRect(0,0,cv.width,cv.height);
   const lo=Math.min(...pts),hi=Math.max(...pts),rg=(hi-lo)||1;
   const X=i=>8+i*(cv.width-16)/(pts.length-1);
   const Y=v=>cv.height-8-(v-lo)*(cv.height-16)/rg;
   const up=pts[pts.length-1]>=pts[0];
   ctx.strokeStyle=up?'#1a7f37':'#cf222e';ctx.lineWidth=2;ctx.beginPath();
   pts.forEach((v,i)=>{i?ctx.lineTo(X(i),Y(v)):ctx.moveTo(X(i),Y(v));});
   ctx.stroke();
   const chg=(pts[pts.length-1]/pts[0]-1)*100;
   $('daychg').innerHTML=`Day: <b class="${chg>=0?'pos':'neg'}">${chg>=0?'▲ +':'▼ '}${chg.toFixed(2)}%</b>`;
  } else {$('daychg').innerHTML='<span class="empty">No intraday curve yet.</span>';}
  $('autonomy').innerHTML=`Policy <b>${esc(h.exec_policy)}</b> · window ${esc(h.window)} · max $${esc(h.max_order)}`;
  $('res').innerHTML=r.length?'<table><tr><th>Ticker</th><th>Date</th><th>Action</th><th>Decision</th><th>Note</th></tr>'+
   r.slice(0,8).map(x=>`<tr><td><b>${esc(x.ticker)}</b></td><td>${esc(x.date)}</td><td>${esc(x.action||'')}</td><td>${esc(x.decision||'')}</td><td>${esc((x.reason||x.status||x.order_id||'').slice(0,90))}</td></tr>`).join('')+'</table>':
   '<span class="empty">No runs yet.</span>';
  $('guard').innerHTML=g.positions&&g.positions.length?g.positions.map(p=>
   `<div><b>${esc(p.symbol)}</b> ${esc(p.qty)} @ $${esc(p.entry)} → $${esc(p.mark)} `+
   `<b>${esc(p.signal)}</b> (${esc(p.return_pct)}) · stop $${esc(p.stop)} · target $${esc(p.target)}</div>`).join('')
   :'<span class="empty">No guarded positions.</span>';
  $('lessons').innerHTML=l.standing&&l.standing.length?'<ul>'+
   l.standing.map(s=>`<li><b>${esc(s.lesson)}</b><br><small>${esc(s.evidence||'')} → ${esc(s.rule||'')}</small></li>`).join('')+'</ul>':
   '<span class="empty">No lessons recorded yet.</span>';
 }catch(e){showError('today',e);}
}
$('kill').onclick=async()=>{if(!confirm('STOP ALL trading? (positions kept, no liquidation)'))return;
 try{await api('/api/kill',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"confirm":true}'});alert('Kill switch ENGAGED');}
 catch(e){alert('Failed: '+e.message);}tick();};
$('resume').onclick=async()=>{if(!confirm('Resume trading? Explicit operator action.'))return;
 try{await api('/api/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"confirm":true}'});alert('Resumed');}
 catch(e){alert('Failed: '+e.message);}tick();};
tick();setInterval(tick,5000);
