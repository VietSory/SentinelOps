# Lane B host releases and user services

`scripts/lane_b_host.py` packages the control-plane application and manages only
three local user services: `api`, `health`, `incidents`. It uses systemd, not a
custom PID supervisor. It never runs remediation, provisions infrastructure,
trains a model or approves an H1/H4 handoff. Foreground `lane_b.py` commands remain
available; do not run a foreground and managed copy of the same role together.

## Build and select

Use Linux with a working user systemd manager, `systemctl`, `systemd-analyze`,
Python 3.11 and the installed project dependencies. Start the isolated B stack
using the existing [local runtime](LOCAL_TESTBED.md#lane-b-isolated-application-runtime).
Do not run B workload tests alongside an A acquisition on the same host.

```sh
python -m scripts.lane_b up
# Commit source/config changes before taking a release snapshot.
python -m scripts.lane_b_host build
python -m scripts.lane_b_host verify --release-id <returned-id>
python -m scripts.lane_b_host start --role api --release-id <returned-id>
python -m scripts.lane_b_host start --role health --release-id <returned-id>
python -m scripts.lane_b_host status --role health
python -m scripts.lane_b_host start --role incidents --release-id <returned-id> \
  --health-stream <exact-health-stream-id-from-status>
```

Build produces a new private directory under `artifacts/lane-b/host-releases/`.
`lane-b-host/v2` contains an installed application wheel (including schemas/UI),
copies of the existing worker CLIs and policy/topology bytes, a copied dependency
tree and their checksums. The dependency snapshot comes from the selected installed
Python environment, including its optional packages; it is software, not a fitted
or approved model. Build refuses dirty/changing Git inputs or a changing dependency
tree. Failed/incomplete builds have no usable release manifest and are not selected.
The tool never overwrites a release. Keep the returned manifest SHA/source commit.

Each launch uses the pinned interpreter with `-I -S`: no editable-install `.pth`,
user site, `PYTHONPATH` or checkout import fallback. The application must resolve
inside the installed wheel; dependencies resolve inside the copied release tree.
Changing the dev virtualenv later does not change those running dependency bytes.
Bytecode caches are excluded/disabled, and unexpected release files or changed
checksums block a subsequent launch. Unit/session selection is pinned by hash.

This is **host-bound**, not a portable or hermetic Python distribution. It retains
the exact Python executable/version and selected loader environment; Python's
standard library and OS shared libraries, Docker and systemd remain host prerequisites.
On Nix, supply the correct runtime library paths when building; they are recorded
for the unit. If the pinned interpreter is garbage-collected or incompatible OS
libraries change, restore that runtime or build/select a new release. No silent
switch to another interpreter, editable code or demo scores exists.

Docker images/bind inputs are a separate scope: use the existing deployment lock
and `lane_b up --deployment-ref ...` when those must also be pinned. A host release
is not a DB backup or an approved known-good Payment image ledger.

## Lifecycle and readiness

```sh
python -m scripts.lane_b_host status --role api
python -m scripts.lane_b_host restart --role health
python -m scripts.lane_b_host stop --role incidents
python -m scripts.lane_b_host stop --role health
python -m scripts.lane_b_host stop --role api
```

Unit names contain a hash of the owning checkout/runtime root. The manager refuses
foreign unit fragments and never adopts/kills an unrelated listener on port 18000.
A filesystem lock serializes lifecycle commands. Repeated start with the same
release/source/model selection keeps the existing PID; changing selection requires
explicit stop then start. Stop removes only the reviewed user-unit link and retains
the release, source stores, DB history and volumes. Units are not enabled at boot.

systemd owns the process/cgroup and graceful SIGTERM shutdown. Unexpected exits
restart after five seconds, bounded by three starts per sixty seconds. An explicit
restart rechecks selected bytes/session/source before stopping a running process,
then clears the systemd failure limit for the reviewed operator retry. It does not
reset an incident action budget or change a remediation threshold. Corrupt release
or session bytes keep the existing process running when manual restart is refused.

`active`/PID is **process state**, not readiness or SLO health. Status explicitly
returns readiness `NOT_OBSERVED` and integrity `NOT_CHECKED`; use `verify` for byte
integrity. `/health` is liveness, protected `/ready/incidents` checks the store and
`/ready` independently remains 503 without an accepted model. Worker READY/commit
events are in the selected unit's journal; inspect its `unit` name from status:

```sh
journalctl --user --unit=<unit-name-from-status> --no-pager
```

Health start derives and pins the existing producer identity from the owned B
Prometheus configuration, copied policy and **original B health store path**.
Native source ownership validation is reused. Restart checks the same identity;
it cannot silently switch to another stream. Incident start requires that exact
explicit stream, original source/incident roots and the existing consumer's frozen
configuration. Existing DB advisory locks, transactions, raw lineage and checkpoints
remain authoritative. Changed consumer configuration needs the established migration
process; no force/reset or automatic data migration is added. A backed-up worker
can process overdue buckets as UNKNOWN. A restart does not make it caught up or healthy.

## Credentials, model selection and trust

Credentials are read through the existing B environment validator and written only
to private session JSON (0600 under a 0700 directory), separately from release files.
They are not in unit arguments, public status, release manifests or CI artifacts.
Rotate a credential by explicit stop/start, not by editing a selected session.
Inherited model/source/OTel settings from other processes are cleared at bootstrap.
Changing source/model selection on restart is rejected.

Optional H1 is still explicit and paired for API/incident start:

```sh
python -m scripts.lane_b_host start --role api --release-id <id> \
  --model-store /owned/checkout/artifacts/lane-b/models/<handoff> \
  --model-release-ref artifact://manifests/<accepted-release-hash>.json
```

The existing registry performs artifact/source/library/admission/release checks.
Packaging installed ML libraries does not approve a model; the release itself
always carries `model_activation_allowed=false` and `remediation_approval=false`.
Health never accepts model settings. Optional action observations for the API use
`SENTINELOPS_ACTION_READ_DATABASE_URL`, restricted to B's loopback control DB port
15433; existing API SELECT-only readiness checks still reject writer credentials.
This does not expose action execution or production authentication.

Release/session stores and the local user manager are trusted operator boundaries.
Hashes detect changed bytes, not a hostile owner who can rewrite code and systemd
configuration. No cloud credentials, raw corpus, sealed holdout or action writer is
required. Known guard failures produce structured BLOCKED reasons; unexpected
failures report operation/error type without echoing private DSNs or tokens.

## Reproducible actual acceptance

With the B stack up, a verified release and no managed role installed:

```sh
python -m scripts.check_host_processes --release-id <id> \
  --output artifacts/lane-b/host-process-new.json
```

The check runs real wheel/systemd/API/PostgreSQL processes. It verifies auth/model
unavailability, idempotent API start, checksum rejection before stopping a live API,
pinned incident revision reads, actual health SIGKILL/automatic restart, READY from
the correct new PID and a strictly advancing durable checkpoint. Incident restart
must emit READY with the same consumer and preserve its prior immutable checkpoint.
It then stops only the roles it created. No healthy observation, model prediction,
incident fixture, IER recovery or action grant is manufactured. CI repeats it on
an ephemeral runner user manager; only that runner gets lingering enabled. Its
artifact retains the operational health/incident stores for checkpoint references;
it excludes private sessions, tokens and the large dependency tree. These operational
observations are not admitted training data. After downloading, use the `health/`
and `incidents/` store roots separately, as in the existing worker replay tools.
