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

On WSL, inspect host/guest time synchronization before opening a group. With
`hv_utils.timesync_implicit=1`, Ubuntu 24.04 and earlier can run a competing
`systemd-timesyncd` client; [Canonical recommends disabling that guest service](https://ubuntu.com/wsl/docs/stable/explanation/time-sync/)
when Windows/Hyper-V supplies time (`sudo systemctl disable --now systemd-timesyncd.service`).
Make clock configuration changes only with no active capture, then check clock
continuity before opening a fresh group. This preflight does not certify absolute
UTC accuracy. An invalid interval still requires the whole-group disposition below;
never adjust recorded timestamps or restart its failed measurement in place.

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


### Terminal host interruption

An interrupted workload is never resumed in its original slot after the host
context changes. Preserve the group, including its completed captures and every
partial raw file. When the registered host boot has changed and all seven original
owned containers are stopped without a restart, an operator can explicitly close
the whole group and reserve an unused ordinal beyond the plan's minimum, within
its maximum:

```sh
python -m scripts.close_interrupted_group --root /absolute/corpus/root \
  --role train --group 1 --replacement-group 4
```

Run from a clean committed implementation checkout. Both controller and capture
locks must be free. The operation observes original Docker identities/state and
host boot, verifies any completed captures, hashes all original group files and
writes immutable observation/inventory/closure refs. It changes no container,
volume, raw file or result. A complete group, changed original source, frozen
corpus, later partition or already reserved replacement is refused. Other failure
modes require investigation; this command does not infer a host interruption from
a missing process or timeout alone.

The entire closed group contributes **zero** fitting/evaluation rows, including
its previously completed healthy or gradual captures. Audit retains its explicit
exclusion and original bytes and checks its time span against later groups. All
original support floors, chronological roles and maximum ordinals still apply.
An exclusion is not a source/model approval and cannot hide corrupt completed
captures. The closed group cannot receive new traffic.

Restart the existing campaign from a clean immutable worktree after the explicit
closure. It follows the recorded same-role replacement, preserves the original
campaign plan registration and runs each replacement kind in its new context.
It never chooses an additional replacement automatically. A failed replacement
stops again for investigation. Keep all prior raw data/volumes; do not rename old
captures into the replacement, concatenate groups or open final data.

### Invalid client UTC intervals

A client completion before its own recorded UTC start is invalid source evidence,
even when its separately sampled monotonic duration is positive. Never repair the
UTC timestamp, drop the attempt or relabel its affected buckets as healthy. The
prospective oracle continues to reject these inputs. The runner now latches such
a failure and raises `ClientClockDiscontinuity` from its load loop, retaining
original outcomes and restoring fault settings through its existing cleanup.
It also rejects a client start before the recorded prospective run start. This
observes invalid intervals; it neither converts clocks nor proves host-clock
stability or the cause of a clock adjustment.

After investigating a stopped campaign, a clean committed implementation checkout
can explicitly exclude the **whole** clock-invalid group and reserve an unused
same-role replacement within the original maximum:

```sh
python -m scripts.close_clock_invalid_group --root /absolute/corpus/root \
  --role train --group 4 --replacement-group 5
```

Both acquisition/controller locks must be free. The command recomputes an invalid
UTC witness from the original content-addressed client ledger, verifies its
registration, source/code/recipe/truth identities, checks completed source inputs
and observes the original owned container epoch. A partial attempt is eligible
only when its original producer failed with `ClientClockDiscontinuity`, has
clock-guard implementation provenance and contains an actual invalid client
interval. Generic incomplete/failed sources, forged failure flags, unrelated
corruption, valid groups selected by model outcomes, changed containers, later
partitions, frozen corpora and used/out-of-budget replacements are refused.

The immutable clock disposition stores original-source proof, observer identity
and an inventory of every original group file. Audit rechecks all of them. Every
kind in the group, including earlier valid healthy/Payment/Inventory captures,
supplies zero healthy, precursor, fitting or independent-group support afterward.
This is exclusion of invalid acquisition evidence, never admission or G2 PASS.
The original plan, chronology, minimum counts and final-access restriction stay
unchanged. Existing host-interruption dispositions retain their separate rules.

The command stops no container and changes no raw/result file. After the explicit
closure, stop only the identified old group containers, retain every volume and
source checkout, and continue the existing campaign from a new clean immutable
checkout. It follows only the recorded replacement chain; it never automatically
allocates another ordinal or retries a failed slot. A new failure stops again for
investigation. New groups require fresh contexts and original source verification.
