from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
import yaml

from sentinelops.contracts import validate_payload
from sentinelops.incidents.correlation import correlate_health
from sentinelops.incidents.correlation_replay import replay_correlation
from sentinelops.incidents.health import advance_health
from sentinelops.telemetry.core import BucketMetrics, normalize_bucket, stable_id
from sentinelops.telemetry.store import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "configs/slo_health_policy.json").read_text())
TOPOLOGY = yaml.safe_load((ROOT / "configs/topology.yaml").read_text())
END = datetime(2026, 10, 26, tzinfo=timezone.utc)
SCOPE = "b" * 64


def opening(service, offset=0, *, environment="local"):
    state = None
    for index in (1, 2):
        end = END + timedelta(seconds=offset + index * 60)
        obs = normalize_bucket(BucketMetrics(service, end, 120, 12, ((0, 0), (100, 120), (float('inf'), 120)),
            1., end + timedelta(seconds=30), (f"fixture://{service}/{offset}/{index}",)), environment=environment)
        state = advance_health(state, obs, service=service, environment=environment,
            source_profile=obs["source_profile"], bucket_end=end, evaluated_at=end + timedelta(seconds=30), policy=POLICY)
    return state


def correlate(signals, topology=TOPOLOGY, *, as_of=END+timedelta(hours=1), scope=SCOPE):
    return correlate_health(signals, topology, as_of=as_of, source_scope_sha256=scope)


def test_dependency_cascade_groups_with_path_and_preserves_ids_under_order_duplicates():
    pay, checkout = opening("payment-service"), opening("checkout-service")
    result = correlate([pay, checkout])
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert group["affected_services"] == ["checkout-service", "payment-service"]
    assert group["structural_candidates"] == ["payment-service"]
    assert group["dependency_support"][0]["path"] == ["checkout-service", "payment-service"]
    reordered = correlate([checkout, pay, pay])
    assert reordered["groups"] == result["groups"] and reordered["result_id"] == result["result_id"]
    assert reordered["duplicate_count"] == 1 and not result["rca_evaluated"] and not result["actions_allowed"]
    assert correlate([pay, checkout], scope="c"*64)["groups"][0]["group_id"] != group["group_id"]


def test_independent_roots_are_never_merged_through_shared_checkout_or_shared_rds():
    signals = [opening(service) for service in ("checkout-service", "payment-service", "inventory-service")]
    result = correlate(signals)
    assert len(result["groups"]) == 3
    ambiguous = next(group for group in result["groups"] if group["affected_services"] == ["checkout-service"])
    assert ambiguous["grouping_reason"] == "ambiguous-shared-consumer"
    assert ambiguous["structural_candidates"] == ["inventory-service", "payment-service"]
    assert len(ambiguous["dependency_support"]) == 2
    assert all("rds" not in group["structural_candidates"] for group in result["groups"])
    assert len({s for group in result["groups"] for s in group["signal_ids"]}) == 3


def test_time_chains_do_not_expand_group_beyond_the_frozen_120s_span():
    result = correlate([opening("payment-service"), opening("checkout-service", 120), opening("inventory-service", 240)])
    assert len(result["groups"]) == 2
    assert max(len(g["affected_services"]) for g in result["groups"]) == 2


def test_future_and_non_opening_signals_remain_in_accounting_without_becoming_incidents():
    pay = opening("payment-service")
    later = opening("checkout-service", 60)
    healthy = json.loads((ROOT / "samples/health-signal.sample.json").read_text())
    result = correlate([pay, later, healthy], as_of=END+timedelta(seconds=180))
    assert result["future_count"] == 2 and len(result["groups"]) == 1
    assert len(result["input_signal_ids"]) == 3 and len(result["opening_signal_ids"]) == 1
    assert correlate([pay, healthy])["non_opening_count"] == 1
    assert correlate([])["groups"] == []


