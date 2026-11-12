import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiFailure, IncidentClient} from '../../src/sentinelops/ui/assets/client.js';
import {Workspace} from '../../src/sentinelops/ui/assets/workspace.js';

const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
const envelope = (id, revision, next=null) => ({incident_id:id,revision,context:{availability:'UNKNOWN',lifecycle_stage:'ESCALATED',trigger_sources:[],source_refs:[]},
  payload:{incident_id:id,affected_services:[],evidence:[],service_signals:[],root_cause_ranking:[]},
  timeline:{events:[{event_id:`${id}-${revision}`,incident_id:id,revision}],next_after_revision:next}});
const factory = overrides => ({list:async () => ({items:[],next_cursor:null}),readiness:async () => ({dependencies:{model:false,incident_store:true}}),
  context:async (id,rev) => envelope(id,rev),...overrides});

test('readiness authenticates against protected incident readiness even when public readiness succeeds',async () => {
  const body={status:'ready',dependencies:{model:true,incident_store:true,authentication:true},direct_inference:false};
  for (const [status,code] of [[401,'unauthorized'],[403,'insufficient_scope']]) {
    const calls=[];
    const client=new IncidentClient('private-fixture-token',async (path,options) => {
      calls.push({path,options});
      return new Response(JSON.stringify(path==='/ready' ? body : {code}),{status:path==='/ready' ? 200 : status});
    });
    await assert.rejects(() => client.readiness(),error => error.status===status && error.code===code);
    assert.deepEqual(calls.map(call => call.path).sort(),['/ready','/ready/incidents']);
    assert.ok(calls.every(call => call.options.headers.Authorization==='Bearer private-fixture-token' &&
      call.options.credentials==='omit'));
  }
});

test('protected readiness preserves the public no-model state and rejects an invalid protected response',async () => {
  const body={status:'not_ready',dependencies:{model:false,incident_store:true,authentication:true}};
  for (const protectedBody of [{status:'ready',component:'incident-store'},{}]) {
    const client=new IncidentClient('fixture',async path => new Response(JSON.stringify(
      path==='/ready' ? body : protectedBody),{status:path==='/ready' ? 503 : 200}));
    if (protectedBody.component) assert.deepEqual(await client.readiness(),body);
    else await assert.rejects(() => client.readiness(),error => error.code==='invalid_response');
  }
});

test('a public-probe network error cannot mask a later protected permission rejection',async () => {
  const protectedReply=deferred();
  const client=new IncidentClient('fixture',async path => {
    if (path==='/ready') throw new Error('fixture public-probe network failure');
    return protectedReply.promise;
  });
  const outcome=client.readiness().then(() => null,error => error);
  await new Promise(resolve => setImmediate(resolve));
  protectedReply.resolve(new Response(JSON.stringify({code:'insufficient_scope'}),{status:403}));
  const error=await outcome;
  assert.equal(error.status,403);assert.equal(error.code,'insufficient_scope');
});

test('observed loss of incident-read scope clears all private state during the readiness heartbeat',async () => {
  let denied=false;
  const workspace=new Workspace(() => {},() => factory({
    list:async () => ({items:[{incident_id:'private-record',revision:1}],next_cursor:null}),
    readiness:async () => {
      if (denied) throw new ApiFailure(403,'insufficient_scope');
      return {dependencies:{model:false,incident_store:true}};
    },
  }));
  await workspace.connect('fixture');await workspace.select('private-record',1);
  denied=true;await workspace.checkReadiness();
  assert.equal(workspace.state.session,false);assert.deepEqual(workspace.state.rows,[]);
  assert.equal(workspace.state.detail,null);assert.equal(workspace.state.readiness,null);
  assert.equal(workspace.state.actions,null);assert.equal(workspace.state.explanation,null);
  assert.match(workspace.state.notice,/cannot read incidents/);
});

