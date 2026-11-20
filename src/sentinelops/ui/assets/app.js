import {Workspace, proposalRemaining} from './workspace.js';
import {CognitoLogin, IncidentClient} from './client.js';
import {mount as mountDeeplinks} from './deeplinks.js';
import {mount as mountFilters} from './filters.js';
import {mount as mountSummary} from './summary.js';
import {mount as mountProposalwatch} from './proposalwatch.js';

const loginCallback = location.pathname === '/ui/callback' ? location.href : null;
if (loginCallback) history.replaceState(null,'','/ui/');
let cognito = null, manualAllowed = false;

const $ = selector => document.querySelector(selector);
const node = (tag, text = null, className = '') => {
  const item = document.createElement(tag);
  if (text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
};
const raw = (tag,text,className = '') => { const item=node(tag,null,className); item.textContent=String(text); return item; };
const button = (text, action, className = 'button quiet') => {
  const item = node('button',text,className); item.type = 'button'; item.addEventListener('click',action); return item;
};
const stateLabels = {incident:'Incident',degrading:'Degrading',recovering:'Recovering',recovered:'Recovered',escalated:'Escalated'};
const severityLabels = {low:'Low',medium:'Medium',high:'High',critical:'Critical'};
const severityTones = {low:'good',medium:'warning',high:'serious',critical:'critical'};
const stateTones = {incident:'critical',degrading:'serious',recovering:'warning',recovered:'good',escalated:'critical'};
const statusIcons = {good:'✓',warning:'!',serious:'▲',critical:'✕',neutral:'?'};
const statusChip = (label, tone) => {
  const item = node('span',null,`status-chip status-chip--${tone}`);
  item.dataset.status = tone;
  const icon = node('span',statusIcons[tone],'status-chip__icon'); icon.setAttribute('aria-hidden','true');
  item.append(icon,node('span',label,'status-chip__label'));
  return item;
};
const badge = (value, kind) => {
  const labels = kind === 'severity' ? severityLabels : stateLabels;
  const tones = kind === 'severity' ? severityTones : stateTones;
  const known = Object.hasOwn(labels,value), tone = known ? tones[value] : 'neutral';
  const item = statusChip(known ? labels[value] : `Unknown ${kind}`,tone);
  item.title = `${kind === 'severity' ? 'Severity' : 'State'}: ${known ? labels[value] : value || 'not recorded'}`;
  return item;
};
const date = value => {
  if (typeof value !== 'string' || !value) return 'Unavailable';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Unavailable' : parsed.toLocaleString('en-US',{timeZone:'UTC',
    year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})+' UTC';
};
const expiryTime = value => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Unavailable' : parsed.toLocaleTimeString('en-GB', {
    hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false,
  });
};
const proposalStatus = status => ['approved','consumed','engineer'].includes(status) ? 'Decision recorded' :
  ({pending:'Pending',expired:'Expired',stale:'Stale',not_yet_valid:'Not yet valid'}[status] || status);
const time = value => { const item = node('time',date(value)); if (value) item.dateTime = value; return item; };
const relativeTime = value => {
  const item = time(value), timestamp = typeof value === 'string' && value ? Date.parse(value) : NaN;
  item.title = date(value);
  if (!Number.isFinite(timestamp)) { item.textContent = 'Time unknown'; return item; }
  item.dataset.relativeTime = value;
  updateRelativeTime(item);
  return item;
};
function updateRelativeTime(item) {
  const delta = Date.now() - Date.parse(item.dataset.relativeTime), elapsed = Math.abs(delta);
  const unit = elapsed < 60000 ? 's' : elapsed < 3600000 ? 'm' : elapsed < 86400000 ? 'h' : 'd';
  const count = Math.floor(elapsed / ({s:1000,m:60000,h:3600000,d:86400000}[unit]));
  item.textContent = delta < 0 ? `in ${count}${unit}` : `${count}${unit} ago`;
}
const short = (value, width = 26) => value.length > width ? value.slice(0,width-7)+'…'+value.slice(-6) : value;
const score = value => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) : 'Unavailable';
const serviceClass = value => ({'checkout-service':'service-checkout','payment-service':'service-payment','inventory-service':'service-inventory'}[value] || '');
const service = value => String(value).replace(/-service$/,'').replace(/^[a-z]/,letter => letter.toUpperCase());
const empty = (title, description, detail = false) => {
  const item = node('div',null,`empty ${detail ? 'detail-empty' : ''}`);
  const icon = node('div',detail ? '⌕' : '▤','empty-icon'); icon.setAttribute('aria-hidden','true');
  item.append(icon,node('h3',title),node('p',description)); return item;
};
function showError(container, error) {
  container.replaceChildren(); container.hidden = !error;
  if (!error) return;
  container.append(node('span',error.message));
  if (error.requestId) container.append(node('code',`Request ${error.requestId}`));
}
function status(container, value, yes, no) {
  const kind = value === true ? 'good' : value === false ? 'warning' : 'neutral';
  container.dataset.status = kind;
  const dot = node('span',null,`status-dot ${kind}`);
  container.replaceChildren(dot,document.createTextNode(value === true ? yes : value === false ? no : 'Unknown'));
}

