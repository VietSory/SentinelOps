# Prospective local HTTP corpus — ADR-014

Owner approval on 2026-10-31 accepts [ADR-014](../adr/ADR-014-proposed-local-training-evidence.md).
[Training contract v2](../../configs/training_data_contract_v2.json) governs new prospective fitting.
The original v1 contract/schema and source hashes remain historical reproduction inputs.
RCAEval is still evaluation-only; previous pilots/worker captures cannot be promoted.

## Capture registration

The [capture plan](../../configs/local_http_capture_plan.json) fixes role/group bounds,
traffic, targets and durations before source access. Whole acquisition sessions
contain healthy, Payment-gradual and Inventory-gradual runs; all members retain
one group/role. `train`, `calibration`, `development-validation` and `sealed-final`
remain distinct even when legacy run metadata maps the two middle roles to
`validation`. The prospective registration is the new role authority.

`experiments.local_corpus` registers an immutable provisioning intent, creates
only an unused owned Compose context, verifies container/volume creation clocks,
records images/configuration/host context and checks the initial payments table.
An interrupted startup can resume its own intent, but cannot appropriate an
unregistered existing project/volume. A new session registration precedes traffic.
The existing runner then captures original metrics, logs, traces and a separate
request-outcome ledger, including monotonic client duration. Traffic enters all
three services directly. A gradual run targets Payment or Inventory; control
schedules remain outside serving features.

Every slot has an exclusive pre-traffic registration and a content-addressed
completed-result binding. A failed/interrupted slot cannot be overwritten or
admitted as completed. Earlier-role capture is rejected after a later role begins;
transition requires the previous role's minimum complete groups. This acquisition
check is not a healthy/precursor admission certificate. Source verification and
actual counts must still pass before any estimator is constructed.

Preparing the next group stops only verified owned containers from completed
sessions. Volumes and source evidence are preserved. Incomplete sessions block
that transition. Container recreation and context changes within an open session
are rejected. Fresh contexts and distinct names do not by themselves establish
statistical independence; the corpus verifier must retain whole-group provenance
and report the realized independent-support limitations.

## Local execution

Use a clean committed capture checkout. Keep it immutable during capture; use
an isolated worktree if implementation continues elsewhere. Source/plan/image
hashes and capture receipts must match that frozen checkout. The CLI owns only
`sentinelops-corpus-*` Compose projects. PostgreSQL uses loopback port 55435;
business/Prometheus/OTel/Jaeger ports are the existing local testbed ports.
Existing projects/volumes are never deleted to satisfy admission.

```sh
python scripts/capture_local_corpus.py --root artifacts/local-http-corpus-v1 \
  --role train --group 1 --kind healthy --prepare
python scripts/capture_local_corpus.py --root artifacts/local-http-corpus-v1 \
  --role train --group 1 --kind payment-gradual
python scripts/capture_local_corpus.py --root artifacts/local-http-corpus-v1 \
  --role train --group 1 --kind inventory-gradual
```

Current plan: healthy train active time 6300 seconds plus 300-second warmup,
calibration 3300 plus warmup; each gradual run has 300-second warmup,
1200-second ramp and 360-second recovery. Each direct entry runs at 1 request/s.
Durations are acquisition budgets, not promises of eligible windows. Missing
traffic, source gaps, unexpected outcomes or SLO breaches may invalidate a run.
Do not shorten the 300-second feature history or multiply correlated windows into
independent-run counts. No final capture/read is permitted by this CLI.

## Admission and model boundary

Registration and run completion do not certify healthy data. The source-backed
verifier must reconstruct raw features, reconcile the complete client/server
oracle and compute actual eligible SLO intervals. Healthy labels require observed
activity and compliance; risk positives require independently measured future
SLO onset within the accepted 300-second horizon and verified pre-onset deterioration.
Unknown/censored intervals remain unavailable. Intervention timestamps alone are
not targets. Model fitting and activation remain blocked until every gate passes.

The implemented verifier is `ml.local_data.audit_local_corpus`. It verifies original
run/source/image identities, loaded implementation hashes against Git objects,
exclusive registrations, raw replay, nonoverlapping chronological whole groups,
fresh container/volume identities and actual post-run Docker process clocks. A
missing context receipt is pending, not admitted; corrupt completed evidence is
quarantined without deleting it. `inspect_local_capture.py` records a real receipt
after completion, including its own committed observer identity. It does not
invent a receipt at the historical event time.

The oracle reconciles unsampled request starts/completions with native trace,
request and correlation IDs and an independent client status/elapsed ledger.
Every complete bucket needs sufficient directly observed traffic. Missing starts,
completions or traffic make affected buckets UNKNOWN; conflicting identities,
statuses or source clocks reject the evidence. JSON round trips preserve bucket
keys. Both client and server clocks are checked according to their actual semantics;
UTC and monotonic duration samples are not assumed to be atomically identical.

Features are reconstructed from past raw observations at their actual availability
cutoff and frozen before any outcome joins. The risk forecast anchor `t` is the
minute-aligned closed feature-window end; receipt and inference completion must
remain in that same minute. SLO onsets occur on minute boundaries, so this retains
the identical set of future onsets in `(t,t+300s]`. A delayed prediction crossing
the next minute has unavailable risk, while otherwise valid IF inference remains
available under its existing freshness rule. Negative labels require the complete
two-bucket SLO confirmation tail at the horizon endpoint. Incident/post-incident,
censored or incomplete windows do not supply risk targets. A positive additionally
needs three strictly rising independently observed pre-onset p95 buckets and
corroborated actual gradual control steps; controls are never model features.

```sh
python scripts/inspect_local_capture.py --root artifacts/local-http-corpus-v1 \
  --slot train-01-healthy
python scripts/audit_local_corpus.py --root artifacts/local-http-corpus-v1 \
  --output artifacts/local-http-corpus-v1/admission.json
```

Only a fully reverified READY_FOR_FITTING report may use `--freeze`. The exclusive
freeze binds that exact report and rejects further acquisition in this corpus
epoch. Training recomputes the original evidence again and consumes only admitted
train/calibration rows. Development is checked for independent support but its
numeric rows never enter fitting. Final references remain unopened. Caller-authored
READY/PASS flags, historical pilots and relabelled v1 bundles cannot bypass this.

## Bounded campaign and restart

`capture_local_campaign.py` acquires the plan's minimum three train, two calibration
and five development groups, with all three capture kinds in each: 30 nonfinal
slots. Its configured workload budget is 1170 minutes (19.5 hours), excluding
startup, collection, verification and interruptions. This is a budget, not an
eligible-data or completion guarantee. It never opens final data or fits/activates
a model. Source gaps, insufficient precursors or other failed gates remain blocked.

```sh
python scripts/capture_local_campaign.py --root /absolute/corpus/root --follow-existing
```

Run from an immutable clean worktree. The controller preserves the exact original
clean checkout of any existing acquisition group, then uses its own frozen source
for later groups. It waits for a registered active slot only in explicit follow
mode. A process failure never replays an interrupted workload or overwrites its
slot. Completed receipts and content-addressed campaign events allow resume;
incomplete/failed sources stop for investigation. A same-plan descendant controller
fix binds a new immutable successor receipt, preserving the original registration;
changed roles, plans and budgets are rejected. Separate campaign and
acquisition locks reject competing controllers/workloads; process exit releases
the locks. End-context verification precedes source admission and the next slot.
Before a later partition starts, the prior role's measured per-service admission
must pass. Otherwise the controller stops while additional whole earlier-role
groups can still be collected within the predeclared maximum; it cannot silently
make a deficient train/calibration split unrepairable by advancing chronology.
