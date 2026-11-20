"""Patch only the inspected React proposal card and shared status renderer.

The existing image is the source of truth: all three input assets must match
their reviewed SHA-256 before any file is written.  The API client is checked
but never changed.  Usage: python scripts/patch_react_oom_review.py ASSETS_DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

try:
    from .patch_oom_demo_ui import TEMPLATE
except ImportError:  # Direct script invocation.
    from patch_oom_demo_ui import TEMPLATE


ORIGINAL_HASHES = {
    "LiveApp-DryBTgPw.js": "3a729f5e69dc6e8c7a5255c2478a807b36505b3311726ab0a45cc8ab127293fe",
    "App-CYliiKd_.js": "d133b2c813842dc0297d60452524804e948e5d6419cb19c17d7e94df1c3e9b4c",
    "client.js": "2e28236d528c31a6032761be4437432d7039938c580ec13e98b1b25f0e1ade57",
}
IMPORT = 'import {memoryReview as oomMemoryReview} from "./client.js";\n'
STATUS_RENDERER = 'function Sy({status:e}){const{isEn:t}=vt(),n=jQ[e];return h.jsx(be,{tone:n.tone,dot:!0,children:t?n.en:n.vi})}'
MEMORY_PHASES = {
    "dispatching": {"vi": "Đang gửi thực thi", "en": "Dispatching", "tone": "orange"},
    "verifying": {"vi": "Đang xác minh hồi phục", "en": "Verifying recovery", "tone": "medium"},
    "recovered": {"vi": "Đã xác minh hồi phục", "en": "Verified recovery", "tone": "success"},
    "blocked": {"vi": "Bị chặn", "en": "Blocked", "tone": "critical"},
    "escalated": {"vi": "Chuyển kỹ sư", "en": "Escalated", "tone": "critical"},
}


def proposal_component() -> str:
    """Bind the reviewed card to this bundle without shadowing React's p."""
    component = re.sub(r"\bp\.", "proposal.", TEMPLATE.replace("p=row.proposal", "proposal=row.proposal"))
    component = component.replace("DATE(proposal.expires_at)", "DATE(proposal.expires_at,isEn)")
    component = component.replace("row.status==='recovered'?'low'", "row.status==='recovered'?'success'")
    bindings = dict(FUNCTION="Ge", REACT="p", JSX="r", CARD="pe", BADGE="De",
                    FACTS="Ue", DATE="ke", CALLOUT="P", RECEIPT="je", BUTTON="re")
    return re.sub(r"\b(" + "|".join(bindings) + r")\b", lambda m: bindings[m[0]], component)


def _once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError("Reviewed React asset anchor is missing or ambiguous")
    return text.replace(old, new, 1)


def prepare(assets: Path) -> dict[str, bytes]:
    """Validate every original first, then construct both edits in memory."""
    originals = {}
    for name, expected in ORIGINAL_HASHES.items():
        raw = (assets / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"Unreviewed React asset SHA-256: {name}")
        originals[name] = raw.decode("utf-8")
    if "export function memoryReview(row) {" not in originals["client.js"]:
        raise ValueError("Reviewed memoryReview export is missing")

    live = originals["LiveApp-DryBTgPw.js"]
    start_marker, end_marker = "function Ge(", "function Ke("
    if live.count(start_marker) != 1 or live.count(end_marker) != 1 or "oomMemoryReview" in live:
        raise ValueError("Reviewed proposal component boundary is missing or ambiguous")
    start, end = live.index(start_marker), live.index(end_marker)
    if end <= start or "function " in live[start + len(start_marker):end]:
        raise ValueError("Reviewed proposal component boundary changed")
    live = IMPORT + live[:start] + proposal_component() + live[end:]

    app = originals["App-CYliiKd_.js"]
    phases = json.dumps(MEMORY_PHASES, ensure_ascii=False, separators=(",", ":"))[1:-1]
    app = _once(app, "jQ={expired:", "jQ={" + phases + ",expired:")
    fallback = 'Object.prototype.hasOwnProperty.call(jQ,e)?jQ[e]:{vi:"Chưa rõ",en:"Unknown",tone:"neutral"}'
    app = _once(app, STATUS_RENDERER, STATUS_RENDERER.replace("jQ[e]", fallback, 1))
    return {"LiveApp-DryBTgPw.js": live.encode("utf-8"), "App-CYliiKd_.js": app.encode("utf-8")}


def apply(assets: str | Path) -> dict[str, str]:
    assets = Path(assets)
    prepared = prepare(assets)
    for name, raw in prepared.items():
        (assets / name).write_bytes(raw)
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in prepared.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", type=Path)
    args = parser.parse_args()
    print(json.dumps(apply(args.assets), sort_keys=True))
