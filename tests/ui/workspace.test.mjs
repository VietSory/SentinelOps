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