test('readiness retains a confirmed store outage but never swallows identity failures or contradictory status',async () => {
  for (const [store,code,accepted] of [[false,'control_store_unavailable',true],
    [true,'control_store_unavailable',false],[false,'authentication_unavailable',false]]) {
    const body={status:'not_ready',dependencies:{incident_store:store,model:false,authentication:true}};
    const client=new IncidentClient('fixture',async path => new Response(JSON.stringify(
      path==='/ready' ? body : {code}),{status:503}));
    if (accepted) assert.deepEqual(await client.readiness(),body);
    else await assert.rejects(() => client.readiness(),error => error.status===503 && error.code===code);
  }
});

test('a late permission failure from an old connection cannot disconnect its replacement',async () => {
  const late=deferred();let connection=0;
  const workspace=new Workspace(() => {},() => {
    const current=++connection;
    return factory({readiness:() => current===1 ? late.promise : Promise.resolve({dependencies:{model:false}})});
  });
  const old=workspace.connect('old-fixture');await workspace.connect('new-fixture');
  await workspace.select('current-private-record',1);
  late.reject(new ApiFailure(403,'insufficient_scope'));await old;
  assert.equal(workspace.state.session,true);assert.equal(workspace.state.notice,null);
  assert.equal(workspace.state.detail.incident_id,'current-private-record');
});

test('identity-provider outage preserves readable records with unknown readiness, without calling them healthy',async () => {
  let down=false;
  const workspace=new Workspace(() => {},() => factory({readiness:async () => {
    if (down) throw new ApiFailure(503,'authentication_unavailable');
    return {dependencies:{model:false,incident_store:true}};
  }}));
  await workspace.connect('fixture');await workspace.select('private-record',1);
  down=true;await workspace.checkReadiness();
  assert.equal(workspace.state.session,true);assert.equal(workspace.state.detail.incident_id,'private-record');
  assert.equal(workspace.state.readiness,null);assert.equal(workspace.state.readinessError.code,'authentication_unavailable');
});

test('an unavailable identity adapter is displayed as authentication failure rather than store outage',async () => {
  const client=new IncidentClient('fixture-token',async () => new Response(JSON.stringify({
    code:'authentication_adapter_unavailable',request_id:'fixture-request'}),{status:503}));
  await assert.rejects(() => client.list({},null),error => error.status===503 &&
    error.code==='authentication_adapter_unavailable' && /Authentication is unavailable/.test(error.message));
});

const explanationPage = (id,revision) => ({schema_version:'1.0',incident_id:id,revision,
  generated_at:'2026-01-01T00:00:00Z',provider:'deterministic-facts/v1',fallback:true,
  payload:{schema_version:'1.0',incident_id:id,summary:'Software fixture, not a verified cause',claims:[]}});

test('explanation client pins encoded identity/revision and rejects foreign references or provider drift',async () => {
  for (const mutate of [x => x.revision=2,x => x.incident_id='foreign',x => x.payload.incident_id='foreign',
    x => x.generated_at='bad',x => x.fallback=false,x => x.provider='unapproved-model',
    x => x.payload.claims=[{text:'unsupported',evidence_refs:['foreign']}],
    x => x.payload.claims=[{text:'bad',evidence_refs:['e1','e1']}],x => x.payload.claims=[null]]) {
    const body=explanationPage('a',1);mutate(body);
    const client=new IncidentClient('fixture',async () => new Response(JSON.stringify(body)));
    await assert.rejects(() => client.explanation('a',1,['e1']),e => e.code==='invalid_response');
  }
  let requested;
  const client=new IncidentClient('private-fixture-token',async path => {
    requested=path; return new Response(JSON.stringify(explanationPage('a&b',3)));
  });
  assert.equal((await client.explanation('a&b',3,[])).fallback,true);
  assert.equal(requested,'/v1/incidents/a%26b/explanation?revision=3');
  await assert.rejects(() => client.explanation('a&b',Number.MAX_SAFE_INTEGER+1,[]),e => e.code==='invalid_response');
});

