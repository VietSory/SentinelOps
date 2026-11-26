import pytest

from scripts.draft_ltfp_protocol import _records, run


def test_records_preserve_complete_one_hop_topology_and_time_dependencies():
    edges = {('a', 'b'), ('b', 'c'), ('a', 'c'), ('c', 'd')}
    rows = [dict(record_id='one', service='a', minute=170, role='train'),
            dict(record_id='two', service='b', minute=171, role='train')]
    records, used = _records(rows, edges, 'train')
    assert [entry['service'] for entry in records[0]['contributors']] == ['b', 'c']
    assert records[0]['directed_edges'] == [('a', 'b'), ('a', 'c'), ('b', 'c')]
    assert [entry['service'] for entry in records[1]['contributors']] == ['a', 'c']
    assert records[1]['directed_edges'] == records[0]['directed_edges']
    assert used == records[0]['directed_edges']
    assert (records[0]['required_start'], records[0]['required_end']) == (102, 177)
    assert (records[0]['partition_start'], records[0]['partition_end']) == (0, 179)


def test_development_record_retains_calibration_parent():
    rows = [dict(record_id='one', service='a', minute=324, role='development')]
    records, edges = _records(rows, set(), 'calibration')
    assert records[0]['parent_split'] == 'calibration'
    assert (records[0]['partition_start'], records[0]['partition_end']) == (180, 359)
    assert records[0]['contributors'] == records[0]['directed_edges'] == edges == []


def test_existing_draft_is_not_overwritten(tmp_path):
    draft = tmp_path / 'preflight/protocol-draft-v1'
    draft.mkdir(parents=True)
    (draft / 'protocol.json').write_text('existing')
    with pytest.raises(ValueError, match='already exists'):
        run(tmp_path, 'a' * 40)
    assert (draft / 'protocol.json').read_text() == 'existing'
