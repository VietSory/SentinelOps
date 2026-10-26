from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path

import pytest

from sentinelops.incidents.domain import IncidentWrite
from sentinelops.incidents.repository import RepositoryIntegrityError,snapshot


def row_fixture():
    root=Path(__file__).resolve().parents[1]
    envelope=json.loads((root/"samples/incident-envelope.sample.json").read_text())
    context=envelope["context"]
    command=IncidentWrite(envelope["payload"],context["lifecycle_stage"],0,"fixture-key",envelope["actor"],envelope["reason"],
                          {key:value for key,value in context.items() if key!="lifecycle_stage"})
    return {**envelope,"recorded_at":datetime(2026,10,26,tzinfo=timezone.utc),
            "payload":json.dumps(envelope["payload"]),"context":json.dumps(context),
            "idempotency_key":"fixture-key","request_hash":command.fingerprint()}


def test_read_validates_the_persisted_snapshot_before_returning_it():
    row=row_fixture()
    assert snapshot(row)["revision"]==1
    changed=json.loads(row["payload"]);changed["severity"]="critical"
    row["payload"]=json.dumps(changed)
    with pytest.raises(RepositoryIntegrityError,match="integrity checks"):
        snapshot(row)


@pytest.mark.parametrize("mutation",["context","identity","hash","malformed_json"])
def test_read_rejects_corrupt_context_identity_or_fingerprint(mutation):
    row=row_fixture()
    if mutation=="context": row["context"]="{}"
    elif mutation=="identity": row["incident_id"]="wrong"
    elif mutation=="hash": row["request_hash"]="0"*64
    else: row["payload"]="invalid json"
    with pytest.raises(RepositoryIntegrityError):
        snapshot(row)
