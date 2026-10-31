/** Same-origin read client. Secrets live only in this instance, never in state/storage. */
export class ApiFailure extends Error {
  constructor(status, code, requestId = null) {
    const messages = {
      unauthorized: 'Token was rejected. Connect again with your local API token.',
      authentication_unavailable: 'API authentication is not configured for this environment.',
      incident_store_unavailable: 'The incident store is unavailable. Previously loaded data may be stale.',
      incident_not_found: 'This incident or revision could not be found.',
      timeout: 'The API did not respond in time. Try again.',
      network: 'Cannot reach the API. Check the connection and try again.',
      invalid_response: 'The API returned an unsupported response. Refresh or check its version.',
    };
    super(messages[code] || (status === 401 ? messages.unauthorized : status === 404 ? messages.incident_not_found :
      status === 503 ? messages.incident_store_unavailable : 'The request could not be completed. Try again.'));
    this.status = status; this.code = code; this.requestId = requestId;
  }
}

export class IncidentClient {
  #token; #fetch;
  constructor(token, fetcher = globalThis.fetch.bind(globalThis)) { this.#token = token; this.#fetch = fetcher; }
  async get(path, signal, {allowUnavailable = false} = {}) {
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Only same-origin API paths are allowed');
    const controller = new AbortController();
    let timedOut = false;
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, {once: true});
    if (signal?.aborted) controller.abort();
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 10000);
    try {
      const response = await this.#fetch(path, {signal:controller.signal, cache:'no-store', credentials:'omit',
        headers:{'Authorization':`Bearer ${this.#token}`, 'Accept':'application/json'}});
      const body = await response.json().catch(() => { throw new ApiFailure(response.status,'invalid_response'); });
      if (!response.ok && !(allowUnavailable && response.status === 503 && body.dependencies)) {
        throw new ApiFailure(response.status,body.code,response.headers.get('x-request-id'));
      }
      return body;
    } catch (error) {
      if (error instanceof ApiFailure) throw error;
      if (signal?.aborted) throw new DOMException('Cancelled','AbortError');
      throw new ApiFailure(0,timedOut ? 'timeout' : 'network');
    } finally { clearTimeout(timeout); signal?.removeEventListener('abort',abort); }
  }
  async list(filters, cursor, signal) {
    const query = new URLSearchParams({limit:'25'});
    for (const key of ['state','service']) if (filters[key]) query.set(key,filters[key]);
    if (cursor) query.set('cursor',cursor);
    const body = await this.get(`/v1/incidents?${query}`,signal);
    if (!Array.isArray(body.items) || !(body.next_cursor === null || typeof body.next_cursor === 'string') ||
      body.items.some(row => !row.incident_id || !Number.isSafeInteger(row.revision) || row.revision < 1)) {
      throw new ApiFailure(200,'invalid_response');
    }
    return body;
  }
  async context(id, revision, after, signal) {
    const query = new URLSearchParams({limit:'20',after_revision:String(after)});
    if (revision !== null) query.set('revision',String(revision));
    const body = await this.get(`/v1/incidents/${encodeURIComponent(id)}/context?${query}`,signal);
    if (body.incident_id !== id || !Number.isSafeInteger(body.revision) || body.revision < 1 ||
        (revision !== null && body.revision !== revision) || body.payload?.incident_id !== id ||
        !['ELIGIBLE','UNKNOWN'].includes(body.context?.availability) || !body.context.lifecycle_stage ||
        !Array.isArray(body.context.trigger_sources) || !Array.isArray(body.context.source_refs) ||
        !Array.isArray(body.payload.affected_services) || !Array.isArray(body.timeline?.events) || !Array.isArray(body.payload.evidence) ||
        !Array.isArray(body.payload.service_signals) || !Array.isArray(body.payload.root_cause_ranking) ||
        body.timeline.events.some(event => event.incident_id !== id || !Number.isSafeInteger(event.revision) ||
          event.revision > body.revision || event.revision < 1) ||
        !(body.timeline.next_after_revision === null || (Number.isSafeInteger(body.timeline.next_after_revision) &&
          body.timeline.next_after_revision > after && body.timeline.next_after_revision < body.revision))) {
      throw new ApiFailure(200,'invalid_response');
    }
    return body;
  }
  readiness(signal) { return this.get('/ready',signal,{allowUnavailable:true}); }
}