let tab = 'overview', highlightedEvidence = null, previous = {};
let proposalClient = null, watcher = null, sessionAbort = new AbortController();
const handles = [];
const workspace = new Workspace(render,token => {
  proposalClient = new IncidentClient(token);
  return proposalClient;
});
const ctx = Object.freeze({
  get state() { return workspace.state; },
  getState: () => workspace.state,
  getDetail: () => workspace.state.detail,
  resetFilters: () => { tab = 'overview'; return workspace.filter({state:'',service:''}); },
  onChange: result => {
    renderList({...workspace.state,rows:result.rows});
    const unavailable = !result.total && ['error','disconnected','loading'].includes(result.status);
    $('#loaded-count').textContent = unavailable ? result.status === 'error' ? 'Results unavailable' : 'Unknown' :
      `${result.matched} of ${result.total}`;
  },
  onReview: async row => {
    await openIncident(row.proposal.incident_id,row.proposal.incident_revision);
    $('#detail-pane [data-testid=runbook-proposals]')?.scrollIntoView({block:'nearest'});
  },
  getRcaAsOf: row => {
    const clocks = (row.incident?.evidence || []).filter(fact => fact?.type === 'timeline' && fact.service === null &&
      typeof fact.fact === 'string' && fact.fact.startsWith('Source-verified continuous RCA hypotheses;') &&
      /^artifact:\/\/manifests\/[a-f0-9]{64}\.json$/.test(fact.source_ref)).map(fact => fact.observed_at);
    return clocks.length && clocks.every(value => typeof value === 'string' && Number.isFinite(Date.parse(value))) ?
      clocks.reduce((earliest,value) => Date.parse(value) < Date.parse(earliest) ? value : earliest) : null;
  },
  refresh,
  get signal() { return sessionAbort.signal; },
  client: Object.freeze({get: async (path, signal) => {
    if (path !== '/v1/runbook-proposals?limit=100') throw new TypeError('Unsupported proposal watch path');
    const client = proposalClient, session = sessionAbort;
    if (!client || !workspace.state.session || session.signal.aborted) throw new DOMException('Cancelled','AbortError');
    try {
      const body = await client.get(path,AbortSignal.any([session.signal,...(signal ? [signal] : [])]),{method:'GET'});
      if (session.signal.aborted || client !== proposalClient) throw new DOMException('Cancelled','AbortError');
      return body;
    } catch (error) {
      if ([401,403].includes(error.status) && client === proposalClient) workspace.disconnect(error.message);
      throw error;
    }
  }}),
});

function stopWatcher() {
  sessionAbort.abort();
  if (watcher) {
    watcher.destroy(); handles.splice(handles.indexOf(watcher),1); watcher = null;
  }
  proposalClient = null;
}

function refresh() {
  return Promise.all([workspace.loadList(),workspace.checkReadiness(),workspace.loadProposals()]);
}

function render(state) {
  if (previous.filters !== state.filters) {
    $('#state-filter').value = state.filters.state; $('#service-filter').value = state.filters.service;
  }
  $('#connection').hidden = state.session;
  $('#disconnect').hidden = !state.session;
  $('#workspace-status').hidden = $('#slot-summary').hidden = $('#incident-workspace').hidden = !state.session;
  $('#session-label').textContent = state.session ? 'Connected' : 'Not connected';
  $('#session-indicator .status-dot').className = `status-dot ${state.session ? 'good' : 'neutral'}`;
  $('#refresh').disabled = !state.session || state.listLoading;
  $('#refresh-label').textContent = state.listLoading ? 'Refreshing…' : 'Refresh';
  $('#state-filter').disabled = $('#service-filter').disabled = !state.session;
  $('#filter-form button[type=submit]').disabled = !state.session;
  $('#notice').textContent = state.notice || ''; $('#notice').hidden = !state.notice;
  const dependencies = state.readiness?.dependencies;
  status($('#store-status'),dependencies?.incident_store,'Available','Unavailable');
  status($('#model-status'),dependencies?.model,'Available','Unavailable');
  $('#readiness-error').textContent = state.readinessError?.message || '';
  const countsAvailable = state.session && !state.listError && Boolean(state.updatedAt) && (!state.listLoading || state.rows.length > 0);
  $('#loaded-count').textContent = countsAvailable ? `${state.rows.length} loaded` : '—';
  $('#load-more').hidden = !state.session || !state.nextCursor;
  $('#load-more').disabled = state.listLoading;
  $('#updated-at').textContent = state.listLoading ? 'Loading records…' :
    state.updatedAt ? `Last loaded ${date(state.updatedAt)}` : 'Waiting for records';
  showError($('#list-error'),state.listError);
  if (previous.rows !== state.rows || previous.selected !== state.selected || previous.session !== state.session ||
      previous.listLoading !== state.listLoading || previous.listError !== state.listError) renderList(state);
  if (previous.detail !== state.detail || previous.selected !== state.selected || previous.detailLoading !== state.detailLoading ||
      previous.detailError !== state.detailError || previous.historyLoading !== state.historyLoading ||
      previous.historyError !== state.historyError || previous.session !== state.session || previous.actions !== state.actions ||
      previous.actionsLoading !== state.actionsLoading || previous.actionsError !== state.actionsError ||
      previous.quarantines !== state.quarantines || previous.explanation !== state.explanation ||
      previous.explanationLoading !== state.explanationLoading || previous.explanationError !== state.explanationError ||
      previous.proposals !== state.proposals || previous.proposalsLoading !== state.proposalsLoading ||
      previous.proposalsError !== state.proposalsError || previous.decisionLoading !== state.decisionLoading) renderDetail(state);
  if (!state.session) stopWatcher();
  else if (!watcher) {
    sessionAbort = new AbortController();
    watcher = mountProposalwatch($('#slot-proposalwatch'),ctx); handles.push(watcher);
  }
  for (const handle of handles) handle.update?.(state) ?? handle.refresh?.(state);
  previous = {...state};
}

