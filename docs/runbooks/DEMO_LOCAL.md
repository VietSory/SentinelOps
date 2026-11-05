# Local demo guide — lane B

A walkthrough of what SentinelOps can actually be shown doing today, on one
machine, with no cloud and no accepted model. Every command and every expected
value below was executed against the real lane B stack on 2026-11-05; where a
step could not be executed, that is stated rather than implied.

## What this demo proves, and what it does not

**It shows:** real telemetry from three real services flowing through the
Collector and Prometheus into the health worker, correlated into an IER with
revisions in PostgreSQL, read back over authenticated HTTP by a read-only
operator UI, explained by the deterministic grounded backend, and the worker
pipeline itself observed through Prometheus rules and a Grafana console.

**It does not show, and cannot:**

| Not demonstrable | Why |
| --- | --- |
| Model inference / anomaly scores | No model has passed G2/G3; `/v1/detect` answers 503 by design |
| RCA diagnostic accuracy | Full G4 is unaccepted; rankings are suspects with evidence, not confirmed causes |
| Bedrock explanations | G5 unaccepted; the demo uses the real deterministic fallback |
| Automatic Payment rollback | G6 unaccepted; no approved known-good ledger and auto-action is off |
| Anything on AWS | Not deployed. IaC is described and validated only; see [AWS baseline](AWS_BASELINE.md) |

A limited demo is deliverable early, but it does not substitute for acceptance of
anything above.

## Prerequisites

Linux with Docker, Python 3.11 and the project installed (`pip install -e '.[dev]'`).
Do not run this while lane A is capturing on the same host. Ports used are lane
B's reserved allocation only.

## 1. Bring up the isolated stack

```sh
python -m scripts.lane_b config   # inspect rendered ports before mutating anything
python -m scripts.lane_b up
python -m scripts.lane_b status
```

Expect **nine** containers healthy: `checkout-service`, `payment-service`,
`inventory-service`, `postgres`, `control-postgres`, `otel-collector`,
`prometheus`, `jaeger`, `grafana`. Volumes survive `down`.

## 2. Start the control plane and workers

Three separate terminals — these are host processes, not containers, so their
loopback ownership guards stay intact. Do not pipe them through `tail`; you need
their streaming output.

```sh
python -m scripts.lane_b serve      # API on 127.0.0.1:18000
python -m scripts.lane_b health     # health worker; prints a READY line
```

Copy the **actual** `stream_id` from the health worker's READY line — never a
guessed or remembered value — then:

```sh
python -m scripts.lane_b incidents --health-stream <stream_id>
```

The incident worker prints `READY` with its `consumer_id`, `health_stream_id`
and `"actions_allowed": false`. If you cannot see the READY line, read the
stream id from the control database instead:

```sh
docker exec sentinelops-b-control-postgres-1 \
  psql -U control_bootstrap -d sentinelops_control \
  -c "select stream_id, last_bucket_end from sentinel_control.health_streams;"
```

## 3. Show that readiness is three different questions

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/ready
TOKEN=$(cat artifacts/lane-b/operator-token)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:18000/ready/incidents
```

Observed: `200`, **`503`**, `200`.

This is the single most important thing to explain in the demo. `/health` is
liveness. `/ready` is **503 on purpose** because no accepted model exists.
`/ready/incidents` is 200 because incident storage genuinely is ready. A 503
from `/ready` is not a broken demo; a demo that showed 200 there would be
claiming a model it does not have.

## 4. Show the credential boundary

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/v1/incidents
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/incidents
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "Authorization: Bearer $TOKEN" -H 'If-Match: "0"' \
  -H 'Idempotency-Key: demo' -H 'Content-Type: application/json' \
  -d '{}' http://127.0.0.1:18000/v1/incidents
```

Observed: `401` unauthenticated; the operator token reads the incident list; the
operator POST is **`403`**. The operator credential can read everything the UI
needs and import nothing. Only `artifacts/lane-b/api-token` retains write scope.

These are static local development credentials. Production authentication is not
implemented and fails closed elsewhere — do not present this as an auth demo.

## 5. Show model unavailability honestly

```sh
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"schema_version":"1.0","service":"payment-service",
       "window_start":"2026-11-05T15:00:00Z","window_end":"2026-11-05T15:05:00Z",
       "features":{"request_rate":1.0,"error_ratio":0.0,"p95_latency_ms":100.0,"saturation":0.1}}' \
  http://127.0.0.1:18000/v1/detect
```

Observed `503` with `{"code":"model_unavailable","message":"No validated model
bundle is activated"}`. A malformed body returns `422` instead, so use a valid
FeatureWindow if you want to show the model gate rather than schema validation.

## 6. Operator UI

Open `http://127.0.0.1:18000/ui/` and paste the **operator** token from
`artifacts/lane-b/operator-token`. The token lives in tab memory, is cleared
from the input after connecting, and is never written to browser storage.

Walk through: incident list → filter by state/service → open an incident →
pinned revision detail → Evidence → Timeline → Explanation. Then Disconnect and
show the incident state is cleared.

