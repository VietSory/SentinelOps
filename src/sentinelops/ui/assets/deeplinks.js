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
    panel.replaceChildren();
    panel.hidden = true;
    const payload = ctx.getDetail()?.payload;
    if (!payload) return;
    const grafana = baseUrl(document, 'grafana');
    const jaeger = baseUrl(document, 'jaeger');
    if (!grafana && !jaeger) return;
    const links = document.createElement('div');
    links.className = 'incident-deeplinks-list';
    function add(url, label, evidenceId = null) {
      const anchor = document.createElement('a');
      anchor.className = 'incident-deeplinks-link';
      anchor.href = url.href;
      anchor.textContent = label;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      anchor.setAttribute('aria-label', `${label} (opens in a new tab)`);
      if (evidenceId) anchor.dataset.evidenceId = evidenceId;
      links.append(anchor);
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
        add(url, 'Open SLO dashboard');
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
    if (!links.childElementCount) return;
    const heading = document.createElement('h3');
    heading.className = 'incident-deeplinks-heading';
    heading.textContent = 'Explore incident telemetry';
    panel.append(heading);
    if (validWindow) {
      const window = document.createElement('p');
      window.className = 'incident-deeplinks-window';
      window.textContent = `Incident window: ${new Date(from).toISOString()} to ${new Date(to).toISOString()}`;
      panel.append(window);
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
