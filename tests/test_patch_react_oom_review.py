"""Fail-closed asset edits and executable proposal/status rendering guards."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import patch_react_oom_review as patcher


@pytest.fixture
def assets(tmp_path, monkeypatch):
    files = {
        "LiveApp-DryBTgPw.js": 'const preserve="unrelated";function Ge({row}){return row;}function Ke(){return 1;}',
        "App-CYliiKd_.js": 'const jQ={expired:{vi:"Hết hạn",en:"Expired",tone:"neutral"}};' + patcher.STATUS_RENDERER + 'const preserve="auth-and-api";',
        "client.js": "export function memoryReview(row) { return row; }",
        "index.html": "unchanged entrypoint",
        "style.css": "unchanged CSS",
    }
    for name, value in files.items():
        (tmp_path / name).write_text(value)
    monkeypatch.setattr(patcher, "ORIGINAL_HASHES", {
        name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in patcher.ORIGINAL_HASHES
    })
    return tmp_path


def snapshot(assets):
    return {p.name: p.read_bytes() for p in assets.iterdir()}


@pytest.mark.parametrize("name", tuple(patcher.ORIGINAL_HASHES))
def test_unreviewed_hash_fails_before_any_write(assets, name):
    (assets / name).write_text((assets / name).read_text() + "changed")
    before = snapshot(assets)
    with pytest.raises(ValueError, match="Unreviewed React asset SHA-256"):
        patcher.apply(assets)
    assert snapshot(assets) == before


@pytest.mark.parametrize("name,old,new", [
    ("LiveApp-DryBTgPw.js", "function Ke(", "function Unknown("),
    ("App-CYliiKd_.js", "jQ={expired:", "jQ={missing:"),
    ("App-CYliiKd_.js", patcher.STATUS_RENDERER, "function Sy(){return null;}"),
    ("client.js", "export function memoryReview", "export function unknownReview"),
])
def test_structural_failure_prepares_all_edits_before_any_write(assets, monkeypatch, name, old, new):
    path = assets / name
    path.write_text(path.read_text().replace(old, new))
    monkeypatch.setitem(patcher.ORIGINAL_HASHES, name, hashlib.sha256(path.read_bytes()).hexdigest())
    before = snapshot(assets)
    with pytest.raises(ValueError):
        patcher.apply(assets)
    assert snapshot(assets) == before


def test_scoped_bundles_and_canonical_clients_change(assets):
    before = snapshot(assets)
    changed = patcher.apply(assets)
    assert set(changed) == {"LiveApp-DryBTgPw.js", "App-CYliiKd_.js", "client.js", "workspace.js"}
    for name in ("index.html", "style.css"):
        assert (assets / name).read_bytes() == before[name]
    for name in ("client.js", "workspace.js"):
        assert (assets / name).read_bytes() == (Path(__file__).parents[1] / "src/sentinelops/ui/assets" / name).read_bytes()
    live = (assets / "LiveApp-DryBTgPw.js").read_text()
    assert live.startswith(patcher.IMPORT + 'const preserve="unrelated";')
    assert live.endswith("function Ke(){return 1;}")
    assert "r.jsxs('p'," in live
    assert "className:'p-5 space-y-4'" in live
    assert "className:'block w-full mt-2 rounded-lg border border-slate-300 p-3'" in live
    assert "proposal=row.proposal" in live
    assert "p.useState('')" in live
    assert "ke(proposal.expires_at,isEn)" in live


def node(code):
    if not shutil.which("node"):
        pytest.skip("Node is required to execute the existing React JavaScript")
    result = subprocess.run(["node", "--input-type=module"], input=code, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("status,tone", [
    ("dispatching", "orange"), ("verifying", "medium"), ("recovered", "success"),
    ("blocked", "critical"), ("escalated", "critical"), ("unknown-future-phase", "neutral"),
    ("__proto__", "neutral"), ("toString", "neutral"),
])
def test_historical_memory_statuses_and_unknown_fallback_render(assets, status, tone):
    patcher.apply(assets)
    code = 'const vt=()=>({isEn:true}),h={jsx:(type,props)=>props},be="badge";'
    code += (assets / "App-CYliiKd_.js").read_text()
    code += "console.log(JSON.stringify(Sy({status:" + json.dumps(status) + "})));"
    result = node(code)
    assert result["tone"] == tone
    assert result["children"]
    if status in {"unknown-future-phase", "__proto__", "toString"}:
        assert result["children"] == "Unknown"


def render_case(*, mutation="", status="pending", can_approve=True):
    # Execute the actual current client validator, not a permissive test stub.
    client = (Path(__file__).parents[1] / "src/sentinelops/ui/assets/client.js").read_text()
    review = client[client.index("export const memoryRunbook"):client.index("export function validProposal")].replace("export ", "")
    code = review + "\n" + r"""
