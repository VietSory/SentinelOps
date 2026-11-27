"""One accepted batch joined to four development forecast rows; no activation."""
from datetime import timedelta
from pathlib import Path

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_live import project_graph_window
from sentinelops.ml.model_benchmark_forecast import forecast_rows
from sentinelops.ml.model_benchmark_scoring import baseline_scores, refit_score
from sentinelops.ml.models import read_bundle_manifest
from sentinelops.telemetry.core import stable_id


def score_batch(store, *, batch_ref, anchor, as_of, service, source_feature_ref,
                node_ids, topology, expected_graph_sha256, config, bundle,
                manifest_sha256, oracle, health_policy, run_id, group_id,
                models=None, fixture_scope=False, xgboost_refit=None, runtime=None):
    """Score a target with trusted local, digest-pinned artifacts.

    models optionally maps ltfp_graph/ltfp_self_only to directory,
    metadata_sha256, identity and fitted_verified. fitted_verified is a caller
    attestation, not a fit certificate generated here. Untrained weights can
    score only with explicit fixture_scope=True; these rows never imply readiness.
    source_feature_ref is the genuine immutable source clock frame, retained
    separately when baseline inference supplies its exact relative projection.
    xgboost_refit pins the paired training input, refit manifest and weights.
    """
    end, cutoff = utc_time(anchor), utc_time(as_of)
    if (not run_id or not group_id or type(fixture_scope) is not bool
            or end.second or end.microsecond or cutoff < end):
        raise ValueError('invalid batch identity or cutoff')
    if store.read(batch_ref)['bucket_end'] != anchor:
        raise ValueError('accepted batch anchor mismatch')
    if service not in node_ids or len(set(node_ids)) != len(node_ids):
        raise ValueError('target requires unique aligned graph nodes')
    if not source_feature_ref.startswith('artifact://features/'):
        raise ValueError('genuine source feature artifact required')
    source = store.read(source_feature_ref)
    if (source['service'] != service or utc_time(source['window_end']) != end
            or utc_time(source['window_start']) != end - timedelta(minutes=5)
            or utc_time(source['as_of']) != cutoff):
        raise ValueError('source feature frame clock or service mismatch')
    specs = models or {}
    if set(specs) - {'ltfp_graph', 'ltfp_self_only'} or not expected_graph_sha256:
        raise ValueError('unknown model or missing inference topology pin')
    if runtime is None:
        manifest = read_bundle_manifest(Path(bundle), expected_manifest_sha256=manifest_sha256)
    else:
        loaded_bundle = runtime['bundle']
        if (Path(loaded_bundle['directory']).resolve() != Path(bundle).resolve()
                or loaded_bundle['manifest_sha256'] != manifest_sha256):
            raise ValueError('preloaded baseline differs from the pinned bundle')
        manifest = loaded_bundle['manifest']
    if (manifest['provenance']['config_sha256'] != stable_id(config)
            or manifest['ordered_features'] != config['features']):
        raise ValueError('baseline and graph feature config mismatch')
    for name, spec in specs.items():
        if (spec['identity']['self_only'] is not (name == 'ltfp_self_only')
                or spec['identity']['config_sha256'] != stable_id(config)):
            raise ValueError('model variant or config mismatch')
    baseline = baseline_scores(store, batch_ref, service, as_of=cutoff, bundle=bundle,
                               manifest_sha256=manifest_sha256, loaded=runtime['bundle'] if runtime is not None else None)
    if baseline['config_sha256'] != stable_id(config):
        raise ValueError('baseline and graph feature config mismatch')
    scores = dict(baseline['scores'])
    identities = {name: {'model_version': baseline['model_version'],
                        'manifest_sha256': manifest_sha256, 'scope': 'pinned-baseline'}
                  for name in scores}
    if xgboost_refit is not None:
        scores['xgboost'], identities['xgboost'] = refit_score(
            store, baseline, config, **xgboost_refit, loaded=runtime['xgboost'] if runtime is not None else None)
    elif not fixture_scope:
        scores['xgboost'] = {'score': None, 'missing_reason': 'benchmark-refit-required'}
        identities['xgboost'] = {'scope': 'unavailable'}
    graph_provenance = {}
    for name, self_only in (('ltfp_self_only', True), ('ltfp_graph', False)):
        spec = specs.get(name)
        reason, value = 'model-artifact-unavailable', None
        identities[name] = {'scope': 'unavailable'}
        if spec is not None:
            identity = spec['identity']
            from sentinelops.ml.ltfp_model import load, predict

            if runtime is None:
                model = load(spec['directory'], metadata_sha256=spec['metadata_sha256'], expected_identity=identity)
            else:
                item = runtime['ltfp'][name]
                if (Path(item['directory']).resolve() != Path(spec['directory']).resolve()
                        or item['metadata_sha256'] != spec['metadata_sha256']
                        or item['identity_sha256'] != stable_id(identity)):
                    raise ValueError('preloaded LTFP differs from its pinned identity')
                model = item['model']
            fitted = identity['weights_status'] == 'development-fitted' and spec.get('fitted_verified') is True
            scope = 'development-fitted' if fitted else 'fixture' if fixture_scope else 'unavailable'
            identities[name] = {'identity': dict(identity), 'metadata_sha256': spec['metadata_sha256'],
                                'scope': scope}
            reason = 'fitted-model-identity-required'
            if fitted or fixture_scope:
                reason = 'forecast-anchor-minute-advanced'
                if cutoff < end + timedelta(minutes=1):
                    graph = project_graph_window(store, batch_ref, node_ids=node_ids,
                        topology=topology, as_of=cutoff, config=config, self_only=self_only)
                    reason = 'accepted-graph-history-unavailable'
                    if graph is not None:
                        if graph['graph_sha256'] != expected_graph_sha256:
                            raise ValueError('inference topology pin mismatch')
                        value = predict(model, identity, graph,
                                        expected_graph_sha256=expected_graph_sha256)[service]
                        index = graph['node_ids'].index(service)
                        neighbor_count = (sum(graph['attention_mask'][index]) - graph['attention_mask'][index][index]
                                          if graph['target_available'][index] else None)
                        graph_provenance[name] = {'graph_sha256': graph['graph_sha256'],
                            'mask_sha256': graph['mask_sha256'], 'input_sha256': stable_id(graph),
                            'raw_refs': graph['raw_refs'], 'as_of': graph['as_of'],
                            'step_minutes': graph['step_minutes'], 'source_identity': graph['source_identity'],
                            'neighbor_count': neighbor_count}
                        reason = None if value is not None else 'incomplete-feature-sequence'
        scores[name] = {'score': value, 'missing_reason': reason}
    feature_ref = baseline['feature_ref'] or source_feature_ref
    frame = store.read(feature_ref)
    row = {key: frame[key] for key in ('service', 'window_start', 'window_end')}
    row.update(feature_ref=feature_ref, prediction_cutoff=frame['as_of'], run_id=run_id, group_id=group_id)
    rows = forecast_rows(store, row, oracle, health_policy, scores)
    return [{**item, 'source_feature_ref': source_feature_ref,
             'model_provenance': identities[item['model']],
             'graph_provenance': graph_provenance.get(item['model']),
             'fixture_scope': fixture_scope, 'activation_allowed': False} for item in rows]
