const mounts = new WeakMap();
const statuses = new Set(['pending', 'stale', 'expired', 'approved', 'consumed', 'engineer', 'not_yet_valid']);
const labels = {pending:'Pending', stale:'Stale', expired:'Expired', approved:'Approved',
  consumed:'Consumed', engineer:'Engineer', not_yet_valid:'Not yet valid'};
const terminal = new Set(['approved', 'consumed', 'engineer']);
const secondsUntil = (deadline, now) => Math.max(0, Math.ceil((deadline - now) / 1000));
const timestamp = value => typeof value === 'string' ? Date.parse(value) : NaN;
const displayTime = value => new Date(value).toLocaleString('en-GB', {hour12:false});
const expiryTime = value => new Date(value).toLocaleTimeString('en-GB', {
  hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false,
});
const statusLabel = status => terminal.has(status) ? 'Decision recorded' : labels[status];

function validate(body, incidentId) {
  if (!Array.isArray(body?.items) || typeof body.has_more !== 'boolean' || body.items.length > 100) {
    throw new Error('Invalid proposal list');
  }
  const identifiers = new Set();
  for (const row of body.items) {
    const proposal = row?.proposal;
    if (proposal?.proposal_version !== 'runbook-proposal/v1' ||
        !/^[a-f0-9]{64}$/.test(proposal.proposal_id) || identifiers.has(proposal.proposal_id) ||
        !/^[a-f0-9]{64}$/.test(row.proposal_sha256) || !statuses.has(row.status) ||
        typeof proposal.incident_id !== 'string' || !proposal.incident_id ||
        (incidentId !== undefined && proposal.incident_id !== incidentId) ||
        typeof proposal.runbook_id !== 'string' || !proposal.runbook_id ||
        !Number.isSafeInteger(proposal.incident_revision) || proposal.incident_revision < 1 ||
        !Number.isSafeInteger(row.current_revision) || row.current_revision < proposal.incident_revision ||
        !Number.isFinite(timestamp(proposal.created_at)) ||
        !Number.isFinite(timestamp(proposal.expires_at)) ||
        timestamp(proposal.expires_at) <= timestamp(proposal.created_at)) {
      throw new Error('Invalid proposal row');
    }
    identifiers.add(proposal.proposal_id);
  }
  return body;
}

function rcaTime(row, resolver) {
  if (resolver) {
    try { return timestamp(resolver(row)); } catch { return NaN; }
  }
  const facts = row.incident?.evidence;
  if (!Array.isArray(facts)) return NaN;
  const clocks = facts.filter(fact => fact?.type === 'timeline' && fact.service === null &&
    typeof fact.fact === 'string' && fact.fact.startsWith('Source-verified continuous RCA hypotheses;') &&
    /^artifact:\/\/manifests\/[a-f0-9]{64}\.json$/.test(fact.source_ref))
    .map(fact => timestamp(fact.observed_at));
  return clocks.length && clocks.every(Number.isFinite) ? Math.min(...clocks) : NaN;
}

function timing(row, rca, now) {
  const proposal = row.proposal;
  const expiry = timestamp(proposal.expires_at);
  const ttl = secondsUntil(expiry, now);
  let status = row.status;
  if (!terminal.has(status) && now >= expiry) status = 'expired';
  else if (status === 'pending' && row.current_revision !== proposal.incident_revision) status = 'stale';
  const futureRca = Number.isFinite(rca) && rca > now;
  const deadline = Number.isFinite(rca) ? Math.min(expiry, rca + 900000) : NaN;
  const remaining = futureRca ? 0 : Number.isFinite(deadline) ? secondsUntil(deadline, now) : null;
  return {ttl, status, remaining, deadline, futureRca,
    reviewable:status === 'pending' && now >= timestamp(proposal.created_at) &&
      ttl > 0 && !futureRca && (remaining === null || remaining > 0)};
}

