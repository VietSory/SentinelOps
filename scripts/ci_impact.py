"""Conservative CI dispatch. Unknown code paths request the full existing suite."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess


def classify(paths, *, force_full=False):
    ui = False
    core = force_full
    for path in paths:
        if path.startswith('docs/') or path in {'README.md','LICENSE','CHANGELOG.md'}:
            continue
        if (path.startswith(('src/sentinelops/ui/','tests/ui/')) or
            path in {'tests/test_operator_ui.py','scripts/check_operator_ui.py','package.json','package-lock.json'}):
            ui = True
        else:
            core = True
    return {'native':core,'public_data':core,'ui':core or ui}


def changed_paths(event_name, event):
    if event_name == 'workflow_dispatch': return [],True
    if event_name == 'pull_request':
        base, head = event['pull_request']['base']['sha'],event['pull_request']['head']['sha']
        separator = '...'
    elif event_name == 'push':
        base, head, separator = event['before'],event['after'],'..'
        if base == '0'*40: return [],True
    else:
        raise ValueError('unknown CI event must not silently skip validation')
    if not all(re.fullmatch('[0-9a-f]{40}',sha) for sha in (base,head)):
        raise ValueError('CI diff requires exact source identities')
    # A rename from a core module into ui/ must retain the deleted core path;
    # considering only the destination would incorrectly skip native checks.
    raw = subprocess.check_output(['git','diff','--no-renames','--name-only','-z',base+separator+head])
    return [path.decode('utf-8') for path in raw.split(b'\0') if path],False


def main():
    paths, force = changed_paths(os.environ['GITHUB_EVENT_NAME'],json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()))
    result = classify(paths,force_full=force)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
        for key, value in result.items(): stream.write(key+'='+str(value).lower()+'\n')
    print(json.dumps({'changed_paths':len(paths),'force_full':force,'jobs':result}))


if __name__ == '__main__': main()
