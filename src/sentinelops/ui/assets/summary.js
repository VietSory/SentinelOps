const openStates = new Set(['degrading','incident','recovering','escalated']);
const severityBands = [
  ['critical','Critical','critical','!'],
  ['high','High','serious','◆'],
  ['medium','Medium','warning','▲'],
  ['low','Low (watch)','good','○'],
  ['unknown','Unknown','neutral','?'],
];

const incidentSeverity = {low:'Thấp',medium:'Trung bình',high:'Cao',critical:'Nghiêm trọng'};
const incidentState = {degrading:'Đang suy giảm',incident:'Sự cố',recovering:'Đang phục hồi',recovered:'Đã phục hồi',escalated:'Đã leo thang'};
const finite = value => typeof value === 'number' && Number.isFinite(value);
const decimal = (value, digits = 2) => value.toFixed(digits).replace('.',',');
const shortService = value => value.replace(/-service$/,'');
const parsedFact = (item, key) => {
  if (typeof item?.fact !== 'string') return null;
  const match = item.fact.match(new RegExp(`(?:^|[;:]\\s*|\\s)${key}=([0-9]+(?:\\.[0-9]+)?)(?=;|$)`));
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
};
const evidenceScore = (evidence, key) => {
  for (const item of evidence) {
    if (item?.type !== 'model' || typeof item.service !== 'string') continue;
    const value = parsedFact(item,key);
    if (value !== null) return {service:item.service,value};
  }
  return null;
};

/** Client-only, deterministic incident synopsis. Every number comes from the IER or loaded action state. */
export function incidentSummaryLines(payload = {}, loaded = {}) {
  const lines = [];
  const services = Array.isArray(payload.affected_services) ?
    payload.affected_services.filter(value => typeof value === 'string' && value) : [];
  if (services.length) lines.push(`Dịch vụ bị ảnh hưởng: ${services.join(', ')}`);

  const severity = incidentSeverity[payload.severity];
  const state = incidentState[payload.state];
  if (severity || state) lines.push(`Mức độ/trạng thái: ${[severity,state].filter(Boolean).join(' · ')}`);

  const ranking = Array.isArray(payload.root_cause_ranking) ? payload.root_cause_ranking : [];
  const likely = ranking.find(item => typeof item?.service === 'string' && finite(item.score));
  if (likely) lines.push(`Nguyên nhân nhiều khả năng: ${likely.service} (điểm ${decimal(likely.score)})`);

  const evidence = Array.isArray(payload.evidence) ? payload.evidence : [];
  const server = evidence.find(item => item?.type === 'trace' &&
    (!likely || item.service === likely.service) && parsedFact(item,'server_duration_ns') !== null);
  const serverNs = parsedFact(server,'server_duration_ns');
  if (serverNs !== null) {
    const database = evidence.find(item => item?.type === 'trace' &&
      ['rds','postgres','postgresql'].includes(item.service) && parsedFact(item,'postgresql_client_duration_ns') !== null);
    const databaseNs = parsedFact(database,'postgresql_client_duration_ns');
    const root = server.service || likely?.service;
    lines.push(`Bằng chứng chính: trace cho thấy ${root} xử lý ~${decimal(serverNs/1_000_000,serverNs%1_000_000 ? 1 : 0)} ms${
      databaseNs === null ? '' : ` trong khi DB ~${decimal(databaseNs/1_000_000,databaseNs%1_000_000 ? 1 : 0)} ms`}`);
  }

  const model = new Map();
  for (const signal of Array.isArray(payload.service_signals) ? payload.service_signals : []) {
    if (typeof signal?.service !== 'string') continue;
    model.set(signal.service,{
      anomaly:finite(signal.anomaly_score) ? signal.anomaly_score : null,
      risk:finite(signal.degradation_risk) ? signal.degradation_risk : null,
    });
  }
  const riskFact = evidenceScore(evidence,'risk_score');
  const anomalyFact = evidenceScore(evidence,'anomaly_score');
  if (riskFact) {
    const entry = model.get(riskFact.service) || {risk:null,anomaly:null};
    if (entry.risk === null) entry.risk = riskFact.value;
    model.set(riskFact.service,entry);
  }
  if (anomalyFact) {
    const entry = model.get(anomalyFact.service) || {risk:null,anomaly:null};
    if (entry.anomaly === null) entry.anomaly = anomalyFact.value;
    model.set(anomalyFact.service,entry);
  }
  const risk = [...model].find(([,value]) => value.risk !== null);
  const anomaly = risk && risk[1].anomaly !== null ? [risk[0],risk[1].anomaly] :
    [...model].map(([name,value]) => [name,value.anomaly]).find(([,value]) => value !== null);
  if (risk || anomaly) {
    const parts = [];
    if (risk) parts.push(`XGBoost rủi ro ${decimal(risk[1].risk)}`);
    if (anomaly) parts.push(`Isolation Forest bất thường ${decimal(anomaly[1])}`);
    const sameService = risk && anomaly && risk[0] === anomaly[0];
    if (sameService) parts[parts.length-1] += ` (${shortService(risk[0])})`;
    else {
      if (risk) parts[0] += ` (${shortService(risk[0])})`;
      if (anomaly) parts[parts.length-1] += ` (${shortService(anomaly[0])})`;
    }
    lines.push(`Model: ${parts.join('; ')}`);
  }

  const actions = Array.isArray(loaded.actions?.items) ? loaded.actions.items : null;
  const proposals = Array.isArray(loaded.proposals) ? loaded.proposals : null;
  if (actions?.length) {
    const action = actions[0];
    lines.push(`Trạng thái xử lý: ${action.runbook || 'action'} · pha ${action.phase}`);
  } else if (proposals) {
    const proposal = proposals.find(item => item?.status === 'pending') || proposals[0];
    if (!proposal) {
      const hasDeploymentChange = evidence.some(item => item?.type === 'change');
      lines.push(hasDeploymentChange ? 'Cần engineer xử lý (chưa có đề xuất hành động)' :
        'Cần engineer xử lý (không có bằng chứng thay đổi deployment để rollback)');
    } else {
      const rollback = String(proposal.proposal?.runbook_id || proposal.proposal?.executor_runbook || '').includes('rollback');
      const action = rollback ? 'rollback' : 'runbook';
      const status = {pending:'chờ duyệt',approved:'đã được duyệt',consumed:'đã chuyển cho controller',
        engineer:'đã chuyển engineer',expired:'đã hết hạn',stale:'không còn mới',not_yet_valid:'chưa có hiệu lực'}[proposal.status] || proposal.status;
      lines.push(`Hướng xử lý: đề xuất ${action} ${status}`);
    }
  }
  return lines.slice(0,6);
}

