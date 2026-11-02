"""Full 120-shard census run of frozen v37 (strict) (cache only). Resumable: a shard with a
summary file is skipped. Shards run one at a time because registry.py writes a local
SQLite subset cache."""
import hashlib
import json
import time
from collections import Counter
from prepare import HERE, ROOT, read, write
from run import apply_rows
from v37.rules import apply

FROZEN = '031a3cd0b71327200995b3963fe32a794b96c9e20f75603cfcdce0f81dd4d6c8'
DEST = HERE / 'v37/census'


def main():
    sha = hashlib.sha256((HERE / 'v37/rules.py').read_bytes()).hexdigest()
    assert sha == FROZEN, 'v37/rules.py is not the frozen version'
    shards = sorted(p.name for p in (ROOT / 'census_s03_v35_20261019/shards').iterdir() if p.name.startswith('r_'))
    assert len(shards) == 120
    for shard in shards:
        dest = DEST / shard
        if (dest / 'summary.json').exists():
            continue
        t = time.monotonic()
        path = ROOT / 'census_s03_v35_20261019/shards' / shard / 'outputs'
        s02, old = [[{**r, 'shard': shard} for r in read(path / (n + '.csv'))] for n in ('s02_references', 's03_identity')]
        rows, metrics = apply_rows(s02, old, apply)
        dest.mkdir(parents=True, exist_ok=True)
        write(dest / 's03_identity.csv', rows)
        summary = {'shard': shard, 'rows': len(rows), 'rules_sha256': sha,
                   'v35_states': dict(Counter(r['state'] for r in rows)),
                   'v36c_states': dict(Counter(r['v36_state'] for r in rows)),
                   'transitions': metrics['transitions'], 'rule_counts': metrics['rule_counts'],
                   'author_notes': metrics['author_notes'], 'live_network_calls': metrics['live_network_calls'],
                   'seconds': round(time.monotonic() - t, 1)}
        (dest / 'summary.json').write_text(json.dumps(summary, indent=1))
        print(shard, summary['transitions'], summary['seconds'], flush=True)
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
