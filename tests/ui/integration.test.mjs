import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../src/sentinelops/ui/assets/app.js',import.meta.url),'utf8');

function integration() {
  const calls = [];
  let reply = async () => ({items:[],has_more:false});
  class IncidentClient {
    constructor(token) { assert.equal(token,'private-test-token'); }
    get(path,signal,options) { calls.push({path,signal,options}); return reply(); }
  }
  class Workspace {
    constructor(notify,factory) { this.notify = notify; this.factory = factory; this.state = {session:false}; }
    connect() { this.factory('private-test-token'); this.state.session = true; }
    disconnect() { this.state = {session:false}; this.notify(this.state); }
  }
  const context = vm.createContext({IncidentClient,Workspace,AbortController,AbortSignal,DOMException});
  const start = source.indexOf('let proposalClient =');
  const end = source.indexOf('function render(state)');
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start,end)+`
    function render(state) { if (!state.session) stopWatcher(); }
    globalThis.api = {ctx,workspace,watch(handle) { watcher = handle; handles.push(handle); }};
  `,context);
  return {...context.api,calls,setReply:callback => { reply = callback; }};
}

test('watch adapter rejects every path outside its one GET endpoint without making a request',async () => {
  const {ctx,workspace,calls} = integration();
  workspace.connect();
  for (const path of ['/ready','/v1/incidents','/v1/runbook-proposals','/v1/runbook-proposals?limit=100&incident_id=INC-A',
    '/v1/runbook-proposals/id/decision','//other.example/v1/runbook-proposals?limit=100',
    '/v1/runbook-proposals?limit=100#fragment',null,{}]) {
    await assert.rejects(ctx.client.get(path),error => error.name === 'TypeError');
  }
  assert.equal(calls.length,0);
  assert.equal(Object.isFrozen(ctx),true);
  assert.equal(Object.isFrozen(ctx.client),true);
  assert.deepEqual(Object.keys(ctx.client),['get']);
  await ctx.client.get('/v1/runbook-proposals?limit=100',undefined,{method:'POST',body:{decision:'approve'}});
  assert.equal(calls.length,1);
  assert.equal(calls[0].options.method,'GET');
  assert.deepEqual(Object.keys(calls[0].options),['method']);
});

test('watch adapter clears session and aborts/destroys watcher on either 401 or 403',async () => {
  for (const status of [401,403]) {
    const {ctx,workspace,setReply,watch,calls} = integration();
    workspace.connect();
    let destroyed = 0;
    watch({destroy() { destroyed++; }});
    const sessionSignal = ctx.signal;
    setReply(async () => { throw Object.assign(new Error('Denied'),{status}); });
    await assert.rejects(ctx.client.get('/v1/runbook-proposals?limit=100'),error => error.status === status);
    assert.equal(workspace.state.session,false);
    assert.equal(sessionSignal.aborted,true);
    assert.equal(calls[0].signal.aborted,true);
    assert.equal(destroyed,1);
    await assert.rejects(ctx.client.get('/v1/runbook-proposals?limit=100'),error => error.name === 'AbortError');
    assert.equal(calls.length,1);
  }
});

test('watch adapter discards a late response after disconnect even when transport ignores abort',async () => {
  const {ctx,workspace,setReply,calls} = integration();
  workspace.connect();
  let resolve;
  setReply(() => new Promise(done => { resolve = done; }));
  const pending = ctx.client.get('/v1/runbook-proposals?limit=100');
  workspace.disconnect();
  assert.equal(calls[0].signal.aborted,true);
  resolve({items:[{private:true}],has_more:false});
  await assert.rejects(pending,error => error.name === 'AbortError');
});
