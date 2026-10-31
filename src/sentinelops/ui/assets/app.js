import {Workspace} from './workspace.js';

const $ = selector => document.querySelector(selector);
const node = (tag, text = null, className = '') => {
  const item = document.createElement(tag);
  if (text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
};
const button = (text, action, className = 'button quiet') => {
  const item = node('button',text,className); item.type = 'button'; item.addEventListener('click',action); return item;
};
const badge = value => node('span',value,`badge ${['incident','degrading','recovering','recovered','escalated','low','medium','high','critical'].includes(value) ? value : ''}`);
const date = value => {
  if (typeof value !== 'string' || !value) return 'Unavailable';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Unavailable' : parsed.toLocaleString(undefined,{timeZone:'UTC',
    year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})+' UTC';
};
const time = value => { const item = node('time',date(value)); if (value) item.dateTime = value; return item; };
const short = (value, width = 26) => value.length > width ? value.slice(0,width-7)+'…'+value.slice(-6) : value;
const score = value => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) : 'Unavailable';
const service = value => String(value).replace(/-service$/,'');
const empty = (title, description, detail = false) => {
  const item = node('div',null,`empty ${detail ? 'detail-empty' : ''}`);
  item.append(node('div',detail ? '⌕' : '▤','empty-icon'),node('h3',title),node('p',description)); return item;
};
function showError(container, error) {
  container.replaceChildren(); container.hidden = !error;
  if (!error) return;
  container.append(node('span',error.message));
  if (error.requestId) container.append(node('code',`Request ${error.requestId}`));
}
function status(container, value, yes, no) {
  const dot = node('span',null,`status-dot ${value === true ? 'good' : value === false ? 'warning' : 'neutral'}`);
  container.replaceChildren(dot,document.createTextNode(value === true ? yes : value === false ? no : 'Unknown'));
}

let tab = 'overview', highlightedEvidence = null, previous = {};
const workspace = new Workspace(render);

function render(state) {
  if (previous.filters !== state.filters) {
    $('#state-filter').value = state.filters.state; $('#service-filter').value = state.filters.service;
  }
  $('#connection').hidden = state.session;
  $('#disconnect').hidden = !state.session;
  $('#session-label').textContent = state.session ? 'Local session · read-only' : 'Not connected';
  $('#refresh').disabled = !state.session || state.listLoading;
  $('#state-filter').disabled = $('#service-filter').disabled = !state.session;
  $('#filter-form button').disabled = !state.session;
  $('#notice').textContent = state.notice || ''; $('#notice').hidden = !state.notice;
  const dependencies = state.readiness?.dependencies;
  status($('#store-status'),dependencies?.incident_store,'Available','Unavailable');
  status($('#model-status'),dependencies?.model,'Available','Unavailable');
  $('#readiness-error').textContent = state.readinessError?.message || '';
  $('#feed-dot').className = `status-dot ${state.session && !state.listError && state.updatedAt ? 'good' : 'neutral'}`;
  $('#nav-count').textContent = state.session ? String(state.rows.length) : '—';
  $('#loaded-count').textContent = `${state.rows.length} loaded`;
  $('#load-more').hidden = !state.session || !state.nextCursor;
  $('#load-more').disabled = state.listLoading;
  $('#updated-at').textContent = state.listLoading ? 'Loading records…' : state.updatedAt ? `Last loaded ${date(state.updatedAt)}` : 'Waiting for connection';
  showError($('#list-error'),state.listError);
  if (previous.rows !== state.rows || previous.selected !== state.selected || previous.session !== state.session ||
      previous.listLoading !== state.listLoading || previous.listError !== state.listError) renderList(state);
  if (previous.detail !== state.detail || previous.selected !== state.selected || previous.detailLoading !== state.detailLoading ||
      previous.detailError !== state.detailError || previous.historyLoading !== state.historyLoading ||
      previous.historyError !== state.historyError || previous.session !== state.session) renderDetail(state);
  previous = {...state};
}

