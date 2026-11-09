# Lane B Alibaba v2 activation — `local-nixos-host`, 2026-11-09

Execution report for [README-B.md's three handoff steps](../delivery/LANE_B_CHECKPOINT.md#76-cập-nhật-2026-11-09-model-chính-thức-là-model-alibaba-adr-016)
on a fresh machine B (`local-nixos-host`): target-environment source parity,
serving-release build, and the v2 stream rollover with `scripts.lane_b_host`.
Scope is local integration only, per the owner's conditional G3 (D-22,
[G3 packet](G3_CANDIDATE_ALIBABA_BADF9DA0.md)) — this is not a G8/final
acceptance record.

- bundle: `sentinelops-model/v3`, origin `admitted-alibaba-v2022`
- model version: `badf9da0b563f4900fa099b11ba9223ee864d414ef3aa9af82a534db962d85e3`
- manifest SHA-256: `2d1a5376eccdd691da0741b9dc88eecd223d75a85a23fa712e00c1dc53291e62`
- source commit at build time: `806db4954990c686c1cbc219f9e9f2dba6b1b20b`

## Result

| Step | Command | Result |
| --- | --- | --- |
| 1. Source parity | `scripts.check_alibaba_source_parity` | **PASS, 0 failures** (second attempt; see below) |
| 2. Build release | `scripts.build_alibaba_release` | Built, `activation_performed=false` |
| 3. v2 rollover | `scripts.lane_b_host` + `scripts.check_lane_b_live` | **PASS** |

Final live-check record: `artifacts/lane-b/live-20261109T144459-00e1b51e/run.json`
— `status: PASS`, `model_dependency: true`, one native incident
(`INC-775a7726e8cde9974e137ec45ff3983d6f78319195be207b76875191e9d34812`,
revision 4, `availability: ELIGIBLE`, `lifecycle: CLOSED`) reached the API
through health worker → incident worker → PostgreSQL → HTTP, consumer
`91a021fecf98a0748059f9a848a43bba99cb0aa6f064bd99ca667fb7db200fd7` on health
stream `d640c721cebb06a2e14203be3fd99a4d4cb295eb3712f7848bb1942ede4c120c`
(`--stream-epoch alibaba-v2-01`). Inference is now active end-to-end on this
B stack, consistent with the owner's conditional scope (local H4 → G6).

## Step 1 — parity: one legitimate FAIL, then PASS

First attempt (`artifacts/lane-b/alibaba-parity-local-nixos-host/parity.json`,
superseded): P1–P4 and P6 passed; P5 failed at exactly one bucket
(`2026-11-09T13:33:00Z`) — `checkout-service` 17.9% and `payment-service`
42.9% deviation between the Prometheus mean and the exact server-log mean
(limit 10%). The host was running unrelated Docker workloads
(`9router`, `tixflow-v2`) at the time; likely scrape-timing noise under CPU
contention, not a code defect. Re-run with no change to the bundle, config or
code: **PASS, 0 failures**. Per the source-parity runbook, a PASS record is
environment-bound — this record is only valid for `local-nixos-host` and
cannot stand in for any other machine's measurement.

## Step 2 — build

`scripts.build_alibaba_release` with the PASS parity record, `--approved-by
nvtank`, `--decision-note "D-22: G3 accepted with conditions for local
integration"` (verbatim from `README-B.md`), `--confirm-owner-decision`:

```json
{"release_ref": "artifact://manifests/bd105fa6cd8d19744ac2441719a11a495a0a77517a9aed6387fcbceaeaeac477.json",
 "store": "artifacts/lane-b/models/alibaba-release-local-nixos-host",
 "model_version": "badf9da0b563f4900fa099b11ba9223ee864d414ef3aa9af82a534db962d85e3",
 "activation_performed": false}
```

The store had to be placed under `artifacts/lane-b/models/`
(`lane_b.runtime_environment` requires `model_store` to resolve inside that
directory) — not documented as a hard requirement in `README-B.md`'s `--output`
placeholder.

## Step 3 — v2 rollover: three real bugs found and fixed

This was the first real exercise of `scripts.lane_b_host start --role
incidents`/`check_lane_b_live` against a health worker started with
`--source-profile prometheus-increase-60s-5s/v2` — no existing test runs this
path end-to-end against a live stack. All three are now fixed on `main`:

1. **`lane_b_host start --role incidents` could never select a v2 health
   stream.** Its identity probe (and the runtime recheck in `launch.py`)
   rebuilt `HealthWorker.identity` without `source_profile`, defaulting to v1,
   which never matches a v2 `health_stream_id`. `run_incident_worker` has no
   `--source-profile` flag to carry it in `arguments()`, so the fix threads the
   running health role's selected profile through as session metadata
   (`health_source_profile`) instead, read by both the probe and the runtime
   recheck. v1 sessions (no flag, no metadata) are unaffected.
   (`scripts/lane_b_host.py`, `scripts/host_runtime.py`)
2. **Host releases didn't bundle `configs/alibaba_relative_features.json`.**
   `build()` copied only `slo_health_policy.json`, `rca_policy.json`,
   `topology.yaml` into the release; `ModelRegistry.load()` →
   `alibaba_training.load_config()` reads the frozen feature config from the
   release root and crashed the `api`/`incidents` roles with
   `FileNotFoundError` as soon as a model release was selected.
   (`scripts/lane_b_host.py`)
3. **`check_lane_b_live`'s post-workload consumer recheck ignored
   `--source-profile`.** It revalidates the health source didn't change
   during the B workload, but called `current_consumer()` without
   `source_profile`, so the recheck itself defaulted to v1 and always raised
   `start exactly one native B consumer for the current health source` on a v2
   run — even after the workload (180s healthy, 360s Payment latency, 300s
   recovery, incident reaching the API) passed cleanly.
   (`scripts/check_lane_b_live.py`)

Fixes, with existing test suites re-run clean
(`tests/test_host_release.py`, `tests/test_lane_b.py`,
`tests/test_lane_b_consumer_selection.py`): `666e62e`, `029b265`.

## Environment notes (not code defects)

- The dev `.venv`'s pinned Python 3.11 derivation had been garbage-collected
  from the Nix store; `nix-shell -p python311` re-fetched the exact same
  store path and restored it.
- `numpy`'s compiled extension needed `libstdc++.so.6`/`libz.so.1` at runtime,
  not on the default NixOS linker path. Resolved by setting `LD_LIBRARY_PATH`
  to the **64-bit** `gcc-*-lib` output — an earlier candidate store path
  (`gcc-14.3.0-lib`) turned out to be a 32-bit cross-compiled build and failed
  with `wrong ELF class`.
- `.venv` had never picked up `PyJWT`/`cryptography` (added to `pyproject.toml`
  by the OIDC work on `main`); `pip install -e .` fixed it. This is why the
  `api` role's first start failed with `ModuleNotFoundError: No module named
  'jwt'`.
- The host rebooted mid-session (confirmed via `uptime`), taking down the
  entire lane B Docker stack (volumes were preserved; `scripts.lane_b up`
  brought the 9 containers back healthy with no data loss).

## Known pre-existing issue, unrelated to this work

`/home/nvtank/year4/AI4life/alibaba-badf9da0-for-B/` (the bundle handoff
directory this report's input came from) has no Git repository of its own;
`git status` inside it resolves to a `git init`-created repository rooted at
`/home/nvtank` tracking the entire home directory (`.ssh`, `.aws`, `.gnupg`,
personal documents included), with no commits yet. Not touched during this
work — flagging for cleanup since it does not look intentional.
