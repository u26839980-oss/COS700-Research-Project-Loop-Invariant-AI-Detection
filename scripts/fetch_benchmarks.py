"""Retrieve pinned benchmark assets and verify their byte counts and SHA-256 hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = {
    'code2inv': ('https://github.com/PL-ML/code2inv',
                 'https://raw.githubusercontent.com/PL-ML/code2inv/{commit}/{path}'),
    'sv-benchmarks': ('https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks',
                      'https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks/-/raw/{commit}/{path}'),
}


def within(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if parts.is_absolute() or '..' in parts.parts or '\\' in relative or ':' in relative:
        raise ValueError('Invalid benchmark asset path: '+relative)
    path = (root/relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Asset escapes benchmark directory: '+relative)
    return path


def validate(raw: bytes, asset: dict) -> None:
    if len(raw) != asset['bytes'] or hashlib.sha256(raw).hexdigest() != asset['sha256']:
        raise ValueError('Benchmark content mismatch: '+asset['path'])


def fetch(manifest_path: Path, destination: Path, *, cache: Path | None = None, verify=False):
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    assets = {}
    for asset in manifest.get('provenance_assets', [])+[a for t in manifest['tasks'] for a in t['assets']]:
        if asset['repository'] not in UPSTREAM:
            raise ValueError('Unknown upstream repository.')
        pin = manifest['source_pins'][asset['repository']]
        if pin['repository'] != UPSTREAM[asset['repository']][0] or pin['commit'] != asset['commit']:
            raise ValueError('Source pin mismatch.')
        upstream = PurePosixPath(asset['upstream_path'])
        if upstream.is_absolute() or '..' in upstream.parts or '\\' in asset['upstream_path']:
            raise ValueError('Invalid upstream path.')
        if asset['path'] in assets and assets[asset['path']] != asset:
            raise ValueError('Conflicting asset records.')
        assets[asset['path']] = asset
    destination = destination.resolve()
    completed = 0
    for asset in assets.values():
        target = within(destination, asset['path'])
        if target.exists():
            validate(target.read_bytes(), asset)
        elif verify:
            raise FileNotFoundError('Benchmark asset missing: '+str(target))
        else:
            if cache is not None:
                raw = within(cache.resolve(), asset['path']).read_bytes()
            else:
                url = UPSTREAM[asset['repository']][1].format(
                    commit=quote(asset['commit'], safe=''), path=quote(asset['upstream_path'], safe='/'))
                with urlopen(Request(url, headers={'User-Agent':'LoOpS-benchmark-fetch/1'}), timeout=60) as response:
                    raw = response.read(asset['bytes']+1)
            validate(raw, asset)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(raw)
                if target.exists():
                    raise FileExistsError('Asset appeared during download: '+str(target))
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        completed += 1
    target_manifest = destination/'manifest.json'
    if target_manifest.exists():
        if target_manifest.read_bytes() != manifest_bytes:
            raise ValueError('Existing runtime manifest differs from the pinned manifest.')
    elif verify:
        raise FileNotFoundError('Runtime manifest missing: '+str(target_manifest))
    else:
        with target_manifest.open('xb') as handle:
            handle.write(manifest_bytes)
    return dict(tasks=len(manifest['tasks']), verified_assets=completed,
                manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(), model_calls=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT/'benchmarks/source_manifest.json')
    parser.add_argument('--output', type=Path, default=ROOT/'benchmarks/source_verified_v1')
    parser.add_argument('--cache', type=Path, help='Use an existing local benchmark directory; no network requests.')
    parser.add_argument('--verify', action='store_true', help='Verify existing files without downloading or writing.')
    args = parser.parse_args()
    print(json.dumps(fetch(args.manifest, args.output, cache=args.cache, verify=args.verify), indent=2))


if __name__ == '__main__':
    main()
