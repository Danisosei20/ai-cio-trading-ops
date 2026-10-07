async function tick(){try{const l=await api('/api/log');$('log').textContent=l.tail;}catch(e){showError('log',e);}}
tick();setInterval(tick,5000);
