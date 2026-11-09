# G6 local trials — Alibaba release `3966e75e` (2026-11-09)

Release `artifact://manifests/3966e75e…3730.json` (bundle `badf9da0`, G3 accepted
with conditions, D-22). Operator: Claude, delegated by the owner for these trials.
Each trial runs on a new owned kind cluster from a clean checkout. The known-good
ledger is approved before the bad image is deployed. Native H4 grants dispatch
authority, then rollback runs, then the 600 s executor verification decides the
terminal outcome. Summary: `scripts/summarize_g6_trials.py` (receipts only).

## Golden path (Q-09: ≥ 9/10 recovered for this release, no pooling)

| Trial | Commit | Outcome | Bad image → action | Action → terminal |
| --- | --- | --- | --- | --- |
| 02 | `68abb6f` | recovered | 6 m 44 s | 5 m 00 s |
| 03 | `58dfaaf` | recovered | 6 m 47 s | 5 m 01 s |
| 04 | `58dfaaf` | **blocked** | 6 m 49 s | 0 s |
| 05 | `58dfaaf` | recovered | 6 m 48 s | 5 m 03 s |
| 06 | `f615428` | recovered | 7 m 04 s | 5 m 00 s |
| 07 | `f615428` | recovered | 6 m 49 s | 4 m 57 s |
| 08 | `f615428` | recovered | 6 m 47 s | 4 m 59 s |
| 09 | `f615428` | recovered | 6 m 48 s | 5 m 00 s |
| 10 | `f615428` | recovered | 7 m 04 s | 5 m 01 s |
| 11 | `927a3e8` | recovered | 7 m 02 s | 4 m 56 s |

Trial 04: the harness sent request revision 1 after the incident had advanced to
revision 2. The executor guard rejected the request and nothing was mutated. This
is correct guard behaviour and a harness defect, fixed in `f615428` (re-snapshot
when the revision advances before dispatch). It counts as not recovered. It is not
re-run or excluded.

## Failed verify (Q-09: 100 % escalated with a complete audit)

| Trial | Scenario | Outcome | Audit chain |
| --- | --- | --- | --- |
| 12 | `commerce-db-unavailable-after-rollback` (commit `927a3e8`) | **escalated**, reason `verification-timeout-target-quarantined` | complete: same action ID, hash chain from `prepared` to `escalated`, ending at the last event hash |

## Safety (Q-10)

Trials 02–12 (summary v2): 10 mutations, 0 unsafe. Every patch set the
known-good image approved before the bad change, on the target pinned by the
policy (patch target hash = `stable_id` of the pinned target). Trial 04 made no
mutation. No background task failures were recorded in any trial. Q-10 also requires the
mandatory safety cases (negative, crash and concurrency tests); this table is
only the per-trial input.

## Trial 12 detail

The rollback was dispatched at 08:31:40Z. The harness then scaled the in-cluster
commerce PostgreSQL to zero. Payment needs the database to start and to pass
`/ready`, so the rolled-back pod never became healthy. The executor escalated when
the 600 s verification window expired, and the target stays quarantined for operator
review. The harness change `927a3e8` (retry source outages only during the injected
outage) was in place but not exercised: no `incident-source-unavailable` event
occurred.

## Result

Measured against Q-09 for this release (no pooling):

| Clause | Target | Measured |
| --- | --- | --- |
| Golden path | ≥ 9/10 recovered | **9/10** (02–11; 04 blocked by a harness race, counted) |
| Failed verify | 100 % escalated with complete audit | **1/1** |
| Per-trial unsafe patches (Q-10 input) | 0 | **0** of 10 mutations |

The release meets the local Q-09 criteria exactly at the threshold. It is not
yet G6 PASS: Q-10 still requires the mandatory negative, crash and concurrency
safety cases, and G6 acceptance is an owner decision (`G6_accepted=false` in every
receipt). The failed-verify clause rests on a single scenario.

The earlier trial with H1 release `656bc7c5` (2026-11-08, recovered) is reported
separately and is not pooled. The host was released to the layer-2 evaluation at
08:41:50Z.
