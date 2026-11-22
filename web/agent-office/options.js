/* Nextus options view: read-only table renderer for the desk's local artifacts.
   One render per snapshot — no animation loop, no scanner, no order entry. */
(() => {
  const $ = id => document.getElementById(id);
  const statusEl = $('options-status');
  const candidatesEl = $('options-candidates');
  const rejectedEl = $('options-rejected');
  const noteEl = $('options-note');
  if (!statusEl || !candidatesEl || !rejectedEl || !noteEl) return;

  const text = v => (v === null || v === undefined ? '' : String(v));
  const finite = v => (v === null || v === undefined || v === ''
    || (typeof v === 'string' && !v.trim()) ? null
    : Number.isFinite(Number(v)) ? Number(v) : null);
  const fmt = (v, digits = 2) => {
    const n = finite(v);
    return n === null ? '\u2014' : n.toFixed(digits);
  };
  const pct = v => {
    const n = finite(v);
    return n === null ? '\u2014' : (n * 100).toFixed(1) + '%';
  };
  const odds = v => {
    const n = finite(v);
    return n === null ? '\u2014' : (n * 100).toFixed(0) + '%';
  };

  function cell(row, value, cls) {
    const td = document.createElement('td');
    td.textContent = value;
    if (cls) td.className = cls;
    row.append(td);
    return td;
  }

  function emptyState(tbody, columns, message) {
    tbody.replaceChildren();
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.className = 'options-empty';
    td.colSpan = columns;
    td.textContent = message;
    tr.append(td);
    tbody.append(tr);
  }

  function unavailable(reason) {
    statusEl.textContent = 'data unavailable' + (reason ? ' \u2014 ' + reason : '');
    emptyState(candidatesEl, 12, 'data unavailable \u2014 no sourced candidates.');
    emptyState(rejectedEl, 4, 'data unavailable \u2014 no sourced rejections.');
    noteEl.textContent = 'Sources are unavailable: the desk artifacts could not be read. '
      + 'No candidate is shown unless its source can be verified.';
  }

  function renderCandidates(rows) {
    candidatesEl.replaceChildren();
    if (!rows.length) {
      emptyState(candidatesEl, 12, 'No candidates in this snapshot.');
      return;
    }
    for (const c of rows) {
      const tr = document.createElement('tr');
      cell(tr, text(c.contract), 'mono');
      cell(tr, text(c.underlying), 'mono');
      cell(tr, text(c.right).toUpperCase());
      cell(tr, fmt(c.strike));
      cell(tr, text(c.expiry), 'mono');
      cell(tr, fmt(c.dte, 0));
      cell(tr, fmt(c.premium));
      cell(tr, fmt(c.delta, 4));
      cell(tr, pct(c.ev_pct));
      cell(tr, '$' + fmt(c.ev_usd), 'mono'); // sourced ev_usd_per_contract
      cell(tr, odds(c.target_first) + ' / ' + odds(c.stop_first));
      cell(tr, text(c.source), 'source'); // verbatim artifact + timestamp
      candidatesEl.append(tr);
    }
  }

  function renderRejected(rows) {
    rejectedEl.replaceChildren();
    if (!rows.length) {
      emptyState(rejectedEl, 4, 'No policy rejections in this snapshot.');
      return;
    }
    for (const r of rows) {
      const tr = document.createElement('tr');
      cell(tr, text(r.underlying), 'mono');
      cell(tr, text(r.right).toUpperCase());
      cell(tr, text(r.reason), 'reason');
      cell(tr, text(r.source), 'source'); // verbatim artifact + timestamp
      rejectedEl.append(tr);
    }
  }

  function renderNote(data) {
    const sources = Array.isArray(data.sources) ? data.sources : [];
    noteEl.textContent = 'source' + (sources.length === 1 ? '' : 's') + ': '
      + (sources.length ? sources.join('  \u00b7  ') : 'none listed')
      + (data.generated_at ? '  \u00b7  snapshot ' + text(data.generated_at).replace('T', ' ') : '');
  }

  async function load() {
    statusEl.textContent = 'Loading desk option candidates\u2026';
    try {
      const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
        ? AbortSignal.timeout(4000) : undefined;
      const res = await fetch('/api/options', { signal });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (!data || data.status !== 'ok' || !Array.isArray(data.candidates)) {
        unavailable(data && data.reason);
        return;
      }
      const count = data.candidates.length;
      statusEl.textContent = count + ' candidate' + (count === 1 ? '' : 's')
        + ' \u00b7 read-only desk artifacts';
      renderCandidates(data.candidates);
      renderRejected(Array.isArray(data.rejected) ? data.rejected : []);
      renderNote(data);
    } catch (_) {
      unavailable();
    }
  }

  load();
})();