test('late explanation cannot overwrite a newer revision even when transport ignores cancellation',async () => {
  const old=deferred(),recent=deferred(),calls=[];
  const workspace=new Workspace(() => {},() => factory({explanation:(id,revision,refs) => {
    calls.push({id,revision,refs});return revision===1 ? old.promise : recent.promise;
  }}));
  await workspace.connect('fixture');await workspace.select('a',1);
  const first=workspace.loadExplanation();await workspace.select('a',2);
  const second=workspace.loadExplanation();old.resolve(explanationPage('a',1));await first;
  assert.equal(workspace.state.explanation,null);assert.equal(workspace.state.explanationLoading,true);
  recent.resolve(explanationPage('a',2));await second;
  assert.equal(workspace.state.explanation.revision,2);
  assert.deepEqual(calls[1],{id:'a',revision:2,refs:[]});
});

test('explanation reload failure clears old text without hiding the incident or model-unavailable state',async () => {
  let calls=0;
  const workspace=new Workspace(() => {},() => factory({explanation:async (id,rev) => {
    if (++calls>1) throw new ApiFailure(503,'explanation_unavailable');return explanationPage(id,rev);
  }}));
  await workspace.connect('fixture');await workspace.select('a',3);await workspace.loadExplanation();
  assert.equal(workspace.state.explanation.revision,3);
  await workspace.loadExplanation();assert.equal(workspace.state.explanation,null);
  assert.equal(workspace.state.explanationError.status,503);assert.equal(workspace.state.detail.revision,3);
  assert.equal(workspace.state.readiness.dependencies.model,false);
});

test('filter/logout discard late explanation results and private explanation state',async () => {
  for (const reset of [workspace => workspace.filter({state:'incident',service:''}),workspace => workspace.disconnect()]) {
    const late=deferred();
    const workspace=new Workspace(() => {},() => factory({explanation:() => late.promise}));
    await workspace.connect('fixture');await workspace.select('a',1);
    const request=workspace.loadExplanation();await reset(workspace);late.resolve(explanationPage('a',1));await request;
    assert.equal(workspace.state.explanation,null);assert.equal(workspace.state.explanationLoading,false);
  }
});

test('late list response cannot overwrite a newer filter even if transport ignores abort',async () => {
  const slow = deferred(); let calls=0;
  const workspace = new Workspace(() => {},() => factory({list:() => ++calls===1 ? slow.promise : Promise.resolve({items:[{incident_id:'new',revision:1}],next_cursor:null})}));
  const connected = workspace.connect('test-only-token');
  await workspace.filter({service:'payment-service',state:'escalated'});
  slow.resolve({items:[{incident_id:'old',revision:1}],next_cursor:'stale'}); await connected;
  assert.equal(workspace.state.rows[0].incident_id,'new'); assert.equal(workspace.state.nextCursor,null);
});

test('switching incidents rejects late detail/history and pins history to the selected revision',async () => {
  const slow = deferred(); const calls=[];
  const workspace = new Workspace(() => {},() => factory({context:(id,rev,after) => {
    calls.push({id,rev,after}); if (id==='a' && after>0) return slow.promise;
    return Promise.resolve(envelope(id,rev,after===0 ? 1 : null));
  }}));
  await workspace.connect('fixture'); await workspace.select('a',3);
  const old = workspace.loadHistory(); await workspace.select('b',2);
  slow.resolve(envelope('a',3)); await old;
  assert.equal(workspace.state.detail.incident_id,'b');
  assert.deepEqual(calls.find(c => c.after===1),{id:'a',rev:3,after:1});
});

test('logout/401 clears private records and late requests cannot resurrect the session',async () => {
  const slow = deferred();
  const workspace = new Workspace(() => {},() => factory({list:() => slow.promise}));
  const connected = workspace.connect('fixture'); workspace.disconnect();
  slow.resolve({items:[{incident_id:'private',revision:1}],next_cursor:null}); await connected;
  assert.equal(workspace.state.session,false); assert.deepEqual(workspace.state.rows,[]);
  const denied = new Workspace(() => {},() => factory({list:async () => { throw new ApiFailure(401,'unauthorized'); }}));
  await denied.connect('fixture'); assert.equal(denied.state.session,false); assert.match(denied.state.notice,/Token was rejected/);
});

