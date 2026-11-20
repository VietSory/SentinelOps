const SEVERITIES = ['critical','high','medium'];
const SORTS = ['newest','oldest','severity'];
const RANGES = {all:0,'24h':86400000,'7d':604800000,'30d':2592000000};
const DEFAULTS = {query:'',severity:'',sort:'newest',range:'all'};
const HASH_KEYS = {query:'f.q',severity:'f.severity',sort:'f.sort',range:'f.range'};
const shareableQuery = value => /^(?:INC-)?[a-f0-9]{4,64}$/i.test(value);

function readFilters(hash) {
  const params = new URLSearchParams(hash.replace(/^#/,''));
  const query = params.get(HASH_KEYS.query) || '';
  const severity = params.get(HASH_KEYS.severity) || '';
  const sort = params.get(HASH_KEYS.sort);
  const range = params.get(HASH_KEYS.range);
  return {query:shareableQuery(query) ? query : '',severity:SEVERITIES.includes(severity) ? severity : '',
    sort:SORTS.includes(sort) ? sort : DEFAULTS.sort,range:Object.hasOwn(RANGES,range) ? range : DEFAULTS.range};
}

function detectedTime(row) {
  const value = typeof row.detected_at === 'string' ? Date.parse(row.detected_at) : NaN;
  return Number.isFinite(value) ? value : null;
}

function filterRows(rows, filters, now) {
  const query = filters.query.trim().toLowerCase();
  const duration = RANGES[filters.range];
  return rows.filter(row => {
    if (!String(row.incident_id).toLowerCase().includes(query)) return false;
    if (filters.severity && row.severity !== filters.severity) return false;
    const detected = detectedTime(row);
    return !duration || detected !== null && detected >= now-duration && detected <= now;
  }).sort((left,right) => {
    if (filters.sort === 'severity') {
      const rank = value => ({critical:0,high:1,medium:2,low:3}[value] ?? 4);
      const difference = rank(left.severity)-rank(right.severity);
      if (difference) return difference;
    }
    const leftTime = detectedTime(left), rightTime = detectedTime(right);
    if (leftTime === null && rightTime !== null) return 1;
    if (rightTime === null && leftTime !== null) return -1;
    const difference = leftTime === null ? 0 : filters.sort === 'oldest' ? leftTime-rightTime : rightTime-leftTime;
    return difference || String(left.incident_id).localeCompare(String(right.incident_id),'en');
  });
}

export function mount(root, ctx) {
  if (!root || typeof ctx?.getState !== 'function' || typeof ctx?.onChange !== 'function' ||
      typeof ctx?.resetFilters !== 'function') {
    throw new TypeError('Incident filters require a root, getState, onChange and resetFilters.');
  }
  const document = root.ownerDocument, window = document.defaultView;
  let filters = readFilters(window.location.hash), timer = null, destroyed = false, composing = false;
  const listeners = [];
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const listen = (element, event, action) => {
    element.addEventListener(event,action);
    listeners.push(() => element.removeEventListener(event,action));
  };
  const panel = node('section',undefined,'incident-filters');
  panel.setAttribute('aria-label','Search and sort loaded incidents');
  const controls = node('div',undefined,'incident-filters-controls');
  const field = (label, control) => {
    const wrapper = node('label',undefined,'incident-filters-field');
    control.setAttribute('aria-label',label);
    wrapper.append(node('span',label),control);
    controls.append(wrapper);
    return control;
  };
  const search = field('Search incident ID',node('input'));
  search.type = 'search'; search.name = 'incident-query'; search.maxLength = 256;
  search.placeholder = 'Enter all or part of an incident ID'; search.autocomplete = 'off';
  search.spellcheck = false;
  const select = (label, name, options) => {
    const control = field(label,node('select'));
    control.name = name;
    for (const [value,text] of options) {
      const option = node('option',text); option.value = value; control.append(option);
    }
    return control;
  };
  const severity = select('Severity','incident-severity',[
    ['','All severities'],['critical','Critical'],['high','High'],['medium','Medium']]);
  const range = select('Detected within','incident-range',[
    ['all','All time'],['24h','Last 24 hours'],['7d','Last 7 days'],['30d','Last 30 days']]);
  const sort = select('Sort by','incident-sort',[
    ['newest','Newest detected first'],['oldest','Oldest detected first'],['severity','Highest severity first']]);
  const reset = node('button','Clear all filters','incident-filters-reset'); reset.type = 'button';
  controls.append(reset);
  const summary = node('p',undefined,'incident-filters-summary');
  summary.setAttribute('role','status'); summary.setAttribute('aria-live','polite'); summary.setAttribute('aria-atomic','true');
  const scope = node('p',undefined,'incident-filters-note');
  const privacy = node('p',undefined,'incident-filters-note');
  privacy.textContent = 'Only incident-ID searches (INC- plus hexadecimal characters, or at least 4 hexadecimal characters) are saved in the link. Other searches stay in this tab until refresh.';
  const message = node('div',undefined,'incident-filters-message');
  const title = node('strong'), description = node('p'); message.append(title,description);
  panel.append(controls,summary,scope,privacy,message); root.append(panel);

  function syncControls() {
    search.value = filters.query; severity.value = filters.severity;
    range.value = filters.range; sort.value = filters.sort;
  }

  function writeHash() {
    const params = new URLSearchParams(window.location.hash.slice(1));
    for (const [key,hashKey] of Object.entries(HASH_KEYS)) {
      params.delete(hashKey);
      if (filters[key] !== DEFAULTS[key] && (key !== 'query' || shareableQuery(filters.query))) {
        params.set(hashKey,filters[key]);
      }
    }
    const hash = params.toString();
    window.history.replaceState(window.history.state,'',window.location.pathname+window.location.search+(hash ? `#${hash}` : ''));
  }

  function refresh() {
    if (destroyed) return;
    const state = ctx.getState();
    const loaded = Array.isArray(state.rows) ? state.rows : [];
    const rows = state.session ? filterRows(loaded,filters,Date.now()) : [];
    const total = state.session ? loaded.length : 0;
    const available = state.session && Boolean(state.updatedAt) && !state.listError;
    const pending = timer !== null || composing;
    let status = 'ready', heading = '', detail = '';
    if (!state.session) {
      status = 'disconnected'; heading = 'Connect to view incidents'; detail = 'No incident data is loaded for this session.';
    } else if (state.listError) {
      status = 'error'; heading = 'Incident feed unavailable';
      detail = total ? 'Showing previously loaded records. Refresh to restore current results.' : 'The request failed. This does not mean there are no incidents.';
    } else if (!total && (state.listLoading || !state.updatedAt)) {
      status = 'loading'; heading = 'Waiting for incidents'; detail = 'Results will appear after incident data has loaded.';
    } else if (!total) {
      status = 'empty'; heading = 'No incidents loaded';
      detail = 'The current server filters returned no records. This does not establish service health.';
    } else if (!rows.length) {
      status = 'no-match'; heading = 'No incidents match these filters';
      detail = `${total} incident${total === 1 ? ' is' : 's are'} loaded. Change the search or clear filters to see them.`;
    }
    for (const control of [search,severity,range,sort,reset]) control.disabled = !state.session;
    summary.textContent = available || total ? `${rows.length} of ${total}${pending ? ' · Applying search…' : ''}` : 'Results unavailable';
    scope.textContent = state.nextCursor ? 'Search, filters and sorting apply only to loaded incidents. Load more to include additional records.' :
      'Search, filters and sorting apply to loaded incidents within the current state and service filters.';
    panel.setAttribute('aria-busy',String(pending || Boolean(state.listLoading)));
    message.hidden = status === 'ready'; title.textContent = heading; description.textContent = detail;
    const result = {rows,total,matched:rows.length,filters:{...filters},status,
      emptyTitle:heading,emptyDescription:detail,partial:Boolean(state.nextCursor),pending};
    ctx.onChange(result);
    return result;
  }

  function cancelSearch() {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  }

  function applyControls() {
    cancelSearch();
    filters = {query:search.value.trim(),severity:severity.value,sort:sort.value,range:range.value};
    writeHash(); refresh();
  }

  function scheduleSearch() {
    cancelSearch();
    if (composing) return;
    timer = window.setTimeout(applyControls,250);
    summary.textContent = 'Applying search…'; panel.setAttribute('aria-busy','true');
  }

  listen(search,'input',scheduleSearch);
  listen(search,'compositionstart',() => { composing = true; cancelSearch(); });
  listen(search,'compositionend',() => { composing = false; scheduleSearch(); });
  listen(search,'keydown',event => {
    if (event.key === 'Enter') { event.preventDefault(); if (!composing) applyControls(); }
  });
  for (const control of [severity,range,sort]) listen(control,'change',applyControls);
  listen(reset,'click',() => {
    cancelSearch(); composing = false; filters = {...DEFAULTS}; syncControls(); writeHash();
    ctx.resetFilters(); refresh(); search.focus();
  });
  const restore = () => { cancelSearch(); composing = false; filters = readFilters(window.location.hash); syncControls(); refresh(); };
  listen(window,'hashchange',restore); listen(window,'popstate',restore);
  syncControls(); refresh();
  return {refresh,destroy() {
    if (destroyed) return;
    destroyed = true; cancelSearch(); listeners.forEach(remove => remove()); panel.remove();
  }};
}
