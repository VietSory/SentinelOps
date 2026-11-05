"""Recovery fixtures prove lifecycle/gate behavior, never real source admission."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from sentinelops.experiments import local_recovery as recovery
from sentinelops.experiments import local_corpus as corpus
from sentinelops.experiments.local_campaign import campaign_lock, planned_slots
from sentinelops.ml import local_data
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from sentinelops.telemetry.store import ArtifactStore


@pytest.fixture
def interrupted(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "corpus")
    plan = corpus.load_plan()
    names = list(BUSINESS_ROUTES) + ["postgres", "prometheus", "otel-collector", "jaeger"]
    created = "2020-01-01T00:00:01Z"
    images = {name: "sha256:" + f"{i+1:064x}" for i, name in enumerate(BUSINESS_ROUTES)}
    containers = {name: {"container_id": f"{i+1:064x}", "created_at": created,
        "created_at_source": created, "image": images.get(name, "sha256:" + "f"*64)}
        for i, name in enumerate(names)}
    session = {"registration_version": "local-http-session/v1", "group_id": "train-01",
        "role": "train", "ordinal": 1, "source_commit": "c"*40, "compose_project": "sentinelops-corpus-train-01",
        "plan_sha256": stable_id(plan), "policy_sha256": plan["policy_sha256"],
        "plan_ref": store.write("manifests", plan), "registered_at": "2020-01-01T00:01:00Z",
        "context_created_after": "2020-01-01T00:00:00Z", "initial_payment_rows": 0,
        "fitting_allowed_by_registration": False,
        "context": {"containers": containers, "images": images, "host_boot_sha256": "a"*64,
            "database_volume": {"name": "sentinelops-corpus-train-01_postgres",
                "created_at": created, "created_at_source": created}}}
    corpus.bind(store, corpus.registration_path(store, "train-01"), session)
    root = store.root / "runs/train-01-inventory-gradual"
    root.mkdir(parents=True)
    corpus.bind(store, root / "registration.json", {"fixture_only": True})
    (root / "incomplete-raw.json").write_bytes(b'{"fixture_only":true,"complete":false}')
    actual = [{"Id": item["container_id"], "Image": item["image"], "Created": created,
        "Config": {"Labels": {"com.docker.compose.project": session["compose_project"],
                             "com.docker.compose.service": name}},
        "State": {"Running": False, "Status": "exited", "ExitCode": 255,
            "StartedAt": "2020-01-01T00:00:02Z", "FinishedAt": "2020-01-01T01:00:00Z"},
        "RestartCount": 0} for name, item in containers.items()]
    monkeypatch.setattr(recovery, "docker", lambda *args: json.dumps(actual))
    monkeypatch.setattr(recovery, "clean_source", lambda *_: "d"*40)
    monkeypatch.setattr(recovery, "_code_hash", lambda *_: "e"*64)
    return store, plan, session, actual


def close(fixture, **overrides):
    store, plan, _, _ = fixture
    return recovery.abandon_interrupted_group(store, plan,
        **{"role": "train", "ordinal": 1, "replacement_ordinal": 4, **overrides})


def test_explicit_closure_preserves_original_bytes_and_only_replaces_whole_group(interrupted):
    store, plan, session, _ = interrupted
    before = recovery._inventory(store, "train-01")
    original = recovery.effective_slots(store, plan, planned_slots(plan))
    assert original == planned_slots(plan)  # Incomplete sources never auto-retry/allocate.
    result = close(interrupted)
    assert recovery._inventory(store, "train-01") == before
    assert result["excluded_whole_group"] and result["source_preserved"] and not result["activation_allowed"]
    assert result["replacement_group_id"] == "train-04" and result["models_fitted"] == 0
    ref, value = recovery.read_abandonment(store, session, plan)
    assert ref == result["closure_ref"] and value["incomplete_slots"] == result["incomplete_slots"]
    slots = recovery.effective_slots(store, plan, original)
    assert len(slots) == len(set(slots)) == 30
    assert slots[:3] == [("train", 4, kind) for kind in corpus.KINDS]
    assert slots[3:] == original[3:]


def test_abandoned_group_supplies_zero_windows_positives_and_group_support(interrupted, monkeypatch):
    store, plan, _, _ = interrupted
    healthy = store.root / "runs/train-01-healthy"
    corpus.bind(store, healthy / "result.json", {"fixture_only": True, "status": "completed"})
    calls = []
    # Mock the historical completed capture verifier, not current admission.
    monkeypatch.setattr(local_data, "verify_capture", lambda *a, **kw: calls.append(a[3]))
    close(interrupted)
    report, rows, cases = local_data.audit_local_corpus(store, plan=plan)
    assert calls and not rows and not cases
    assert report["status"] == "BLOCKED" and not report["training_ready"]
    assert report["registered_groups"] == 1 and report["verified_completed_runs"] == 0
    assert report["pending_slots"] == [] and len(report["excluded_groups"]) == 1
    assert all(value == 0 for role in report["counts"].values() for counts in role.values() for value in counts.values())
    assert any(c["condition"] == "train/payment-service/healthy_groups" and c["required"] == 3
               for c in report["failed_conditions"])
    assert not (store.root / "dataset-freeze.json").exists()


@pytest.mark.parametrize("change", ["running", "foreign", "different-id", "restart", "image", "future-clock"])
def test_unproven_or_live_context_never_gets_a_terminal_disposition(interrupted, change):
    store, _, _, actual = interrupted
    if change == "running":
        actual[0]["State"].update(Running=True, Status="running")
    elif change == "foreign":
        actual[0]["Config"]["Labels"]["com.docker.compose.project"] = "other-project"
    elif change == "different-id":
        actual[0]["Id"] = "f"*64
    elif change == "restart":
        actual[0]["RestartCount"] = 1
    elif change == "image":
        actual[0]["Image"] = "sha256:" + "0"*64
    else:
        actual[0]["State"]["StartedAt"] = "2020-01-01T00:02:00Z"
    with pytest.raises(ValueError):
        close(interrupted)
    assert not (store.root / "sessions/train-01/closed.json").exists()


def test_same_boot_is_not_a_host_interruption_proof(interrupted, monkeypatch):
    original_observe = recovery._observe
    def same_boot(session, *args):
        observed = original_observe(session, *args)
        observed["host_boot_sha256"] = session["context"]["host_boot_sha256"]
        recovery._validate_observation(session, observed)
    monkeypatch.setattr(recovery, "_observe", same_boot)
    with pytest.raises(ValueError, match="changed host boot"):
        close(interrupted)


@pytest.mark.parametrize("ordinal", [True, 1, 2, 3, 11])
def test_replacement_cannot_consume_reserved_or_out_of_budget_groups(interrupted, ordinal):
    with pytest.raises(ValueError):
        close(interrupted, replacement_ordinal=ordinal)


def test_final_and_frozen_corpus_refuse_terminal_changes(interrupted):
    store, _, _, _ = interrupted
    with pytest.raises(ValueError, match="sealed-final"):
        close(interrupted, role="sealed-final")
    (store.root / "dataset-freeze.json").write_text("{}")
    with pytest.raises(ValueError, match="frozen corpus"):
        close(interrupted)


def test_active_controller_or_capture_lock_refuses_abandonment(interrupted):
    store, _, _, _ = interrupted
    with campaign_lock(store):
        with pytest.raises(ValueError, match="another campaign"):
            close(interrupted)
    with corpus.acquisition_lock(store):
        with pytest.raises(ValueError, match="another acquisition"):
            close(interrupted)


def test_later_role_or_used_replacement_cannot_be_repaired_in_place(interrupted):
    store, _, _, _ = interrupted
    corpus.bind(store, store.root / "sessions/dev-01/registration.json", {"role": "development-validation"})
    with pytest.raises(ValueError, match="later role"):
        close(interrupted)


def test_existing_replacement_registration_is_never_overwritten(interrupted):
    store, _, _, _ = interrupted
    corpus.bind(store, corpus.registration_path(store, "train-04"), {"fixture_only": True})
    with pytest.raises(ValueError, match="replacement has already begun"):
        close(interrupted)


def test_corrupt_completed_capture_cannot_be_hidden_by_closure(interrupted, monkeypatch):
    store, _, _, _ = interrupted
    corpus.bind(store, store.root / "runs/train-01-healthy/result.json", {"fixture_only": True})
    def corrupt(*args, **kwargs):
        raise ValueError("corrupt original completed source")
    monkeypatch.setattr(local_data, "verify_capture", corrupt)
    with pytest.raises(ValueError, match="corrupt original"):
        close(interrupted)
    assert not (store.root / "sessions/train-01/closed.json").exists()


def test_complete_group_cannot_be_replaced_based_on_measured_outcomes(interrupted, monkeypatch):
    store, _, _, _ = interrupted
    for kind in corpus.KINDS:
        corpus.bind(store, store.root / ("runs/train-01-" + kind) / "result.json", {"fixture_only": True})
    monkeypatch.setattr(local_data, "verify_capture", lambda *_a, **_kw: {})
    with pytest.raises(ValueError, match="completed groups"):
        close(interrupted)


def test_raw_mutation_and_symlinks_cannot_be_hidden_after_abandonment(interrupted):
    store, plan, session, _ = interrupted
    close(interrupted)
    raw = store.root / "runs/train-01-inventory-gradual/incomplete-raw.json"
    raw.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="source changed"):
        recovery.read_abandonment(store, session, plan)
    raw.unlink()
    raw.symlink_to(store.root / ".campaign.lock")
    with pytest.raises(ValueError, match="symlink"):
        recovery.read_abandonment(store, session, plan)


def test_closed_group_cannot_receive_traffic_and_does_not_satisfy_prior_role_minimum(interrupted, monkeypatch):
    store, plan, _, _ = interrupted
    close(interrupted)
    monkeypatch.setattr(corpus, "clean_source", lambda *_: "d"*40)
    def forbidden(*args):
        pytest.fail("closed group must be rejected before Compose setup")
    monkeypatch.setattr(corpus, "configure_compose", forbidden)
    with pytest.raises(ValueError, match="closed acquisition group"):
        corpus.register_session(store, plan, role="train", ordinal=1, prepare=True)
    with pytest.raises(ValueError, match="insufficient registered groups"):
        corpus.require_role_order(store, plan, "calibration")
    with pytest.raises(ValueError, match="already closed"):
        close(interrupted)


def test_audit_cutoff_cannot_precede_terminal_observation(interrupted):
    from datetime import datetime, timezone
    store, plan, _, _ = interrupted
    close(interrupted)
    with pytest.raises(ValueError, match="disposition"):
        local_data.audit_local_corpus(store, plan=plan, now=datetime(2020,1,2,tzinfo=timezone.utc))


def test_observer_code_or_receipt_cannot_be_replaced_by_flags(interrupted, monkeypatch):
    store, plan, session, _ = interrupted
    close(interrupted)
    monkeypatch.setattr(recovery, "_code_hash", lambda *_: "f"*64)
    with pytest.raises(ValueError, match="observer identity"):
        recovery.read_abandonment(store, session, plan)


def test_parent_symlink_cannot_smuggle_an_external_group_inventory(interrupted):
    import shutil
    store, _, _, _ = interrupted
    original=store.root / "runs/train-01-inventory-gradual"
    moved=store.root.parent / "foreign-group"
    shutil.move(original,moved)
    original.symlink_to(moved,target_is_directory=True)
    with pytest.raises(ValueError,match="symlink"):
        close(interrupted)


def test_effective_schedule_rejects_relabelled_registration(interrupted):
    store,plan,session,_=interrupted
    alias={**session,"group_id":"cal-01","role":"calibration"}
    # Bind a different readable manifest at the expected path: not admission authority.
    path=corpus.registration_path(store,"train-01")
    path.unlink()
    corpus.bind(store,path,alias)
    with pytest.raises(ValueError,match="group/role registration"):
        recovery.effective_slots(store,plan,planned_slots(plan))
