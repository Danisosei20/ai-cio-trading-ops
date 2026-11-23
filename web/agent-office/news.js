/* Nextus news view: read-only renderer for AI news explanations.
   Refreshes the latest sourced snapshot every 30s while the tab is visible.
   No animation loop, no scanner, no order entry, no gate change. All values
   arrive as untrusted text from a local artifact. */
(() => {
  const $ = id => document.getElementById(id);
  const statusEl = $('news-status');
  const listEl = $('news-list');
  const noteEl = $('news-note');
  if (!statusEl || !listEl || !noteEl) return;

  // A refresh never overlaps another; a failed refresh fails closed and the
  // next tick retries, so a transient error cannot leave a stale snapshot up.
  let inFlight = false;

  const text = v => (v === null || v === undefined ? '' : String(v));
  const finite = v => (v === null || v === undefined || v === ''
    || (typeof v === 'string' && !v.trim()) ? null
    : Number.isFinite(Number(v)) ? Number(v) : null);
  // move_pct is already a percentage (e.g. -20.0), not a fraction.
  const pct = v => {
    const n = finite(v);
    return n === null ? '\u2014' : n.toFixed(2) + '%';
  };
  const httpOnly = new RegExp('^https?://', 'i');
  const safeUrl = value => {
    const raw = text(value).trim();
    if (!httpOnly.test(raw)) return null;
    try {
      const url = new URL(raw);
      return (url.protocol === 'http:' || url.protocol === 'https:') ? url.href : null;
    } catch (_) {
      return null;
    }
  };

  function el(tag, cls, value) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined) node.textContent = value;
    return node;
  }

  function emptyState(message) {
    listEl.replaceChildren();
    const card = el('article', 'news-card');
    card.append(el('p', 'news-empty-text', message));
    listEl.append(card);
  }

  function unavailable(reason) {
    statusEl.textContent = 'data unavailable' + (reason ? ' \u2014 ' + text(reason) : '');
    emptyState('data unavailable \u2014 no sourced explanation artifact.');
    noteEl.textContent = 'Sources are unavailable: the desk artifacts could not be read. '
      + 'No explanation is shown unless its sources can be verified. '
      + 'AI news analysis \u00b7 sources cited \u00b7 no trade recommendations.';
  }

  function citationsBlock(citations) {
    const wrap = el('div', 'news-citations');
    wrap.append(el('span', 'news-label', 'Citations'));
    if (!citations.length) {
      wrap.append(el('span', 'news-none', ' none verified'));
      return wrap;
    }
    const list = document.createElement('ol');
    list.className = 'news-cite-list';
    for (const cite of citations) {
      const item = document.createElement('li');
      const label = text(cite && cite.title) || 'source';
      const href = safeUrl(cite && cite.url);
      if (href) {
        const link = document.createElement('a');
        link.href = href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = label;
        item.append(link);
      } else {
        // Non-http(s) or malformed URLs are shown as inert text, never linked.
        item.append(el('span', 'news-unsafe', label + ' (invalid URL \u2014 link omitted)'));
      }
      list.append(item);
    }
    wrap.append(list);
    return wrap;
  }

  function renderRows(rows) {
    listEl.replaceChildren();
    if (!rows.length) {
      emptyState('No explanations in this snapshot.');
      return;
    }
    for (const row of rows) {
      const card = el('article', 'news-card');
      const head = el('div', 'news-card-head');
      head.append(el('span', 'news-symbol', text(row && row.symbol)));
      head.append(el('span', 'news-move', pct(row && row.move_pct)));
      head.append(el('span', 'news-confidence',
        'confidence: ' + (text(row && row.confidence) || 'unspecified')));
      card.append(head);
      card.append(el('p', 'news-explanation',
        text(row && row.explanation) || 'No explanation supplied.'));
      if (row && row.error) {
        card.append(el('p', 'news-error', 'error: ' + text(row.error)));
      }
      const cited = Array.isArray(row && row.citations) ? row.citations : [];
      card.append(citationsBlock(cited));
      const sources = Array.isArray(row && row.sources) ? row.sources : [];
      const sourcesBlock = el('div', 'news-sources');
      sourcesBlock.append(el('span', 'news-label',
        'source' + (sources.length === 1 ? '' : 's')));
      sourcesBlock.append(el('div', 'news-source-list',
        sources.length ? sources.map(text).join('  \u00b7  ') : 'none listed'));
      card.append(sourcesBlock);
      listEl.append(card);
    }
  }

  function renderNote(data) {
    const sources = Array.isArray(data.sources) ? data.sources : [];
    noteEl.textContent = 'source' + (sources.length === 1 ? '' : 's') + ': '
      + (sources.length ? sources.map(text).join('  \u00b7  ') : 'none listed')
      + (data.model ? '  \u00b7  model ' + text(data.model) : '')
      + (data.generated_at ? '  \u00b7  snapshot ' + text(data.generated_at).replace('T', ' ') : '')
      + '  \u00b7  AI news analysis \u00b7 sources cited \u00b7 no trade recommendations.';
  }

  async function load() {
    if (inFlight) return;
    inFlight = true;
    statusEl.textContent = 'Loading AI news analysis\u2026';
    try {
      const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
        ? AbortSignal.timeout(4000) : undefined;
      const res = await fetch('/api/news', { signal });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (!data || data.status !== 'ok' || !Array.isArray(data.rows)) {
        unavailable(data && data.reason);
        return;
      }
      const rows = data.rows;
      statusEl.textContent = rows.length + ' explanation' + (rows.length === 1 ? '' : 's')
        + ' \u00b7 sources cited \u00b7 no trade recommendations \u00b7 auto-refresh';
      renderRows(rows);
      renderNote(data);
    } catch (_) {
      unavailable();
    } finally {
      inFlight = false;
    }
  }

  load();
  // GET-only, bounded, visible-only. A failed tick fails closed and retries.
  setInterval(() => {
    if (document.hidden || inFlight) return;
    load();
  }, 30000);
})();
