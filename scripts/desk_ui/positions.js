async function tick(){
 try{
  const [a,g]=await Promise.all([api('/api/account'),api('/api/guard')]);
  $('acct').innerHTML=a.ok?
   `<span class="kpi">$${esc(a.portfolio)}</span><br>Cash $${esc(a.cash)} · Buying power $${esc(a.buying_power)} · Acct <small>${esc(a.account)}</small>`:
   '<span class="empty">broker unreachable ('+esc(a.error||'')+')</span>';
  $('pos').innerHTML=a.ok&&a.positions.length?'<table><tr><th>Symbol</th><th>Qty</th><th>Price</th><th>Entry</th><th>Unrealized</th></tr>'+
   a.positions.map(p=>`<tr><td><b>${esc(p.symbol)}</b></td><td>${esc(p.qty)}</td><td>$${esc(p.current_price||'')}</td><td>$${esc(p.avg_entry_price||'')}</td><td>${esc(p.unrealized_pl||'')}</td></tr>`).join('')+'</table>':
   '<span class="empty">No positions.</span>';
  $('ord').innerHTML=a.ok&&a.orders.length?'<table><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Limit</th><th>Status</th></tr>'+
   a.orders.map(o=>`<tr><td>${esc(o.symbol)}</td><td>${esc(o.side)}</td><td>${esc(o.qty)}</td><td>${esc(o.limit_price||o.type||'')}</td><td>${esc(o.status)}</td></tr>`).join('')+'</table>':
   '<span class="empty">None.</span>';
  $('guard').innerHTML=g.positions&&g.positions.length?g.positions.map(p=>
   `<div><b>${esc(p.symbol)}</b> ${esc(p.qty)} @ $${esc(p.entry)} → $${esc(p.mark)} `+
   `<b>${esc(p.signal)}</b> (${esc(p.return_pct)}) · stop $${esc(p.stop)} · target $${esc(p.target)}`+
   `${p.order_id?` · sold <small>${esc(p.order_id)}</small>`:''}</div>`).join('')
   :'<span class="empty">No guarded positions.</span>';
 }catch(e){showError('acct',e);}
}
tick();setInterval(tick,5000);