const oomMemoryReview=memoryReview;
const oomMemoryRecoveryReview=memoryRecoveryReview;
const nodes=[],dates=[],decisions=[];
const p={useState:()=>['Operator reviewed the exact target',()=>{}]};
const r={jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})};
const pe='CARD',De='BADGE',Ue='FACTS',P='CALLOUT',je='RECEIPT',re='BUTTON';
const ke=(value,isEn)=>{dates.push(isEn);return 'date';};
const window={confirm:()=>true};
const now=Date.parse('2026-11-20T19:00:00Z');
const image='registry.example/oom-demo@sha256:'+'a'.repeat(64);
const target={namespace:'sentinelops-b-actions',deployment:'sentinelops-oom-demo',container:'oom-demo',image};
const row={status:'pending',manual_recovery_available:true,current_revision:1,proposal_sha256:'b'.repeat(64),proposal:{
executor_runbook:memoryRunbook,resource_change:{memory_before:'64Mi',memory_after:'256Mi'},
cause:'OOMKilled',pod_uid:'owned-pod-uid',target,current_image:image,incident_id:'oom-incident',
incident_revision:1,runbook_id:'pod-memory-limit-demo/v1',proposal_id:'c'.repeat(64),
created_at:new Date(now-1000).toISOString(),expires_at:new Date(now+60000).toISOString()},
review_change:{kind:'memory_limit',memory_before:'64Mi',memory_after:'256Mi',cause:'OOMKilled',
pod_uid:'owned-pod-uid',deployment:target.deployment,container:target.container,image}};
const state={canApprove:CAN_APPROVE,decisionLoading:false};
const engine={decideProposal:(...args)=>decisions.push(args)};
""".replace("CAN_APPROVE", json.dumps(can_approve))
    code += "row.status=" + json.dumps(status) + ";\n" + mutation + "\n" + patcher.proposal_component()
    code += r"""
const tree=Ge({row,state,engine,now,isEn:true});
function walk(value){if(Array.isArray(value))return value.forEach(walk);if(!value||typeof value!=='object')return;
 if(value.type){nodes.push(value);walk(value.props?.children);}}
walk(tree);
const buttons=nodes.filter(n=>n.type==='BUTTON');
for(const button of buttons)if(!button.props.disabled)button.props.onClick();
await Promise.resolve();
console.log(JSON.stringify({buttons:buttons.map(n=>({disabled:n.props.disabled,label:n.props.children})),
facts:nodes.find(n=>n.type==='FACTS').props.items,dates,decisions,
badge:nodes.find(n=>n.type==='BADGE').props,forms:nodes.filter(n=>n.type==='form').length,
paragraphs:nodes.filter(n=>n.type==='p').length,notices:nodes.filter(n=>n.type==='CALLOUT').map(n=>n.props.children)}));
"""
    return node(code)


def test_valid_memory_review_uses_current_client_and_preserves_human_decision_gate():
    result = render_case()
    assert result["buttons"] == [{"disabled": False, "label": "Approve"}, {"disabled": False, "label": "Reject"}]
    assert [row[1] for row in result["facts"] if row[0] == "Memory limit"] == ["64Mi → 256Mi"]
    assert result["dates"] == [True]
    assert result["paragraphs"] >= 1
    assert [decision[1] for decision in result["decisions"]] == ["approve", "reject"]


@pytest.mark.parametrize("mutation", [
    "row.review_change=null;",
    "row.proposal.target.namespace='other-namespace';",
    "row.proposal.target.deployment='other-deployment';",
    "row.proposal.target.container='other-container';",
    "row.review_change.image='untrusted:latest';",
    "row.proposal.current_image='untrusted:latest';",
    "row.proposal.resource_change.memory_after='512Mi';",
    "row.review_change.memory_before='32Mi';",
])
def test_incomplete_or_mismatched_memory_recovery_bounds_never_approve(mutation):
    result = render_case(mutation=mutation)
    assert result["buttons"][0] == {"disabled": True, "label": "Approve"}
    assert result["buttons"][1] == {"disabled": False, "label": "Reject"}
    assert [decision[1] for decision in result["decisions"]] == ["reject"]


@pytest.mark.parametrize("mutation", [
    "row.current_revision=2;",
    "row.proposal.expires_at=new Date(now-1).toISOString();",
    "row.proposal.created_at=new Date(now+1).toISOString();",
    "state.decisionLoading=true;",
    "state.canApprove=false;",
])
def test_pending_guard_remains_closed_without_current_authorized_window(mutation):
    result = render_case(mutation="row.manual_recovery_available=false;"+mutation)
    assert all(button["disabled"] for button in result["buttons"])
    assert result["decisions"] == []


@pytest.mark.parametrize("status", ["recovered", "blocked", "escalated"])
def test_terminal_memory_review_keeps_visible_disabled_decision_controls(status):
    result = render_case(status=status, mutation="row.manual_recovery_available=false;")
    assert result["forms"] == 1
    assert result["buttons"] == [{"disabled": True, "label": "Approve"}, {"disabled": True, "label": "Reject"}]
    assert result["decisions"] == []
    assert result["badge"]["tone"] == ("success" if status == "recovered" else "neutral")


def test_missing_evidence_keeps_approve_visible_without_blocking_explanation():
    result = render_case(mutation="row.review_change=null;")
    assert result["buttons"][0] == {"disabled": True, "label": "Approve"}
    assert result["notices"] == []


@pytest.mark.parametrize("status", ["pending", "expired", "blocked"])
def test_server_eligible_manual_recovery_does_not_require_oom_evidence(status):
    result = render_case(status=status, mutation="""
row.recovery_change={kind:'memory_limit',memory_before:'64Mi',memory_after:'256Mi',
 namespace:target.namespace,deployment:target.deployment,container:target.container,image};
row.review_change=null;delete row.proposal.cause;delete row.proposal.pod_uid;
if(row.status!=='pending')row.proposal.expires_at=new Date(now-1).toISOString();
""")
    assert result["buttons"][0] == {"disabled": False, "label": "Approve"}
    assert result["buttons"][1] == {"disabled": status != "pending", "label": "Reject"}
    assert [decision[1] for decision in result["decisions"]] == (["approve", "reject"] if status == "pending" else ["approve"])
    assert [item[1] for item in result["facts"] if item[0] == "Kubernetes failure"] == ["—"]
