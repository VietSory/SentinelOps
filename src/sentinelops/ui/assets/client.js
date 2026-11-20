/** Same-origin client. Secrets live only in this instance, never in state/storage. */
export const memoryRunbook = 'pod-memory-limit-demo/v1';
export function memoryReview(row) {
  const p = row?.proposal, review = row?.review_change, target = p?.target;
  return p?.executor_runbook === memoryRunbook && review?.kind === 'memory_limit' &&
    p.resource_change?.memory_before === '64Mi' && p.resource_change?.memory_after === '256Mi' &&
    review.memory_before === '64Mi' && review.memory_after === '256Mi' &&
    review.cause === 'OOMKilled' && p.cause === 'OOMKilled' && typeof p.pod_uid === 'string' &&
    !!p.pod_uid && review.pod_uid === p.pod_uid && target?.deployment === 'sentinelops-oom-demo' &&
    target.namespace === 'sentinelops-b-actions' && target.container === 'oom-demo' &&
    review.deployment === target.deployment && review.container === target.container &&
    typeof target.image === 'string' && /^.+@sha256:[a-f0-9]{64}$/.test(target.image) &&
    review.image === target.image && p.current_image === target.image ? review : null;
}
export function memoryRecoveryReview(row) {
  const p = row?.proposal, review = row?.recovery_change ?? row?.review_change, target = p?.target;
  return p?.executor_runbook === memoryRunbook && review?.kind === 'memory_limit' &&
    p.resource_change?.memory_before === '64Mi' && p.resource_change?.memory_after === '256Mi' &&
    review.memory_before === '64Mi' && review.memory_after === '256Mi' &&
    target?.namespace === 'sentinelops-b-actions' && target.deployment === 'sentinelops-oom-demo' &&
    target.container === 'oom-demo' && (review.namespace === undefined || review.namespace === target.namespace) &&
    review.deployment === target.deployment && review.container === target.container &&
    typeof target.image === 'string' && /^.+@sha256:[a-f0-9]{64}$/.test(target.image) &&
    review.image === target.image && p.current_image === target.image ? review : null;
}
export function validProposal(row) {
  const p = row?.proposal;
  const memory = p?.executor_runbook === memoryRunbook;
  return p?.proposal_version === 'runbook-proposal/v1' && /^[a-f0-9]{64}$/.test(p.proposal_id) &&
    /^[a-f0-9]{64}$/.test(row.proposal_sha256) && typeof p.incident_id === 'string' &&
    Number.isSafeInteger(p.incident_revision) && p.incident_revision > 0 &&
    Number.isFinite(Date.parse(p.expires_at)) && Number.isFinite(Date.parse(p.created_at)) &&
    typeof p.runbook_id === 'string' && (memory || Number.isFinite(p.score) && Number.isFinite(p.margin)) &&
    (row.recovery_change == null || memory && Boolean(memoryRecoveryReview(row))) &&
    (row.manual_recovery_available === undefined || typeof row.manual_recovery_available === 'boolean') &&
    (memory ? ['pending','approved','engineer','expired','consumed','stale','not_yet_valid',
      'dispatching','verifying','recovered','blocked','escalated','rejected'] :
      ['pending','approved','engineer','expired','consumed','stale','not_yet_valid']).includes(row.status);
}
export class ApiFailure extends Error {
  constructor(status, code, requestId = null) {
    const messages = {
      unauthorized: 'Token was rejected. Connect again with a valid access token.',
      insufficient_scope: 'This token cannot read incidents. Connect with a token that has incident read permission.',
      authentication_unavailable: 'Authentication is unavailable. Check the API identity configuration or retry when its provider is available.',
      authentication_adapter_unavailable: 'Authentication is unavailable for this environment. Check its identity adapter.',
      incident_store_unavailable: 'The incident store is unavailable. Previously loaded data may be stale.',
      incident_not_found: 'This incident or revision could not be found.',
      action_store_unavailable: 'Action history is unavailable. This does not establish that no action occurred.',
      stored_action_unavailable: 'Action history could not be verified. Reload when its evidence is available.',
      explanation_unavailable: 'Explanation is unavailable for this revision. The incident and evidence remain readable.',
      proposal_store_unavailable: 'The proposal store is unavailable. Reload to try again.',
      proposal_changed: 'The proposal changed. Reload and review it before making a decision.',
      proposal_stale: 'The incident has a newer revision. Review its current proposal before approving.',
      proposal_not_pending: 'The proposal expired or already has a decision. Reload to see its status.',
      proposal_review_incomplete: 'Approval requires complete pinned change evidence. Send the proposal to an engineer or wait for a new one.',
      operator_required: 'An operator access token is required to review and approve proposals.',
      approval_scope_required: 'This token has read-only access. Proposal decisions require the remediation.approve scope.',
      timeout: 'The API did not respond in time. Try again.',
      network: 'Cannot reach the API. Check the connection and try again.',
      invalid_response: 'The API returned an unsupported response. Refresh or check its version.',
    };
    super(messages[code] || (status === 401 ? messages.unauthorized : status === 404 ? messages.incident_not_found :
      status === 503 ? messages.incident_store_unavailable : 'The request could not be completed. Try again.'));
    this.status = status; this.code = code; this.requestId = requestId;
  }
}