test('a store outage retains an explicit error, never an empty healthy success',async () => {
  const workspace = new Workspace(() => {},() => factory({list:async () => { throw new ApiFailure(503,'incident_store_unavailable','fixture-request-id'); }}));
  await workspace.connect('fixture'); assert.equal(workspace.state.listError.status,503);
  assert.equal(workspace.state.updatedAt,null); assert.equal(workspace.state.readiness.dependencies.model,false);
});

test('pagination merges IDs, filters reset cursor, and unsupported revisions never issue a request',async () => {
  const calls=[];
  const workspace = new Workspace(() => {},() => factory({list:async (filters,cursor) => {
    calls.push({filters,cursor}); return {items:[{incident_id:'a',revision:1}],next_cursor:cursor ? null : 'opaque'};
  },context:async () => assert.fail('unsupported revision reached API')}));
  await workspace.connect('fixture'); await workspace.loadList(true); assert.equal(workspace.state.rows.length,1);
  await workspace.filter({state:'incident',service:''}); assert.equal(calls.at(-1).cursor,null);
  await workspace.select('a',Number.MAX_SAFE_INTEGER+1); assert.equal(workspace.state.detailError.code,'invalid_response');
});

test('client uses read-only same-origin requests, headers and opaque encoded filters',async () => {
  const calls=[];
  const client = new IncidentClient('fixture-secret',async (path,options) => {
    calls.push({path,options}); return new Response(JSON.stringify({items:[],next_cursor:null}),{status:200});
  });
  await client.list({state:'incident',service:'payment&foreign=true'},'opaque+/=',new AbortController().signal);
  assert.equal(new URL(calls[0].path,'http://local').searchParams.get('service'),'payment&foreign=true');
  assert.equal(calls[0].options.headers.Authorization,'Bearer fixture-secret'); assert.equal(calls[0].options.credentials,'omit');
  assert.equal(JSON.stringify(client).includes('fixture-secret'),false);
  await assert.rejects(() => client.get('https://foreign.test'),/same-origin/);
  await assert.rejects(() => client.get('//foreign.test'),/same-origin/);
});

test('context rejects wrong identity, future history, unsafe revisions and absent qualification context',async () => {
  for (const mutate of [x => x.incident_id='foreign', x => x.timeline.events[0].revision=4,
    x => x.revision=Number.MAX_SAFE_INTEGER+1, x => delete x.context]) {
    const value=envelope('a',3); mutate(value);
    const client=new IncidentClient('fixture',async () => new Response(JSON.stringify(value),{status:200}));
    await assert.rejects(() => client.context('a',3,0),error => error.code==='invalid_response');
  }
});

const actionPage = (id,revision,cursor=null) => ({observation_version:'action-list/v1',incident_id:id,incident_revision:revision,
  items:[{observation_version:'action-observation/v1',incident_id:id,incident_revision:revision,action_id:'a'.repeat(64),
    action_revision:1,phase:'blocked',runbook:'payment-rollback-known-good/v1',history:[{revision:1,phase:'blocked'}]}],next_cursor:cursor});

test('action client rejects wrong scope, later incident revisions, invalid phases and inconsistent audit',async () => {
  for (const mutate of [x => x.incident_revision=4,x => x.items[0].incident_id='foreign',
    x => x.items[0].incident_revision=4,x => x.items[0].phase='approved',x => x.items[0].history.push({revision:2,phase:'dispatching'}),
    x => x.items[0].history[0]=null,x => x.items[0].history[0].phase='recovered']) {
    const value=actionPage('a',3); mutate(value);
    const client=new IncidentClient('fixture',async () => new Response(JSON.stringify(value),{status:200}));
    await assert.rejects(() => client.actions('a',3,null),error => error.code==='invalid_response');
  }
});

