from types import SimpleNamespace
import hashlib
import json

import pytest

from scripts.verify_detector_development_support import summarize
from scripts import admit_detector_development_support as admission
from test_model_benchmark_protocol import fixture


def test_support_retains_unavailable_and_requires_distinct_known_and_novel_runs():
    anchors = {('run-a', 'service-a', 60): 1, ('run-b', 'service-b', 120): 1,
               ('run-c', 'service-c', 180): None}
    known = SimpleNamespace(model_id='adsketch', run_id='run-a', service='service-a',
                            timestamp=60, score=.9, pattern_kind='known', pattern_id='pattern')
    novel = SimpleNamespace(model_id='adsketch', run_id='run-b', service='service-b',
                            timestamp=120, score=.9, pattern_kind='novel', pattern_id=None)
    result = summarize([known, novel], anchors)
    assert result['status'] == 'passed'
    assert result['counts'] == {'known:1': 1, 'novel:1': 1, 'unavailable:unknown': 1}
    assert result['anchors'] == 3
    assert summarize([known], anchors)['status'] == 'blocked'
    with pytest.raises(ValueError, match='duplicate calibration prediction'):
        summarize([known, known], anchors)


def test_admission_requires_matching_reproduced_real_support(tmp_path, monkeypatch):
    protocol, ref = fixture(tmp_path)
    protocol['checks'] = [check for check in protocol['checks'] if not check['test_id'].startswith('B')]
    support = json.loads((tmp_path / protocol['data']['support']['path']).read_bytes())
    support['lanes']['b'] = {'status': 'blocked', 'scope': 'real'}
    protocol['data']['support'] = ref('blocked-support.json', support)
    draft = tmp_path / 'preflight/protocol-draft-v1'
    draft.mkdir(parents=True)
    (draft / 'protocol.json').write_text(json.dumps(protocol))
    receipt = tmp_path / 'support.json'
    receipt.write_text('{}')
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    observed = {'scope': 'alibaba-proxy-development', 'model_sha256': '1' * 64,
                'b02': {'status': 'passed'}, 'b06': {'status': 'blocked'},
                'fixtures': {'status': 'passed'}, 'limitations': []}
    monkeypatch.setattr(admission, 'verify_support', lambda *args: observed)
    args = dict(input_revision=1, output_revision=2, fit_root=tmp_path, fit_sha256='2' * 64,
                support_path=receipt, support_sha256=digest)
    with pytest.raises(ValueError, match='incomplete'):
        admission.run(tmp_path, **args)
    assert not (tmp_path / 'preflight/protocol-draft-v2').exists()
    observed['b06']['status'] = 'passed'
    result = admission.run(tmp_path, **args)
    assert result['status'] == 'ready'
    saved = json.loads((tmp_path / 'preflight/protocol-draft-v2/protocol.json').read_bytes())
    assert {check['test_id'] for check in saved['checks']} >= {'B01', 'B02', 'B03', 'B04', 'B05', 'B06'}
    with pytest.raises(ValueError, match='receipt pin'):
        admission.run(tmp_path, **{**args, 'support_sha256': '0' * 64})
