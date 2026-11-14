# Step 3 — OpenTelemetry Observability Baseline

## Goal

Instrument the three business services so a single request produces correlated:

- metrics
- structured logs
- distributed traces

Local development uses the pinned upstream OpenTelemetry Collector and Jaeger only as development sinks. The Collector image supports the configured Prometheus exporter and health-check extension. The AWS target remains ADOT with Amazon CloudWatch Metrics/Logs and AWS X-Ray as the canonical observability destinations.

## What is frozen

1. OpenTelemetry is the application instrumentation standard.
2. `correlation_id` remains an application-level correlation key.
3. `trace_id` / `span_id` come from OpenTelemetry.
4. Checkout propagates W3C trace context to Payment and Inventory.
5. Metrics use low-cardinality service/route/method/status dimensions.
6. Logs remain JSON on stdout; AWS deployment can ship stdout to CloudWatch Logs.
7. No ML, RCA, Bedrock or remediation is added in this step.

## Local topology

```text
Checkout / Payment / Inventory
            |
            | OTLP gRPC :4317
            v
   OpenTelemetry Collector
        |             |
        |             +--> Prometheus-format :8889
        |
        +--> Jaeger :4317 --> Jaeger UI :16686
```

## Signals

### Metrics

- `sentinelops_http_requests`
- `sentinelops_http_request_duration_ms`
- `sentinelops_http_errors`

These are exported to the Collector and exposed in Prometheus format on port 8889.
Prometheus may append unit/type suffixes, for example `_total` for a counter and
`_bucket`, `_sum`, and `_count` for the duration histogram. These are exported
names; the instrumentation names above remain stable.

Dimensions are `service`, `method`, `route` and `status_code`. Parameterized routes
use their template, such as `/v1/inventory/{sku}`. Unknown URLs share the route
`__unmatched__` so arbitrary paths do not create new metric series. Correlation
and trace IDs are kept in logs/spans, never in metric labels.

### Logs

Each JSON log contains:

- `timestamp_unix_ms`
- `service`
- `event`
- `correlation_id`
- `trace_id`
- `span_id`
- event-specific fields

### Traces

A checkout request should result in a trace containing at minimum:

```text
checkout-service (SERVER)
├── inventory-service (CLIENT, emitted by Checkout)
│   └── inventory-service (SERVER)
└── payment-service (CLIENT, emitted by Checkout)
    └── payment-service (SERVER)
```

Checkout injects W3C headers **inside** each active CLIENT span, so the downstream
SERVER span has that CLIENT span as its parent. All spans share the same trace ID.
Responses include `x-correlation-id` and `x-trace-id`. Server spans with HTTP 5xx
and client spans with downstream errors/timeouts carry an error status.

`/health` is a liveness probe. `/ready` checks database availability for Payment
and Inventory; Checkout checks both downstream `/ready` endpoints.

## Run and verify

From the repository root, with the project's virtual environment activated:

```bash
pip install -e '.[dev]'
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 120
python scripts/step2_smoke_test.py
python scripts/step3_smoke_test.py
pytest -q
```

Rebuilding is necessary after changing service code: the containers copy source
at build time. This command preserves the database volume.

- Jaeger UI: http://127.0.0.1:16686
- Collector health: http://127.0.0.1:13133
- Metrics: http://127.0.0.1:8889/metrics
- Checkout: http://127.0.0.1:8001

The Step 3 smoke test runs healthy, Payment latency, Payment error and recovery
requests. Each uses a fresh correlation ID and sampled W3C trace ID. The test
reads each exact trace through Jaeger's stable v3 OTLP JSON query API
(`/api/v3/traces/{trace_id}`), checks SERVER -> CLIENT -> SERVER parentage, compares
JSON logs against those server span IDs, and checks that business-route request,
duration and error counts have increased since its initial metrics snapshot.
The service registry is checked through `/api/v3/services`. Collector export and
Jaeger indexing are polled with bounded timeouts and diagnostic error messages.

The test temporarily controls Payment and Inventory faults and restores both
original configurations in `finally`, including when a scenario fails. Run it
against the local sandbox without a concurrent fault experiment. Step 2 also
restores its original Payment fault configuration.

Logs stay on the services' stdout/stderr; there is no Collector log-export
pipeline in this milestone. The smoke test reads them with `docker compose logs`,
so it needs access to the Docker daemon as well as the local HTTP ports.

## Prometheus source identity and stream rollover

New producers use `prometheus-source-config/v2`: the hash pins the running config
except `rule_files` and `alerting`. Global settings, every scrape job/target,
labels/relabeling and unknown future source settings remain part of the hash.
The fixed HTTP RED queries read scraped counters/histograms, not worker recording
rules. Operator rule files remain trusted, image/config-locked deployment inputs;
this identity is not authentication or proof of rule-file integrity. Adding a
business recording-rule dependency requires a new source/profile review.

Changing only operator rules or alert delivery no longer forks a **v2** health
stream. Source-semantics changes require an explicit worker stop/start; an active
stream identity is never rewritten. Changing source inputs still creates a distinct stream. Native evidence
uses the same hash; legacy full-config identities remain accepted only against
their exact original running config. Old captures, stream IDs, consumer IDs,
checkpoints and incident history are never rewritten or merged into v2.