const timestamp = value => typeof value === 'string' && value.trim() ? Date.parse(value) : NaN;
const date = value => new Date(value).toLocaleString('en-US',{
  timeZone:'UTC',year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,
})+' UTC';
const elapsed = milliseconds => {
  if (milliseconds < 0) return 'Future timestamp';
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
  const context = node('div',undefined,'incident-summary__context');
  const scope = node('p',undefined,'incident-summary__scope');
  scope.setAttribute('role','status');
  const help = (label, kind, iconOnly = false) => {
    const element = node('details',undefined,`help-disclosure incident-summary__help incident-summary__help--${kind}`);
    const trigger = node('summary');
    const icon = node('span','i','info-icon');
    icon.setAttribute('aria-hidden','true');
    trigger.append(icon,node('span',label,iconOnly ? 'sr-only' : undefined));
    element.append(trigger);
    context.append(element);
    return element;
  };
  context.append(scope);
  const notes = help('Metric details','scope',true);
  const notesBody = node('div',undefined,'help-disclosure__body');
  const scopeNote = node('p');
  const stateNote = node('p');
  const missingTimes = node('p');
  notesBody.append(scopeNote,stateNote,missingTimes,
    node('p','Low is a watch-level incident, not a service health signal.'));
  notes.append(notesBody);
  const mttr = help('MTTR: Unavailable','mttr');
  mttr.append(node('p','No recovery timestamps in API.','help-disclosure__body'));

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
  const metric = (container, label) => {
    const list = node('dl',undefined,'incident-summary__metrics');
    const value = node('dd','Unknown','incident-summary__value');
    list.append(node('dt',label,'incident-summary__label'),value);
    container.append(list);
    return value;
  };
  const loadedCard = card('Total incidents','loaded');
  const loaded = metric(loadedCard,'Loaded records');
  const severityCard = card('Severity mix','severity');
  const severityEmpty = node('p','Unknown','incident-summary__empty');
  // SVG geometry attributes preserve exact proportions without inline styles under CSP.
  const bar = document.createElementNS('http://www.w3.org/2000/svg','svg');
  bar.setAttribute('class','incident-summary__bar');
  bar.setAttribute('viewBox','0 0 100 12');
  bar.setAttribute('preserveAspectRatio','none');
  bar.setAttribute('aria-hidden','true');
  const legend = node('dl',undefined,'incident-summary__legend');
  const bands = severityBands.map(([key,label,status,icon]) => {
    const segment = document.createElementNS('http://www.w3.org/2000/svg','rect');
    segment.setAttribute('class',`incident-summary__segment incident-summary__segment--${status}`);
    segment.setAttribute('y','0');
    segment.setAttribute('height','12');
    bar.append(segment);
    const group = node('div',undefined,`incident-summary__metric incident-summary__metric--${key}`);
    const term = node('dt',undefined,'status-chip');
    term.dataset.status = status;
    const symbol = node('span',icon,'status-chip__icon');
    symbol.setAttribute('aria-hidden','true');
    term.append(symbol,node('span',label,'status-chip__label'));
    const value = node('dd');
    group.append(term,value);
    legend.append(group);
    return {key,segment,group,value};
  });
  severityCard.append(severityEmpty,bar,legend);

  const openCard = card('Open','state');
  const open = metric(openCard,'In loaded records');
  const openLabel = open.previousElementSibling;

  const latestCard = card('Latest incident','latest');
  const latestAge = node('p','Unknown','incident-summary__value incident-summary__age');
  const latestId = node('p',undefined,'incident-summary__id');
  const latestTime = node('time',undefined,'incident-summary__note');
  latestCard.append(latestAge,latestId,latestTime);
  section.append(context,rail);
  root.append(section);
  let latest = null, destroyed = false;

  function updateAge() {
    if (latest !== null) latestAge.textContent = elapsed(Date.now()-latest);
  }

  function update(state = {}) {
    if (destroyed) return;
    const rows = Array.isArray(state.rows) ? state.rows : [];
    const available = state.session && !state.listError && Boolean(state.updatedAt) &&
      (!state.listLoading || rows.length > 0);
    section.setAttribute('aria-busy',String(Boolean(state.listLoading)));
    section.dataset.availability = available ? 'available' : 'unknown';
    scope.textContent = !state.session ? 'Not connected · Counts unknown' : state.listError ?
      'Request failed · Counts unknown; this does not mean no incidents.' : !available ?
      'Loading · Counts unknown' :
      `Loaded records only · Current filters${state.nextCursor ? ' · More available' : ''}${state.listLoading ? ' · Refreshing snapshot' : ''}${!rows.length ? ' · Health unknown' : ''}`;
    scopeNote.textContent = !state.session ? 'Connect to load incident records.' : state.listError ?
      'The incident request failed. This does not mean there are no incidents. Refresh to reload records.' : !available ?
      'Waiting for incident records to load.' :
      `Summary covers ${rows.length} loaded records for the current filters, not all incidents.${state.nextCursor ? ' More records are available.' : ''}${state.listLoading ? ' Refreshing; showing the loaded snapshot.' : ''}${!rows.length ? ' Service health is not established by an empty list.' : ''}`;
    const severity = {critical:0,high:0,medium:0,low:0,unknown:0};
    let openCount = 0, recoveredCount = 0, unknownStates = 0, invalidTimes = 0, latestRow = null;
    latest = null;
    if (available) for (const row of rows) {
      if (Object.hasOwn(severity,row.severity)) severity[row.severity]++;
      else severity.unknown++;
      if (row.state === 'recovered') recoveredCount++;
      else if (openStates.has(row.state)) openCount++;
      else unknownStates++;
      const detected = timestamp(row.detected_at);
      if (!Number.isFinite(detected)) invalidTimes++;
      else if (latest === null || detected > latest) { latest = detected; latestRow = row; }
    }
    loaded.textContent = available ? String(rows.length) : 'Unknown';
    open.textContent = available && !unknownStates ? String(openCount) : 'Unknown';
    openLabel.textContent = unknownStates ? `${openCount} confirmed open` : 'In loaded records';
    stateNote.textContent = available ?
      `Open: ${openCount} (degrading, incident, recovering or escalated). Recovered: ${recoveredCount}.${unknownStates ? ` Unknown state: ${unknownStates}; the open total is unknown.` : ''}` :
      'Incident states are unknown until records load successfully.';
    let offset = 0;
    const hasDistribution = available && rows.length > 0;
    for (const {key,segment,group,value} of bands) {
      const width = hasDistribution ? severity[key]/rows.length*100 : 0;
      segment.setAttribute('x',String(offset));
      segment.setAttribute('width',String(width));
      offset += width;
      group.hidden = !hasDistribution || !severity[key];
      value.textContent = hasDistribution ? String(severity[key]) : '';
    }
    legend.hidden = !hasDistribution;
    bar.toggleAttribute('hidden',!hasDistribution);
    severityEmpty.hidden = hasDistribution;
    severityEmpty.textContent = available ? 'No data' : 'Unknown';
    latestAge.textContent = !available ? 'Unknown' : !rows.length ? 'None loaded' : latest === null ? 'Unavailable' : '';
    latestId.textContent = latestRow ? latestRow.incident_id : '';
    latestId.title = latestId.textContent;
    latestId.hidden = !latestRow;
    latestTime.textContent = latest === null ? '' : date(latest);
    if (latest !== null) latestTime.dateTime = new Date(latest).toISOString();
    else latestTime.removeAttribute('datetime');
    latestTime.hidden = latest === null;
    missingTimes.textContent = invalidTimes ? `${invalidTimes} detection timestamp(s) unavailable; latest uses valid times only.` : '';
    if (latest !== null && latest > Date.now()) missingTimes.textContent += ' Detection time is in the future; check the source clock.';
    missingTimes.hidden = !missingTimes.textContent;
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
