# Lane B — Alibaba `de38fc6b`, local activation 2026-11-14

Model `de38fc6b` has completed the README-B handoff on `local-nixos-host`: source
parity PASS, serving-release build, new stream epoch, native incident workload
and model-signal replay verification. Scope: local integration under owner
[D-27](../architecture/CONSTRAINTS_AND_GATES.md), using
[ADR-019](../adr/ADR-019-breach-relative-retrain.md). This does not replace the
single `ald` layer-2 evaluation or grant G6/G7/G8 acceptance.

## Exact inputs and releases

| Item | Value |
| --- | --- |
| Source commit for parity | `e5b16b5741b591a2991844beac275342c9a8f38b` |
| Source commit for host release | `c75a8dbaea0b8d6c40e48b6a1e7791344ff458e3` |
| Model version | `de38fc6bb89896856556e13e7c6142ba21c8d0fea88ebd082f95e96d4e5b0e57` |
| Bundle manifest SHA-256 | `d52df5846ac5027507d96b67c751f02f2c5519e3ea970fbdc3599207d18eb84e` |
| Feature/source versions | `http-relative-5m/v2` / `prometheus-increase-60s-5s/v2` |
| Model store | `artifacts/lane-b/models/alibaba-de38fc6b-20261114` |
| Model release | `artifact://manifests/092290cedcea07578f664aa670eabf236f66ce7db27ebf159c2c386c3ee5216b.json` |
| Host release | `5c520ae661e440c892195c814670677f` |
| Host manifest SHA-256 | `2651195534b583d1d803b7b39606adf9f4556c6895c064e6c907954bd64c0e21` |
| Stream epoch | `alibaba-de38fc6b-20261114b` |
| Health stream | `b95058b4d9eaef6e411bc493263068963668de2ced39f89351c3efd3588a4a8f` |
| Consumer | `0d27b26c90e6eaa9c0065cdf1a8de34f4fcdff39aa82016ce07f2848e6bdbb32` |

All six delivered SHA256SUMS entries passed before loading the trusted model.
The pinned kind v0.30.0 binary hash also matched README-B. Owner decision `D-27`
was recorded by `build_alibaba_release`; that command reported
`activation_performed=false`. API and incident worker were then explicitly
started with the same model-store/release pair and the new immutable host release.

## Verification and evidence

Regression checks after the log-collection and numeric-zero fixes: **90 passed** in
`tests/test_alibaba_release.py`, including pod pinning, cleanup on interruption,
retained early-minute completions, trace mismatch rejection and native
floating-point zero error counts. The float-zero release test failed before the
fix and passed afterward; booleans, nonzero counts and nonfinite values still fail. The sandboxed
test run was interrupted after 62 tests when its async thread work hung; the
complete suite was then run outside that sandbox. This is the
release/parity regression suite, not a new full-project test run. No fit, threshold adjustment or
holdout access was performed.

| Parity criterion | Recorded checks | Result |
| --- | --- | --- |
| P1 | 1 | PASS |
| P2 | 33 | PASS |
| P3 | 34 | PASS |
| P4 | 33 | PASS |
| P5 | 33 | PASS |
| P6 | 3 | PASS |

