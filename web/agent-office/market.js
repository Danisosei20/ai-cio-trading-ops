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
  let hover = null;

  const finite = v => (v === null || v === undefined || v === '' ? null
    : Number.isFinite(Number(v)) ? Number(v) : null);
  const fmt = v => { const n = finite(v); return n === null ? '—' : n.toFixed(2); };
  const text = v => (v === null || v === undefined ? '' : String(v));

  // Derive the legend's bar interval from the bars' own timestamps. Only
  // consecutive bars in the same session (ts.slice(0,10)) contribute, and the
  // median delta tolerates an occasional gap. No valid delta -> "desk bars".
  function barIntervalLabel(bars) {
    const deltas = [];
    for (let i = 1; i < bars.length; i++) {
      const prevTs = bars[i - 1] && bars[i - 1].ts;
      const curTs = bars[i] && bars[i].ts;
      if (typeof prevTs !== 'string' || typeof curTs !== 'string') continue;
      if (prevTs.slice(0, 10) !== curTs.slice(0, 10)) continue;
      const minutes = (Date.parse(curTs) - Date.parse(prevTs)) / 60000;
      if (Number.isFinite(minutes) && minutes > 0) deltas.push(minutes);
    }
    if (!deltas.length) return 'desk bars';
    deltas.sort((a, b) => a - b);
    const mid = Math.floor(deltas.length / 2);
    const median = deltas.length % 2 ? deltas[mid] : (deltas[mid - 1] + deltas[mid]) / 2;
    const n = Math.round(median);
    return n >= 60 ? Math.round(n / 60) + 'h' : n + 'm';
  }

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
    if (w < 300 || h < 180 || !bars.length) return;

    // Chart surface uses a TradingView-like dark palette; the desk shell keeps
    // its own tokens. No order path, no fabricated values.
    const TV = {
      bg: '#131722', grid: '#2a2e39', text: '#d1d4dc', dim: '#9598a1',
      up: '#26a69a', down: '#ef5350', cross: '#758696',
      axis: '#0f1219', chip: 'rgba(15,18,25,0.9)'
    };

    const axisW = 74, padL = 12, padT = 30, timeH = 26;
    const volH = Math.max(38, Math.min(78, h * 0.18));
    const gap = 16;
    const priceTop = padT;
    const priceBottom = h - timeH - volH - gap;
    const priceH = Math.max(30, priceBottom - priceTop);
    const volTop = priceBottom + gap;
    const volBottom = h - timeH;
    const plotRight = w - axisW;
    const plotW = Math.max(10, plotRight - padL);
    const step = plotW / bars.length;
    const xOf = i => padL + (i + 0.5) * step;

    // Price scale follows the bars (as a charting terminal does); a level far
    // outside that range becomes an edge marker instead of compressing price.
    let hi = -Infinity, lo = Infinity;
    bars.forEach(b => {
      const high = finite(b && b.high), low = finite(b && b.low);
      if (high !== null) hi = Math.max(hi, high);
      if (low !== null) lo = Math.min(lo, low);
    });
    if (!Number.isFinite(hi) || !Number.isFinite(lo)) return;
    const range = Math.max(hi - lo, Math.abs(hi) * 0.01, 0.01);
    const min = lo - range * 0.08, max = hi + range * 0.08;
    const yOf = p => priceTop + (max - p) / (max - min) * priceH;

    const levels = data.levels && typeof data.levels === 'object' ? data.levels : {};
    const marks = [], zones = [];
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

    // Canvas, right price axis and bottom time axis.
    ctx.fillStyle = TV.bg;
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = TV.axis;
    ctx.fillRect(plotRight, 0, axisW, h);
    ctx.fillRect(0, h - timeH, w, timeH);
    ctx.strokeStyle = TV.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(plotRight + 0.5, 0); ctx.lineTo(plotRight + 0.5, h);
    ctx.moveTo(0, h - timeH + 0.5); ctx.lineTo(w, h - timeH + 0.5);
    ctx.moveTo(plotRight + 0.5, volBottom + 0.5); ctx.lineTo(w, volBottom + 0.5);
    ctx.stroke();

    // Horizontal price grid with labels in the right axis.
    ctx.font = MONO;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    for (let i = 0; i <= 4; i++) {
      const p = min + (max - min) * i / 4;
      const gy = Math.round(yOf(p)) + 0.5;
      ctx.strokeStyle = TV.grid;
      ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(plotRight, gy); ctx.stroke();
      ctx.fillStyle = TV.dim;
      ctx.fillText(p.toFixed(2), plotRight + 8, gy);
    }
    ctx.textBaseline = 'alphabetic';

    // Sourced supply/demand bands behind price.
    for (const z of zones) {
      if (!z.nums.length) continue;
      const zLo = Math.min(...z.nums), zHi = Math.max(...z.nums);
      const half = Math.max((max - min) * 0.006, (zHi - zLo) * 0.12);
      const top = Math.max(priceTop, yOf(zHi + half));
      const bottom = Math.min(priceBottom, yOf(zLo - half));
      if (bottom <= top) continue;
      ctx.fillStyle = z.name === 'supply' ? 'rgba(239,83,80,0.09)' : 'rgba(38,166,154,0.09)';
      ctx.fillRect(padL, top, plotW, bottom - top);
      ctx.strokeStyle = z.name === 'supply' ? TV.down : TV.up;
      ctx.globalAlpha = 0.5;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(padL, top); ctx.lineTo(plotRight, top);
      ctx.moveTo(padL, bottom); ctx.lineTo(plotRight, bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.fillStyle = TV.dim;
      ctx.textAlign = 'left';
      ctx.fillText(z.name.toUpperCase(), padL + 6, top - 5);
    }

    // Session dividers first, so time ticks can avoid their date labels.
    const sessionX = [];
    let priorDate = null;
    bars.forEach((b, i) => {
      const d = text(b && b.ts).slice(0, 10);
      if (d && priorDate !== null && d !== priorDate) sessionX.push({ x: xOf(i), label: d.slice(5) });
      if (d) priorDate = d;
    });
    for (const s of sessionX) {
      const sx = Math.round(s.x) + 0.5;
      ctx.save();
      ctx.strokeStyle = TV.grid;
      ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(sx, priceTop); ctx.lineTo(sx, h - timeH); ctx.stroke();
      ctx.restore();
      ctx.fillStyle = TV.text;
      ctx.textAlign = 'center';
      ctx.fillText(s.label, sx, h - 19);
    }
    // Vertical time grid; skip a tick too close to a session date.
    const tickIdx = [...new Set([0, Math.floor((bars.length - 1) / 4),
      Math.floor((bars.length - 1) / 2), Math.floor(3 * (bars.length - 1) / 4), bars.length - 1])];
    for (const i of tickIdx) {
      const gx = Math.round(xOf(i)) + 0.5;
      ctx.strokeStyle = TV.grid;
      ctx.beginPath(); ctx.moveTo(gx, priceTop); ctx.lineTo(gx, h - timeH); ctx.stroke();
      if (sessionX.some(s => Math.abs(s.x - gx) < 44)) continue;
      const ts = text(bars[i] && bars[i].ts);
      ctx.fillStyle = TV.dim;
      ctx.textAlign = 'center';
      ctx.fillText(ts.slice(11, 16) || ts, gx, h - 8);
    }
    ctx.fillStyle = TV.dim;
    ctx.textAlign = 'left';
    ctx.fillText('VOL', padL, volTop - 6);

    // Candlesticks and volume.
    const bw = Math.max(2, Math.min(11, step * 0.62));
    const maxVol = bars.reduce((m, b) => Math.max(m, finite(b && b.volume) || 0), 1);
    bars.forEach((b, i) => {
      const o = finite(b && b.open), c = finite(b && b.close);
      const high = finite(b && b.high), low = finite(b && b.low);
      if (o === null || c === null || high === null || low === null) return;
      const color = c >= o ? TV.up : TV.down;
      const cx = Math.round(xOf(i)) + 0.5;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(cx, yOf(high)); ctx.lineTo(cx, yOf(low)); ctx.stroke();
      const bodyTop = yOf(Math.max(o, c));
      ctx.fillRect(cx - bw / 2, bodyTop, bw, Math.max(1, yOf(Math.min(o, c)) - bodyTop));
      const vh = (finite(b && b.volume) || 0) / maxVol * (volH - 8);
      ctx.globalAlpha = 0.55;
      ctx.fillRect(cx - bw / 2, volBottom - vh, bw, vh);
      ctx.globalAlpha = 1;
    });

    // Sourced levels: same-price values merge into one label. Levels outside
    // the price scale get a small edge marker instead of squeezing the candles.
    const inView = [], offView = [];
    for (const m of marks) {
      if (m.value >= min && m.value <= max) inView.push({ ...m, yy: yOf(m.value) });
      else offView.push(m);
    }
    inView.sort((a, b) => a.yy - b.yy);
    const grouped = new Map();
    for (const m of inView) {
      const key = m.value.toFixed(2);
      const found = grouped.get(key);
      if (found) { if (!found.names.includes(m.name)) found.names.push(m.name); }
      else grouped.set(key, { value: m.value, yy: m.yy, names: [m.name] });
    }
    const entries = [...grouped.values()].sort((a, b) => a.yy - b.yy);
    const rowH = Math.max(11, Math.min(16, (priceH - 16) / Math.max(1, entries.length)));
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
    ctx.textBaseline = 'middle';
    for (const p of placed) {
      let primary = p.names[0], bestWidth = -1;
      for (const n of p.names) {
        const width = LEVEL_STYLE[n] ? LEVEL_STYLE[n].width : 0;
        if (width > bestWidth) { bestWidth = width; primary = n; }
      }
      const style = LEVEL_STYLE[primary] || { color: TV.dim, dash: [2, 4], width: 1, alpha: 0.8 };
      ctx.save();
      ctx.setLineDash(style.dash);
      ctx.strokeStyle = style.color;
      ctx.lineWidth = style.width;
      ctx.globalAlpha = style.alpha;
      ctx.beginPath(); ctx.moveTo(padL, p.yy); ctx.lineTo(plotRight, p.yy); ctx.stroke();
      ctx.restore();
      if (p.labelY - p.yy > 3) {
        ctx.save(); ctx.strokeStyle = style.color; ctx.globalAlpha = 0.5;
        ctx.beginPath(); ctx.moveTo(plotRight - 2, p.yy); ctx.lineTo(plotRight - 8, p.labelY); ctx.stroke();
        ctx.restore();
      }
      const label = p.names.join('/').toUpperCase() + ' ' + fmt(p.value);
      const chipW = ctx.measureText(label).width + 14;
      const lx = Math.max(padL, plotRight - chipW - 6);
      ctx.fillStyle = TV.chip;
      ctx.fillRect(lx, p.labelY - 8, chipW, 16);
      ctx.fillStyle = style.color;
      ctx.fillRect(lx + 3, p.labelY - 5, 4, 10);
      ctx.fillStyle = TV.text;
      ctx.fillText(label, lx + 11, p.labelY);
    }

    // Off-scale levels: a triangle at the edge with its name and value.
    const edgeGroups = new Map();
    for (const m of offView) {
      const key = (m.value > max ? 'hi:' : 'lo:') + m.value.toFixed(2);
      const found = edgeGroups.get(key);
      if (found) { if (!found.names.includes(m.name)) found.names.push(m.name); }
      else edgeGroups.set(key, { value: m.value, top: m.value > max, names: [m.name] });
    }
    let hiRow = 0, loRow = 0;
    for (const e of edgeGroups.values()) {
      const label = e.names.join('/').toUpperCase() + ' ' + fmt(e.value) + (e.top ? ' ↑' : ' ↓');
      const chipW = ctx.measureText(label).width + 14;
      const lx = Math.max(padL, plotRight - chipW - 6);
      const yy = e.top ? priceTop + 6 + hiRow++ * 16 : priceBottom - 6 - loRow++ * 16;
      if (yy < priceTop || yy > priceBottom) continue;
      ctx.fillStyle = TV.chip;
      ctx.fillRect(lx, yy - 8, chipW, 16);
      ctx.fillStyle = e.top ? TV.down : TV.up;
      ctx.beginPath();
      const ax = lx + 6;
      if (e.top) { ctx.moveTo(ax, yy - 4); ctx.lineTo(ax - 3, yy + 2); ctx.lineTo(ax + 3, yy + 2); }
      else { ctx.moveTo(ax, yy + 4); ctx.lineTo(ax - 3, yy - 2); ctx.lineTo(ax + 3, yy - 2); }
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = TV.text;
      ctx.fillText(label, lx + 14, yy);
    }
    ctx.textBaseline = 'alphabetic';

    // Crosshair: dashed guides with price and time boxes on the axes.
    if (hover && hover.x >= padL && hover.x <= plotRight &&
        hover.y >= priceTop && hover.y <= priceBottom) {
      ctx.save();
      ctx.strokeStyle = TV.cross;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(hover.x, priceTop); ctx.lineTo(hover.x, h - timeH);
      ctx.moveTo(padL, hover.y); ctx.lineTo(plotRight, hover.y);
      ctx.stroke();
      ctx.restore();
      const p = max - (hover.y - priceTop) / priceH * (max - min);
      ctx.fillStyle = TV.cross;
      ctx.fillRect(plotRight, hover.y - 9, axisW, 18);
      ctx.fillStyle = TV.axis;
      ctx.font = MONO; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.fillText(p.toFixed(2), plotRight + 7, hover.y);
      const idx = Math.max(0, Math.min(bars.length - 1, Math.floor((hover.x - padL) / step)));
      const ts = text(bars[idx] && bars[idx].ts);
      const tLabel = ts.length >= 16 ? ts.slice(5, 16).replace('T', ' ') : ts;
      const tw = ctx.measureText(tLabel).width + 12;
      const bx = Math.max(0, Math.min(w - tw, hover.x - tw / 2));
      ctx.fillStyle = TV.cross;
      ctx.fillRect(bx, h - timeH, tw, timeH);
      ctx.fillStyle = TV.axis;
      ctx.fillText(tLabel, bx + 6, h - timeH / 2);
      ctx.textBaseline = 'alphabetic';
    }

    // Terminal-style legend from the hovered bar (or the last bar).
    const idx = hover && hover.x >= padL && hover.x <= plotRight
      ? Math.max(0, Math.min(bars.length - 1, Math.floor((hover.x - padL) / step)))
      : bars.length - 1;
    const b = bars[idx];
    const o = finite(b && b.open), c = finite(b && b.close);
    const bh = finite(b && b.high), bl = finite(b && b.low);
    const prev = idx > 0 ? finite(bars[idx - 1] && bars[idx - 1].close) : null;
    const change = c !== null && prev ? (c - prev) / prev * 100 : null;
    ctx.font = MONO;
    ctx.textAlign = 'left';
    let symLabel = text(data.symbol) + ' · ' + barIntervalLabel(bars);
    while (symLabel.length > 3 && ctx.measureText(symLabel).width > plotW * 0.4) {
      symLabel = symLabel.slice(0, -2) + '…';
    }
    const ohlcLabel = 'O ' + fmt(o) + '  H ' + fmt(bh) + '  L ' + fmt(bl) + '  C ' + fmt(c);
    const changeLabel = change === null ? ''
      : (change >= 0 ? '+' : '') + change.toFixed(2) + '%';
    const symW = ctx.measureText(symLabel).width;
    const ohlcW = ctx.measureText(ohlcLabel).width;
    const changeW = ctx.measureText(changeLabel).width;
    const x0 = padL + 4;
    ctx.fillStyle = TV.text;
    ctx.fillText(symLabel, x0, 14);
    // Draw only what fits inside the plot; a narrow viewport keeps the symbol
    // and change rather than clipping text into the price axis.
    if (x0 + symW + 12 + ohlcW + 12 + changeW <= plotRight - 4) {
      ctx.fillStyle = TV.dim;
      ctx.fillText(ohlcLabel, x0 + symW + 12, 14);
      ctx.fillStyle = change >= 0 ? TV.up : TV.down;
      ctx.fillText(changeLabel, x0 + symW + 12 + ohlcW + 12, 14);
    } else if (x0 + symW + 12 + changeW <= plotRight - 4) {
      ctx.fillStyle = change >= 0 ? TV.up : TV.down;
      ctx.fillText(changeLabel, x0 + symW + 12, 14);
    }
    const isPut = text(data.direction).toLowerCase() === 'put';
    badge((isPut ? 'PUT' : 'CALL') + ' · READ-ONLY', isPut ? TV.down : TV.up,
      isPut ? 'rgba(239,83,80,0.12)' : 'rgba(38,166,154,0.12)', padL + 4, 34);
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
  canvas.addEventListener('mousemove', e => {
    const r = canvas.getBoundingClientRect();
    hover = { x: e.clientX - r.left, y: e.clientY - r.top };
    if (current) draw(current);
  });
  canvas.addEventListener('mouseleave', () => {
    hover = null;
    if (current) draw(current);
  });
  window.addEventListener('resize', resize);
  if (typeof ResizeObserver === 'function') new ResizeObserver(resize).observe(canvas);
  resize();
  load();
  loadSymbols();
})();
