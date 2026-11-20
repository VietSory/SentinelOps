function baseUrl(document, name) {
  const value = document.querySelector(`meta[name="sentinelops-${name}-url"]`)?.content?.trim();
  if (!value) return null;
  try {
    const url = new URL(value, document.location.href);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
    url.pathname = `${url.pathname.replace(/\/+$/, '')}/`;
    return url;
  } catch { return null; }
}

export function mount(root, ctx) {
  const document = root.ownerDocument;
  const panel = document.createElement('nav');
  panel.className = 'incident-deeplinks';
  panel.setAttribute('aria-label', 'Incident observability links');
  panel.hidden = true;
  root.append(panel);
  let destroyed = false;

  function refresh() {
    if (destroyed) return;
    const menuWasOpen = panel.querySelector('details')?.open;
    const previousIncident = panel.dataset.incident;
    panel.replaceChildren();
    panel.hidden = true;
    panel.removeAttribute('title');
    const detail = ctx.getDetail(), payload = detail?.payload;
    if (!payload) return;
    panel.dataset.incident = `${detail.incident_id}:${detail.revision}`;
    const grafana = baseUrl(document, 'grafana');
    const jaeger = baseUrl(document, 'jaeger');
    if (!grafana && !jaeger) return;
    const links = document.createElement('div');
    links.className = 'incident-deeplinks-list';
    const traces = document.createElement('div');
    traces.className = 'incident-deeplinks-options';
    function icon(symbol) {
      const item = document.createElement('span');
      item.textContent = symbol;
      item.setAttribute('aria-hidden', 'true');
      return item;
    }
    function add(url, label, evidenceId = null, container = traces) {
      const anchor = document.createElement('a');
      anchor.className = 'incident-deeplinks-link';
      anchor.href = url.href;
      if (container === links) anchor.append(icon('▥'));
      const text = document.createElement('span'); text.textContent = label; anchor.append(text);
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      anchor.setAttribute('aria-label', `${label} (opens in a new tab)`);
      if (evidenceId) anchor.dataset.evidenceId = evidenceId;
      container.append(anchor);
      return anchor;
    }

    const detected = typeof payload.detected_at === 'string' ? Date.parse(payload.detected_at) : NaN;
    const from = Math.max(0, detected - 10 * 60 * 1000);
    const to = detected + 20 * 60 * 1000;
    const validWindow = Number.isSafeInteger(from * 1000) && Number.isSafeInteger(to * 1000) && to > from;
    if (validWindow) {
      if (grafana) {
        const url = new URL('d/sentinelops-slo', grafana);
        url.searchParams.set('from', String(from));
        url.searchParams.set('to', String(to));
        const anchor = add(url, 'Grafana', null, links);
        anchor.title = 'Open SLO dashboard';
        anchor.setAttribute('aria-label', 'Grafana: Open SLO dashboard (opens in a new tab)');
      }
      if (jaeger) {
        const services = Array.isArray(payload.affected_services) ? payload.affected_services : [];
        for (const service of new Set(services)) {
          if (typeof service !== 'string' || !service.trim()) continue;
          const url = new URL('search', jaeger);
          url.searchParams.set('service', service);
          url.searchParams.set('start', String(from * 1000));
          url.searchParams.set('end', String(to * 1000));
          url.searchParams.set('lookback', 'custom');
          add(url, `Find traces: ${service}`);
        }
      }
    }
    if (jaeger) {
      const seen = new Set();
      const evidence = Array.isArray(payload.evidence) ? payload.evidence : [];
      for (const item of evidence) {
        if (item?.type !== 'trace' || typeof item.fact !== 'string') continue;
        const trace = /^Trace ([0-9a-f]{32}): /.exec(item.fact)?.[1];
        if (!trace || /^0+$/.test(trace) || seen.has(trace)) continue;
        seen.add(trace);
        add(new URL(`trace/${trace}`, jaeger), `Open trace ${trace}`, item.id);
      }
    }
    if (traces.childElementCount) {
      const menu = document.createElement('details'); menu.className = 'incident-deeplinks-menu';
      menu.open = Boolean(menuWasOpen && previousIncident === panel.dataset.incident);
      const summary = document.createElement('summary');
      summary.className = 'incident-deeplinks-link';
      const label = document.createElement('span'); label.textContent = 'Jaeger';
      summary.append(icon('⌕'), label, icon('⌄'));
      summary.title = 'Find service traces or open recorded traces';
      menu.append(summary, traces);
      menu.addEventListener('keydown', event => {
        if (event.key === 'Escape') { menu.open = false; summary.focus(); event.stopPropagation(); }
      });
      traces.addEventListener('click', event => {
        if (event.target.closest('a')) { menu.open = false; summary.focus(); }
      });
      links.append(menu);
    }
    if (!links.childElementCount) return;
    if (validWindow) {
      panel.title = `Incident window: ${new Date(from).toISOString()} to ${new Date(to).toISOString()}`;
    }
    panel.append(links);
    panel.hidden = false;
  }

  refresh();
  return {
    refresh,
    destroy() { destroyed = true; panel.remove(); },
  };
}
