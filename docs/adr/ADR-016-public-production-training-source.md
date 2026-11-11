# ADR-016 — Train detectors on public production traces (Alibaba v2022)

**Status:** Accepted 2026-11-08 — explicit owner approval ("Duyệt, bắt đầu ngay")
after the owner decided that self-generated data must not be a training source.
**Supersedes:** ADR-014 for the **training source** of Isolation Forest and XGBoost.
ADR-014's evidence discipline (provenance, grouping, chronology, label freeze,
sealed final) still applies. The local HTTP corpus and ADR-015 epochs become
**evaluation-only**.
**Related:** ADR-002, ADR-008, ADR-010, ADR-014, ADR-015.

## Context

Owner and examiners require training data from a reputable, real source. No
public dataset matches the product's HTTP RED features with incident labels.
Alibaba's `cluster-trace-microservices-v2022` is production telemetry from 1,300+
microservices and 90,000+ containers. Its `MSRTMCR` table has per-container,
per-minute HTTP response time (`http_rt`) and call rate (`http_mcr`), but no error
or anomaly fields. The repository README permits research and study use and asks
publications to notify the maintainers.

## Decision

1. **Training source:** `MSRTMCR` files fetched from the official OSS location
   named in the repository script. Raw archives are immutable and checksummed.
   Microservices and time ranges are assigned to train / calibration /
   evaluation splits **before** any model is fitted; splits are disjoint by
   microservice.
2. **Feature set `http-relative-5m/v1`:** a new versioned set, causal and
   service-relative. The 300-second window and 60-second step are unchanged. Each
   feature compares the service's last five minutes with its **own trailing
   history** (latency level and slope, call-rate level and slope). Scale-free
   ratios let models trained on Alibaba run on Checkout/Payment/Inventory without
   any local fitting data. Live inference uses the same transform on the serving
   stream.
3. **IF** fits healthy-reference windows from Alibaba train services. Healthy means
   no latency breach in the window and its label horizon under the frozen rule
   below. Thresholds are calibrated on the Alibaba calibration split with the
   existing 1% false-alert budget.
4. **XGBoost** keeps its role and the 300-second horizon. A positive is a breach
   in `(t, t+300s]` with no breach at `t`. A breach is the service's per-minute
   latency exceeding a frozen, service-relative rule. That rule is fixed in a
   versioned label config **before** any fit or comparison.
5. **Errors (5xx):** Alibaba has no error field. Error detection remains with the
   objective SLO rule and the MAD control, whose baseline is computed online from
   the service's own history. This limitation is stated in every report.
6. **Evaluation:** Alibaba evaluation split (unseen microservices), the
   ADR-015 detector-event epoch, and new local evaluation epochs. Local data is
   **never** used to fit, preprocess, select or threshold models. RCAEval remains
   RCA-only.
7. **Unchanged:** IER and contracts, workers, RCA policy, UI, release/registry
   gates, H4/G6 paths, the remediation safeguards, and the sealed final protocol.
   The registry accepts the new feature set only through a new versioned bundle
   origin.

## Consequences

- Live features need mean latency, so the source profile gains histogram
  `_sum/_count` queries under a new version. Older captures lack `_sum` and can be
  evaluated only with a disclosed p95-relative fallback.
- Candidate `38bcc766` (local-trained) remains integration evidence only: its H4
  pass shows the pipeline works with an approved model. It is not the reported
  model.
- The scenario mismatch is real: Alibaba breaches are production latency
  regressions, not the local gradual ramps. Claims are scoped accordingly.

## Owner decisions fixed before any result (2026-11-08)

- **Official model:** the reported model is always the Alibaba-trained one. If it
  underperforms on the local system, report that honestly with its causes. Do not
  revert to self-generated training data.
- **Role split:** ML covers latency anomaly detection and breach forecasting.
  HTTP 5xx is covered by the objective SLO rule and the MAD control.
- **Final results:** two layers, each frozen before it runs and run once:
  1. unseen Alibaba microservices;
  2. a new local evaluation epoch locked in advance.
- **Schedule:** more than three weeks. Complete the local work first, then AWS
  and Bedrock.

## Integration plan (architect decisions)

1. **Same statistic as training.** Add per-minute mean latency
   (`increase(_sum)/increase(_count)` of the duration histogram) under a new source
   profile `prometheus-increase-60s-5s/v2`. p95 is kept for the SLO rule; the
   relative model features use the mean, matching Alibaba `http_rt`. v1 captures
   remain readable; they cannot feed the new models.
2. **Relative features at inference.** The model-signal worker builds
   `http-relative-5m/v1` from the service's own accepted observations: trailing
   60 minutes, minimum 5, strictly before the window, with the same transform
   code as training (`ml/alibaba_training.py`, shared rather than duplicated).
   Before the minimum history exists, signals are explicitly unavailable, never
   zero.
3. **Registry and release.** A new bundle origin, `admitted-alibaba-v2022`, binds
   the archive checksums, the frozen config hash and the fitting commit. The
   registry accepts it only with that origin and the matching feature set. The
   G3/parity/release gates are unchanged.
4. **Evaluation layer 2 (D-21).** A new local evaluation epoch on profile v2,
   with ≥30 healthy minutes before each fault. It is frozen before capture and
   evaluated once.
5. **Retirement (after the Alibaba release passes H4).** Remove local-corpus
   training code and contract v2, and move ADR-014 to the archive, after the
   owner reviews the file list. The testbed and the evaluation epochs stay.
   *Done 2026-11-09 (D-23):* H4 passed and the owner approved the list. Entry
   points, handoff, release, parity and the `admitted-local-http` bundle origin
   were removed. Contract v2 and ADR-014 stay in place as superseded records,
   because ADRs are never deleted. Step 2 removed the fitting-row path
   (`admitted_local_rows` and the local branch of `load_training_rows`). The
   capture plan, admission and freeze stay: they are the data pipeline for the
   local RCA development evaluation (Q-08) and the evaluation campaigns.

## Owner exclusion of an incident group (2026-11-11)

Epoch 2 (`alb-*`, source `3b17205`) stopped with `STOPPED_REQUIRES_INVESTIGATION`:
the host slept for 7 h 48 min inside `alb-04-inventory-abrupt` (systemd: 19 h 33 min
wall clock against 11 h 44 min monotonic; one 28 094 s gap in that slot's client
ledger, every other slot ≤ 1.2 s). The slot failed source verification (no 30
verified healthy pre-fault minutes). The frozen dispositions cover a host restart and
invalid client UTC only. Owner decision: treat it as a one-off incident, exclude the
whole group, replace it and finish the epoch; no measurement criterion changes.

`python -m scripts.exclude_incident_group --root STORE --group 4 --replacement-group 6
--decision TEXT` records that decision once per epoch
(`local-http-owner-exclusion/v1`). It refuses a fully verified group and a failure
without an independent host-pause witness (an unverified slot whose original client
ledger has request starts more than 300 s apart), keeps and binds
every source byte, stops (never removes) the group's containers and uses an ordinal
beyond the reserved minimum (`alb-04` → `alb-06`). Its commit, a descendant of the
epoch source that changes only the recovery and source-binding code, becomes the
epoch's successor capture source for the replacement groups.