def test_conflicting_episode_source_and_policy_cannot_be_silently_deduplicated_or_joined():
    pay = opening("payment-service")
    altered = deepcopy(pay)
    altered["evaluated_at"] = altered["episode_confirmed_at"] = "2026-10-26T00:02:31Z"
    altered["signal_id"] = stable_id({k:v for k,v in altered.items() if k!='signal_id'})
    with pytest.raises(ValueError, match="conflicting"): correlate([pay, altered])
    with pytest.raises(ValueError, match="mix"): correlate([pay, opening("checkout-service", environment="another")])
    with pytest.raises(ValueError, match="scope identity"): correlate([pay], scope="missing")


@pytest.mark.parametrize("mutation", ["cycle", "unknown", "duplicate", "empty"])
def test_bad_topology_fails_instead_of_inventing_dependencies(mutation):
    topology = deepcopy(TOPOLOGY)
    if mutation == "cycle": topology['dependencies']['payment-service'] = ['checkout-service']
    elif mutation == "unknown": topology['dependencies']['payment-service'] = ['nonexistent']
    elif mutation == "duplicate": topology['dependencies']['payment-service'] = ['rds','rds']
    else: topology['dependencies'] = {}
    with pytest.raises(ValueError): correlate([opening("payment-service")], topology)


def test_rehashing_a_result_cannot_invent_grouping_or_unresolved_dependency_support():
    result = correlate([opening("payment-service"), opening("checkout-service")])
    group = result["groups"][0]
    group["structural_candidates"] = ["checkout-service"]
    group['group_id'] = stable_id({k:v for k,v in group.items() if k!='group_id'})
    result['result_id'] = stable_id({k:v for k,v in result.items() if k not in ('result_id','duplicate_count')})
    with pytest.raises(ValueError, match="conservative episode grouping"):
        validate_payload('correlation-result.schema.json', result)


def test_opening_event_cannot_mislabel_a_recovering_health_snapshot():
    signal = opening("payment-service")
    signal.update(health="recovering",breach_streak=0,clear_streak=2)
    signal['signal_id'] = stable_id({k:v for k,v in signal.items() if k!='signal_id'})
    with pytest.raises(ValueError, match="opening event"):
        validate_payload('health-signal.schema.json', signal)


def test_immutable_upstream_health_replay_consumer_has_explicit_single_scope(tmp_path):
    from sentinelops.incidents.health_replay import replay_health_manifest
    from sentinelops.telemetry.pipeline import process_captures
    from test_slo_health import capture
    source = ArtifactStore(tmp_path / 'source')
    manifest = process_captures([capture(0),capture(1)],source)
    health = replay_health_manifest(source,manifest['manifest_ref'],POLICY,tmp_path/'health')
    health_store = ArtifactStore(tmp_path/'health')
    one = replay_correlation(health_store,health['report_ref'],TOPOLOGY,tmp_path/'one')
    two = replay_correlation(health_store,health['report_ref'],TOPOLOGY,tmp_path/'two')
    assert one==two and len(one['correlation']['groups'])==1
    assert one['correlation']['groups'][0]['affected_services']==['payment-service']
    assert ArtifactStore(tmp_path/'one').read(one['report_ref'])['correlation']==one['correlation']
    with pytest.raises(FileExistsError): replay_correlation(health_store,health['report_ref'],TOPOLOGY,tmp_path/'one')
    invalid=health_store.read(health['report_ref']);invalid['signal_refs'].pop()
    ref=health_store.write('manifests',invalid)
    with pytest.raises(ValueError,match='incomplete'): replay_correlation(health_store,ref,TOPOLOGY,tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()
    altered=health_store.read(health['report_ref'])
    signal=health_store.read(altered['signal_refs'][0]);signal['previous_signal_id']='a'*64
    signal['signal_id']=stable_id({k:v for k,v in signal.items() if k!='signal_id'})
    altered['signal_refs'][0]=health_store.write('signals',signal)
    ref=health_store.write('manifests',altered)
    with pytest.raises(ValueError,match='chain/cutoff'):
        replay_correlation(health_store,ref,TOPOLOGY,tmp_path/'bad-chain')