function renderList(state) {
  const target = $('#incident-list'); target.setAttribute('aria-busy',String(state.listLoading));
  target.replaceChildren();
  if (!state.session) return;
  if (!state.rows.length) {
    target.append(empty(state.listLoading ? 'Loading incidents…' : state.listError ? 'Feed unavailable' : 'No incidents in this view',
      state.listLoading ? 'Reading persisted records from the incident API.' : state.listError ?
        'The request failed. Retry when the store is available; an outage does not mean the system is healthy.' :
        'No matching records were returned. This alone does not establish service health.'));
    return;
  }
  const list = node('ul',null,'incident-queue'); list.setAttribute('aria-label','Loaded incidents');
  for (const [index,row] of state.rows.entries()) {
    const item = node('li'); item.dataset.testid = 'incident-row';
    const selected = row.incident_id === state.selected;
    const open = button('',() => openIncident(row.incident_id,row.revision),`incident-link incident-link--compact ${selected ? 'selected' : ''}`);
    open.dataset.severity = Object.hasOwn(severityTones,row.severity) ? row.severity : 'unknown';
    open.title = `${row.incident_id} · revision ${row.revision}`; open.setAttribute('aria-label',`Open ${row.incident_id}, revision ${row.revision}`);
    if (selected) open.setAttribute('aria-current','true');
    const heading = node('span',null,'queue-heading'), chevron = node('span','›','queue-chevron');
    chevron.setAttribute('aria-hidden','true');
    const id = raw('strong',short(row.incident_id,23)); id.title = row.incident_id;
    heading.append(id,chevron);
    const services = node('span',null,'row-services');
    for (const name of row.affected_services || []) {
      const chip = raw('span',service(name),'incident-service'); chip.title = name; services.append(chip);
    }
    if (!services.childNodes.length) services.append(node('span','Services unknown','incident-services-unknown'));
    const footer = node('span',null,'queue-badges');
    footer.append(badge(row.severity,'severity'),badge(row.state,'state'));
    services.id = `queue-services-${index}`;
    footer.id = `queue-status-${index}`;
    open.setAttribute('aria-describedby',`${services.id} ${footer.id}`);
    const detected = relativeTime(row.detected_at); detected.className = 'row-time';
    detected.setAttribute('aria-label',`Detected ${date(row.detected_at)}`);
    open.append(heading,services,footer,detected);
    item.append(open); list.append(item);
  }
  target.append(list);
}

async function openIncident(id, revision = null) {
  tab = 'overview'; highlightedEvidence = null;
  await workspace.select(id,revision);
  if (workspace.state.selected === id && workspace.state.detail) {
    const query = new URLSearchParams(location.hash.slice(1));
    query.set('incident',id); query.set('revision',String(workspace.state.detail.revision));
    history.replaceState(null,'',`#${query}`);
    $('#detail-title')?.focus({preventScroll:!matchMedia('(max-width: 760px)').matches});
    await workspace.loadProposals();
  }
}

