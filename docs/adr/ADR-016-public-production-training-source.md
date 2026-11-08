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
