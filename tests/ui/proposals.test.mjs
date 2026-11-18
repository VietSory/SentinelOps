import test from 'node:test';
import assert from 'node:assert/strict';
import {IncidentClient, ApiFailure} from '../../src/sentinelops/ui/assets/client.js';
import {Workspace, proposalRemaining} from '../../src/sentinelops/ui/assets/workspace.js';

const row = () => ({proposal:{proposal_version:'runbook-proposal/v1',proposal_id:'a'.repeat(64),
  incident_id:'INC-a&b',incident_revision:2,runbook_id:'rollback',score:.95,margin:.3,
  created_at:'2026-01-01T00:00:00Z',expires_at:'2099-01-01T00:15:00Z'},proposal_sha256:'b'.repeat(64),
  status:'pending',incident:{incident_id:'INC-a&b',severity:'high',root_cause_ranking:[]}});
const receipt = (value, decision) => ({...value,status:decision === 'reject' ? 'engineer' : 'approved',
  approval:{decision,approved_by:'server-operator',proposal_sha256:value.proposal_sha256}});
const deferred = () => {let resolve; const promise = new Promise(r => {resolve=r;}); return {resolve,promise};};
const client = overrides => ({list:async () => ({items:[],next_cursor:null}),readiness:async () => ({}),
  context:async (id,revision) => ({incident_id:id,revision,payload:{evidence:[]}}),
  proposals:async () => [row()],...overrides});
async function workspace(overrides={}) {
  const w = new Workspace(() => {},() => client(overrides));
  await w.connect('private-token'); await w.select('INC-a&b',2); await w.loadProposals(); return w;
}

test('proposal reads encode incident filter and pin selected revision',async () => {
  let path;
  const c = new IncidentClient('secret',async p => {path=p; return Response.json({items:[row(),
    {...row(),proposal:{...row().proposal,incident_revision:3}}],has_more:false});});
  assert.equal((await c.proposals('INC-a&b',2)).length,1);
  assert.equal(new URL(path,'http://local').searchParams.get('incident_id'),'INC-a&b');
});

test('approve/reject POST carries exact If-Match and only decision/reason, with bearer kept private',async () => {
  for (const decision of ['approve','reject']) {
    let request;
    const c = new IncidentClient('private-token',async (path,options) => {
      request={path,options}; return Response.json(receipt(row(),decision),{status:201});
    });
    assert.equal((await c.decideProposal(row(),decision,' reviewed ')).approval.approved_by,'server-operator');
    assert.equal(request.path,`/v1/runbook-proposals/${'a'.repeat(64)}/decision`);
    assert.equal(request.options.method,'POST');
    assert.equal(request.options.headers['If-Match'],`"${'b'.repeat(64)}"`);
    assert.equal(request.options.headers.Authorization,'Bearer private-token');
    assert.equal(request.options.credentials,'omit');
    assert.deepEqual(JSON.parse(request.options.body),{decision,reason:'reviewed'});
    assert.equal(JSON.stringify(c).includes('private-token'),false);
  }
});

test('foreign proposal/hash/decision receipts and incomplete list fail validation',async () => {
  for (const mutate of [r => r.proposal.proposal_id='c'.repeat(64),r => r.proposal_sha256='d'.repeat(64),
    r => r.approval.decision='reject',r => delete r.approval.approved_by]) {
    const result = receipt(row(),'approve'); mutate(result);
    const c = new IncidentClient('secret',async () => Response.json(result));
    await assert.rejects(() => c.decideProposal(row(),'approve','reviewed'),e => e.code === 'invalid_response');
  }
  const c = new IncidentClient('secret',async () => Response.json({items:[row()],has_more:true}));
  await assert.rejects(() => c.proposals('INC-a&b',2),e => e.code === 'invalid_response');
});

test('a controller consuming or escalating an approval before the reply keeps the verified receipt',async () => {
  for (const status of ['consumed','engineer']) {
    const result = {...receipt(row(),'approve'),status};
    const c = new IncidentClient('secret',async () => Response.json(result,{status:201}));
    assert.equal((await c.decideProposal(row(),'approve','reviewed')).status,status);
  }
});

test('countdown reaches zero at expiry and prevents an expired decision',async () => {
  const r = row(), expiry = Date.parse(r.proposal.expires_at);
  assert.equal(proposalRemaining(r,expiry-61000),61);
  assert.equal(proposalRemaining(r,expiry),0); assert.equal(proposalRemaining(r,expiry+1000),0);
  let sent = 0;
  const w = await workspace({decideProposal:async () => {sent++;}});
  w.state.proposals[0].proposal.expires_at='2020-01-01T00:00:00Z';
  await w.decideProposal(r.proposal.proposal_id,'approve','reviewed'); assert.equal(sent,0);
});

test('duplicate clicks send one decision; rejection displays engineer and cannot be approved again',async () => {
  const pending = deferred(); let sent = 0;
  const w = await workspace({decideProposal:() => {sent++; return pending.promise;}});
  const a = w.decideProposal(row().proposal.proposal_id,'reject','needs engineer');
  await w.decideProposal(row().proposal.proposal_id,'approve','reviewed'); assert.equal(sent,1);
  pending.resolve(receipt(row(),'reject')); await a;
  assert.equal(w.state.proposals[0].status,'engineer');
  await w.decideProposal(row().proposal.proposal_id,'approve','reviewed'); assert.equal(sent,1);
});

test('lost response or CAS failure clears decision controls until a fresh read',async () => {
  for (const status of [0,409,412]) {
    const w = await workspace({decideProposal:async () => {throw new ApiFailure(status,'proposal_changed');}});
    await w.decideProposal(row().proposal.proposal_id,'approve','reviewed');
    assert.equal(w.state.proposals,null); assert.equal(w.state.decisionLoading,false);
    assert.equal(w.state.proposalsError.status,status);
    await w.loadProposals(); assert.equal(w.state.proposals[0].status,'pending');
  }
});

test('late proposals and decisions cannot cross incidents or resurrect logout',async () => {
  const read = deferred(), write = deferred();
  const w = await workspace({decideProposal:() => write.promise});
  const decision = w.decideProposal(row().proposal.proposal_id,'approve','reviewed');
  await w.select('other',3); write.resolve(receipt(row(),'approve')); await decision;
  assert.equal(w.state.proposals,null); assert.equal(w.state.detail.incident_id,'other');
  const w2 = new Workspace(() => {},() => client({proposals:() => read.promise}));
  await w2.connect('secret'); await w2.select('INC-a&b',2);
  const loading = w2.loadProposals(); w2.disconnect(); read.resolve([row()]); await loading;
  assert.equal(w2.state.session,false); assert.equal(w2.state.proposals,null);
});