function fact(label, value) {
  const item = node('div'); item.append(node('span',label,'fact-label'),raw('div',value,`fact-value ${['UNKNOWN','abstained','ineligible'].includes(value) ? 'uncertain' : ''}`)); return item;
}
function section(title, note = null) {
  const heading = node('div',null,'section-heading'); heading.append(node('h3',title));
  if (note) heading.append(node('span',note)); return heading;
}
function helpDisclosure(title, ...content) {
  const item = node('details',null,'help-disclosure');
  const body = node('div',null,'help-disclosure__body'); body.append(...content);
  const summary = node('summary'), icon = node('span','i','info-icon'); icon.setAttribute('aria-hidden','true');
  summary.append(icon,node('span',title));
  item.append(summary,body); return item;
}
function overview(detail) {
  const target = node('div'), payload = detail.payload, context = detail.context;
  const grid = node('div',null,'facts-grid overview-facts');
  grid.append(fact('Lifecycle',context.lifecycle_stage),
    fact('Evidence availability',context.availability),fact('Recovery source',context.recovery_source || 'Not recorded'),
    fact('Revision recorded',date(detail.recorded_at)));
  target.append(helpDisclosure('Revision context',grid));
  target.append(section('Root-cause hypotheses',`${payload.root_cause_ranking.length} ranked`));
  target.append(helpDisclosure('Diagnostic qualification not exposed',node('p',
    'The ranking shows relative evidence support. This revision omits RCA coverage, margin and a qualified diagnostic decision.')));
  if (!payload.root_cause_ranking.length) target.append(node('p','No root-cause hypotheses were recorded.','subtle'));
  payload.root_cause_ranking.forEach((candidate,index) => {
    const card = node('div',null,'hypothesis'), heading = node('div',null,'hypothesis-heading');
    heading.append(node('span',index+1,'rank'),raw('strong',candidate.service,`service-label ${serviceClass(candidate.service)}`),node('span',`support ${score(candidate.score)}`,'score'));
    card.append(heading);
    const refs = node('div',null,'reference-list');
    for (const reference of candidate.evidence_refs) {
      const exists = payload.evidence.some(item => item.id === reference);
      const link = button(exists ? short(reference,24) : 'Missing reference',() => {
        tab = 'evidence'; highlightedEvidence = reference; renderDetail(workspace.state);
        const selected = [...document.querySelectorAll('[data-evidence-id]')].find(item => item.dataset.evidenceId === reference);
        if (selected) { selected.open = true; selected.scrollIntoView({block:'nearest'}); selected.querySelector('summary')?.focus(); }
      },`reference ${exists ? '' : 'missing'}`);
      link.title = exists ? reference : `Not present in this revision: ${reference}`; refs.append(link);
    }
    card.append(refs); target.append(card);
  });
  target.append(section('Model signals','Recorded in this revision'));
  if (!payload.service_signals.length) target.append(node('p','No model signals were recorded in this revision. Missing scores are unavailable, not zero.','subtle'));
  for (const signal of payload.service_signals) {
    const item = node('div',null,'signal');
    item.append(raw('strong',signal.service,`service-label ${serviceClass(signal.service)}`),node('span',`Anomaly ${score(signal.anomaly_score)}`),node('span',`Risk ${score(signal.degradation_risk)}`));
    target.append(item);
  }
  target.append(section('Detection sources','Recorded triggers'));
  if (!context.trigger_sources.length) target.append(node('p','No detection triggers were recorded.','subtle'));
  for (const trigger of context.trigger_sources) {
    target.append(node('p',`${trigger.source.toUpperCase()} · emitted ${date(trigger.emitted_at)}`,'subtle'),
      raw('code',trigger.source_ref,'source'));
  }
  return target;
}

function evidence(detail) {
  const target = node('div');
  target.append(section('Evidence for this revision'));
  target.append(helpDisclosure('About evidence references',node('p','Source references are identifiers, not automatically downloadable links. Facts are shown exactly as recorded.','subtle')));
  if (!detail.payload.evidence.length) target.append(node('p','No evidence was recorded in this revision.','subtle'));
  for (const item of detail.payload.evidence) {
    const card = node('details',null,`evidence-card ${highlightedEvidence === item.id ? 'highlight' : ''}`);
    card.dataset.evidenceId = item.id;
    const summary = node('summary'); summary.append(node('span',item.type,'evidence-kind'),node('span',short(item.id,24)),node('span',item.service || 'No service','evidence-service'));
    const body = node('div',null,'evidence-body'); body.append(raw('p',item.fact));
    const source = node('code',item.source_ref || 'Source reference unavailable','source'); body.append(source);
    const meta = node('div',null,'evidence-meta'); meta.append(time(item.observed_at));
    if (item.source_ref) {
      const copy = button('Copy reference',async () => {
        try { await navigator.clipboard.writeText(item.source_ref); copy.textContent = 'Copied'; }
        catch { copy.textContent = 'Select reference to copy'; }
      }); meta.append(copy);
    }
    body.append(meta); card.append(summary,body); target.append(card);
  }
  target.append(section('Revision source references'));
  for (const reference of detail.context.source_refs) target.append(node('code',reference,'source'));
  return target;
}

function timeline(detail, state) {
  const target = node('div');
  target.append(helpDisclosure('About this timeline',node('p',`Recorded lifecycle events through revision ${detail.revision}. Recorded times are not inferred detection or recovery latencies.`,'subtle')));
  const list = node('ol',null,'timeline');
  for (const event of detail.timeline.events) {
    const item = node('li'), heading = node('div',null,'timeline-heading');
    heading.append(node('strong',event.lifecycle_stage),node('span',`r${event.revision}`,'count'));
    item.append(heading,time(event.recorded_at),raw('p',event.reason),node('span',event.actor,'actor')); list.append(item);
  }
  target.append(list);
  const error = node('div',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.historyError); target.append(error);
  if (detail.timeline.next_after_revision !== null) {
    const more = button(state.historyLoading ? 'Loading revisions…' : 'Load more revisions',async () => {
      await workspace.loadHistory(); $('#history-more')?.focus();
    },'button secondary'); more.id = 'history-more'; more.disabled = state.historyLoading; target.append(more);
  }
  return target;
}

