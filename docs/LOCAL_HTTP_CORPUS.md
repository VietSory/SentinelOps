# Prospective local HTTP corpus — ADR-014

Owner approval on 2026-10-31 accepts [ADR-014](adr/ADR-014-proposed-local-training-evidence.md).
[Training contract v2](../configs/training_data_contract_v2.json) governs new prospective fitting.
The original v1 contract/schema and source hashes remain historical reproduction inputs.
RCAEval is still evaluation-only; previous pilots/worker captures cannot be promoted.

## Capture registration

The [capture plan](../configs/local_http_capture_plan.json) fixes role/group bounds,
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
