async function tick(){
 try{
  const h=await api('/api/health');
  const row=(name,ok,note)=>`<div>${esc(name)}: <span class="${ok?'health-ok':'health-bad'}">${ok?'HEALTHY':'DOWN'}</span> <small>${esc(note||'')}</small></div>`;
  let acct=false,db=true;
  try{const a=await api('/api/account');acct=a.ok;}catch(_){acct=false;}
  try{await api('/api/desk');}catch(_){db=false;}
  $('health').innerHTML=
   row('Paper broker (Alpaca)',acct,'account, positions, clock')+
   row('Ledger database',db,'approvals, debates, learning')+
   row('Kill switch',!h.killed,h.killed?'ENGAGED — no new trades':'off')+
   row('Market clock',true,h.market_open?'open':'closed')+
   row('Trading mode',true,h.mode+(h.live_enabled?' — LIVE ENABLED':' — live off'))+
   row('LLM research',true,'on demand (kimi-k3 via NVIDIA NIM)')+
   row('Scheduler',true,'trader 10:20 ET weekdays · guard every 15 min');
 }catch(e){showError('health',e);}
}
tick();setInterval(tick,15000);