function explanation(state) {
  const target = node('div'), result = state.explanation;
  target.setAttribute('aria-busy',String(state.explanationLoading));
  target.append(section('Evidence summary','Generated explanation'));
  target.append(helpDisclosure('About this explanation',node('p',`Evidence summary for incident revision ${state.detail.revision}. Recorded evidence and ranked hypotheses do not establish a confirmed cause or action permission.`,'subtle')));
  if (state.explanationError) {
    const error = node('div',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.explanationError); target.append(error);
  }
  if (state.explanationLoading) target.append(node('p','Reading explanation…','subtle'));
  if (result) {
    target.append(section(result.fallback ? 'Deterministic fallback' : 'Provider-selected evidence'),
      node('p',`Provider ${result.provider} · generated ${date(result.generated_at)}`,'subtle'),
      helpDisclosure('Generated summary',raw('p',result.payload.summary,'explanation-summary')));
    if (!result.payload.claims.length) target.append(node('p','No bounded evidence claims are available for this revision.','subtle'));
    for (const claim of result.payload.claims) {
      const card = node('div',null,'explanation-claim'); card.append(raw('p',claim.text));
      const refs = node('div',null,'reference-list');
      for (const ref of claim.evidence_refs) {
        const link = button(short(ref,24),() => {
          tab = 'evidence'; highlightedEvidence = ref; renderDetail(workspace.state);
          const item = [...document.querySelectorAll('[data-evidence-id]')].find(value => value.dataset.evidenceId === ref);
          if (item) { item.open = true; item.scrollIntoView({block:'nearest'}); item.querySelector('summary')?.focus(); }
        },'reference'); link.title = ref; refs.append(link);
      }
      card.append(refs); target.append(card);
    }
  }
  const reload = button(state.explanationLoading ? 'Loading explanation…' : result ? 'Reload explanation' : 'Load explanation',async () => {
    const {incident_id,revision} = state.detail;
    await workspace.loadExplanation();
    if (tab === 'explanation' && workspace.state.detail?.incident_id === incident_id && workspace.state.detail?.revision === revision) {
      $('#explanation-reload')?.focus();
    }
  },'button secondary');
  reload.id = 'explanation-reload'; reload.disabled = state.explanationLoading; target.append(reload);
  return target;
}

function actionHistory(state,opened) {
  const target = node('div');
  target.append(section('Action journal','Recorded execution and verification'));
  target.append(helpDisclosure('About action phases',node('p',`Action journal through incident revision ${state.detail.revision}. Action phases do not change the incident lifecycle. Review the proposal panel for its approval decision.`,'subtle')));
  if (state.actionsError) {
    const error = node('div',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.actionsError); target.append(error);
  }
  if (state.actionsLoading) target.append(node('p','Reading action journal…','subtle'));
  if (state.actions && !state.actions.items.length) target.append(node('p','No action records were returned through this revision. This does not establish service health or recovery.','subtle'));
  for (const item of state.actions?.items || []) {
    const card = node('details',null,'evidence-card'), heading = node('summary');
    card.dataset.actionId=item.action_id;card.open=opened.has(item.action_id);
    heading.append(node('strong',item.phase),node('code',short(item.action_id)),node('span',`action r${item.action_revision} · incident r${item.incident_revision}`));
    card.append(heading);
    const body = node('div',null,'evidence-body');
    if (item.reason) body.append(raw('p',item.reason));
    body.append(node('p',item.runbook,'subtle'),node('p',`Recorded ${date(item.recorded_at)} · updated ${date(item.updated_at)}`,'subtle'));
    for (const [label,value] of [['Previous image',item.before_image],['Known-good image',item.known_good_image]]) {
      body.append(node('span',label,'fact-label'),node('code',value || 'Unavailable','source'));
    }
    const events = node('ol',null,'timeline');
    for (const event of item.history) {
      const row = node('li'); row.append(node('strong',`r${event.revision} · ${event.phase}`),time(event.recorded_at));
      if (event.reason) row.append(raw('p',event.reason)); events.append(row);
    }
    body.append(events);
    const quarantine=state.quarantines[item.action_id],result=quarantine?.result;
    body.append(section('Manual quarantine review','Read-only observation'));
    if (result) {
      const labels={held:'Quarantine held',released:'Quarantine released by operator',not_held:'No quarantine claim recorded for this action'};
      body.append(node('strong',labels[result.quarantine_state]),node('p',
        'This is the latest loaded status for this action. A released lock does not establish recovery or authorize another dispatch.','subtle'));
      if (result.resolution) {
        body.append(node('p',`Operator ${result.resolution.operator_actor} · recorded ${date(result.resolution.recorded_at)}`),
          node('code',result.resolution.resolution_id,'source'));
      }
    }
    if (quarantine?.error) {
      const error=node('div',null,'inline-error');error.setAttribute('role','alert');showError(error,quarantine.error);body.append(error);
    }
    const inspect=button(quarantine?.loading ? 'Reading quarantine…' : result ? 'Reload quarantine status' : 'View quarantine status',
      async () => {
        await workspace.loadQuarantine(item.action_id);
        if (tab==='actions' && workspace.state.detail?.incident_id===state.detail.incident_id &&
            workspace.state.detail?.revision===state.detail.revision) {
          document.querySelector(`[data-quarantine-action="${item.action_id}"]`)?.focus();
        }
      },'button secondary');
    inspect.dataset.quarantineAction=item.action_id;
    inspect.disabled=Boolean(quarantine?.loading);body.append(inspect);
    card.append(body); target.append(card);
  }
  const refresh = button(state.actionsLoading ? 'Loading action history…' : 'Reload action history',() => workspace.loadActions(),'button secondary');
  refresh.disabled = state.actionsLoading; target.append(refresh);
  if (state.actions?.next_cursor) {
    const more = button('Load more actions',() => workspace.loadActions(true),'button secondary');
    more.disabled = state.actionsLoading; target.append(more);
  }
  return target;
}