P3 includes one normalization-summary check and 33 service-minute checks.
Maximum request-count difference: **1.782%** (limit 5%).
Maximum mean-latency difference: **7.164%** (limit 10%).
Selected parity run: attempt 5, zero failures;
the owned cluster/registry and sandbox credentials were removed on exit.
The first attempt is preserved at `artifacts/lane-b/alibaba-de38fc6b-20261114/parity`: P5 failed for
Checkout at 2026-11-14T02:24:00Z (Prometheus 20.606 ms vs server mean 25.210 ms,
18.263% difference). P1–P4 and P6 passed. Attempt 2, retained at
`artifacts/lane-b/alibaba-de38fc6b-20261114/parity-02`, failed two P4 count checks (5.455%) and three P5
mean checks (maximum 36.794%). Repeating the original configuration did not fix
source fidelity. Commit `5f29a16` reduced the SDK metric export interval from
2 s to 1 s and removed the Collector's additional 2 s batch timeout for metrics;
traces retain batching. The selected measurement uses this committed change,
the same bundle and unchanged 5%/10% P4/P5 bounds. Attempt 3, at
`artifacts/lane-b/alibaba-de38fc6b-20261114/parity-03`, passed every P4 check (maximum 1.818%) but failed
one P5 Payment bucket at 03:07Z (20.057%). The source query uses Prometheus
[extrapolated increases](https://prometheus.io/docs/prometheus/latest/querying/functions/#increase);
sparse traffic remains sensitive to outliers near scrape boundaries.
The selected run uses committed measurement protocol `alibaba-source-measurement/v2`,
five requests/s per entry with at most three in flight, and retains the full
private request/completion ledgers. Attempt 4 (`artifacts/lane-b/alibaba-de38fc6b-20261114/parity-04`)
failed 35 P4/P5 checks: the final log snapshot was missing early Payment and
Inventory completions; Checkout passed all 11 count and mean checks. The final
patch follows each pinned pod before traffic (`pod-follow-before-traffic/v1`)
and rejects an exited follower. Raw logs are private and removed on exit; the
allow-listed ledger survives. Kubernetes exposes only the latest log file in a
normal `kubectl logs` snapshot, so it cannot preserve a full run after rotation.
This records the load explicitly and scopes
healthy parity to it; it does not guarantee accuracy at arbitrary low rates or
during bursts. No failed bucket was removed, and no model parameter was changed.

Before release build, a separate ledger check matched every server completion to
the client trace/correlation, then recomputed the exact counts and mean latency
for all 33 service-minutes and matched them to P4/P5. Ledger byte hashes:

| Ledger | SHA-256 |
| --- | --- |
| `requests.jsonl` | `0ed7f11d05df97797afd6ab94fc4f237d13f3c7481f381b6b37e909558f9a47c` |
| `server-completions.jsonl` | `37bb3221bfb79bbb5541756c6366a9fcb7856f0f6d1feb1580ea0297f2a67db4` |

Before starting incidents, the exact health PID journal contained READY for the
selected stream and COMMITTED. Healthy requests then continued until all three
services produced real inference from accepted baseline history. The existing
live check ran its unchanged protocol: 180 s healthy, 360 s Payment +1200 ms
latency, 300 s recovery. Payment fault mode was restored. Live result:
**PASS**, `model_dependency=true`.

- `INC-c14e2b160c80a51f8945c10b7548a88c45431a9a3cb75497734f10e338780f0d`, revision 4, CLOSED, ELIGIBLE.

Additional verification read the pinned incident revisions with the operator
token, validated their schemas and reproduced **3** distinct
`validated-window-model/v3` signals using the exact bundle and accepted source
batch. IER `service_signals` contained model results. `/ready` returned 200 with
all dependencies true; `/ui/` returned 200. Both `/v1/detect` and `/v1/risk`
returned the designed 409 `model_requires_stream_history`. All three user units
were active on the new host release, with zero automatic restarts at verification.

Local evidence (artifacts stay outside Git):

- [Selected parity](../../artifacts/lane-b/alibaba-de38fc6b-20261114/parity-05/parity.json) and
  [verification](../../artifacts/lane-b/alibaba-de38fc6b-20261114/parity-05/verification.json); the
  [first FAIL](../../artifacts/lane-b/alibaba-de38fc6b-20261114/parity/verification.json) is retained.
- [Serving release](../../artifacts/lane-b/alibaba-de38fc6b-20261114/model-release.json),
  [host release](../../artifacts/lane-b/alibaba-de38fc6b-20261114/host-release.json), and
  [health PID readiness](../../artifacts/lane-b/alibaba-de38fc6b-20261114/health-readiness.json).
- [Accepted baseline inference](../../artifacts/lane-b/alibaba-de38fc6b-20261114/baseline-inference.json).
- [Live check](../../artifacts/lane-b/live-20261114T051455-7d7bc5cc/run.json) and
  [final verification](../../artifacts/lane-b/alibaba-de38fc6b-20261114/final-verification.json).
- [Reproduced model signals](../../artifacts/lane-b/alibaba-de38fc6b-20261114/verified-model-signals.json).

## Environment fixes and rollback

The first release build rejected the native P4 `error_count=0.0`: its validator
required an integer even though Prometheus `increase()` returns floats. The
release validator now requires finite numeric zero, rejecting booleans and any
nonzero error. Original parity JSON and ledger bytes were kept unchanged; this
consumer-only fix does not change source measurement, tolerances, features or
models. Parity and serving source commits are recorded separately above.

The checkout was behind ADR-019. `git pull --no-rebase origin main` merged the
latest upstream without discarding local commits. The delivered binary handoff
was kept at its original path and locally excluded through `.git/info/exclude`;
it was not committed as source and its checksums were not changed.

On NixOS the existing Python environment could not import NumPy because
`libstdc++.so.6` was outside the linker path. The verified host-library paths are
recorded as `LD_LIBRARY_PATH` in the new immutable host release:

```text
/nix/store/0iv8glcslgfcgn371lbjr5jjw5a6cqir-gcc-15.3.0-lib/lib
/nix/store/dbz6pb9g67kpgpl95k8d85kzpxm1c32p-zlib-1.3.2/lib
```

The lane B Docker stack runs with its existing database volumes. The three
old user units were inactive in the recorded baseline. Their immutable sessions, old host releases, model store and
`alibaba-v2-01` stream remain available; see
[baseline](../../artifacts/lane-b/alibaba-de38fc6b-20261114/baseline.json). For rollback, stop incidents,
health and API, then select the recorded old host releases with the old model
store/release and old epoch. Keep the old code with its v1 model: current
ADR-019 code rejects `badf9da0`. Do not remove historical checkpoints.

The current README, team handoff and model runbook now describe feature v2,
breach-relative monotone risk, bundled thresholds, evaluation-only MAD/slope and
the required accepted baseline. Native integration is not model-quality
acceptance: Q-04…Q-07 still have the S2/S4, live MTTD and G8 limits recorded in
the [layer-2 report](LAYER2_LOCAL_ALIBABA_DE38FC6B.md). No cloud deployment or
automatic remediation was activated.
