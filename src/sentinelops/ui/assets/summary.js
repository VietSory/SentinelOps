const openStates = new Set(['degrading','incident','recovering','escalated']);
const severityBands = ['critical','high','medium','low'];

const timestamp = value => typeof value === 'string' && value.trim() ? Date.parse(value) : NaN;
const date = value => new Date(value).toLocaleString('en-US',{
  timeZone:'UTC',year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,
})+' UTC';
const elapsed = milliseconds => {
  if (milliseconds < 0) return 'Detection time is in the future';
  const seconds = Math.floor(milliseconds/1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds/60);
  if (minutes < 60) return `${minutes}m ${seconds%60}s ago`;
  const hours = Math.floor(minutes/60);
  if (hours < 24) return `${hours}h ${minutes%60}m ago`;
  return `${Math.floor(hours/24)}d ${hours%24}h ago`;
};

export function mount(root, ctx = {}) {
  const document = root.ownerDocument;
  const clock = document.defaultView || globalThis;
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const section = node('section',undefined,'incident-summary');
  section.setAttribute('aria-label','Loaded incident summary');
  const scope = node('p',undefined,'incident-summary__scope');
  scope.setAttribute('role','status');
  const rail = node('div',undefined,'incident-summary__rail');
  rail.tabIndex = 0;
  rail.setAttribute('role','group');
  rail.setAttribute('aria-label','Incident metrics; scroll horizontally to view all cards');
  const card = (title, kind) => {
    const element = node('div',undefined,`incident-summary__card incident-summary__card--${kind}`);
    element.append(node('h3',title,'incident-summary__heading'));
    rail.append(element);
    return element;
  };
  const metric = (container, label, kind = '') => {
    const group = node('div',undefined,`incident-summary__metric ${kind ? `incident-summary__metric--${kind}` : ''}`);
    const value = node('dd','—','incident-summary__value');
    group.append(node('dt',label,'incident-summary__label'),value);
    container.append(group);
    return value;
  };
  const loadedCard = card('Loaded records','loaded');
  const loadedList = node('dl',undefined,'incident-summary__metrics');
  const loaded = metric(loadedList,'Incidents');
  loadedCard.append(loadedList);
  const severityCard = card('Severity','severity');
  const severityList = node('dl',undefined,'incident-summary__metrics');
  const critical = metric(severityList,'Critical','critical');
  const high = metric(severityList,'High','high');
  const medium = metric(severityList,'Medium');
  const otherSeverities = node('p',undefined,'incident-summary__note');
  severityCard.append(severityList,otherSeverities);
  const stateCard = card('State','state');
  const stateList = node('dl',undefined,'incident-summary__metrics');
  const open = metric(stateList,'Open');
  const recovered = metric(stateList,'Recovered','recovered');
  const otherStates = node('p',undefined,'incident-summary__note');
  stateCard.append(stateList,otherStates);
  const mttrCard = card('MTTR','mttr');
  mttrCard.append(node('p','Unavailable','incident-summary__text'),
    node('p','Recovery timestamps are not provided by the incident API.','incident-summary__note'));
  const latestCard = card('Latest detection','latest');
  const latestId = node('p','Unavailable','incident-summary__text incident-summary__id');
  const latestTime = node('time',undefined,'incident-summary__note');
  const latestAge = node('p',undefined,'incident-summary__note');
  const missingTimes = node('p',undefined,'incident-summary__note');
  latestCard.append(latestId,latestTime,latestAge,missingTimes);
  section.append(scope,rail);
  root.append(section);
  let latest = null, destroyed = false;

  function updateAge() {
    latestAge.textContent = latest === null ? '' : elapsed(Date.now()-latest);
  }

  function update(state = {}) {
    if (destroyed) return;
    const rows = Array.isArray(state.rows) ? state.rows : [];
    const available = state.session && !state.listError && Boolean(state.updatedAt) &&
      (!state.listLoading || rows.length > 0);
    section.setAttribute('aria-busy',String(Boolean(state.listLoading)));
    const suffix = state.nextCursor ? ' More records are available.' : '';
    scope.textContent = !state.session ? 'Connect to load incident records.' : state.listError ?
      'Summary unavailable: the incident request failed. Refresh to reload records.' : !available ?
      'Waiting for incident records to load.' :
      `Loaded records only: ${rows.length} for the current filters.${suffix}${state.listLoading ? ' Refreshing; showing the loaded snapshot.' : ''}${!rows.length ? ' Service health is not established by an empty list.' : ''}`;
    const severity = {critical:0,high:0,medium:0,low:0,unknown:0};
    let openCount = 0, recoveredCount = 0, unknownStates = 0, invalidTimes = 0, latestRow = null;
    latest = null;
    if (available) for (const row of rows) {
      if (severityBands.includes(row.severity)) severity[row.severity]++;
      else severity.unknown++;
      if (row.state === 'recovered') recoveredCount++;
      else if (openStates.has(row.state)) openCount++;
      else unknownStates++;
      const detected = timestamp(row.detected_at);
      if (!Number.isFinite(detected)) invalidTimes++;
      else if (latest === null || detected > latest) { latest = detected; latestRow = row; }
    }
    for (const [element, value] of [[loaded,rows.length],[critical,severity.critical],
      [high,severity.high],[medium,severity.medium],[open,openCount],[recovered,recoveredCount]]) {
      element.textContent = available ? String(value) : '—';
    }
    otherSeverities.textContent = [severity.low ? `Low (watch): ${severity.low}` : '',
      severity.unknown ? `Unknown: ${severity.unknown}` : ''].filter(Boolean).join(' · ');
    otherSeverities.hidden = !otherSeverities.textContent;
    otherStates.textContent = unknownStates ? `Unknown: ${unknownStates}` : '';
    otherStates.hidden = !unknownStates;
    latestId.textContent = latestRow ? latestRow.incident_id : available && !rows.length ? 'No incidents loaded' : 'Unavailable';
    latestId.title = latestId.textContent;
    latestTime.textContent = latest === null ? '' : date(latest);
    if (latest !== null) latestTime.dateTime = new Date(latest).toISOString();
    else latestTime.removeAttribute('datetime');
    latestTime.hidden = latest === null;
    missingTimes.textContent = invalidTimes ? `${invalidTimes} detection timestamp(s) unavailable; latest uses valid times only.` : '';
    missingTimes.hidden = !invalidTimes;
    updateAge();
  }

  update(ctx.state);
  const timer = clock.setInterval(updateAge,1000);
  return {
    update,
    destroy() {
      destroyed = true;
      clock.clearInterval(timer);
      latest = null;
      section.remove();
    },
  };
}
