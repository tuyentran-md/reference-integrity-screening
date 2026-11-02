"""Small shared helpers: CSV in/out and one JSON log per step (row counts, code hash, timing)."""
import csv, hashlib, json, socket, sys, time
from pathlib import Path

import config

csv.field_size_limit(10**9)

# IPv6 routes to Cloudflare-hosted APIs (OpenAlex, doi.org) hang at TCP connect on this network (seen 04/10/2026):
# resolve IPv4 only so a request fails fast or succeeds instead of waiting out every timeout.
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, family=0, *a, **k: _getaddrinfo(host, port, socket.AF_INET, *a, **k)
sys.path.insert(0, str(config.LEGACY_CODE))


def read_csv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


class StepLog:
    """Usage: log = StepLog('s03', __file__); ...; log.done(rows_in=.., rows_out=.., **counts)."""

    def __init__(self, step, script):
        self.step, self.script, self.t0 = step, script, time.time()

    def done(self, **info):
        config.LOGS.mkdir(parents=True, exist_ok=True)
        rec = {'step': self.step, 'script': Path(self.script).name, 'code_hash': file_hash(self.script),
               'config_hash': file_hash(Path(config.__file__)), 'finished': time.strftime('%Y-%m-%d %H:%M:%S'),
               'seconds': round(time.time() - self.t0, 1), **info}
        (config.LOGS / f'{self.step}.json').write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str))
        print(json.dumps(rec, indent=1, ensure_ascii=False, default=str))