test('action pagination pins revision, deduplicates IDs and switching incidents rejects late action responses',async () => {
  const late=deferred(),calls=[];let count=0;
  const workspace=new Workspace(() => {},() => factory({actions:async (id,revision,cursor) => {
    calls.push({id,revision,cursor}); if (++count===3) return late.promise;
    return actionPage(id,revision,cursor ? null : 'opaque-actions');
  }}));
  await workspace.connect('fixture');await workspace.select('a',3);
  await workspace.loadActions();await workspace.loadActions(true);
  assert.equal(workspace.state.actions.items.length,1);
  assert.deepEqual(calls[1],{id:'a',revision:3,cursor:'opaque-actions'});
  const previous=workspace.loadActions();await workspace.select('b',2);
  late.resolve(actionPage('a',3));await previous;
  assert.equal(workspace.state.actions,null);assert.equal(workspace.state.actionsLoading,false);
});

test('unavailable action history retains its error and does not hide a readable incident',async () => {
  const workspace=new Workspace(() => {},() => factory({actions:async () => {throw new ApiFailure(503,'action_store_unavailable');}}));
  await workspace.connect('fixture');await workspace.select('a',3);await workspace.loadActions();
  assert.equal(workspace.state.detail.incident_id,'a');assert.equal(workspace.state.actions,null);
  assert.match(workspace.state.actionsError.message,/does not establish that no action occurred/);
  workspace.disconnect();assert.equal(workspace.state.actionsError,null);
});

const quarantinePage=(id,revision) => ({observation_version:'quarantine-status/v1',action_id:id,action_revision:revision,
  action_phase:'escalated',quarantine_state:'released',resolution:{status:'RELEASED',resolution_id:'d'.repeat(64),
    action_id:id,operator_actor:'fixture-operator',payload:{release_version:'manual-quarantine-release/v1',
      action_id:id,action_revision:revision,dispatch_authorized:false,recovery_accepted:false,ier_mutated:false}}});

test('quarantine client rejects foreign/stale/forged recovery claims in an operator receipt',async () => {
  for (const mutate of [x => x.action_id='b'.repeat(64),x => x.action_revision=4,x => x.quarantine_state='healthy',
    x => x.action_phase='recovered',x => x.resolution.payload.recovery_accepted=true,x => x.resolution.action_id='b'.repeat(64),
    x => x.resolution.payload.dispatch_authorized=true]) {
    const value=quarantinePage('a'.repeat(64),3);mutate(value);
    const client=new IncidentClient('fixture',async () => new Response(JSON.stringify(value),{status:200}));
    await assert.rejects(() => client.quarantine('a'.repeat(64),3),error => error.code==='invalid_response');
  }
});

test('late quarantine status cannot resurrect an earlier incident or survive logout',async () => {
  const late=deferred();let requested=null;
  const workspace=new Workspace(() => {},() => factory({actions:async (id,revision) => actionPage(id,revision),
    quarantine:(id,revision) => {requested={id,revision};return late.promise;}}));
  await workspace.connect('fixture');await workspace.select('a',3);await workspace.loadActions();
  const request=workspace.loadQuarantine('a'.repeat(64));
  assert.deepEqual(requested,{id:'a'.repeat(64),revision:1});
  await workspace.select('b',2);late.resolve(quarantinePage('a'.repeat(64),1));await request;
  assert.deepEqual(workspace.state.quarantines,{});
  workspace.disconnect();assert.deepEqual(workspace.state.quarantines,{});
});

test('quarantine 503 remains unavailable while the existing action and incident remain readable',async () => {
  const workspace=new Workspace(() => {},() => factory({actions:async (id,revision) => actionPage(id,revision),
    quarantine:async () => {throw new ApiFailure(503,'stored_action_unavailable');}}));
  await workspace.connect('fixture');await workspace.select('a',3);await workspace.loadActions();
  await workspace.loadQuarantine('a'.repeat(64));
  assert.equal(workspace.state.quarantines['a'.repeat(64)].result,null);
  assert.equal(workspace.state.quarantines['a'.repeat(64)].error.status,503);
  assert.equal(workspace.state.detail.incident_id,'a');assert.equal(workspace.state.actions.items.length,1);
});