function runbookProposals(state, drafts) {
  const panel = node('section',null,'runbook-proposals');
  panel.setAttribute('aria-label','Runbook proposals'); panel.dataset.testid = 'runbook-proposals';
  const pending = state.proposals?.filter(row => row.status === 'pending' && proposalRemaining(row)).length || 0;
  panel.classList.toggle('has-pending',pending > 0);
  const heading = node('div',null,'proposal-heading');
  const title = node('div',null,'proposal-title');
  title.append(node('h3','Runbook proposals'));
  if (pending) title.append(statusChip(`${pending} awaiting decision`,'warning'));
  const reload = button('Reload proposals',() => workspace.loadProposals(),'button secondary');
  reload.disabled = state.proposalsLoading || state.decisionLoading;
  heading.append(title,reload); panel.append(heading);
  if (pending) panel.append(helpDisclosure('Approval safeguards',node('p','The controller checks all safeguards after approval. Rejection sends the proposal to an engineer.')));
  if (pending && !state.canApprove) panel.append(node('p','This session has read-only access. Decisions require the remediation.approve scope.','callout uncertain'));
  if (state.proposalsLoading) panel.append(node('p','Loading proposals…'));
  if (state.proposalsError) {
    const error = node('p',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.proposalsError); panel.append(error);
    panel.append(node('p','The outcome is unconfirmed. Reload the proposal before making another decision.','subtle'));
  }
  if (state.proposals?.length === 0) panel.append(node('p','No proposals for this revision.'));
  for (const row of state.proposals || []) {
    const p = row.proposal, card = node('article',null,'proposal-card');
    const grid = node('div',null,'facts-grid');
    const currentImage = row.review_images?.current_image;
    const targetImage = row.review_images?.rollback_image;
    grid.append(fact('Incident / revision',`${p.incident_id} / r${p.incident_revision}`),
      fact('Severity',p.symptoms?.severity || row.incident.severity),fact('Proposed runbook',p.runbook_id),
      fact('Score',score(p.score)),fact('Margin',score(p.margin)),
      fact('Current image',currentImage || 'Image evidence unavailable'),
      fact('Rollback image',targetImage || 'Image evidence unavailable'),
      fact('Approval deadline',date(p.expires_at)),fact('Status',proposalStatus(row.status)));
    card.append(grid,node('h4','RCA top-3'));
    if (!currentImage || !targetImage) card.append(node('p','This proposal needs both image digests for approval. You can reject it and send it to an engineer.','callout uncertain'));
    const ranking = node('ol');
    for (const item of row.incident.root_cause_ranking.slice(0,3)) ranking.append(raw('li',`${item.service} · ${score(item.score)}`));
    if (!ranking.childNodes.length) ranking.append(node('li','No root-cause hypotheses recorded.'));
    card.append(ranking,raw('code',`SHA-256: ${row.proposal_sha256}`,'source'));
    card.dataset.proposalExpires = p.expires_at;
    card.append(node('p',`Expires ${expiryTime(p.expires_at)}`));
    if (row.approval) card.append(raw('p',`${row.approval.decision} · ${row.approval.approved_by} · ${date(row.approval.approved_at)} · ${row.approval.reason}`));
    if (row.status === 'engineer') card.append(node('p',row.approval?.decision === 'reject' ?
      'An engineer will review the rejected proposal. The controller will skip execution.' : 'The controller referred this proposal to an engineer. See the outcome for details.'));
    if (row.outcome) card.append(raw('p',`${row.outcome.phase}: ${row.outcome.reason}`));
    const form = node('form',null,'proposal-decision'), label = node('label','Decision reason'), reason = node('textarea');
    reason.id = `reason-${p.proposal_id}`; reason.required = true; reason.maxLength = 2000; reason.rows = 2;
    reason.value = drafts.get(reason.id) || '';
    label.htmlFor = reason.id; form.append(label,reason);
    for (const [decision,labelText] of [['approve','Approve'],['reject','Reject']]) {
      const item = button(labelText,() => {
        if (form.reportValidity() && reason.value.trim()) workspace.decideProposal(p.proposal_id,decision,reason.value);
      },`button ${decision === 'approve' ? 'primary' : 'secondary'}`);
      item.dataset.decision = decision;
      const locked = !state.canApprove || state.decisionLoading || row.status !== 'pending' || decision === 'approve' && (!currentImage || !targetImage);
      item.disabled = locked || !proposalRemaining(row);
      item.dataset.locked = String(locked); form.append(item);
    }
    form.addEventListener('submit',event => event.preventDefault());
    card.append(form); panel.append(card);
  }
  return panel;
}