function renderList(state) {
  const target = $('#incident-list'); target.setAttribute('aria-busy',String(state.listLoading));
  target.replaceChildren();
  if (!state.session) { target.append(empty('Your incident feed starts here','Connect to read the incidents persisted by your health and incident workers.')); return; }
  if (!state.rows.length) {
    target.append(empty(state.listLoading ? 'Loading incidents…' : state.listError ? 'Feed unavailable' : 'No incidents in this view',
      state.listLoading ? 'Reading persisted records from the incident API.' : state.listError ?
        'The request failed. Retry when the store is available; an outage does not mean the system is healthy.' :
        'No matching records were returned. This alone does not establish service health.'));
    return;
  }
  const wrap = node('div',null,'table-wrap'), table = node('table');
  const caption = node('caption','Persisted incident records in the current view','sr-only');
  const head = node('thead'), headings = node('tr');
  for (const label of ['Incident / services','State','Severity']) { const th = node('th',label); th.scope = 'col'; headings.append(th); }
  head.append(headings); const body = node('tbody');
  for (const row of state.rows) {
    const tr = node('tr',null,row.incident_id === state.selected ? 'selected' : '');
    tr.dataset.testid = 'incident-row';
    const identity = node('td');
    const open = button(short(row.incident_id),() => openIncident(row.incident_id,row.revision),'incident-link');
    open.title = row.incident_id; open.setAttribute('aria-label',`Open ${row.incident_id}, revision ${row.revision}`);
    identity.append(open,node('span',(row.affected_services || []).map(service).join(' · ') || 'No services recorded','row-services'),
      node('div',`r${row.revision} · ${date(row.detected_at)}`,'row-time'));
    const stateCell = node('td'), severity = node('td'); stateCell.append(badge(row.state)); severity.append(badge(row.severity));
    tr.append(identity,stateCell,severity); body.append(tr);
  }
  table.append(caption,head,body); wrap.append(table); target.append(wrap);
}

async function openIncident(id, revision = null) {
  tab = 'overview'; highlightedEvidence = null;
  await workspace.select(id,revision);
  if (workspace.state.selected === id && workspace.state.detail) {
    const query = new URLSearchParams({incident:id,revision:String(workspace.state.detail.revision)});
    history.replaceState(null,'',`#${query}`);
    $('#detail-title')?.focus({preventScroll:!matchMedia('(max-width: 1250px)').matches});
  }
}