The first upgrade from legacy hashing intentionally creates one new stream:

1. On B, preserve the existing DB/health/incident stores. Stop the old incident
   and health sessions explicitly (`python -m scripts.lane_b_host stop --role incidents`, then
   `python -m scripts.lane_b_host stop --role health`). Do not delete consumer rows, copy cursors or edit bundles.
2. Build/verify a new immutable host release from this code. Start its health
   role with the original B health-store path. Read its exact READY `stream_id`;
   wait for new commits, which do not inherit old episode/window state.
3. Start the new incident role with that explicit `--health-stream`. The policy,
   topology, evidence profile and incident store must match its frozen identity.
   Keep any existing explicit model selection unchanged; this is not H1 approval.
4. Run `python -m scripts.check_lane_b_live`. It derives the current identity from
   live Prometheus plus the B policy/store and requires exactly one matching
   consumer, checking both stored source and consumer identities. Historical
   consumers are retained, not silently selected by latest timestamp. Zero,
   ambiguous, foreign or stale-configuration selections fail before fault load.
   The checker revalidates source selection after the workload as well.

For foreground launchers use `python -m scripts.lane_b health` and
`python -m scripts.lane_b incidents --health-stream`
with the same explicit stop/start sequence. A software rollback uses the old
release and exact original config/stream; an altered legacy config cannot resume
another stream. No SQL/schema migration, history retirement or reset is needed.
Process readiness is separate from freshness/backlog and model/action acceptance.

## Operator worker rules

`observability/rules/sentinelops-workers.yml` holds the operator monitoring for
the existing worker metric surface: heartbeat staleness, readiness, worker-side
errors per fixed component, source eligibility, decision backlog and incident
consumer progress. Prometheus loads it through an explicit `rule_files` entry.

These rules observe **worker telemetry only**. They are not business/SLO health,
model quality or RCA accuracy, and no alert authorizes a remediation action.
Every UNKNOWN-meaning alert carries `signal_meaning: unknown`.

Two invariants are encoded deliberately:

1. Missing telemetry is UNKNOWN, never healthy. A stale or absent heartbeat
   raises an explicit alert rather than evaluating as "no problem".
2. `sentinelops_worker_source_available` is read **only** for a replica that
   committed a local decision, guarded by
   `and on(job, environment, worker_instance) (sentinelops_worker_has_local_decision == 1)`.
   Without that guard a replica that has not decided yet would be reported as
   having an unavailable source. The guard mirrors `scripts/worker_metrics_evidence.py`.

Alerts inline their expressions instead of reading the recording rules in the
same file. An alert that depends on another rule group also depends on rule
evaluation scheduling, which made a rendered annotation value drift between
otherwise identical runs. The recording rules remain for dashboards and ad-hoc
queries.

Staleness only fires while the series still exists. Once Prometheus drops it past
its staleness window, silence would read as health, so `SentinelOpsWorkerTelemetryAbsent`
covers total absence separately. It treats a stopped worker, a broken exporter
path and a scrape failure alike — confirm the intended lifecycle rather than
assuming a fault.

Absence of an accepted model is **not** alerted. `/ready` returning 503 because
no model is approved is this deployment's expected steady state.

Validate rules without starting the stack, and verify a running Prometheus:

```sh
python -m scripts.check_prometheus_rules
python -m scripts.check_prometheus_rules --base-url http://127.0.0.1:19090
python -m pytest -q tests/test_prometheus_rules.py
```

`--base-url` asserts every rule declared here is loaded and has actually
evaluated. It polls, because Prometheus reports a rule's health as `unknown`
until its first evaluation and the group interval makes that the normal state
immediately after startup; only `err` is a real failure.

The checker runs `promtool` from the same pinned Prometheus image Compose runs,
so rules are never validated by a different version than the one that loads them.
It verifies that every referenced rule file is really bind-mounted read-only at
its declared container path — a referenced but unmounted file would stop
Prometheus from starting — rejects globs, since a glob would load files the
deployment lock never hashed, and requires every rule file to have passing
promtool unit tests. Inspect what the running Prometheus actually loaded:

```sh
curl -s http://127.0.0.1:19090/api/v1/rules
curl -s http://127.0.0.1:19090/api/v1/alerts
```

Each rule file is a new owned read-only bind input, so an existing lane B
deployment lock taken before these rules no longer matches the current
configuration and bind bytes. That mismatch is correct: take a new
`lane_b snapshot` instead of editing or reusing the old lock.

## Operator console

Grafana serves the provisioned worker dashboard. For lane B it is published on
loopback `13000` (`http://127.0.0.1:13000`), reserved by the team contract:

```sh
python -m scripts.lane_b up
python -m scripts.check_grafana_dashboards
python -m scripts.check_grafana_dashboards --base-url http://127.0.0.1:13000
python -m pytest -q tests/test_grafana_dashboards.py
```