function updateProposalAvailability() {
  for (const card of document.querySelectorAll('.proposal-card[data-proposal-expires]')) {
    const expired = Date.now() >= Date.parse(card.dataset.proposalExpires);
    for (const item of card.querySelectorAll('[data-decision]')) item.disabled = item.dataset.locked === 'true' || expired;
  }
}

function renderDetail(state) {
  for (const [id,key] of [['nav-incidents','overview'],['nav-explanation','explanation'],['nav-actions','actions']]) {
    const active = (state.detail ? tab : 'overview') === key ||
      (key === 'overview' && !['explanation','actions'].includes(tab));
    const item = $('#'+id); item.classList.toggle('active',active);
    if (active) item.setAttribute('aria-current','page'); else item.removeAttribute('aria-current');
  }
  const target = $('#detail-pane');
  // Keep the mounted module and its handle alive when rebuilding the header.
  const deeplinks = $('#slot-deeplinks');
  if (target.contains(deeplinks)) target.before(deeplinks);
  const titleFocused = document.activeElement === $('#detail-title');
  const drafts = new Map([...target.querySelectorAll('.proposal-decision textarea')].map(item => [item.id,item.value]));
  const opened=new Set([...target.querySelectorAll('details[data-action-id][open]')].map(item => item.dataset.actionId));
  target.replaceChildren(); target.setAttribute('aria-busy',String(state.detailLoading));
  if (state.detailError) {
    const error = node('div',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.detailError);
    target.append(error,button('Retry incident',() => openIncident(state.selected,state.selectedRevision),'button secondary')); return;
  }
  if (!state.detail) {
    target.append(empty(state.detailLoading ? 'Loading incident…' : 'Select an incident',
      state.detailLoading ? 'Reading the selected revision and its evidence.' :
        'Choose a record from the incident queue to review its evidence, timeline and recovery.',true)); return;
  }
  const detail = state.detail, payload = detail.payload;
  const header = node('div',null,'detail-header'), overline = node('div',null,'detail-overline');
  const back = button('← Back to queue',() => {
    ($('#incident-list [aria-current=true]') || $('#list-title')).focus();
  },'button quiet back-to-queue');
  overline.append(node('span','Incident details','detail-label'),back,node('span',`Revision ${detail.revision}`,'count'));
  const title = raw('h2',detail.incident_id); title.id = 'detail-title'; title.tabIndex = -1;
  if (detail.incident_id.length > 36) title.replaceChildren(document.createTextNode(detail.incident_id.slice(0,36)),
    node('wbr'),document.createTextNode(detail.incident_id.slice(36)));
  const titleRow = node('div',null,'incident-title-row'); titleRow.append(title,deeplinks);
  const summary = node('div',null,'detail-summary');
  summary.append(badge(payload.severity,'severity'),badge(payload.state,'state'));
  const essentials = node('dl',null,'incident-essentials');
  const affected = node('div'), serviceChips = node('dd',null,'incident-affected-services');
  for (const name of payload.affected_services) {
    const chip = node('span',service(name),'incident-service'); chip.title = name; serviceChips.append(chip);
  }
  if (!serviceChips.childNodes.length) serviceChips.append(node('span','Services unknown'));
  affected.append(node('dt','Affected services'),serviceChips);
  const detected = node('div'), detectedValue = node('dd');
  detectedValue.append(relativeTime(payload.detected_at),time(payload.detected_at));
  detected.append(node('dt','Detected'),detectedValue);
  essentials.append(affected,detected);
  header.append(overline,titleRow,summary,essentials);
  const form = node('form',null,'revision-controls'), label = node('label','Revision '), input = node('input');
  input.type = 'number'; input.min = '1'; input.max = String(Number.MAX_SAFE_INTEGER); input.step = '1'; input.value = String(detail.revision);
  input.id = 'revision-input'; label.htmlFor = input.id; form.append(label,input);
  const go = node('button','View','button secondary'); go.type = 'submit'; form.append(go,button('Latest revision',() => openIncident(detail.incident_id)));
  form.addEventListener('submit',event => { event.preventDefault(); if (form.reportValidity()) openIncident(detail.incident_id,Number(input.value)); });
  header.append(form); target.append(header);
  const tabs = node('div',null,'tabs'); tabs.setAttribute('role','group'); tabs.setAttribute('aria-label','Incident sections');
  for (const [key,label] of [['overview','Overview'],['evidence',`Evidence (${payload.evidence.length})`],['timeline','Timeline'],['explanation','Explanation'],['actions','Actions']]) {
    const item = button(label,() => openSection(key),`tab ${tab === key ? 'active' : ''}`);
    item.dataset.tab = key; item.setAttribute('aria-pressed',String(tab === key)); tabs.append(item);
  }
  const content = node('div',null,'detail-content'); content.append(tab === 'evidence' ? evidence(detail) : tab === 'timeline' ? timeline(detail,state) : tab === 'explanation' ? explanation(state) : tab === 'actions' ? actionHistory(state,opened) : overview(detail));
  const ier = node('details',null,'ier-record'); ier.append(node('summary','Incident evidence record (JSON)'),raw('pre',JSON.stringify(payload,null,2)));
  target.append(tabs,runbookProposals(state,drafts),content,ier); updateProposalAvailability();
  if (titleFocused) title.focus({preventScroll:true});
}