What to say while showing it: the ranking is a **suspect list with evidence
references**, not a confirmed cause; `UNKNOWN` availability is not healthy; the
model panel reads Unavailable; loaded row counts are not global incident counts.

For a reproducible scripted pass instead of clicking:

```sh
python -m scripts.check_operator_ui --fixtures --output artifacts/lane-b/demo-ui-fixtures
python -m scripts.check_operator_ui --output artifacts/lane-b/demo-ui-native
```

Fixture mode is software evidence only. Native mode reads an actual persisted
SLO incident and never writes one.

## 7. Grounded explanation

```sh
ID=$(curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/incidents \
     | python -c 'import json,sys;print(json.load(sys.stdin)["items"][0]["incident_id"])')
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:18000/v1/incidents/$ID/explanation?revision=4"
```

Observed on the real SLO incident at revision 4: `provider:
deterministic-facts/v1`, `fallback: true`, **12 claims**, every claim's evidence
reference resolving inside that pinned revision.

Say plainly that this is the deterministic fallback, not an LLM. It summarises
facts already in the IER. It is not independent verification, not qualified RCA,
and it grants no action authority. The GET writes no revision and does not move
the incident to EXPLAINED.

## 8. Operator console

Open `http://127.0.0.1:13000` — the dashboard **SentinelOps — worker telemetry**
(uid `sentinelops-workers`) is provisioned read-only. Read the scope panel aloud:
worker telemetry only.

```sh
curl -s http://127.0.0.1:19090/api/v1/alerts
```

On a stack replaying an old backlog you will see
`SentinelOpsDecidedSourceIneligible` **firing** — six series when both workers are
running, three business services each. Explain it correctly: those buckets have
no telemetry, so availability is `UNKNOWN`. It is **not** a newly detected
outage, and it is not evidence the services are down. Every UNKNOWN-meaning alert
carries `signal_meaning: unknown`.

Also worth showing: source eligibility reads the guarded recording rule, so a
replica that has not committed a decision shows `NOT DECIDED` rather than an
unavailable source; and the stale-heartbeat tile shows `UNKNOWN — no replica
reporting` rather than zero when nothing reports at all.

## 9. One real incident end to end

```sh
python -m scripts.check_lane_b_live
```

This drives 180s healthy, 360s injected Payment latency and 300s recovery against
B only, then verifies a **new** source-backed incident reached the API with at
least two revisions and an accepted SLO trigger. It restores the original healthy
fault configuration in `finally`. Output lands under `artifacts/lane-b/live-*`.

This is operational integration evidence. It is **not** admitted training data,
predictive labels, online RCA accuracy or remediation acceptance.

> **Could not be executed in the current environment.** The check requires
> exactly one lane B incident consumer and refuses otherwise:
> `ValueError: start exactly one native B consumer before this workload`.
> This environment now has two, because a source-configuration change forked the
> health stream — see the caveat below. On a fresh environment with one consumer
> this step runs normally; here it is reported as blocked rather than worked
> around, because the alternatives would be deleting a consumer checkpoint or
> loosening the guard.

## 10. Teardown

```sh
# Ctrl+C each host process in its own terminal first.
python -m scripts.lane_b down          # preserves all volumes
```

`down` never removes volumes, corpus or worker checkpoints, and never touches
another project. Do not add `--volumes`: the control database holds the IER
revisions and audit history this demo reads.

## Known caveat: a Prometheus configuration change forks the health stream

The health worker derives its source identity from
`stable_id(<entire running Prometheus configuration>)`, read from
`/api/v1/status/config`. Adding `rule_files` for the operator alert rules
therefore changed that hash even though scrape semantics were untouched:

| Configuration | `configuration_hash` |
| --- | --- |
| with `rule_files` | `44c63e2cd7d6d73b41bd034b67e852b09aedc6cd3baf847baed6dd1c2e4beab9` |
| without `rule_files` | `06dce0d48a07e3cd2e7252513afe42d36533a6ec8d0183cef94e8d84ce728e10` |

`scrape_configs` and `global` are byte-identical between the two, so the actual
source did not change. The consequence is a new health stream and a second
consumer, which is why step 9 is blocked here:

| Stream | Last bucket |
| --- | --- |
| `0b6f3101…` | 2026-11-01 03:06Z — no longer advancing |
| `a2a5cbad…` | current |

No data was lost: the earlier stream and its checkpoint are intact, and the
consumer bound to it still exists. Following the new stream with the old consumer
would need lane A's established consumer migration protocol; resetting
checkpoints or creating duplicate streams to evade that guard is not an option.

Forking on a changed source configuration is correct and deliberate — it stops
data from different sources being silently mixed. The open question is that the
hash covers the whole configuration rather than just the inputs that define
source semantics, so a lane-B alerting or dashboard change forks a lane-A stream
with no source change. `src/sentinelops/telemetry/prometheus.py` is lane A's, so
this is recorded here for A to decide rather than changed by B.
