"""ADR-016 adapter: Alibaba cluster-trace-microservices-v2022 MSRTMCR → per-service minutes.

Raw archives stay immutable: each is streamed from its tar member and bound to the
SHA-256 recorded at acquisition. Only HTTP fields are used. A container row
contributes to its microservice minute with weight `http_mcr` (normalised call
rate). Latency is the call-rate-weighted mean of `http_rt` (milliseconds). No
error/status field exists in this source, so none is invented.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile

SOURCE = 'alibaba/cluster-trace-microservices-v2022/MSRTMCR'
AGGREGATE_VERSION = 'alibaba-http-minute/v1'
COLUMNS = ['timestamp', 'msname', 'http_mcr', 'http_rt']


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def recorded_sha256(path):
    line = Path(str(path) + '.sha256').read_text().split()
    if len(line) != 2 or Path(line[1]).name != Path(path).name:
        raise ValueError('acquisition checksum record does not name this archive')
    return line[0]


def aggregate_archive(path, *, chunk_rows=2_000_000):
    """Return (frame, provenance) for one archive; raises if the archive changed."""
    import pandas as pd
    path = Path(path)
    expected = recorded_sha256(path)
    if file_sha256(path) != expected:
        raise ValueError('raw archive differs from its acquisition checksum')
    parts = []
    with tarfile.open(path, 'r:gz') as archive:
        members = [m for m in archive.getmembers() if m.isfile() and m.name.endswith('.csv')]
        if len(members) != 1:
            raise ValueError('expected exactly one CSV member')
        with archive.extractfile(members[0]) as stream:
            for chunk in pd.read_csv(stream, usecols=COLUMNS, chunksize=chunk_rows):
                chunk = chunk[(chunk.http_mcr > 0) & chunk.http_rt.notna() & (chunk.http_rt >= 0)]
                if chunk.empty:
                    continue
                chunk = chunk.assign(weighted_rt=chunk.http_rt * chunk.http_mcr, containers=1)
                parts.append(chunk.groupby(['timestamp', 'msname'], sort=False)[
                    ['http_mcr', 'weighted_rt', 'containers']].sum())
    if not parts:
        raise ValueError('archive has no HTTP traffic rows')
    frame = pd.concat(parts).groupby(level=[0, 1]).sum().reset_index()
    frame['http_rt_ms'] = frame.weighted_rt / frame.http_mcr
    frame = frame.drop(columns='weighted_rt').rename(columns={'http_mcr': 'http_mcr_sum'})
    provenance = {'aggregate_version': AGGREGATE_VERSION, 'source': SOURCE, 'archive': path.name,
                  'archive_sha256': expected, 'csv_member': members[0].name,
                  'minutes': sorted(int(t) for t in frame.timestamp.unique()),
                  'services': int(frame.msname.nunique()), 'rows': int(len(frame))}
    return frame, provenance


def aggregate_directory(raw, output):
    """Aggregate every checksummed archive once; outputs are exclusive."""
    raw, output = Path(raw), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    done = []
    for path in sorted(raw.glob('MCRRTUpdate_*.tar.gz'), key=lambda p: int(p.name.split('_')[1].split('.')[0])):
        if not Path(str(path) + '.sha256').exists():
            continue
        target = output / (path.name.removesuffix('.tar.gz') + '.parquet')
        meta = output / (path.name.removesuffix('.tar.gz') + '.json')
        if target.exists() and meta.exists():
            continue
        frame, provenance = aggregate_archive(path)
        frame.to_parquet(target, index=False)
        provenance['parquet_sha256'] = file_sha256(target)
        with meta.open('x') as stream:
            json.dump(provenance, stream, indent=2)
        done.append(path.name)
        print(json.dumps({'aggregated': path.name, 'services': provenance['services'], 'rows': provenance['rows']}), flush=True)
    return done