async function openSection(key) {
  if (!workspace.state.detail) {
    $('#notice').textContent = workspace.state.session ?
      'Choose an incident from the feed to view its explanation or action history.' :
      'Connect to your workspace, then choose an incident to view its explanation or action history.';
    $('#notice').hidden = false;
    const target = workspace.state.session ? $('#incident-list') : $('#api-token');
    if (workspace.state.session) target.tabIndex = -1;
    target.focus(); return;
  }
  tab = key; renderDetail(workspace.state);
  if (key === 'actions' && !workspace.state.actions && !workspace.state.actionsError) await workspace.loadActions();
  if (key === 'explanation' && !workspace.state.explanation && !workspace.state.explanationError) await workspace.loadExplanation();
  if (tab === key) document.querySelector(`[data-tab="${key}"]`)?.focus();
}

$('#nav-explanation').addEventListener('click',() => openSection('explanation'));
$('#nav-actions').addEventListener('click',() => openSection('actions'));
$('#nav-incidents').addEventListener('click',event => {
  event.preventDefault();
  tab = 'overview'; renderDetail(workspace.state);
  $(workspace.state.session ? '#list-title' : manualAllowed ? '#api-token' : '#cognito-login').focus();
});

$('#connect-form').addEventListener('submit',async event => {
  event.preventDefault(); if (!manualAllowed) return;
  const token = $('#api-token').value.trim(); $('#api-token').value = '';
  if (token.length < 32) { $('#notice').textContent = 'Enter an access token of at least 32 characters.'; $('#notice').hidden = false; return; }
  await workspace.connect(token);
  if (workspace.state.session && !workspace.state.listError) {
    const query = new URLSearchParams(location.hash.slice(1)), id = query.get('incident'), rev = query.get('revision');
    if (id && rev && /^[1-9][0-9]*$/.test(rev)) await openIncident(id,Number(rev));
  }
});
$('#disconnect').addEventListener('click',() => {
  cognito?.clear(); workspace.disconnect();
  history.replaceState(null,'',location.pathname);
  $('#api-token').value = ''; $(manualAllowed ? '#api-token' : '#cognito-login').focus();
});
$('#cognito-login').addEventListener('click',async () => {
  if (!cognito) return;
  $('#cognito-login').disabled = true;
  try { workspace.disconnect(); location.assign(await cognito.begin()); }
  catch { $('#auth-status').textContent = 'Sign-in could not start. Check whether your browser allows session storage and try again.'; $('#cognito-login').disabled = false; }
});
$('#refresh').addEventListener('click',refresh);
$('#load-more').addEventListener('click',() => workspace.loadList(true));
$('#filter-form').addEventListener('submit',event => { event.preventDefault(); tab = 'overview'; workspace.filter({state:$('#state-filter').value,service:$('#service-filter').value}); });
const checkVisibleSession = () => { if (!document.hidden) {
  workspace.checkReadiness();
  if (!document.activeElement?.closest('.proposal-decision')) workspace.loadProposals();
} };
setInterval(updateProposalAvailability,1000);
setInterval(() => {
  for (const item of document.querySelectorAll('[data-relative-time]')) updateRelativeTime(item);
},30000);
setInterval(checkVisibleSession,30000);
document.addEventListener('visibilitychange',checkVisibleSession);
window.addEventListener('pagehide',() => {
  cognito?.suspend(); workspace.disconnect();
  for (const handle of handles) handle.destroy();
  handles.length = 0;
});
handles.push(mountDeeplinks($('#slot-deeplinks'),ctx),mountSummary($('#slot-summary'),ctx),mountFilters($('#slot-filters'),ctx));
workspace.emit();

async function initializeLogin() {
  try {
    const response = await fetch('/ui/auth-config',{cache:'no-store',credentials:'omit',redirect:'error',signal:AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('unavailable');
    const config = await response.json();
    if (config.mode === 'manual' && !loginCallback) {
      manualAllowed = true; $('#connect-form').hidden = false;
      $('#auth-status').textContent = 'Local or test mode. Your access token stays in this tab.'; return;
    }
    if (config.mode !== 'oidc') throw new Error('unavailable');
    cognito = new CognitoLogin(config); $('#cognito-login').hidden = false;
    $('#auth-status').textContent = 'Sign in with your operator account. Your access token stays in this tab.';
    if (loginCallback) {
      const session = await cognito.complete(loginCallback);
      cognito.expireAt(session.expiresAt,() => {
        workspace.disconnect('Your session expired. Sign in again.');
      });
      await workspace.connect(session.accessToken,{canApprove:session.canApprove});
    }
  } catch {
    $('#auth-status').textContent = 'Sign-in could not complete. Check the configuration or try again.';
  }
}
initializeLogin();
