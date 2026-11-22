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
  const symbolsEl = $('market-symbols');

  // Deep link: ?symbol=<SYM> seeds the text input instead of the HTML default value.
  const linkedSymbol = new URLSearchParams(window.location.search).get('symbol');
  if (linkedSymbol) symbolEl.value = linkedSymbol;

  // Colours mirror office.css tokens exactly; no new accent is introduced.
  const C = {
    up: '#8fd1a9', down: '#e88d96', ink: '#e9f1f3', muted: '#93a7b1',
    faint: '#647781', line: '#243440', soft: '#1a2833', accent: '#6fd6c8',
    warm: '#e3b675', upFill: 'rgba(143,209,169,0.10)', downFill: 'rgba(232,141,150,0.10)'
  };
  // SL is red (--danger / C.down); both take-profits are green (--ok / C.up).
  // Distinct dash patterns let a level be identified without reading its label.
  const LEVEL_STYLE = {
    poc:        { color: C.accent, dash: [],      width: 1.8, alpha: 1 },
    stop:       { color: C.down,  dash: [6, 3],   width: 1.4, alpha: 1 },
    tp1:        { color: C.up,    dash: [6, 3],   width: 1.4, alpha: 1 },
    tp2:        { color: C.up,    dash: [2, 3],   width: 1.2, alpha: 1 },
    resistance: { color: C.warm,  dash: [],      width: 1.1, alpha: 0.9 },
    support:    { color: C.up,    dash: [],      width: 1.1, alpha: 0.9 },
    vah:        { color: C.muted, dash: [2, 4],   width: 1,   alpha: 0.8 },
    val:        { color: C.muted, dash: [2, 4],   width: 1,   alpha: 0.8 },
  };
  const MONO = '10px ui-monospace, "SFMono-Regular", Menlo, monospace';
  const LABEL_FONT = '11px ui-monospace, "SFMono-Regular", Menlo, monospace';
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

    const padL = 54, padR = 152, padT = 46, padB = 42;
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
      ctx.fillStyle = C.muted;
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

    // Session boundaries: a multi-session snapshot must not read as one day.
    let priorDate = null;
    bars.forEach((b, i) => {
      const d = text(b && b.ts).slice(0, 10);
      if (priorDate !== null && d !== priorDate) {
        const sx = Math.round(xOf(i)) + 0.5;
        ctx.save();
        ctx.strokeStyle = C.line;
        ctx.setLineDash([2, 4]);
        ctx.beginPath(); ctx.moveTo(sx, priceTop); ctx.lineTo(sx, volBottom); ctx.stroke();
        ctx.restore();
        ctx.fillStyle = C.muted;
        ctx.textAlign = 'center';
        ctx.fillText(d.slice(5), sx, priceTop - 7);
      }
      priorDate = d;
    });

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
    // Levels that share a price are merged (POC/VAH, VAL/SUPPORT); each label
    // sits on ONE line in a wider gutter with a leader back to its price line.
    const drawn = marks.map(m => ({ ...m, yy: yOf(m.value) }))
      .filter(m => m.yy >= priceTop - 2 && m.yy <= priceBottom + 2)
      .sort((a, b) => a.yy - b.yy);
    const grouped = new Map();
    for (const m of drawn) {
      const key = m.value.toFixed(2);
      const found = grouped.get(key);
      if (found) {
        if (!found.names.includes(m.name)) found.names.push(m.name);
      } else {
        grouped.set(key, { value: m.value, yy: m.yy, names: [m.name] });
      }
    }
    const entries = [...grouped.values()].sort((a, b) => a.yy - b.yy);
    const rowH = Math.max(12, Math.min(18, (priceH - 16) / Math.max(1, entries.length)));
    const placed = [];
    let cursor = -Infinity;
    for (const e of entries) {
      const labelY = Math.max(e.yy, cursor + rowH);
      placed.push({ ...e, labelY });
      cursor = labelY;
    }
    const overflow = placed.length ? placed[placed.length - 1].labelY - (priceBottom - 10) : 0;
    if (overflow > 0) placed.forEach(p => { p.labelY -= overflow; });
    const deficit = placed.length ? placed[0].labelY - (priceTop + 10) : 0;
    if (deficit < 0) placed.forEach(p => { p.labelY -= deficit; });

    ctx.font = LABEL_FONT;
    ctx.textAlign = 'left';
    for (const p of placed) {
      let primary = p.names[0], bestWidth = -1;
      for (const n of p.names) {
        const width = LEVEL_STYLE[n] ? LEVEL_STYLE[n].width : 0;
        if (width > bestWidth) { bestWidth = width; primary = n; }
      }
      const style = LEVEL_STYLE[primary] || { color: C.muted, dash: [2, 4], width: 1, alpha: 0.8 };
      ctx.save();
      ctx.setLineDash(style.dash);
      ctx.strokeStyle = style.color;
      ctx.lineWidth = style.width;
      ctx.globalAlpha = style.alpha;
      ctx.beginPath(); ctx.moveTo(padL, p.yy); ctx.lineTo(lineRight + 4, p.yy); ctx.stroke();
      ctx.restore();
      if (p.labelY - p.yy > 3) {
        ctx.save();
        ctx.strokeStyle = style.color;
        ctx.globalAlpha = 0.5;
        ctx.beginPath(); ctx.moveTo(lineRight + 4, p.yy); ctx.lineTo(lineRight + 10, p.labelY); ctx.stroke();
        ctx.restore();
      }
      ctx.fillStyle = style.color;
      ctx.fillRect(lineRight + 10, p.labelY - 8, 6, 6);
      ctx.fillStyle = C.ink;
      ctx.fillText(p.names.join('/').toUpperCase() + ' ' + fmt(p.value), lineRight + 21, p.labelY - 3);
    }
    ctx.font = MONO;

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

  async function loadSymbols() {
    if (!symbolsEl) return;
    try {
      const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
        ? AbortSignal.timeout(4000) : undefined;
      const res = await fetch('/api/symbols', { signal });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (!data || data.status !== 'ok' || !Array.isArray(data.symbols)) throw new Error('bad payload');
      for (const raw of data.symbols) {
        const sym = text(raw).trim();
        if (!sym) continue;
        if (!/^[A-Z0-9._-]{1,8}$/.test(sym)) continue;
        const opt = document.createElement('option');
        opt.value = sym;
        opt.textContent = sym;
        symbolsEl.append(opt);
      }
      // Deep link: reflect the linked symbol in the picker only when it is a loaded option.
      // Otherwise leave the placeholder selected (e.g. an ad-hoc/custom symbol).
      if (linkedSymbol && Array.from(symbolsEl.options).some(o => o.value === linkedSymbol)) {
        symbolsEl.value = linkedSymbol;
      }
    } catch (_) {
      // Fail closed: keep only the placeholder and disable the picker; never fabricate symbols.
      for (const opt of Array.from(symbolsEl.options)) {
        if (opt.value !== '') opt.remove();
      }
      symbolsEl.disabled = true;
    }
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
  if (symbolsEl) {
    symbolsEl.addEventListener('change', () => {
      const sym = symbolsEl.value;
      if (!sym) return;
      symbolEl.value = sym;
      load();
      history.replaceState(null, '', '?symbol=' + encodeURIComponent(sym));
    });
  }
  window.addEventListener('resize', resize);
  if (typeof ResizeObserver === 'function') new ResizeObserver(resize).observe(canvas);
  resize();
  load();
  loadSymbols();
})();
