// WP4 acceptance (frozen): no approval countdown, early-warning cards, honest score labels.
// Spec: docs/delivery/DEMO_UPGRADE_SPEC.md, WP4 (R4.1-R4.6, A4.1-A4.6).
import test from 'node:test';
import assert from 'node:assert/strict';
import {proposalTiming, proposalStatusLabel, riskCards, warningLead, incidentTimeline, scoreLabel}
  from '../../web/src/live/demoLogic.ts';
import {validProposal} from '../../src/sentinelops/ui/assets/client.js';
import {proposalRemaining} from '../../src/sentinelops/ui/assets/workspace.js';

const NOW = Date.parse('2026-12-01T12:00:00Z');
const v2 = (extra = {}) => ({proposal:{proposal_version:'runbook-proposal/v2', approval_policy:'pending-until-decision/v1',
  proposal_id:'a'.repeat(64), incident_id:'INC-1', incident_revision:2, runbook_id:'payment-bad-deploy-rollback',
  score:.91, margin:.4, created_at:'2026-12-01T09:00:00Z'}, proposal_sha256:'b'.repeat(64), status:'pending',
  current_revision:2, ...extra});
const v1 = (expires) => {const row = v2(); row.proposal = {...row.proposal, proposal_version:'runbook-proposal/v1',
  expires_at:expires}; delete row.proposal.approval_policy; return row;};

test('A4.1 v2 proposals are reviewable without a countdown; v1 keeps its timer', () => {
  const timing = proposalTiming(v2(), NOW);
  assert.equal(timing.reviewable, true);
  assert.equal(timing.remaining, null);
  assert.equal(timing.waiting, true);
  assert.equal(timing.waitingSince, '2026-12-01T09:00:00Z');
  assert.equal(proposalTiming(v2({status:'consumed'}), NOW).reviewable, false);
  assert.equal(proposalTiming(v2({current_revision:3}), NOW).reviewable, false);
  assert.equal(proposalTiming(v2({proposal_sha256:'x'}), NOW).reviewable, false);
  const legacy = proposalTiming(v1('2026-12-01T12:05:00Z'), NOW);
  assert.equal(legacy.remaining, 300);
  assert.equal(legacy.waiting, false);
  assert.equal(legacy.reviewable, true);
  assert.equal(proposalTiming(v1('2026-12-01T11:59:00Z'), NOW).reviewable, false);
});

test('A4.2 obsolete status has a plain label in both languages', () => {
  assert.equal(proposalStatusLabel('obsolete', 'vi'), 'Đã đóng: tình trạng đã thay đổi');
  assert.equal(proposalStatusLabel('obsolete', 'en'), 'Closed: situation changed');
  assert.equal(proposalStatusLabel('pending', 'vi'), 'Chờ duyệt');
  assert.equal(proposalStatusLabel('pending', 'en'), 'Waiting for approval');
});

test('A4.3 risk cards show watch/degrading, mute unavailable, keep nulls', () => {
  const body = {schema_version:'live-risk/v1', services:{
    'payment-service':{state:'degrading', score:.71, episode_active:true, episode_started_at:'2026-12-01T11:56:00Z',
      last_scored_at:'2026-12-01T11:59:30Z', thresholds:{watch:.485, degrading:.665}},
    'checkout-service':{state:'watch', score:.5, episode_active:false, episode_started_at:null,
      last_scored_at:'2026-12-01T11:59:30Z', thresholds:{watch:.485, degrading:.665}},
    'inventory-service':{state:'normal', score:.1, episode_active:false, episode_started_at:null,
      last_scored_at:'2026-12-01T11:59:30Z', thresholds:{watch:.485, degrading:.665}}}};
  const cards = riskCards(body);
  assert.deepEqual(cards.map(c => [c.service, c.state, c.muted]),
    [['payment-service', 'degrading', false], ['checkout-service', 'watch', false]]);
  assert.equal(cards[0].score, .71);
  assert.equal(cards[0].startedAt, '2026-12-01T11:56:00Z');
  assert.deepEqual(cards[0].thresholds, {watch:.485, degrading:.665});
  body.services['inventory-service'] = {state:'unavailable', score:null, episode_active:null, episode_started_at:null,
    last_scored_at:null, thresholds:{watch:null, degrading:null}};
  const muted = riskCards(body).find(c => c.service === 'inventory-service');
  assert.equal(muted.muted, true);
  assert.equal(muted.score, null);
  assert.deepEqual(riskCards({schema_version:'other'}), []);
  assert.deepEqual(riskCards(null), []);
});

test('A4.4 warning lead is measured from the last episode start before onset', () => {
  assert.equal(warningLead(['2026-12-01T10:00:00Z', '2026-12-01T11:55:30Z'], '2026-12-01T12:00:00Z'), 270);
  assert.equal(warningLead(['2026-12-01T12:01:00Z'], '2026-12-01T12:00:00Z'), null);
  assert.equal(warningLead([], '2026-12-01T12:00:00Z'), null);
  assert.equal(warningLead(['2026-12-01T10:00:00Z'], '2026-12-01T12:00:00Z'), null);
  assert.equal(warningLead(['2026-12-01T11:55:30Z'], null), null);
});

test('A4.4 incident timeline is ordered with gaps and skips missing steps', () => {
  const steps = incidentTimeline({warning:'2026-12-01T11:55:30Z', breach:'2026-12-01T12:00:00Z',
    incident:'2026-12-01T12:02:30Z', approved:null, recovered:'2026-12-01T12:10:00Z'});
  assert.deepEqual(steps.map(s => [s.key, s.secondsFromPrevious]),
    [['warning', null], ['breach', 270], ['incident', 150], ['recovered', 450]]);
});

test('A4.5 score labels are not percentages', () => {
  assert.equal(scoreLabel('rca', .82, 'vi'), 'Điểm RCA 0,82');
  assert.equal(scoreLabel('rca', .82, 'en'), 'RCA score 0.82');
  assert.equal(scoreLabel('runbook', .91, 'vi'), 'Mức khớp runbook 0,91');
  assert.equal(scoreLabel('runbook', .91, 'en'), 'Runbook match 0.91');
  assert.equal(scoreLabel('rca', null, 'vi'), 'Điểm RCA —');
  assert.ok(!scoreLabel('rca', .82, 'vi').includes('%'));
});

test('A4.6 the shared client accepts v2 proposals and the old timer ignores them', () => {
  assert.equal(validProposal(v2()), true);
  assert.equal(validProposal(v2({status:'obsolete'})), true);
  assert.equal(validProposal(v1('2099-01-01T00:00:00Z')), true);
  const broken = v2(); broken.proposal = {...broken.proposal, proposal_version:'runbook-proposal/v3'};
  assert.equal(validProposal(broken), false);
  const missingPolicy = v2(); delete missingPolicy.proposal.approval_policy;
  assert.equal(validProposal(missingPolicy), false);
  assert.equal(proposalRemaining(v2(), NOW), null);
  assert.equal(proposalRemaining(v1('2026-12-01T12:05:00Z'), NOW), 300);
});
