# S5 development: independent faults and shared-consumer ambiguity

Two real local cases ran from clean committed implementation `740574ae4df3915d05353f9b7abef0a457cad7dc`, after the typed trace/contract change `e23cdc5`. The [development protocol](../../configs/s5_development_protocol.json), case parameters and protocol file hash were frozen before load. [Machine-readable evidence](2026-10-28-s5-development.json) records raw manifests, immutable reports, image digests, coverage, candidate scores and the PostgreSQL acceptance proof.

Both runs are local integration/RCA development in one shared development session. They are not public training data, independent benchmark trials or final heldout examples. The frozen GAMMA/RCAEval contract and initial RCA weights/floors remain unchanged; no model was fitted or activated.

## Actual-source outcomes

Each case requested 3 requests/s at each business entry, 300s warmup, 180s active fault and 180s recovery. Direct Payment/Inventory traffic supplements Checkout's downstream calls. Native service images were reused with recorded digests because service code/Compose/Dockerfile had not changed; the host harness/evaluator ran the new committed implementation.

| Case | Captured observations / eligible features | Retained trace scopes | Grouping / accepted direct failure support |
| --- | --- | --- | --- |
| Dual latency | 36 / 24 | Checkout 12, Payment 12, Inventory 12 | Separate Payment/Inventory groups, one ambiguous Checkout group; four direct support snapshots per root |
| Blocking errors | 36 / 27 | Checkout 8, Payment 12, Inventory 12 | Separate Payment/Inventory groups, one ambiguous Checkout group; three in-window direct support snapshots per root |

Each case had zero missed expected roots and zero groups merging the two expected roots. The shared Checkout group retained both structural alternatives. Repeating each evaluation from immutable raw data produced the identical evaluation and health/correlation/RCA references. Both services' original fault states were verified restored after each run.

During the blocking-error case, Inventory prevented some Checkout requests from reaching Payment. Direct requests still captured native Payment failures. The harness retained the complete direct scope rather than relabelling partial Checkout traces. Error-phase response controls were Checkout 502 and direct Payment/Inventory 503; warmup/recovery controls remained 200.

## Actual request denominators and negative controls

| Case / service | Observed request starts | Matching completions | Top-level requests at that entry |
| --- | ---: | ---: | ---: |
| Latency / Checkout | 1,980 | 1,980 | 1,980 |
| Latency / Payment | 3,960 | 3,960 | 1,980 |
| Latency / Inventory | 3,960 | 3,960 | 1,980 |
| Errors / Checkout | 1,985 | 1,985 | 1,985 |
| Errors / Payment | 3,429 | 3,429 | 1,985 |
| Errors / Inventory | 3,970 | 3,970 | 1,985 |

Completeness was 100% for these captured identities, above the unchanged 95% admission requirement. Payment's lower error-case count is observed, not an assumed denominator: 541 Checkout requests stopped before Payment. The checker links actual starts/completions and top-level trace/correlation identities, so neither counting every Checkout as a Payment call nor treating missing service logs as healthy passes.

Independent unsampled request-log SLO checks found four full eligible warmup buckets per service in latency and five in errors, with no sustained warmup breach. The protocol requires at least three. This is a development negative control, not certified ML training data or evidence of sustained production health.

## RCA uncertainty and clocks

Forensic RCA uses the predeclared maximum of correlation's virtual cutoff and operational receipt clocks; diagnostic windows/scores do not use injected fault times or expected-root labels. This is post-capture analysis, separate from the earlier fixed-cutoff RCA examples and historical online alert availability.

Latency root scores were about 0.400. In blocking errors, both root scores were 0.400; the shared group's root hypotheses tied. Business IF strength, observed deployment support and model risk were unavailable. RDS retained missing support in the error case; absence of DB spans was not declared healthy. Every group abstained and disallowed actions under the unchanged 0.75 score/coverage floors and 0.15 margin floor.

Observed direct residual/error support is not causal proof or permission to choose one root of an ambiguous group. Single-root top-1/top-3/MRR targets and full G4 acceptance are not established by these grouping checks.

## Multi-group persistence and software verification

The actual blocking-error bundle fed the trusted computed-IER producer against an exclusive PostgreSQL DB and real protected Uvicorn/HTTP reads. Eight concurrent retries, stale CAS/changed retry rejection, interruption after OPEN, immutable audit/outbox and DB outage/restart/retry/readback passed.

Two explicit replay intents for the **three groups of one source case** produced six replay incidents, eighteen immutable revisions and eighteen acknowledged outbox events. These are not six independent fault trials. IER model signals stayed empty, availability UNKNOWN and final stage ESCALATED; no action/recovery was asserted. Temporary DB/testbed containers and volumes were removed only within the task's own projects, while evidence was retained.

Local checks passed: 403 regression tests, focused negative/compatibility checks, 23 schemas/17 samples/two registered policy configs, and installed-wheel checks outside the checkout. Earlier actual S0/S3 RCA functional bodies, candidate values and evidence/result references remained unchanged under the new explicit direct profile, excluding code provenance.

CI adds a separate real blocking-error development job with the same 300/180/180-second protocol. Its syntax has been checked; the published candidate still needs its own exact-head five-job CI and artifact verification. Older RCA checkpoint CI is not assigned to S5 code.

## Remaining acceptance

At least five final heldout multi-fault trials remain closed until P11. RCAEval development/holdout partitioning and evaluation, ablations, model/source readiness, continuous workers, explanation/UI, controlled actions and cloud/release gates remain open. No source/target/threshold was changed to turn these checks into model or full-product acceptance.