Everything is provisioned from committed files: the datasource is `editable:
false`, the dashboard provider sets `allowUiUpdates: false`, and the dashboard
itself is `editable: false`. What an operator sees is what is reviewed here, and
a panel cannot be silently repointed at another source.

Anonymous access with the `Viewer` role, sign-up disabled, basic auth disabled,
loopback only. **This is a local sandbox console, not production authentication** —
the same boundary as the local API tokens. It exposes no fault control, no
incident write and no action execution. Grafana's own update checks, plugin
fetches and news feed are disabled so an observability console makes no outbound
calls from the sandbox.

`scripts/check_grafana_dashboards.py` validates the console definition and,
with `--base-url`, a running instance. It checks that every panel query names a
metric or recording rule that actually exists (a typo renders an empty panel that
looks like no problem), that **no panel reads source availability without the
decision guard**, that state panels declare what absent data means and never
present it as healthy, that every mapped value carries text so state is never
colour alone, that no panel introduces a second y-axis or a per-series unit
override, and that every plotted panel shows a legend. Against a running Grafana
it confirms each dashboard is reported `provisioned`, the datasource is present,
and **every expression actually evaluates on the real Prometheus** — static name
checking cannot catch an invalid function or aggregation.

`tests/test_grafana_dashboards.py` proves each guard rejects a violation by
mutating a copy of the real dashboard.

One panel detail worth keeping: "Replicas with a stale heartbeat" uses
`count(... > 15) or (count(...) * 0)`. The bare `count(... > 15)` returns an
empty result both when nothing is stale *and* when no replica reports at all, so
the panel would have displayed "none stale" for a dead worker. The `or` branch
makes zero mean *observed and fresh*, and total absence falls through to
`UNKNOWN — no replica reporting`.

Adding Grafana makes nine owned B containers and three more read-only bind files,
so a deployment lock taken before it no longer matches; take a new
`lane_b snapshot` rather than reusing the old lock.

## Automated checks

- In-memory SDK tests exercise the real service handlers, trace propagation,
  metrics, structured logs, concurrent requests, timeouts and unhandled errors.
  Database operations use a test double in these tests.
- Smoke-validator tests reject incomplete/unrelated traces, wrong parentage,
  unrelated logs and stale metrics.
- CI's `observability` job builds the real Docker testbed and runs both smoke
  tests against PostgreSQL, Collector and Jaeger. Failure logs are collected
  before the CI stack is removed.

## Troubleshooting

```bash
docker compose ps --all
docker compose logs --no-color --tail 100 otel-collector jaeger
docker compose logs --no-color --tail 100 checkout-service payment-service inventory-service
```

- An unknown `prometheus` exporter or `health_check` extension indicates a
  Collector distribution/configuration mismatch; use the pinned Compose image.
- A trace-parent mismatch requires checking header injection inside the client
  span, not only whether all three service names are present.
- Missing metrics can indicate exporter/Collector startup errors; allow for the
  SDK's 1-second export interval and the 5-second Prometheus scrape.
  Metrics bypass the Collector batch processor to keep counter/histogram
  delivery fresh; traces retain their existing batch processor. A parity FAIL
  must retain its evidence and must not be bypassed by widening tolerances.
- The smoke test expects Jaeger's stable v3 query API. A 404 after polling should prompt
  checking the running image/configuration and rebuilding the current Compose
  stack. The decoder handles v3 JSON/NDJSON frames, rejects stream errors/duplicate IDs and verifies each complete trace; it does not infer success from the service registry alone.

## Step 2 correction

The host-side HTTPX smoke test now uses `trust_env=False` so local requests are not routed through an unrelated proxy configuration.

## Definition of Done

- Collector health endpoint responds.
- Jaeger UI responds.
- Checkout still works.
- Custom metrics appear on Collector port 8889.
- Jaeger lists all three business services after a checkout.
- Every tested trace has correct SERVER -> CLIENT -> SERVER parentage.
- Logs contain matching correlation IDs, trace IDs and server span IDs.
- HTTP 5xx is visible in traces and error counters.
- Metric counters and histogram counts increase for the tested business routes.
- Existing Step 2 fault scenarios remain reproducible.
- Both smoke tests pass and fault configurations are restored.


## Alibaba source-parity measurement protocol

The committed `alibaba-source-measurement/v2` protocol sends five healthy
requests per second to each entry, with at most one in-flight request per
entry. This gives denser samples than the former one-request/s driver.
Prometheus [extrapolates counter increases](https://prometheus.io/docs/prometheus/latest/querying/functions/#increase);
a sparse bucket can be sensitive to latency outliers near scrape boundaries.
The parity report records this protocol explicitly and retains private
`requests.jsonl` and allow-listed `server-completions.jsonl` before the
sandbox is removed, so P4 counts and P5 means can be independently checked.
P1–P6, the 5 s scrape/60 s windows, count tolerance 5% and mean tolerance
10% stay unchanged. This is a healthy-source check at the recorded load,
not a guarantee of accuracy for arbitrary low-rate or bursty traffic.
Retain earlier FAIL reports; never select buckets or widen bounds to pass.
