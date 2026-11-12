# G6 local trials — Alibaba release `3966e75e` (2026-11-09)

Release `artifact://manifests/3966e75e…3730.json` (bundle `badf9da0`, G3 accepted
with conditions, D-22). Operator: Claude, delegated by the owner for these trials.
Each trial runs on a new owned kind cluster from a clean checkout. The known-good
ledger is approved before the bad image is deployed. Native H4 grants dispatch
authority, then rollback runs, then the 600 s executor verification decides the
terminal outcome. Summary: `scripts/summarize_g6_trials.py` (receipts only).

## Golden path (Q-09: ≥ 9/10 recovered for this release, no pooling)

| Trial | Commit | Outcome | Bad image → action | Action → terminal |
| --- | --- | --- | --- | --- |
| 02 | `68abb6f` | recovered | 6 m 44 s | 5 m 00 s |
| 03 | `58dfaaf` | recovered | 6 m 47 s | 5 m 01 s |
| 04 | `58dfaaf` | **blocked** | 6 m 49 s | 0 s |
| 05 | `58dfaaf` | recovered | 6 m 48 s | 5 m 03 s |
| 06 | `f615428` | recovered | 7 m 04 s | 5 m 00 s |
| 07 | `f615428` | recovered | 6 m 49 s | 4 m 57 s |
| 08 | `f615428` | recovered | 6 m 47 s | 4 m 59 s |
| 09 | `f615428` | recovered | 6 m 48 s | 5 m 00 s |
| 10 | `f615428` | recovered | 7 m 04 s | 5 m 01 s |
| 11 | `927a3e8` | recovered | 7 m 02 s | 4 m 56 s |

Trial 04: the harness sent request revision 1 after the incident had advanced to
revision 2. The executor guard rejected the request and nothing was mutated. This
is correct guard behaviour and a harness defect, fixed in `f615428` (re-snapshot
when the revision advances before dispatch). It counts as not recovered. It is not
re-run or excluded.

## Failed verify (Q-09: 100 % escalated with a complete audit)

| Trial | Scenario | Outcome | Audit chain |
| --- | --- | --- | --- |
| 12 | `commerce-db-unavailable-after-rollback` (commit `927a3e8`) | **escalated**, reason `verification-timeout-target-quarantined` | complete: same action ID, hash chain from `prepared` to `escalated`, ending at the last event hash |

## Safety (Q-10)

Trials 02–12 (summary v2): 10 mutations, 0 unsafe. Every patch set the
known-good image approved before the bad change, on the target pinned by the
policy (patch target hash = `stable_id` of the pinned target). Trial 04 made no
mutation. No background task failures were recorded in any trial. Q-10 also requires the
mandatory safety cases (negative, crash and concurrency tests); this table is
only the per-trial input.

## Q-10 mandatory safety cases (TECHNICAL_SPEC §13)

Each case maps to tests that assert the expected outcome and the actual side effects. All
of them ran in CI on `main` (`foundation`; the native PostgreSQL and kind checks in
`remediation-integration`).

| Case | Expected | Evidence |
| --- | --- | --- |
| Disabled action / kill switch | blocked, no patch | `test_policy_rejections_never_dispatch[disabled]`, `test_db_failure_before_dispatch_and_kill_switch_after_arm_never_send_patch` |
| Wrong namespace / resource UID | blocked, no dry-run or patch | `Target` refuses any namespace, deployment or container outside the sandbox (`test_target_outside_the_sandbox_cannot_even_be_constructed`); a foreign namespace, deployment or cluster UID reaching the executor is blocked with no Kubernetes mutation (the executor still performs its read-only observation of the Deployment before blocking) and a journal reason (`test_wrong_identity_or_blast_radius_never_reaches_kubernetes_mutation`); `test_foreign_parent_identity_or_incomplete_inventory_rejected`; native kind check (RBAC `resourceNames`) |
| Stale or missing refs | blocked | `[stale]`, `[revision]`, `[wrong-change]`, `[wrong-generation]`; H4 matrix `test_native_h4_requires_verified_kubernetes_model_change_combination` |
| Low confidence / RCA abstained | blocked | `[unqualified]`, `[abstained]`; H4 matrix `rca-abstained` |
| Absent known-good | blocked, no Kubernetes request | `test_absent_known_good_blocks_before_any_kubernetes_request` (added 2026-11-09), registry `test_corrupt_or_unscoped_approval_is_not_a_known_good_image`, `test_known_good_must_predate_entire_observed_mutation_interval` |
| Excessive replicas / blast radius | blocked, no dry-run or patch | `test_blast_radius_and_external_scaling_are_blocked`; executor level: replicas, surge, HPA, paused, no pods and Recreate are blocked before any dry-run or patch (after the read-only observation) (`test_wrong_identity_or_blast_radius_never_reaches_kubernetes_mutation`) |
| Dry-run denial | blocked, no fallback patch | `[dryrun]`, `test_server_dry_run_is_all_and_failed_precondition_has_no_fallback_patch`; native kind dry-run |
| Duplicate / concurrent requests | one dispatch | `test_concurrent_consumers_only_one_dispatch_and_never_double_recovery`; native store CAS check |
| DB audit failure | no patch, or committed patch survives | `test_db_failure_before_dispatch…`, `test_committed_dispatch_survives_database_failure_after_patch_receipt`; native DB stop/start |
| Worker crash before / after patch | never dispatched / reconciled without resend | `test_armed_but_unsent_crash_never_dispatches_and_eventually_quarantines`, `test_restart_observes_uncertain_sent_patch_without_resend_then_verifies` |
| Kubernetes timeout | reconcile, no resend | `test_timeout_after_server_commit_and_restart_reconcile_without_resending` |
| Verification success | recovered | `test_patch_is_verifying_until_new_pod_and_current_post_action_telemetry_succeed`; G6 trials 02, 03 and 05–11 (trial 04 was blocked, not verified) |
| Verification failure | escalated, exactly one patch, no compensation | G6 trial 12 (native); `test_verification_failure_escalates_once_without_a_second_or_compensating_patch` (forged recovery, new pod never ready, no telemetry, inconclusive telemetry, HPA or extra replicas appearing during verification: one dry-run and one patch in total, escalation stays terminal on later reconcile and re-request; a mutant that compensates on any escalation fails all six) |
| Missing telemetry | escalated on timeout | `test_ready_deployment_without_telemetry_times_out_and_no_bad_image_compensation`, verification `test_no_history_and_scan_budget_never_claim_recovery` |