export function mount(root, ctx) {
  if (!root?.ownerDocument || typeof ctx?.client?.get !== 'function' ||
      (ctx.incidentId !== undefined && (typeof ctx.incidentId !== 'string' || !ctx.incidentId)) ||
      (ctx.onReview !== undefined && typeof ctx.onReview !== 'function') ||
      (ctx.getRcaAsOf !== undefined && typeof ctx.getRcaAsOf !== 'function')) {
    throw new TypeError('Proposal watch requires a DOM root and an authenticated same-origin client.get(path, signal).');
  }
  mounts.get(root)?.destroy();
  const document = root.ownerDocument;
  const window = document.defaultView;
  const interval = Number.isFinite(ctx.pollMs) ? Math.min(60000, Math.max(2000, ctx.pollMs)) : 5000;
  const create = (tag, className, content) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined) element.textContent = content;
    return element;
  };
  const button = content => {
    const element = create('button', 'proposalwatch-button', content);
    element.type = 'button';
    return element;
  };
  const setStatus = (element, label, status = 'neutral', icon = '?') => {
    if (element.dataset.status === status && element.dataset.label === label) return;
    element.dataset.status = status; element.dataset.label = label;
    const symbol = create('span','status-chip__icon',icon); symbol.setAttribute('aria-hidden','true');
    element.replaceChildren(symbol,create('span','status-chip__label',label));
  };
  const panel = create('section', 'proposalwatch');
  panel.setAttribute('aria-label', 'Runbook proposal watch');
  const header = create('div', 'proposalwatch-header');
  const heading = create('h2', 'proposalwatch-title', 'Proposal watch');
  const humanDecision = create('span','proposalwatch-description','Human approval required.');
  const controls = create('div', 'proposalwatch-controls');
  const notificationsButton = button('Browser alerts');
  const refreshButton = button('↻');
  refreshButton.setAttribute('aria-label','Refresh proposals now'); refreshButton.title = 'Refresh proposals now';
  const help = create('details','help-disclosure');
  const helpToggle = create('summary');
  const helpIcon = create('span','info-icon','i'); helpIcon.setAttribute('aria-hidden','true');
  helpToggle.append(helpIcon,create('span','sr-only','About proposal watch'));
  const helpBody = create('div','help-disclosure__body');
  help.append(helpToggle,helpBody);
  controls.append(refreshButton,help);
  const notificationStatus = create('p', 'proposalwatch-meta');
  const connection = create('span', 'status-chip proposalwatch-connection');
  setStatus(connection,'Checking proposals…');
  connection.setAttribute('role', 'status');
  header.append(heading,connection,humanDecision,controls);
  const connectionDetail = create('p','proposalwatch-meta');
  const announcement = create('p', 'sr-only proposalwatch-announcement');
  announcement.setAttribute('role', 'status');
  announcement.setAttribute('aria-live', 'polite');
  const coverage = create('p', 'status-chip proposalwatch-warning');
  coverage.hidden = true;
  const explanation = create('p', 'proposalwatch-meta',
    'Approval can close at proposal expiry or when RCA evidence reaches its 900-second freshness limit. The server also checks current evidence and revision.');
  const list = create('div', 'proposalwatch-list');
  const empty = create('p', 'proposalwatch-empty', 'Waiting for the first successful check.');
  const history = create('details','proposalwatch-history'); history.hidden = true;
  const historyToggle = create('summary');
  const historyList = create('div','proposalwatch-list');
  history.append(historyToggle,historyList);
  helpBody.append(create('p', 'proposalwatch-meta', 'New proposals need your review. Approval is always a separate human decision.'),
    notificationsButton,notificationStatus,explanation,connectionDetail,empty);
  panel.append(header,announcement,coverage,list,history);
  root.append(panel);

  const seen = new Map();
  const cards = new Map();
  const notifications = new Set();
  let disposed = false;
  let initialized = false;
  let healthy = false;
  let lastSuccess = null;
  let pollTimer = null;
  let active = null;
  let permissionBusy = false;
  let permissionError = false;

  function notificationState() {
    const available = window.isSecureContext && typeof window.Notification === 'function';
    const permission = available ? window.Notification.permission : 'unavailable';
    notificationsButton.disabled = permissionBusy || permission !== 'default';
    notificationsButton.textContent = permissionBusy ? 'Requesting…' :
      permission === 'granted' ? 'Alerts on' :
      permission === 'denied' ? 'Alerts blocked' :
      permission === 'unavailable' ? 'Alerts unavailable' : 'Browser alerts';
    notificationsButton.setAttribute('aria-label',permission === 'default' ? 'Enable browser notifications' : notificationsButton.textContent);
    notificationStatus.textContent = permission === 'granted' ? 'Browser alerts are enabled. In-page alerts stay active.' :
      permission === 'denied' ? 'Notifications were blocked. In-page alerts stay active; change browser site settings to allow browser alerts.' :
      permission === 'unavailable' ? 'Browser notifications require a supported browser and a secure context. In-page alerts stay active.' :
      permissionError ? 'Browser permission could not be enabled. In-page alerts stay active; you can try again.' :
      'Browser alerts are optional. Permission is requested only when you select Enable browser notifications.';
    notificationsButton.title = notificationStatus.textContent;
  }

  async function enableNotifications() {
    if (permissionBusy || window.Notification?.permission !== 'default' || !window.isSecureContext) return;
    permissionBusy = true;
    permissionError = false;
    try {
      const permission = window.Notification.requestPermission();
      notificationState();
      await permission;
    } catch { permissionError = true; }
    finally {
      permissionBusy = false;
      if (!disposed) notificationState();
    }
  }

  function notify(entry) {
    if (!window.isSecureContext || window.Notification?.permission !== 'granted') return;
    const state = timing(entry.row, entry.rca, Date.now());
    try {
      const notification = new window.Notification('New runbook proposal', {
        body:`${entry.row.proposal.incident_id}: ${statusLabel(state.status)}. ` +
          `Expires ${expiryTime(timestamp(entry.row.proposal.expires_at))}.`,
        tag:`sentinelops-proposal-${entry.row.proposal.proposal_id}`,
      });
      notifications.add(notification);
      notification.onclose = () => notifications.delete(notification);
      notification.onclick = () => {
        if (!disposed) {
          window.focus();
          if (entry.card.parentNode === historyList) history.open = true;
          entry.card.scrollIntoView({block:'nearest'});
          entry.card.focus();
        }
        notification.close();
      };
    } catch {
      notificationStatus.textContent = 'The browser could not show an alert. In-page alerts stay active.';
    }
  }

  function makeCard(row, firstSeen, isNew) {
    const card = create('article', 'proposalwatch-card');
    card.tabIndex = -1;
    const titlebar = create('div', 'proposalwatch-card-header');
    const title = create('h3', 'proposalwatch-card-title');
    const badge = create('span', 'status-chip proposalwatch-badge');
    titlebar.append(title, badge);
    const identity = create('p', 'proposalwatch-meta');
    const discovered = create('p', 'proposalwatch-discovered');
    const expiry = create('p', 'proposalwatch-meta');
    const clocks = create('p', 'proposalwatch-meta');
    const reason = create('p', 'proposalwatch-reason');
    const reasonDetail = create('p','proposalwatch-meta');
    const receipt = create('p', 'proposalwatch-meta');
    const details = create('details','help-disclosure proposalwatch-details');
    const detailsToggle = create('summary',null,'Proposal details');
    const detailsBody = create('div','help-disclosure__body');
    detailsBody.append(identity,discovered,clocks,reasonDetail,receipt);
    details.append(detailsToggle,detailsBody);
    const actions = create('div', 'proposalwatch-controls');
    const review = ctx.onReview ? button('Open incident →') : null;
    review?.setAttribute('aria-label','Open incident proposal review');
    const acknowledge = button('Mark as seen');
    if (review) actions.append(review);
    actions.append(acknowledge);
    actions.append(details);
    card.append(titlebar,expiry,reason,actions);
    const entry = {row, rca:NaN, firstSeen, isNew, card, title, badge, identity, discovered,
      expiry,clocks,reason,reasonDetail,receipt,review,acknowledge};
    acknowledge.addEventListener('click', () => {
      entry.isNew = false;
      updateCard(entry, Date.now());
      announceNew();
      if (review && !review.disabled) review.focus();
      else card.focus();
    });
    review?.addEventListener('click', async () => {
      if (disposed || !healthy || !timing(entry.row, entry.rca, Date.now()).reviewable || entry.opening) return;
      entry.opening = true;
      updateCard(entry, Date.now());
      try { await ctx.onReview(structuredClone(entry.row)); }
      catch {
        if (!disposed) announcement.textContent = 'Proposal review could not be opened. Try opening the incident again.';
      } finally {
        entry.opening = false;
        if (!disposed) updateCard(entry, Date.now());
      }
    });
    return entry;
  }

  function updateCard(entry, now) {
    const row = entry.row;
    const proposal = row.proposal;
    const state = timing(row, entry.rca, now);
    const urgency = state.status === 'pending' ? 'warning' : state.status === 'expired' ? 'critical' :
        state.status === 'stale' ? 'serious' : 'neutral';
    entry.card.dataset.urgency = urgency;
    entry.card.dataset.status = state.status;
    entry.card.dataset.new = String(entry.isNew);
    entry.title.textContent = `${proposal.incident_id} · ${proposal.runbook_id}`;
    const badgeStatus = state.status === 'approved' ? 'good' : state.status === 'not_yet_valid' || state.status === 'engineer' ? 'warning' :
      urgency;
    const badgeLabel = statusLabel(state.status);
    setStatus(entry.badge,badgeLabel,badgeStatus,{good:'✓',warning:'!',serious:'!!',critical:'×',neutral:'?'}[badgeStatus]);
    entry.identity.textContent = `Proposal ${proposal.proposal_id} · revision ${proposal.incident_revision}`;
    entry.discovered.textContent = `${entry.isNew ? 'NEW · ' : ''}Created ${displayTime(timestamp(proposal.created_at))} · ` +
      `First seen ${displayTime(entry.firstSeen)} (this watch session)`;
    entry.expiry.textContent = `Expires ${expiryTime(timestamp(proposal.expires_at))}`;
    entry.clocks.textContent = Number.isFinite(entry.rca) ?
      `RCA time: ${displayTime(entry.rca)} · freshness limit ${displayTime(entry.rca + 900000)} (900s)` :
      'The RCA clock is unavailable. The proposal TTL alone does not establish the usable approval window.';
    let reason = '';
    if (!terminal.has(state.status) && row.current_revision !== proposal.incident_revision) {
      reason = `Incident revision advanced from ${proposal.incident_revision} to ${row.current_revision}. ` +
        'This proposal targets the older revision and cannot be approved. Reload the current incident and review a new proposal.';
    } else if (state.status === 'stale') {
      reason = 'The server marked this proposal stale. Approval is blocked; reload the current incident and review a new proposal.';
    } else if (state.status === 'expired') {
      reason = 'The proposal TTL has ended. Wait for a new proposal and review the current incident.';
    } else if (state.status === 'not_yet_valid' || now < timestamp(proposal.created_at)) {
      reason = 'The proposal is not yet valid. Check the local clock and wait for the next server update.';
    } else if (state.futureRca) {
      reason = 'The RCA timestamp is ahead of the local clock. Check clock synchronization before reviewing.';
    } else if (!terminal.has(state.status) && state.remaining === 0) {
      reason = `RCA evidence reached its 900-second freshness limit even though the proposal TTL may remain. ` +
        `Server status: ${labels[row.status]}. Refresh the incident and obtain fresh evidence before reviewing.`;
    } else if (state.status === 'engineer') {
      reason = row.approval?.decision === 'reject' ? 'Rejected by the operator; engineer review is required.' :
        'The controller escalated this proposal; engineer review is required.';
    } else if (state.status === 'consumed') {
      reason = 'The approval has been consumed. Check action history for the execution result.';
    } else if (state.status === 'approved') {
      reason = 'Approval recorded. This does not establish that execution has completed.';
    }
    if (row.outcome?.reason && typeof row.outcome.reason === 'string') reason += ` Controller reason: ${row.outcome.reason}.`;
    entry.reasonDetail.textContent = reason;
    entry.reasonDetail.hidden = !reason;
    entry.reason.textContent = reason;
    entry.reason.hidden = !reason;
    entry.receipt.textContent = row.approval?.decision ? `Recorded decision: ${row.approval.decision}` : '';
    entry.receipt.hidden = !entry.receipt.textContent;
    entry.acknowledge.hidden = !entry.isNew;
    if (entry.review) {
      entry.review.disabled = !healthy || !state.reviewable || Boolean(entry.opening);
      entry.review.title = !healthy ? 'Proposal updates unavailable. Refresh before review.' :
        reason || 'Open the incident and review this proposal. Approval is a separate human decision.';
    }
  }

  function updateLayout(now) {
    let pending = 0, blocked = 0, archived = 0;
    for (const entry of cards.values()) {
      const state = timing(entry.row,entry.rca,now);
      const historical = terminal.has(state.status) || state.status === 'expired';
      if (state.status === 'pending') pending++;
      else if (!historical) blocked++;
      if (historical) archived++;
      const target = historical ? historyList : list;
      if (entry.card.parentNode !== target) target.append(entry.card);
    }
    list.hidden = pending + blocked === 0;
    history.hidden = archived === 0;
    historyToggle.textContent = `Proposal history (${archived})`;
    panel.dataset.compact = String(pending + blocked === 0);
    humanDecision.hidden = pending + blocked === 0;
    if (healthy) {
      setStatus(connection,pending ? `${pending} pending review` : blocked ? `${blocked} review blocked` :
        coverage.hidden ? 'No pending proposals' : 'No pending in loaded proposals',pending ? 'warning' : blocked ? 'serious' : 'neutral',
        pending || blocked ? '!' : '—');
    }
  }

  function tick() {
    const now = Date.now();
    for (const entry of cards.values()) updateCard(entry, now);
    updateLayout(now);
  }

  function announceNew() {
    const highlighted = [...cards.values()].filter(entry => entry.isNew);
    announcement.textContent = highlighted.length ?
      `${highlighted.length} new proposal${highlighted.length === 1 ? '' : 's'} detected. ` +
      highlighted.map(entry => entry.row.proposal.incident_id).join(', ') + '. Review the highlighted cards.' : '';
  }

  async function refresh() {
    if (disposed || active) return;
    window.clearTimeout(pollTimer);
    const request = {controller:new AbortController()};
    active = request;
    refreshButton.disabled = true;
    const path = '/v1/runbook-proposals?limit=100' +
      (ctx.incidentId === undefined ? '' : `&incident_id=${encodeURIComponent(ctx.incidentId)}`);
    let timeout;
    try {
      const failure = new Promise((resolve, reject) => {
        request.cancel = () => reject(new Error('Cancelled'));
        timeout = window.setTimeout(() => {
          request.controller.abort();
          reject(new Error('Timed out'));
        }, 10000);
      });
      const body = validate(await Promise.race([ctx.client.get(path, request.controller.signal), failure]), ctx.incidentId);
      if (disposed) return;
      const now = Date.now();
      const incoming = new Set(body.items.map(row => row.proposal.proposal_id));
      const added = [];
      healthy = true;
      lastSuccess = now;
      for (const [identifier, entry] of cards) {
        if (!incoming.has(identifier)) { entry.card.remove(); cards.delete(identifier); }
      }
      for (const row of body.items) {
        const identifier = row.proposal.proposal_id;
        const unseen = !seen.has(identifier);
        if (unseen) seen.set(identifier, now);
        let entry = cards.get(identifier);
        if (!entry) {
          entry = makeCard(row, seen.get(identifier), unseen && (initialized || !terminal.has(row.status) && row.status !== 'expired'));
          cards.set(identifier, entry);
          list.append(entry.card);
        }
        entry.row = structuredClone(row);
        entry.rca = rcaTime(entry.row, ctx.getRcaAsOf);
        updateCard(entry, now);
        if (entry.isNew && unseen) added.push(entry);
      }
      // Classify cards before reordering so insertion references stay in their own list.
      updateLayout(now);
      const positions = new Map([[list,list.firstElementChild],[historyList,historyList.firstElementChild]]);
      for (const row of body.items) {
        const card = cards.get(row.proposal.proposal_id).card;
        const status = timing(row,cards.get(row.proposal.proposal_id).rca,now).status;
        const target = terminal.has(status) || status === 'expired' ? historyList : list;
        const position = positions.get(target);
        if (card !== position) target.insertBefore(card,position);
        positions.set(target,card.nextElementSibling);
      }
      initialized = true;
      coverage.hidden = !body.has_more;
      setStatus(coverage,'Partial coverage · latest 100 only','warning','!');
      coverage.title = 'Partial coverage: only the latest 100 proposals are returned. Older proposals may be missing. Filter by incident for a complete review.';
      empty.hidden = cards.size > 0;
      empty.textContent = 'No proposals returned by the server.';
      connection.dataset.state = 'connected';
      connectionDetail.textContent = `Last successful check: ${displayTime(now)} · polling every ${interval / 1000}s. ` +
        'Expiry and review safeguards use the local clock; background tabs may delay alerts.';
      connection.title = connectionDetail.textContent;
      updateLayout(now);
      announceNew();
      for (const entry of added) notify(entry);
    } catch {
      if (disposed) return;
      healthy = false;
      connection.dataset.state = 'error';
      setStatus(connection,'Proposal status unknown','warning','!');
      connectionDetail.textContent = 'Proposal updates unavailable. ' +
        (lastSuccess === null ? 'No proposals have been verified.' : `Last successful check: ${displayTime(lastSuccess)}. Displayed data may be outdated.`) +
        ' Retrying automatically; refresh before review.';
      connection.title = connectionDetail.textContent;
      empty.textContent = 'Proposal status is unknown until a successful check.';
      tick();
    } finally {
      window.clearTimeout(timeout);
      active = null;
      if (!disposed) {
        refreshButton.disabled = false;
        pollTimer = window.setTimeout(refresh, interval);
      }
    }
  }

  function resume() {
    if (document.visibilityState === 'visible') { tick(); notificationState(); void refresh(); }
  }

  function destroy() {
    if (disposed) return;
    disposed = true;
    window.clearTimeout(pollTimer);
    window.clearInterval(ticker);
    active?.controller.abort();
    active?.cancel();
    for (const notification of notifications) notification.close();
    notifications.clear();
    document.removeEventListener('visibilitychange', resume);
    window.removeEventListener('focus', resume);
    ctx.signal?.removeEventListener('abort', destroy);
    notificationsButton.removeEventListener('click', enableNotifications);
    refreshButton.removeEventListener('click', refresh);
    panel.remove();
    cards.clear();
    seen.clear();
    mounts.delete(root);
  }

  const ticker = window.setInterval(tick, 1000);
  const handle = {refresh, destroy};
  mounts.set(root, handle);
  notificationsButton.addEventListener('click', enableNotifications);
  refreshButton.addEventListener('click', refresh);
  document.addEventListener('visibilitychange', resume);
  window.addEventListener('focus', resume);
  ctx.signal?.addEventListener('abort', destroy, {once:true});
  updateLayout(Date.now());
  notificationState();
  if (ctx.signal?.aborted) destroy();
  else void refresh();
  return handle;
}