function fact(label, value) {
  const item = node('div'); item.append(node('span',label,'fact-label'),node('div',value,'fact-value')); return item;
}
function section(title, note = null) {
  const heading = node('div',null,'section-heading'); heading.append(node('h3',title));
  if (note) heading.append(node('span',note)); return heading;
}
function overview(detail) {
  const target = node('div'), payload = detail.payload, context = detail.context;
  const grid = node('div',null,'facts-grid');
  grid.append(fact('Affected services',payload.affected_services.map(service).join(', ') || 'None recorded'),
    fact('Detection timestamp',date(payload.detected_at)),fact('Evidence availability',context.availability),
    fact('Lifecycle',context.lifecycle_stage),fact('Recovery source',context.recovery_source || 'Not recorded'),
    fact('Revision recorded',date(detail.recorded_at))); target.append(grid);
  target.append(section('Root-cause hypotheses',`${payload.root_cause_ranking.length} ranked`));
  const warning = node('div',null,'callout');
  warning.append(node('strong','Diagnostic qualification not exposed'),node('span',
    'Ranking is evidence support, not a confirmed cause. This API revision does not expose RCA coverage, margin or a qualified diagnostic decision.'));
  target.append(warning);
  if (!payload.root_cause_ranking.length) target.append(node('p','No root-cause hypotheses were recorded.','subtle'));
  payload.root_cause_ranking.forEach((candidate,index) => {
    const card = node('div',null,'hypothesis'), heading = node('div',null,'hypothesis-heading');
    heading.append(node('span',index+1,'rank'),node('strong',candidate.service),node('span',`support ${score(candidate.score)}`,'score'));
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
    item.append(node('strong',signal.service),node('span',`Anomaly ${score(signal.anomaly_score)}`),node('span',`Risk ${score(signal.degradation_risk)}`));
    target.append(item);
  }
  target.append(section('Trigger provenance'));
  for (const trigger of context.trigger_sources) {
    target.append(node('p',`${trigger.source.toUpperCase()} · emitted ${date(trigger.emitted_at)}`,'subtle'),node('code',trigger.source_ref,'source'));
  }
  target.append(node('p','Explanation and action execution are unavailable. This console does not authorize remediation.','subtle'));
  return target;
}

function evidence(detail) {
  const target = node('div');
  target.append(node('p','Source references are identifiers, not automatically downloadable links. Facts are shown exactly as recorded.','subtle'));
  if (!detail.payload.evidence.length) target.append(node('p','No evidence was recorded in this revision.','subtle'));
  for (const item of detail.payload.evidence) {
    const card = node('details',null,`evidence-card ${highlightedEvidence === item.id ? 'highlight' : ''}`);
    card.dataset.evidenceId = item.id;
    const summary = node('summary'); summary.append(node('span',item.type,'evidence-kind'),node('span',short(item.id,24)),node('span',item.service || 'No service','evidence-service'));
    const body = node('div',null,'evidence-body'); body.append(node('p',item.fact));
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
  target.append(node('p',`Recorded lifecycle events through revision ${detail.revision}. Recorded times are not inferred detection or recovery latencies.`,'subtle'));
  const list = node('ol',null,'timeline');
  for (const event of detail.timeline.events) {
    const item = node('li'), heading = node('div',null,'timeline-heading');
    heading.append(node('strong',event.lifecycle_stage),node('span',`r${event.revision}`,'count'));
    item.append(heading,time(event.recorded_at),node('p',event.reason),node('span',event.actor,'actor')); list.append(item);
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

function renderDetail(state) {
  const target = $('#detail-pane'); target.replaceChildren(); target.setAttribute('aria-busy',String(state.detailLoading));
  if (state.detailError) {
    const error = node('div',null,'inline-error'); error.setAttribute('role','alert'); showError(error,state.detailError);
    target.append(error,button('Retry incident',() => openIncident(state.selected,state.selectedRevision),'button secondary')); return;
  }
  if (!state.detail) {
    target.append(empty(state.detailLoading ? 'Reading incident evidence…' : 'A closer look starts here',
      state.detailLoading ? 'Loading a revision-pinned record and its lifecycle context.' :
        'Choose an incident to explore its signals, root-cause hypotheses and source evidence.',true)); return;
  }
  const detail = state.detail, payload = detail.payload;
  const header = node('div',null,'detail-header'), overline = node('div',null,'detail-overline');
  overline.append(node('span','INCIDENT DETAIL'),node('span',`REVISION ${detail.revision}`,'count'));
  const title = node('h2',detail.incident_id); title.id = 'detail-title'; title.tabIndex = -1;
  if (detail.incident_id.length > 36) title.replaceChildren(document.createTextNode(detail.incident_id.slice(0,36)),
    node('wbr'),document.createTextNode(detail.incident_id.slice(36)));
  const summary = node('div',null,'detail-summary'); summary.append(badge(payload.state),badge(payload.severity));
  header.append(overline,summary,title);
  const form = node('form',null,'revision-controls'), label = node('label','Revision '), input = node('input');
  input.type = 'number'; input.min = '1'; input.max = String(Number.MAX_SAFE_INTEGER); input.step = '1'; input.value = String(detail.revision);
  input.id = 'revision-input'; label.htmlFor = input.id; form.append(label,input);
  const go = node('button','View','button secondary'); go.type = 'submit'; form.append(go,button('Latest revision',() => openIncident(detail.incident_id)));
  form.addEventListener('submit',event => { event.preventDefault(); if (form.reportValidity()) openIncident(detail.incident_id,Number(input.value)); });
  header.append(form); target.append(header);
  const tabs = node('div',null,'tabs'); tabs.setAttribute('role','group'); tabs.setAttribute('aria-label','Incident sections');
  for (const [key,label] of [['overview','Overview'],['evidence',`Evidence (${payload.evidence.length})`],['timeline','Timeline']]) {
    const item = button(label,() => { tab = key; renderDetail(workspace.state); document.querySelector(`[data-tab="${key}"]`)?.focus(); },`tab ${tab === key ? 'active' : ''}`);
    item.dataset.tab = key; item.setAttribute('aria-pressed',String(tab === key)); tabs.append(item);
  }
  const content = node('div',null,'detail-content'); content.append(tab === 'evidence' ? evidence(detail) : tab === 'timeline' ? timeline(detail,state) : overview(detail));
  target.append(tabs,content);
}

$('#connect-form').addEventListener('submit',async event => {
  event.preventDefault(); const token = $('#api-token').value.trim(); $('#api-token').value = '';
  if (token.length < 32) { $('#notice').textContent = 'Enter a local API token of at least 32 characters.'; $('#notice').hidden = false; return; }
  await workspace.connect(token);
  if (workspace.state.session && !workspace.state.listError) {
    const query = new URLSearchParams(location.hash.slice(1)), id = query.get('incident'), rev = query.get('revision');
    if (id && rev && /^[1-9][0-9]*$/.test(rev)) await openIncident(id,Number(rev));
  }
});
$('#disconnect').addEventListener('click',() => { workspace.disconnect(); history.replaceState(null,'',location.pathname); $('#api-token').value = ''; $('#api-token').focus(); });
$('#refresh').addEventListener('click',() => Promise.all([workspace.loadList(),workspace.checkReadiness()]));
$('#load-more').addEventListener('click',() => workspace.loadList(true));
$('#filter-form').addEventListener('submit',event => { event.preventDefault(); tab = 'overview'; workspace.filter({state:$('#state-filter').value,service:$('#service-filter').value}); });
setInterval(() => { if (!document.hidden) workspace.checkReadiness(); },30000);
window.addEventListener('pagehide',() => workspace.disconnect());
workspace.emit();
