# Captured-source RCA and computed IER persistence

The bounded adapter was committed as `5f39b7a614783eb3724ada3e8301723a31bb40d6` before actual-source scoring. Initial weights/floors were frozen in the preceding core commit `064a08b`; no RCAEval tuning, model fit, source substitution or threshold relaxation was performed. [Machine-readable evidence](2026-10-28-rca-replay.json) records source/report identities, support values and the actual PostgreSQL acceptance report.

## Raw-source results

Both inputs are previously collected local development captures, not additional public training datasets. The adapter reproduces raw normalization, health and correlation before extracting business completion, native trace and connection-capacity facts. Each output archives its complete input scope and standalone RCA detail. Replaying each archived bundle without the original source directories produced the identical report reference.

| Case | Health/correlation outcome | RCA outcome | Archived source objects |
| --- | --- | --- | --- |
| S0 | No opening/group | No hypothesis or incident | 135 |
| S3 | One Payment/Checkout group | Positive support ranking below; **abstained** | 285 |

S3's analytical cutoff is `2026-10-26T03:37:00Z`; its bounded evidence window begins at the observed `03:29` onset. Of 20 captured traces, 15 were outside that window and two had no observed violation; three failing/slow traces supplied support. This is a captured-probe subset, not a request-rate/accuracy denominator.

| Candidate | Weighted support score | Known-weight coverage |
| --- | ---: | ---: |
| Payment | 0.3962432543 | 0.40 |
| Checkout | 0.1512406241 | 0.40 |
| RDS | 0.0061604322 | 0.50 |
| Inventory | 0.0001497816 | 0.40 |

Payment has observed trace-duration share support `0.9849730173` and log presence support `1`; this is not causal proof. Business IF strength, recent deployment support and temporal precedence remain missing. Equal SLO onset buckets do not establish ordering. RDS uses the explicitly labelled connection fraction, not an IF or overall DB-health claim. Missing weights are not redistributed. Score/coverage floors remain `0.75/0.75`, margin floor `0.15`.

Abstention reasons are `insufficient-score`, `insufficient-coverage` and `source-availability-unverified`. Old log/trace receipt clocks were not invented. A top-ranked Payment hypothesis in one development example does not establish Q-08, top-k accuracy, MRR, online availability, MTTD or causality. Models remain unavailable and actions remain disabled.

## Computed IER / PostgreSQL / HTTP acceptance

An immutable producer intent freezes the actual replay emission time before DB writes. `detected_at` and trigger emission use that clock; historical onset and virtual RCA cutoff remain source facts. Reuse the exact intent on retries. Creating a new intent is an explicit new replay, with a distinct incident identity.

The trusted producer recomputes archived raw lineage and RCA support against the selected policy before writing. It projects legacy IERs with `service_signals=[]`, keeps source-linked hypotheses and abstention detail, and writes `OPEN → RCA_READY → ESCALATED` through the existing nonowner repository. Availability stays UNKNOWN. It neither projects recovery nor grants remediation authority.

Real acceptance used an exclusive PostgreSQL 16 container/volume and a real Uvicorn/HTTP process. Checks passed:

- Eight concurrent submissions of the same intent return the same projection; retrying OPEN returns its original revision.
- Changed retry contents and stale revision CAS are rejected.
- A second explicit intent simulates interruption after OPEN, then resumes without duplicate revisions.
- Both replay incidents retain exactly three immutable revisions: six revisions and six transactional outbox events in total. These are two replay intents for **one source incident**, not two independent RCA examples.
- Protected HTTP reads return the computed payload/context/audit; unauthenticated reads fail. Default model readiness remains 503.
- DB outage makes writes/readiness fail closed while API liveness remains available; after restart, payloads/history survive and the original intent retries identically.
- All six outbox events survive until claimed/acknowledged; no pending test events remain.
- Actual S0 publication emits zero incidents and leaves DB incident count unchanged. The CLI successfully retries the first S3 intent without creating another incident.

The first restart attempt failed because Docker's automatically allocated host port changed on stop/start. Its log/evidence was preserved. A new exclusive sandbox with an explicit host port passed the same checks; timeouts/assertions were not relaxed. CI uses an explicit port and verifies that restart preserves the selected mapping.

The producer implementation is committed as `2405b80` with the same source bytes measured by the initial acceptance report. After the clock-driver fix `de769f1`, final software checks passed: 45 focused tests, 370 regression tests, 23 schemas/17 samples/two actual policies, and installed-wheel imports/schema/semantic/API checks outside the checkout. These are separate from raw-source and real-service acceptance. Exact-head remote CI is still required for the published candidate; older CI is not assigned to these changes.

## Fresh Step 4 integration

A separate local Docker project ran a new healthy/latency/error/recovery workload from clean HEAD `06cb020`, with matching clean start/end identities. Step 4 passed with 27 observations and 15 eligible feature windows. Its new immutable aggregate evidence manifest fed the complete health → correlation → RCA chain, followed by a second exclusive PostgreSQL/protected-HTTP acceptance run. Concurrent retry, crash resume, immutable audit/outbox, DB outage/restart and protected read checks all passed again.

The fresh source's analytical cutoff was `2026-10-28T02:24:00Z`. Trace/log receipts were `02:24:31.193429Z` / `02:24:31.380084Z`, so those modalities were correctly unavailable at that cutoff. The one observed Payment/Checkout group therefore retained missing candidate scores and abstained with `no-positive-observed-support` and `unobserved-dependency-candidates`. It did not acquire a fabricated healthy trace, IF score, root diagnosis or action eligibility. The older S3 scored example above and this late-receipt example test different evidence conditions.

Fresh replay cutoffs can also precede actual publication by being slightly ahead of wall time. The acceptance driver waits in bounded intervals for at most the local health policy's 60-second lag; farther-future input fails. Production intent preparation still rejects a future cutoff. Two clock-driver tests cover waiting without backdating and refusing an unbounded wait. This does not permit source facts received after the declared analytical cutoff.

## Limits and remaining gates

This establishes analytical source extraction, deterministic scoring/abstention, portable replay and a protected local persistence path. It does not establish production authentication/deployment, continuous processing, recovery/action execution, model performance or complete G4 acceptance. Real independent-root S5, RCAEval development/holdout partitioning and evaluation, ablations, model-signal integration and worker checkpoints remain separate work. The frozen GAMMA/RCAEval contract, five live HTTP features and pre-SLO XGBoost target are unchanged.
