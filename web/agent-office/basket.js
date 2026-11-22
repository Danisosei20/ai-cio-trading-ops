/* Nextus basket view: read-only renderer for the desk's signal-only $500
   short-basket proposal. One render per snapshot — no animation loop,
   no scanner, no order entry, no gate change. */
(() => {
  const $ = id => document.getElementById(id);
  const statusEl = $('basket-status');
  const rowsEl = $('basket-rows');
  const noteEl = $('basket-note');
  if (!statusEl || !rowsEl || !noteEl) return;

  const text = v => (v === null || v === undefined ? '' : String(v));
  const finite = v => (v === null || v === undefined || v === ''
    || (typeof v === 'string' && !v.trim()) ? null
    : Number.isFinite(Number(v)) ? Number(v) : null);
  const fmt = (v, digits = 2) => {
    const n = finite(v);
    return n === null ? '\u2014' : n.toFixed(digits);
  };
  // The payload's change_pct is already a percentage (e.g. -20.0), not a fraction.
  const pct = v => {
    const n = finite(v);
    return n === null ? '\u2014' : n.toFixed(2) + '%';
  };

  function cell(row, value, cls) {
    const td = document.createElement('td');
    td.textContent = value;
    if (cls) td.className = cls;
    row.append(td);
    return td;
  }

  function emptyState(message) {
    rowsEl.replaceChildren();
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.className = 'basket-empty';
    td.colSpan = 7;
    td.textContent = message;
    tr.append(td);
    rowsEl.append(tr);
  }

  function unavailable(reason) {
    statusEl.textContent = 'data unavailable' + (reason ? ' \u2014 ' + reason : '');
    emptyState('data unavailable \u2014 no sourced proposal.');
    noteEl.textContent = 'Sources are unavailable: the desk artifacts could not be read. '
      + 'No proposal is shown unless its source can be verified. Signal only \u2014 no orders placed.';
  }

  function renderRows(basket) {
    rowsEl.replaceChildren();
    if (!basket.length) {
      emptyState('No names in this snapshot.');
      return;
    }
    for (const row of basket) {
      const tr = document.createElement('tr');
      cell(tr, text(row && row.symbol), 'mono');
      cell(tr, fmt(row && row.reference_price), 'mono');
      cell(tr, pct(row && row.change_pct), 'mono');
      cell(tr, '$' + fmt(row && row.allocation), 'mono');
      cell(tr, fmt(row && row.shares, 4), 'mono');
      cell(tr, '$' + fmt(row && row.notional), 'mono');
      cell(tr, text(row && row.source), 'source'); // verbatim artifact + timestamp
      rowsEl.append(tr);
    }
  }

  function renderNote(data) {
    const sources = Array.isArray(data.sources) ? data.sources : [];
    noteEl.textContent = 'source' + (sources.length === 1 ? '' : 's') + ': '
      + (sources.length ? sources.map(text).join('  \u00b7  ') : 'none listed')
      + (data.generated_at ? '  \u00b7  snapshot ' + text(data.generated_at).replace('T', ' ') : '')
      + '  \u00b7  signal only \u2014 no orders placed.';
  }

  async function load() {
    statusEl.textContent = 'Loading desk short-basket signal\u2026';
    try {
      const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
        ? AbortSignal.timeout(4000) : undefined;
      const res = await fetch('/api/basket', { signal });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (!data || data.status !== 'ok' || !Array.isArray(data.basket)) {
        unavailable(data && data.reason);
        return;
      }
      const basket = data.basket;
      const total = basket.reduce((sum, row) => sum + (finite(row && row.notional) || 0), 0);
      statusEl.textContent = basket.length + ' name' + (basket.length === 1 ? '' : 's')
        + ' \u00b7 capital $' + fmt(data.capital)
        + ' \u00b7 total notional $' + fmt(total)
        + ' \u00b7 signal only';
      renderRows(basket);
      renderNote(data);
    } catch (_) {
      unavailable();
    }
  }

  load();
})();
