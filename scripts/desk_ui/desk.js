let DATA=[];
async function load(){
 try{
  DATA=await api('/api/desk');
  const sel=$('cand');
  sel.innerHTML=DATA.map((t,i)=>{const c=t.candidate||{};
   return `<option value="${i}">${esc(c.symbol||'?')} · ${esc(String(c.candidate_id||'').slice(-12))} · ${esc(c.status||'')}</option>`;}).join('');
  if(!DATA.length){$('chead').innerHTML='<span class="empty">No desk analyses yet.</span>';$('cbars').innerHTML='';$('ctimeline').innerHTML='';$('crisk').innerHTML='';return;}
  sel.onchange=render;render();
 }catch(e){showError('chead',e);}
}
function render(){
 const t=DATA[$('cand').value||0];if(!t||!t.candidate)return;
 const c=t.candidate,j=(t.opinions||[]).find(o=>o.agent==='judge');
 const rds=t.risk_decisions||[];
 const rk=rds.length?rds[rds.length-1]:null;
 $('chead').innerHTML=`<b>${esc(c.symbol)}</b> ${esc(c.status)} · Judge <b>${esc(j?j.verdict:'—')}</b> ${j?esc(j.confidence)+'%':''} · Risk <b>${rk?(rk.approved?'PASS':'FAIL'):'NO RISK RUN'}</b>`;
 $('cbars').innerHTML=(t.opinions||[]).filter(o=>o.agent!=='judge').map(o=>bar(o.confidence,o.agent+' · '+o.verdict)).join('');
 paintBars($('cbars'));
 const ev=[];
 let source='';
 try{source=esc(JSON.parse(c.payload||'{}').source||'');}catch(_){source='';}
 ev.push(`Discovered ${esc(c.created_at||'')} (${esc(c.side||'')} · ${source})`);
 (t.opinions||[]).forEach(o=>{try{const e=JSON.parse(o.evidence||'{}');
  ev.push(`${esc(o.agent)} → ${esc(o.verdict)} ${esc(o.confidence)}% — ${esc(e.thesis||e.summary||(e.evidence||[]).join('; ')||'')}`);}catch(_){ev.push(`${esc(o.agent)} → ${esc(o.verdict)}`);}});
 if(rk){try{const failed=JSON.parse(rk.failed_rules||'[]');
  ev.push(`Risk ${rk.approved?'PASS':'FAIL'} — failed: ${esc(failed.join(', ')||'none')}`);}catch(_){}}
 $('ctimeline').innerHTML=ev.map(x=>`<li>${x}</li>`).join('');
 $('crisk').innerHTML=rk?`<small>${esc(rk.decision_id)} · ${esc(rk.created_at)}</small>`:'<span class="empty">Advisory run — execution stayed with the gated paper flow.</span>';
}
load();setInterval(load,15000);