const pkceKey = 'sentinelops.pkce.v1';
const base64url = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
const jwtBytes = value => Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/') + '='.repeat((4-value.length%4)%4)),c => c.charCodeAt(0));
const jwtObject = value => JSON.parse(new TextDecoder().decode(jwtBytes(value)));

/** Tokens never enter storage. Only a ten-minute, one-use PKCE transaction survives redirect. */
export class CognitoLogin {
  #config; #storage; #crypto; #fetch; #origin; #now; #expiry = null;
  constructor(config, {storage = globalThis.sessionStorage, crypto = globalThis.crypto,
    fetcher = globalThis.fetch.bind(globalThis), origin = globalThis.location?.origin, now = Date.now} = {}) {
    const domain = new URL(config.domain), issuer = new URL(config.issuer);
    if (config.mode !== 'oidc' || domain.protocol !== 'https:' || config.domain !== domain.origin ||
        issuer.protocol !== 'https:' || issuer.username || issuer.password || issuer.search || issuer.hash ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(config.client_id) ||
        config.jwks_url !== config.issuer.replace(/\/$/,'')+'/.well-known/jwks.json' ||
        config.callback_path !== '/ui/callback' || !Array.isArray(config.scopes) || config.scopes.length > 16 ||
        config.scopes.some(s => typeof s !== 'string' || !s || /\s/.test(s)) ||
        new Set(config.scopes).size !== config.scopes.length || !config.scopes.includes('openid') ||
        !config.scopes.includes(config.read_scope) || !config.scopes.includes(config.approval_scope) ||
        config.read_scope === config.approval_scope || new URL(origin).origin !== origin ||
        !(origin.startsWith('https://') || /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(origin))) {
      throw new Error('The sign-in configuration is invalid.');
    }
    this.#config = structuredClone(config); this.#storage = storage; this.#crypto = crypto;
    this.#fetch = fetcher; this.#origin = origin; this.#now = now;
  }
  async #json(url, options = {}) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(),10000);
    try {
      const response = await this.#fetch(url,{...options,signal:controller.signal,credentials:'omit',cache:'no-store',redirect:'error'});
      const data = await response.text();
      if (!response.ok || data.length > 65536) throw new Error('auth request failed');
      return JSON.parse(data);
    } finally { clearTimeout(timeout); }
  }
  async begin() {
    this.clear();
    const random = () => base64url(this.#crypto.getRandomValues(new Uint8Array(32)));
    const transaction = {verifier:random(),state:random(),nonce:random(),created_at:this.#now(),
      client_id:this.#config.client_id,issuer:this.#config.issuer,domain:this.#config.domain,
      redirect_uri:this.#origin + this.#config.callback_path};
    const challenge = base64url(await this.#crypto.subtle.digest('SHA-256',new TextEncoder().encode(transaction.verifier)));
    this.#storage.setItem(pkceKey,JSON.stringify(transaction));
    const url = new URL(this.#config.domain + '/oauth2/authorize');
    url.search = new URLSearchParams({response_type:'code',client_id:transaction.client_id,
      redirect_uri:transaction.redirect_uri,scope:this.#config.scopes.join(' '),state:transaction.state,
      nonce:transaction.nonce,code_challenge:challenge,code_challenge_method:'S256'});
    return url.href;
  }
  async #verifyId(value, nonce) {
    if (typeof value !== 'string' || value.length > 8192 || value.split('.').length !== 3) throw new Error('invalid ID token');
    const [encodedHeader,encodedClaims,signature] = value.split('.'), header = jwtObject(encodedHeader), claims = jwtObject(encodedClaims);
    const seconds = this.#now()/1000;
    if (header.alg !== 'RS256' || typeof header.kid !== 'string' || !header.kid ||
        claims.iss !== this.#config.issuer || claims.aud !== this.#config.client_id ||
        claims.nonce !== nonce || claims.token_use !== 'id' || typeof claims.sub !== 'string' || !claims.sub ||
        typeof claims.exp !== 'number' || !Number.isFinite(claims.exp) || claims.exp <= seconds ||
        typeof claims.iat !== 'number' || !Number.isFinite(claims.iat) || claims.iat > seconds+60) throw new Error('invalid ID claims');
    const document = await this.#json(this.#config.jwks_url);
    const keys = Array.isArray(document.keys) ? document.keys.filter(k => k.kid === header.kid && k.kty === 'RSA' &&
      (!k.alg || k.alg === 'RS256') && (!k.use || k.use === 'sig')) : [];
    if (keys.length !== 1) throw new Error('unknown ID signing key');
    const key = await this.#crypto.subtle.importKey('jwk',keys[0],{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['verify']);
    if (!await this.#crypto.subtle.verify('RSASSA-PKCS1-v1_5',key,jwtBytes(signature),
      new TextEncoder().encode(encodedHeader+'.'+encodedClaims))) throw new Error('invalid ID signature');
    return claims;
  }
  async complete(callback) {
    const stored = this.#storage.getItem(pkceKey); this.#storage.removeItem(pkceKey);
    try {
      const transaction = JSON.parse(stored), url = new URL(callback), query = url.searchParams;
      if (!transaction || url.origin !== this.#origin || url.pathname !== this.#config.callback_path || url.hash ||
          query.has('error') || query.getAll('code').length !== 1 || query.getAll('state').length !== 1 ||
          !query.get('code') || query.get('code').length > 8192 || query.get('state') !== transaction.state ||
          !['verifier','state','nonce'].every(k => /^[A-Za-z0-9_-]{43}$/.test(transaction[k])) ||
          !Number.isFinite(transaction.created_at) || this.#now()-transaction.created_at > 600000 || transaction.created_at > this.#now()+5000 ||
          transaction.client_id !== this.#config.client_id || transaction.issuer !== this.#config.issuer ||
          transaction.domain !== this.#config.domain || transaction.redirect_uri !== this.#origin+this.#config.callback_path) throw new Error('invalid transaction');
      const tokens = await this.#json(this.#config.domain+'/oauth2/token',{method:'POST',
        headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},
        body:new URLSearchParams({grant_type:'authorization_code',client_id:this.#config.client_id,
          code:query.get('code'),redirect_uri:transaction.redirect_uri,code_verifier:transaction.verifier}).toString()});
      if (tokens.token_type?.toLowerCase() !== 'bearer' || typeof tokens.access_token !== 'string' ||
          tokens.access_token.length < 32 || tokens.access_token.length > 8192 ||
          !Number.isFinite(tokens.expires_in) || tokens.expires_in <= 0 || tokens.expires_in > 86400 ||
          tokens.access_token.split('.').length !== 3) throw new Error('invalid access response');
      // Cognito can omit the optional OAuth response scope. These decoded access
      // claims only guide UI/expiry; the API verifies its signature and scopes.
      const access = jwtObject(tokens.access_token.split('.')[1]);
      if (access.iss !== this.#config.issuer || access.client_id !== this.#config.client_id || access.token_use !== 'access' ||
          !Number.isFinite(access.exp) || access.exp*1000 <= this.#now() || typeof access.scope !== 'string' ||
          !access.scope.split(/\s+/).includes(this.#config.read_scope)) throw new Error('invalid access claims');
      const id = await this.#verifyId(tokens.id_token,transaction.nonce);
      if (id.sub !== access.sub) throw new Error('token subjects differ');
      return {accessToken:tokens.access_token,expiresAt:Math.min(access.exp*1000,this.#now()+tokens.expires_in*1000),
        canApprove:access.scope.split(/\s+/).includes(this.#config.approval_scope)};
    } catch {
      throw new Error('Cognito sign-in could not complete. Sign in again.');
    }
  }
  expireAt(time, disconnect) {
    clearTimeout(this.#expiry);
    const remaining = time-this.#now();
    if (!Number.isFinite(remaining) || remaining <= 0) { disconnect(); return; }
    this.#expiry = setTimeout(disconnect,Math.min(remaining,86400000));
  }
  suspend() { clearTimeout(this.#expiry); this.#expiry = null; }
  clear() { this.suspend(); this.#storage.removeItem(pkceKey); }
}

export class IncidentClient {
  #token; #fetch;
  constructor(token, fetcher = globalThis.fetch.bind(globalThis)) { this.#token = token; this.#fetch = fetcher; }
  async get(path, signal, {allowUnavailable = false, timeoutMs = 10000, method = 'GET', body: requestBody, etag} = {}) {
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Only same-origin API paths are allowed');
    const controller = new AbortController();
    let timedOut = false;
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, {once: true});
    if (signal?.aborted) controller.abort();
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    try {
      const response = await this.#fetch(path, {signal:controller.signal, cache:'no-store', credentials:'omit',
        method, ...(requestBody === undefined ? {} : {body:JSON.stringify(requestBody)}),
        headers:{'Authorization':`Bearer ${this.#token}`, 'Accept':'application/json',
          ...(requestBody === undefined ? {} : {'Content-Type':'application/json'}), ...(etag ? {'If-Match':etag} : {})}});
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
  async proposals(id, revision, signal) {
    const body = await this.get(`/v1/runbook-proposals?incident_id=${encodeURIComponent(id)}&limit=100`,signal);
    if (!Array.isArray(body.items) || typeof body.has_more !== 'boolean' || body.has_more ||
        body.items.some(row => !validProposal(row) || row.proposal.incident_id !== id ||
          row.incident?.incident_id !== id || !['low','medium','high','critical'].includes(row.incident.severity) ||
          !Array.isArray(row.incident.root_cause_ranking) || row.incident.root_cause_ranking.some(
            item => typeof item?.service !== 'string' || !Number.isFinite(item.score)))) throw new ApiFailure(200,'invalid_response');
    return body.items.filter(row => row.proposal.incident_revision === revision ||
      row.proposal.executor_runbook === memoryRunbook && row.status !== 'pending' &&
      row.proposal.incident_revision < revision && row.current_revision === revision);
  }
  async proposal(id, signal) {
    if (!/^[a-f0-9]{64}$/.test(id)) throw new ApiFailure(0,'invalid_response');
    const body = await this.get(`/v1/runbook-proposals/${id}`,signal);
    if (!validProposal(body) || body.proposal.proposal_id !== id) throw new ApiFailure(200,'invalid_response');
    return body;
  }
  async decideProposal(row, decision, reason, signal) {
    if (!validProposal(row) || !['approve','reject'].includes(decision) ||
        typeof reason !== 'string' || !reason.trim() || reason.trim().length > 2000) throw new ApiFailure(0,'invalid_response');
    const manualMemory = decision === 'approve' && row.proposal.executor_runbook === memoryRunbook;
    if (manualMemory && (row.manual_recovery_available !== true || !memoryRecoveryReview(row))) throw new ApiFailure(0,'invalid_response');
    const body = await this.get(`/v1/runbook-proposals/${row.proposal.proposal_id}/decision`,signal,
      {method:'POST',etag:`"${row.proposal_sha256}"`,body:{decision,reason:reason.trim(),
        ...(manualMemory ? {approval_mode:'manual_memory_recovery'} : {})}});
    if (!validProposal(body)) throw new ApiFailure(200,'invalid_response');
    const same = (left,right) => left && right && Object.keys(left).length === Object.keys(right).length &&
      Object.keys(left).every(key => Object.hasOwn(right,key) && left[key] === right[key]);
    const boundsMatch = !manualMemory || memoryRecoveryReview(body) && same(body.proposal.target,row.proposal.target) &&
      same(body.proposal.resource_change,row.proposal.resource_change) && body.proposal.current_image === row.proposal.current_image;
    const child = manualMemory && body.proposal.proposal_id !== row.proposal.proposal_id &&
      body.manual_recovery_of === row.proposal.proposal_id && body.proposal.manual_recovery_of === row.proposal.proposal_id &&
      body.proposal.origin_proposal_sha256 === row.proposal_sha256 && body.proposal.origin_incident_id === row.proposal.incident_id &&
      Number.isSafeInteger(row.current_revision) && row.current_revision > 0 &&
      body.proposal.origin_incident_revision === row.current_revision && body.proposal.incident_revision === 1 &&
      body.proposal.incident_id === `INC-OOM-MANUAL-${body.proposal.proposal_id}` &&
      Date.parse(body.proposal.expires_at)-Date.parse(body.proposal.created_at) === 15*60*1000 &&
      Date.parse(body.proposal.expires_at) > Date.now() && Date.parse(body.proposal.created_at) <= Date.now()+1000;
    if ((!child && (body.proposal.proposal_id !== row.proposal.proposal_id || body.proposal_sha256 !== row.proposal_sha256)) ||
        !boundsMatch || body.approval?.decision !== decision ||
        (manualMemory && body.approval?.approval_mode !== 'manual_memory_recovery') ||
        !(decision === 'reject' ? (row.proposal.executor_runbook === memoryRunbook ? ['engineer','rejected'] : ['engineer']) :
          (row.proposal.executor_runbook === memoryRunbook ?
            ['approved','consumed','engineer','dispatching','verifying','recovered','blocked','escalated'] :
            ['approved','consumed','engineer'])).includes(body.status) ||
        body.approval?.proposal_sha256 !== body.proposal_sha256 || typeof body.approval?.approved_by !== 'string') {
      throw new ApiFailure(200,'invalid_response');
    }
    return body;
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
  async readiness(signal) {
    // Public readiness describes configuration, not the bearer token's permissions.
    const publicProbe = this.get('/ready',signal,{allowUnavailable:true})
      .then(body => ({body}),error => ({error}));
    // Start both probes together, but handle protected denial first. A faster public
    // network failure must not hide a 401/403 and leave the private session open.
    const permission = await this.get('/ready/incidents',signal).catch(error => {
      if (error.status === 503 && ['control_store_unavailable','incident_store_unavailable'].includes(error.code)) {
        return error;
      }
      throw error;
    });
    const result = await publicProbe;
    if (result.error) throw result.error;
    const body = result.body;
    // Keep an explicit store outage reported by both endpoints. An identity-provider
    // failure or contradictory observation must not become successful readiness.
    if (permission instanceof ApiFailure) {
      if (body?.dependencies?.incident_store !== false) throw permission;
      return body;
    }
    if (permission?.status !== 'ready' || permission.component !== 'incident-store') {
      throw new ApiFailure(200,'invalid_response');
    }
    return body;
  }
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
      body.payload?.schema_version !== '2.0' || body.payload.incident_id !== id ||
      typeof body.payload.summary !== 'string' || !Array.isArray(body.payload.claims) || body.payload.claims.length > 12 ||
      !body.payload.root_cause ||
      !(body.payload.root_cause.service === null || typeof body.payload.root_cause.service === 'string') ||
      !(body.payload.root_cause.score === null || typeof body.payload.root_cause.score === 'number' &&
        Number.isFinite(body.payload.root_cause.score) && body.payload.root_cause.score >= 0 && body.payload.root_cause.score <= 1) ||
      typeof body.payload.root_cause.explanation !== 'string' || !body.payload.root_cause.explanation ||
      !Array.isArray(body.payload.root_cause.evidence_refs) ||
      new Set(body.payload.root_cause.evidence_refs).size !== body.payload.root_cause.evidence_refs.length ||
      body.payload.root_cause.evidence_refs.some(ref => typeof ref !== 'string' || !known.has(ref)) ||
      (body.payload.root_cause.service === null &&
        (body.payload.root_cause.score !== null || body.payload.root_cause.evidence_refs.length)) ||
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
