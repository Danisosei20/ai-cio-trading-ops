async function tick(){
 try{
  const rows=await api('/api/triggers');
  const badge=s=>s==='CALL_TRIGGER'||s==='PUT_TRIGGER'
   ?`<span class="pill ok">${esc(s)}</span>`
   :s==='CALL_WATCH'?`<span class="pill warn">${esc(s)}</span>`
   :s==='UNKNOWN'?`<span class="pill">UNKNOWN</span>`
   :`<span class="pill">${esc(s)}</span>`;
  $('trig').innerHTML='<table><tr><th>Ticker</th><th>Price</th><th>Support</th><th>Resistance</th><th>Signal</th><th>What it means</th></tr>'+
   rows.map(r=>`<tr><td><b>${esc(r.ticker)}</b></td><td>$${esc(r.price??'—')}</td><td>$${esc(r.support??'—')}</td><td>$${esc(r.resistance??'—')}</td><td>${badge(r.signal)}</td><td>${esc(r.detail||'')}</td></tr>`).join('')+'</table>';
 }catch(e){showError('trig',e);}
}
tick();setInterval(tick,60000);
