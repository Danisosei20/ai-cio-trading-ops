/* Nextus market view: read-only canvas renderer for local desk artifacts.
   One draw per snapshot — no animation loop, no order entry. */
(() => {
  const $ = id => document.getElementById(id);
  const canvas = $('market-chart');
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const statusEl = $('market-status');
  const levelsEl = $('market-levels');
  const noteEl = $('market-note');
  const symbolEl = $('market-symbol');
  const refreshEl = $('market-refresh');

  // Colours mirror office.css tokens exactly; no new accent is introduced.
  const C = {
    up: '#8fd1a9', down: '#e88d96', ink: '#e9f1f3', muted: '#93a7b1',
    faint: '#647781', line: '#243440', soft: '#1a2833', accent: '#6fd6c8',
    warm: '#e3b675', upFill: 'rgba(143,209,169,0.10)', downFill: 'rgba(232,141,150,0.10)'
  };
  // SL is red (--danger / C.down); both take-profits are green (--ok / C.up).
  const LEVEL_COLOR = { poc: C.accent, tp1: C.up, tp2: C.up, stop: C.down };
  const MONO = '10px ui-monospace, "SFMono-Regular", Menlo, monospace';
  let current = null;

  const finite = v => (v === null || v === undefined || v === '' ? null
    : Number.isFinite(Number(v)) ? Number(v) : null);
  const fmt = v => { const n = finite(v); return n === null ? '—' : n.toFixed(2); };
  const text = v => (v === null || v === undefined ? '' : String(v));

  function clearChart() {
    const r = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, r.width, r.height);
  }

  function unavailable(reason) {
    current = null;
    statusEl.textContent = 'data unavailable' + (reason ? ' — ' + reason : '');
    levelsEl.replaceChildren();
    noteEl.textContent = 'No sourced levels — the snapshot could not be read from local desk artifacts.';
    clearChart();
  }

  function renderLevels(levels) {
    levelsEl.replaceChildren();
    for (const [name, level] of Object.entries(levels || {})) {
      const row = document.createElement('li');
      const left = document.createElement('div');
      const label = document.createElement('span');
      label.className = 'level-name';
      label.textContent = name;
      const src = document.createElement('span');
      src.className = 'level-source';
      src.textContent = text(level && level.source); // verbatim, including computed labels
      left.append(label, src);
      const value = document.createElement('span');
      value.className = 'level-value';
      const v = level && level.value;
      value.textContent = Array.isArray(v) ? (v.length ? v.map(fmt).join(' · ') : '—') : fmt(v);
      row.append(left, value);
      levelsEl.append(row);
    }
  }

  function renderNote(data) {
    const sources = Array.isArray(data.sources) ? data.sources : [];
    noteEl.textContent = 'source' + (sources.length === 1 ? '' : 's') + ': '
      + (sources.length ? sources.join('  ·  ') : 'none listed')
      + (data.generated_at ? '  ·  snapshot ' + text(data.generated_at).replace('T', ' ') : '');
  }

  function badge(label, color, fill, x, y) {
    ctx.font = '600 11px ui-monospace, Menlo, monospace';
    const width = ctx.measureText(label).width + 20;
    ctx.fillStyle = fill;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, width, 22, 4);
    else ctx.rect(x, y, width, 22);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, x + 10, y + 12);
    ctx.textBaseline = 'alphabetic';
  }

  function draw(data) {
    const r = canvas.getBoundingClientRect();
    const w = r.width, h = r.height;
    ctx.clearRect(0, 0, w, h);
    const bars = Array.isArray(data.bars) ? data.bars : [];
    if (w < 40 || h < 80 || !bars.length) return;

    const padL = 54, padR = 86, padT = 46, padB = 42;
    const volH = Math.max(24, Math.min(74, h * 0.16));
    const gap = 34;
    const priceTop = padT;
    const priceBottom = h - padB - volH - gap;
    const priceH = Math.max(30, priceBottom - priceTop);
    const volTop = priceBottom + gap;
    const volBottom = h - padB;
    const plotW = Math.max(10, w - padL - padR);
    const lineRight = padL + plotW;

    const levels = data.levels && typeof data.levels === 'object' ? data.levels : {};
    const marks = [];
    const zones = [];
    for (const [name, level] of Object.entries(levels)) {
      const v = level && level.value;
      if (Array.isArray(v)) {
        const nums = v.map(finite).filter(n => n !== null);
        if (name === 'supply' || name === 'demand') zones.push({ name, nums });
        else nums.forEach(n => marks.push({ name, value: n }));
      } else {
        const n = finite(v);
        if (n !== null) marks.push({ name, value: n });
      }
    }
    let hi = -Infinity, lo = Infinity;
    bars.forEach(b => {
      const high = finite(b && b.high), low = finite(b && b.low);
      if (high !== null) hi = Math.max(hi, high);
      if (low !== null) lo = Math.min(lo, low);
    });
    marks.forEach(m => { hi = Math.max(hi, m.value); lo = Math.min(lo, m.value); });
    zones.forEach(z => z.nums.forEach(n => { hi = Math.max(hi, n); lo = Math.min(lo, n); }));
    if (!Number.isFinite(hi) || !Number.isFinite(lo)) return;

    const span = Math.max(hi - lo, Math.abs(hi) * 0.01, 0.01);
    const min = lo - span * 0.06, max = hi + span * 0.06;
    const yOf = p => priceTop + (max - p) / (max - min) * priceH;
    const step = plotW / bars.length;
    const xOf = i => padL + (i + 0.5) * step;

    // Grid and price axis.
    ctx.font = MONO;
    ctx.textAlign = 'right';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const p = min + (max - min) * i / 4;
      const gy = Math.round(yOf(p)) + 0.5;
      ctx.strokeStyle = C.soft;
      ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(lineRight, gy); ctx.stroke();
      ctx.fillStyle = C.faint;
      ctx.fillText(p.toFixed(2), padL - 8, gy + 3);
    }
    ctx.strokeStyle = C.line;
    ctx.beginPath(); ctx.moveTo(padL + 0.5, priceTop); ctx.lineTo(padL + 0.5, volBottom); ctx.stroke();

    // Shaded supply and demand bands sit behind the candles.
    for (const z of zones) {
      if (!z.nums.length) continue;
      const zLo = Math.min(...z.nums), zHi = Math.max(...z.nums);
      const half = Math.max((max - min) * 0.006, (zHi - zLo) * 0.12);
      const top = yOf(zHi + half), bottom = yOf(zLo - half);
      ctx.fillStyle = z.name === 'supply' ? C.downFill : C.upFill;
      ctx.fillRect(padL, top, plotW, bottom - top);
      ctx.strokeStyle = z.name === 'supply' ? C.down : C.up;
      ctx.globalAlpha = 0.5;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(padL, top); ctx.lineTo(lineRight, top);
      ctx.moveTo(padL, bottom); ctx.lineTo(lineRight, bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.fillStyle = C.faint;
      ctx.textAlign = 'left';
      ctx.fillText(z.name.toUpperCase(), padL + 6, top - 5);
    }

    // Candlesticks (existing muted up/down tokens) plus a volume histogram.
    const bw = Math.max(2, Math.min(11, step * 0.6));
    const maxVol = bars.reduce((m, b) => Math.max(m, finite(b && b.volume) || 0), 1);
    bars.forEach((b, i) => {
      const o = finite(b && b.open), c = finite(b && b.close);
      const high = finite(b && b.high), low = finite(b && b.low);
      if (o === null || c === null || high === null || low === null) return;
      const color = c >= o ? C.up : C.down;
      const cx = Math.round(xOf(i)) + 0.5;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(cx, yOf(high)); ctx.lineTo(cx, yOf(low)); ctx.stroke();
      const bodyTop = yOf(Math.max(o, c));
      const bodyH = Math.max(1, yOf(Math.min(o, c)) - bodyTop);
      ctx.fillRect(cx - bw / 2, bodyTop, bw, bodyH);
      const vh = (finite(b && b.volume) || 0) / maxVol * (volH - 6);
      ctx.globalAlpha = 0.55;
      ctx.fillRect(cx - bw / 2, volBottom - vh, bw, vh);
      ctx.globalAlpha = 1;
    });

    // Sourced level lines: poc, tp1, tp2, stop and the zone profile marks.
    // Label rows are spaced apart so clustered levels stay legible when narrow.
    const drawn = marks.map(m => ({ ...m, yy: yOf(m.value) }))
      .filter(m => m.yy >= priceTop - 2 && m.yy <= priceBottom + 2)
      .sort((a, b) => a.yy - b.yy);
    let lastLabelY = -Infinity;
    for (const m of drawn) {
      const color = LEVEL_COLOR[m.name] || C.muted;
      ctx.save();
      ctx.setLineDash(m.name === 'poc' ? [] : [5, 4]);
      ctx.strokeStyle = color;
      ctx.lineWidth = m.name === 'poc' ? 1.5 : 1;
      ctx.globalAlpha = 0.9;
      ctx.beginPath(); ctx.moveTo(padL, m.yy); ctx.lineTo(lineRight + 4, m.yy); ctx.stroke();
      ctx.restore();
      const labelY = Math.max(m.yy, lastLabelY + 24);
      if (labelY - m.yy > 2) {
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.6;
        ctx.beginPath(); ctx.moveTo(lineRight + 4, m.yy); ctx.lineTo(lineRight + 7, labelY); ctx.stroke();
        ctx.globalAlpha = 1;
      }
      ctx.textAlign = 'left';
      ctx.fillStyle = color;
      ctx.fillText(m.name.toUpperCase(), lineRight + 8, labelY - 2);
      ctx.fillStyle = C.muted;
      ctx.fillText(fmt(m.value), lineRight + 8, labelY + 10);
      lastLabelY = labelY;
    }

    // Volume caption, a few time ticks, direction badge and snapshot stamp.
    ctx.fillStyle = C.faint;
    ctx.textAlign = 'left';
    ctx.fillText('VOLUME', padL, volTop - 10);
    ctx.textAlign = 'center';
    [0, Math.floor((bars.length - 1) / 2), bars.length - 1].forEach(i => {
      const ts = text(bars[i] && bars[i].ts);
      ctx.fillText(ts.slice(11, 16) || ts, xOf(i), volBottom + 16);
    });
    const isPut = text(data.direction).toLowerCase() === 'put';
    badge((isPut ? 'PUT' : 'CALL') + ' · READ-ONLY', isPut ? C.down : C.up,
      isPut ? C.downFill : C.upFill, padL, 12);
    ctx.fillStyle = C.muted;
    ctx.textAlign = 'right';
    ctx.fillText(text(data.symbol) + '  ·  ' + text(data.generated_at).replace('T', ' '), w - 10, 23);
  }

  function resize() {
    const r = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const pw = Math.max(1, Math.round(r.width * dpr));
    const ph = Math.max(1, Math.round(r.height * dpr));
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (current) draw(current); else clearChart();
  }

  async function load() {
    const raw = (symbolEl.value || '').trim().toUpperCase() || 'NVDA';
    symbolEl.value = raw;
    statusEl.textContent = 'Loading ' + raw + '…';
    try {
      const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
        ? AbortSignal.timeout(6000) : undefined;
      const res = await fetch('/api/market?symbol=' + encodeURIComponent(raw), { signal });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (!data || data.status !== 'ok' || !Array.isArray(data.bars) || !data.bars.length) {
        unavailable(data && data.reason);
        return;
      }
      current = data;
      statusEl.textContent = raw + ' · ' + data.bars.length + ' bars · '
        + text(data.direction).toUpperCase() + ' · read-only desk snapshot';
      renderLevels(data.levels);
      renderNote(data);
      resize();
    } catch (_) {
      unavailable();
    }
  }

  refreshEl.addEventListener('click', load);
  symbolEl.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); load(); }
  });
  window.addEventListener('resize', resize);
  if (typeof ResizeObserver === 'function') new ResizeObserver(resize).observe(canvas);
  resize();
  load();
})();
