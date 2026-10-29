# Continuous incident consumer — measured local integration

This report records actual local health → correlation → operational RCA → durable
IER processing, followed by independent reads of the protected HTTP API and
portable source reconstruction. The [machine-readable measurements](evidence/2026-10-29-continuous-incident-local.json)
preserve capture checksums, source clocks, group decisions and verification scope.
These are development integration cases, not training data or independent accuracy trials.

## Native results

| Selected case | Accepted buckets / signals | Incident groups | Final lifecycle |
| --- | --- | --- | --- |
| Original S1 protocol | 19 / 57 | Checkout + Payment | OPEN → RCA_READY → ESCALATED → CLOSED |
| Original S5 independent latency + declared healthy observation | 17 / 51 | Inventory; Payment; ambiguous Checkout | All three complete the same four revisions |
| Original S5 independent blocking errors + declared healthy observation | 17 / 51 | Inventory; Payment; ambiguous Checkout | All three complete the same four revisions |

Each consumer was killed after a durable IER existed, then restarted as two
competing processes with the same source/profile/store identity. Accepted input
and the source watermark were reconstructed in full; every incident has exactly
four revisions, commands, command receipts, audit events and outbox events. The
current pointer agrees with history. S1 additionally retains the producer's
process/database/source outage and actual OTLP/Prometheus evidence. An isolated
PostgreSQL disconnect probe verified that a writer losing its session lock cannot
continue through another connection. The consumer therefore uses a dedicated
nonpooled writer; source reads use a separate pool.

For both S5 cases, Inventory and Payment remain distinct single-service structural
candidates. Checkout has `grouping_reason=ambiguous-shared-consumer`, both structural
candidates, and `ambiguous-correlation` abstention. This is checked explicitly even
if a faulty implementation would label Checkout as a single-service group.
Structural candidates are not a localized root-cause claim.

All seven incidents naturally recovered from their matching accepted source
episodes. Current UNKNOWN availability still blocks closure even when older
recovery decisions exist; the separate backlog/database fixture exercised that
negative path. No recovery/action writer inferred health from missing data.

RCA continues to abstain. Captures contain 2,268 business completion records and
three selected traces for S1; the three latency jobs retain 3,735/3,785/3,840
completion records and nine traces each; the errors jobs retain
3,274/3,324/3,374 records and nine traces each. Captures for neighboring jobs can
overlap: these counts must not be summed as independent observations. Missing
anomaly/model support remains missing; existing RCA weights and floors are unchanged.

## Source clocks and limits

The fixed two-bucket opening persistence and inclusive 120-second onset grouping
require a 240-second accepted-input membership watermark from the first onset.
This wait is distinct from processing time. In S1, source onset was 04:50 UTC,
the closing source bucket ended at 04:54, its actual health decision was
04:54:32.895309, and the immutable intent clock was 04:54:33.644989. The approximately
0.750-second decision-to-intent difference is not end-to-end MTTD. Native receipt,
intent and persistence clocks are retained separately; replay does not manufacture
historical alert emission, notification availability or causality.

The native captures used commit `342b5c5`; committed source `ec27de0` has the same
tree. Original capture identities remain unchanged. Later additions strengthen
verification and package a portable replay; they are tested against the original
captures and exercised again by each published candidate's CI. They do not relabel
old live evidence as a fresh run of later code.

## Verification beyond the live assertions

The protected Uvicorn/PostgreSQL API was checked against all seven native incidents:
28 current/historical revisions and 28 outbox rows, unauthenticated rejection,
authenticated readiness/reads, stable `detected_at`, context/history, ETag 304 and
bounded list/timeline pagination. Selected revision/outbox counts stayed unchanged.
Demo mode was disabled and default model readiness remained 503.

The portable verifier recomputes raw health decisions, every consumer state/view,
group membership and intent, raw operational RCA, immutable command fingerprints,
IER payloads, receipts, audit/outbox projection and matching recovery/current
availability references. Each real capture also passed ten isolated mutation
probes: trusted incident and health snapshot checksum mismatch, hash-valid IER
mutation, mathematically valid but raw-unsupported RCA, wrong receipt revision,
missing source group, raw byte corruption, duplicate source step, altered audit
actor and altered outbox lifecycle. Original capture files stayed unchanged.
Per-object byte bounds, duplicate/nonfinite JSON and disabled-assertion rejection
are covered separately. Supplied trusted checksums select captured bytes;
self-consistency alone is not publisher authentication.

Software checks on the final code candidate passed: 73 focused tests, 526 regression
tests (one upstream deprecation warning), 23 schemas, 17 samples, two policy
configurations, a wheel installed and checked outside the checkout, and both
Compose configurations. CI runs the original native S1 and both original S5 cases,
portable positive/negative replay and actual protected HTTP before uploading their
evidence. Every published SHA still requires its own successful CI and inspection
of the transported artifacts; this report records local evidence.

## Unaccepted gates

This implements the local continuous downstream path. It does not accept full G4,
GAMMA healthy-reference/precursor or live feature parity gates, model fitting or
activation, final S5/RCAEval heldout evaluation, ablations/localization accuracy,
all-module operational metrics, explanation/UI, remediation or production HA.
GAMMA/RCAEval source roles and the frozen training contract remain unchanged;
no third training source, holdout opening or action permission is introduced.
