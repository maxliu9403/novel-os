"""Run pinned, real H5 Go Parse against ZIP cases, offline and without a DB.

Supply the zipimport source directory via --source (or H5_ZIPIMPORT_SOURCE).
Cases: [{"package": "relative.zip", "accept": true}]. Relative ZIP paths are
resolved next to the case file. No source in the H5 checkout is modified.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'resources/h5-target/validator.lock.json'
DRIVER = '''package main
import ("context"; "encoding/json"; "os"; "fmt"; "noveloscontract/zipimport")
func main() {
 for _, path := range os.Args[1:] {
  raw, err := os.ReadFile(path)
  if err == nil { _, err = zipimport.Parse(context.Background(), raw) }
  result := map[string]any{"package": path, "accepted": err == nil}
  if err != nil { result["error"] = err.Error() }
  b, _ := json.Marshal(result); fmt.Println(string(b))
 }
}
'''


def verify(source: Path, cases_path: Path, *, go='go') -> list[dict]:
    lock = json.loads(LOCK.read_text())
    cases = json.loads(cases_path.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError('nonempty cases required')
    inputs = [str((cases_path.parent / c['package']).resolve()) for c in cases]
    hashes = [sha256(Path(path).read_bytes()).hexdigest() for path in inputs]
    actual_files = {p.name for p in source.glob('*.go') if not p.name.endswith('_test.go')}
    actual_files.update(p.name for p in source.glob('classification-catalog*.json'))
    if actual_files != set(lock['files']):
        raise ValueError('Target source inventory changed; audit and update the pin explicitly')
    for case in cases:
        if type(case.get('accept')) is not bool:
            raise ValueError('every case requires an explicit boolean accept')
    with tempfile.TemporaryDirectory(prefix='novelos-h5-contract-') as folder:
        root = Path(folder)
        target = root / 'zipimport'
        target.mkdir()
        for name, expected in lock['files'].items():
            raw = (source / name).read_bytes()
            if sha256(raw).hexdigest() != expected:
                raise ValueError(f'Target validator changed: {name}; audit and update the pin explicitly')
            (target / name).write_bytes(raw)
        (root / 'go.mod').write_text('module noveloscontract\n\ngo 1.24.0\n')
        (root / 'main.go').write_text(DRIVER)
        cache = Path(tempfile.gettempdir()) / 'novelos-h5-contract-go-cache'
        cache.mkdir(exist_ok=True)
        env = {**os.environ, 'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOTOOLCHAIN': 'local',
               'GOWORK': 'off', 'CGO_ENABLED': '0', 'GOCACHE': str(cache)}
        subprocess.run([go, 'build', '-o', str(root / 'verify'), '.'], cwd=root, env=env,
                       check=True, capture_output=True, text=True, timeout=120)
        executed = subprocess.run([str(root / 'verify'), *inputs], check=True, capture_output=True,
                                  text=True, timeout=120)
        results = [json.loads(line) for line in executed.stdout.splitlines()]
        if len(results) != len(cases):
            raise RuntimeError('Target verifier omitted a result')
        for case, result, expected_hash in zip(cases, results, hashes):
            if sha256(Path(result['package']).read_bytes()).hexdigest() != expected_hash:
                raise RuntimeError('Contract input changed during target verification')
            result['package_sha256'] = expected_hash
            result['expected_accept'] = case['accept']
            result['matched'] = case['accept'] == result['accepted']
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=os.environ.get('H5_ZIPIMPORT_SOURCE'))
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.source is None:
        parser.error('--source or H5_ZIPIMPORT_SOURCE is required')
    results = verify(Path(args.source), args.cases.resolve())
    output = json.dumps({'validator': json.loads(LOCK.read_text())['revision'], 'results': results},
                        ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(output)
    print(output, end='')
    raise SystemExit(0 if all(r['matched'] for r in results) else 1)


if __name__ == '__main__':
    main()
