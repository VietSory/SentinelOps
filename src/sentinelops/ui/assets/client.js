/** Same-origin read client. Secrets live only in this instance, never in state/storage. */
export class ApiFailure extends Error {
  constructor(status, code, requestId = null) {
    const messages = {
      unauthorized: 'Token was rejected. Connect again with your local API token.',
      authentication_unavailable: 'API authentication is not configured for this environment.',
      authentication_adapter_unavailable: 'Authentication is unavailable for this environment. Check its identity adapter.',
      incident_store_unavailable: 'The incident store is unavailable. Previously loaded data may be stale.',
      incident_not_found: 'This incident or revision could not be found.',
      action_store_unavailable: 'Action history is unavailable. This does not establish that no action occurred.',
      stored_action_unavailable: 'Action history could not be verified. Reload when its evidence is available.',
      explanation_unavailable: 'Explanation is unavailable for this revision. The incident and evidence remain readable.',
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
  async get(path, signal, {allowUnavailable = false, timeoutMs = 10000} = {}) {
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Only same-origin API paths are allowed');
    const controller = new AbortController();
    let timedOut = false;
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, {once: true});
    if (signal?.aborted) controller.abort();
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
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
  async explanation(id, revision, evidenceIds, signal) {
    if (!Number.isSafeInteger(revision) || revision < 1) throw new ApiFailure(0,'invalid_response');
    // The backend provider has a ten-second total deadline before fallback.
    // Leave HTTP overhead room so that fallback can reach the browser.
    const body = await this.get(`/v1/incidents/${encodeURIComponent(id)}/explanation?revision=${revision}`,signal,{timeoutMs:15000});
    const known = new Set(evidenceIds);
    if (body.schema_version !== '1.0' || body.incident_id !== id || body.revision !== revision ||
      typeof body.generated_at !== 'string' || !Number.isFinite(Date.parse(body.generated_at)) ||
      typeof body.provider !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(body.provider) ||
      typeof body.fallback !== 'boolean' || body.fallback !== (body.provider === 'deterministic-facts/v1') ||
      body.payload?.schema_version !== '1.0' || body.payload.incident_id !== id ||
      typeof body.payload.summary !== 'string' || !Array.isArray(body.payload.claims) || body.payload.claims.length > 12 ||
      body.payload.claims.some(claim => !claim || typeof claim.text !== 'string' || !claim.text ||
        !Array.isArray(claim.evidence_refs) || !claim.evidence_refs.length ||
        new Set(claim.evidence_refs).size !== claim.evidence_refs.length ||
        claim.evidence_refs.some(ref => typeof ref !== 'string' || !known.has(ref)))) {
      throw new ApiFailure(200,'invalid_response');
    }
    return body;
  }
  async actions(id, revision, cursor, signal) {
    const query = new URLSearchParams({revision:String(revision),limit:'10'});
    if (cursor) query.set('cursor',cursor);
    const body = await this.get(`/v1/incidents/${encodeURIComponent(id)}/actions?${query}`,signal);
    const phases = ['prepared','blocked','dispatching','verifying','recovered','escalated'];
    if (body.observation_version !== 'action-list/v1' || body.incident_id !== id || body.incident_revision !== revision ||
      !Array.isArray(body.items) || body.items.length > 25 ||
      !(body.next_cursor === null || typeof body.next_cursor === 'string' && body.next_cursor.length > 0) ||
      body.items.some(item => item?.observation_version !== 'action-observation/v1' || item.incident_id !== id ||
        !Number.isSafeInteger(item.incident_revision) || item.incident_revision < 1 || item.incident_revision > revision ||
        !/^[0-9a-f]{64}$/.test(item.action_id) || !phases.includes(item.phase) ||
        item.runbook !== 'payment-rollback-known-good/v1' || !Number.isSafeInteger(item.action_revision) ||
        !Array.isArray(item.history) || item.history.length !== item.action_revision || item.history.length < 1 || item.history.length > 100 ||
        item.history.some((event,index) => !event || event.revision !== index+1 || !phases.includes(event.phase)) ||
        item.history.at(-1).phase !== item.phase)) throw new ApiFailure(200,'invalid_response');
    return body;
  }
  async quarantine(id,revision,signal) {
    const body=await this.get(`/v1/actions/${encodeURIComponent(id)}/quarantine`,signal);
    const receipt=body.resolution,payload=receipt?.payload;
    if (body.observation_version!=='quarantine-status/v1' || body.action_id!==id || body.action_revision!==revision ||
      !['held','released','not_held'].includes(body.quarantine_state) ||
      !['prepared','blocked','dispatching','verifying','recovered','escalated'].includes(body.action_phase) ||
      (body.quarantine_state==='held' && (body.action_phase!=='escalated' || receipt!==null)) ||
      (body.quarantine_state==='not_held' && receipt!==null) ||
      (body.quarantine_state==='released' && (body.action_phase!=='escalated' || receipt?.status!=='RELEASED' ||
        receipt.action_id!==id || !/^[0-9a-f]{64}$/.test(receipt.resolution_id) ||
        typeof receipt.operator_actor!=='string' || !receipt.operator_actor ||
        payload?.release_version!=='manual-quarantine-release/v1' || payload.action_id!==id || payload.action_revision!==revision ||
        payload.dispatch_authorized!==false || payload.recovery_accepted!==false || payload.ier_mutated!==false))) {
      throw new ApiFailure(200,'invalid_response');
    }
    return body;
  }
}