Every case now has at least one test that asserts both the outcome and the side
effects. B's independent review (2026-11-10) found three cases whose side effects
were asserted only at the domain level: wrong namespace/UID, blast radius and
verification failure. The executor-level tests above close them. Each was
mutation-checked: removing the pre-dispatch validation fails 9 of them, and adding a
compensating patch on timeout fails the verification cases.

### B follow-up review — 2026-11-12

Codex reviewed the added executor-level cases against `RollbackExecutor`,
`Deployment.validate`, the Kubernetes adapter, the resume-only worker and the
journal's claim/release semantics at `fa51a72`. The three findings from the
2026-11-10 review are closed: invalid identity/blast radius is rejected before
dry-run or mutation, and failed verification stays escalated without another
patch. No production guard or policy change was necessary.

`tests/test_remediation_transport.py` (`fbc6568`) adds eight executor/adapter integration
checks using the real `LocalKubernetes` implementation with HTTP fixtures:
three foreign UIDs, excessive replicas, HPA, dry-run denial, and loss of a patch
response followed by restart with successful or missing recovery telemetry.
The last two observe one real-request PATCH plus one `dryRun=All` request,
never a resend or compensation. These are protocol fixtures, not new native
rollback trials or independent RCA evidence.

The remediation suite (executor, adapter, registry, verification, worker and
transport) passed 111 tests. The separately owned PostgreSQL check also passed:
CAS/dispatch budget, immutable audit, outage/restart, quarantine and read-only
HTTP. Its incidents/action outcomes are fixtures, and it performs no Kubernetes
mutation. Reproduce with:

```sh
python -m pytest -q tests/test_remediation_executor.py tests/test_remediation_kubernetes.py tests/test_remediation_registry.py tests/test_remediation_verification.py tests/test_remediation_worker.py tests/test_remediation_transport.py
python -m scripts.check_remediation_store --output <new-output.json>
```

This closes the code/test follow-up review, not G6 acceptance. The 9/10 native
golden-path and 1/1 failed-verify denominators above are unchanged. The owner's
gate decision and full-system evidence requirements remain separate.

## Trial 12 detail

The rollback was dispatched at 08:31:40Z. The harness then scaled the in-cluster
commerce PostgreSQL to zero. Payment needs the database to start and to pass
`/ready`, so the rolled-back pod never became healthy. The executor escalated when
the 600 s verification window expired, and the target stays quarantined for operator
review. The harness change `927a3e8` (retry source outages only during the injected
outage) was in place but not exercised: no `incident-source-unavailable` event
occurred.

## Result

Measured against Q-09 for this release (no pooling):

| Clause | Target | Measured |
| --- | --- | --- |
| Golden path | ≥ 9/10 recovered | **9/10** (02–11; 04 blocked by a harness race, counted) |
| Failed verify | 100 % escalated with complete audit | **1/1** |
| Per-trial unsafe patches (Q-10 input) | 0 | **0** of 10 mutations |

The release meets the local Q-09 criteria exactly at the threshold. It is not
yet G6 PASS: B's follow-up review above closes the three test findings, while
G6 acceptance is an owner decision (`G6_accepted=false` in every
receipt). The failed-verify clause rests on a single scenario.

The earlier trial with H1 release `656bc7c5` (2026-11-08, recovered) is reported
separately and is not pooled. The host was released to the layer-2 evaluation at
08:41:50Z.
