async function tick(){
 try{
  const [a,g]=await Promise.all([api('/api/account'),api('/api/guard')]);
  $('acct').innerHTML=a.ok?
   `<span class="kpi">$${esc(a.portfolio)}</span><br>Cash $${esc(a.cash)} · Buying power $${esc(a.buying_power)} · Acct <small>${esc(a.account)}</small>`:
   '<span class="empty">broker unreachable ('+esc(a.error||'')+')</span>';
  $('pos').innerHTML=a.ok&&a.positions.length?'<table><tr><th>Symbol</th><th>Qty</th><th>Price</th><th>Entry</th><th>Day P&amp;L</th></tr>'+
   a.positions.map(p=>{
    const pl=parseFloat(p.unrealized_intraday_pl ?? p.unrealized_pl);
    const cls=isNaN(pl)?'':(pl>0?'pos':(pl<0?'neg':''));
    const arrow=isNaN(pl)?'':(pl>0?'▲ ':(pl<0?'▼ ':''));
    return `<tr><td><b>${esc(p.symbol)}</b></td><td>${esc(p.qty)}</td><td>$${esc(p.current_price||'')}</td><td>$${esc(p.avg_entry_price||'')}</td><td class="${cls}">${arrow}${esc(p.unrealized_intraday_pl ?? p.unrealized_pl ?? '')}</td></tr>`;}).join('')+'</table>':
   '<span class="empty">No positions.</span>';
  $('ord').innerHTML=a.ok&&a.orders.length?'<table><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Limit</th><th>Status</th></tr>'+
   a.orders.map(o=>{
    const side=o.side==='sell'?'pill warn':'pill info';
    const st=(o.status==='filled'||o.status==='new')?'pill ok':'pill';
    return `<tr><td><b>${esc(o.symbol)}</b></td><td><span class="${side}">${esc(o.side)}</span></td><td>${esc(o.qty)}</td><td>${esc(o.limit_price||o.type||'')}</td><td><span class="${st}">${esc(o.status)}</span></td></tr>`;}).join('')+'</table>':
   '<span class="empty">None.</span>';
  $('guard').innerHTML=g.positions&&g.positions.length?g.positions.map(p=>{
   const sig=p.signal==='HOLD'?'pill ok':(p.signal==='STOP'||p.signal==='TARGET'?'pill bad':'pill warn');
   return `<div class="guardrow"><b>${esc(p.symbol)}</b> ${esc(p.qty)} @ $${esc(p.entry)} → $${esc(p.mark)} `+
   `<span class="${sig}">${esc(p.signal)}</span> <span class="${String(p.return_pct).startsWith('-')?'neg':'pos'}">${esc(p.return_pct)}</span> · stop $${esc(p.stop)} · target $${esc(p.target)}`+
   `${p.order_id?` · sold <small>${esc(p.order_id)}</small>`:''}</div>`;}).join('')
   :'<span class="empty">No guarded positions.</span>';
 }catch(e){showError('acct',e);}
}
tick();setInterval(tick,5000);
