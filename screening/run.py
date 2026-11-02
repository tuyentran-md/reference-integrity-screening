"""Run S03-v36 on one saved v35 shard; intentionally no census/batch runner."""
import argparse
import json
import time
from collections import Counter
from frozen import HERE, ROOT, load
from prepare import read, write
from byline import load_bylines
from registry import Registry
from rules import apply, bare_tail


def apply_rows(s02, old, rule_apply=apply):
    start = time.monotonic()
    S = load()
    registry = Registry(S)
    inputs = {(r['review_id'], r['ref_rid']): r for r in s02}
    assert len(inputs) == len(s02) == len(old)
    wanted = set()
    for row in s02:
        e = S.entry(row)
        wanted.update(e['pmids'] + e['hint_pmids'])
        bare = bare_tail(row, S)
        if bare:
            wanted.add(bare)
    wanted.update(r['canonical_pmid'] for r in old if r['canonical_pmid'])
    registry.preload_pmids(wanted)
    bylines = load_bylines(s02)
    out = []
    for row in old:
        key = row['review_id'], row['ref_rid']
        out.append(rule_apply(row, inputs[key], registry, bylines.get(key, {}), S))
    metrics = {'rows': len(out), 'seconds': round(time.monotonic() - start, 3),
               'live_network_calls': registry.live_calls, 'pmid_urls_scanned': registry.pm_urls_scanned,
               'cached_pmids_requested': len(wanted), 'cached_pmids_found': len(set(registry.pmids) & wanted),
               'jats_bylines_found': len(bylines),
               'transitions': dict(Counter(r['state'] + '->' + r['v36_state'] for r in out)),
               'rule_combinations': dict(Counter(r['rule_id'] for r in out)),
               'rule_counts': dict(Counter(rule for r in out for rule in r['rule_id'].split(';'))),
               'author_notes': dict(Counter(r['author_note'] for r in out)),
               'bare_terminal_candidates': sum(bool(bare_tail(r, S)) for r in s02)}
    registry.close()
    return out, metrics


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--shard', required=True, help='A single r_NNN, never a list')
    ap.add_argument('--exclude-prior', action='store_true', help='Reference/review-held-out pilot only after investigator authorizes this alternative')
    args = ap.parse_args()
    import re
    if not re.fullmatch(r'r_\d{3}', args.shard):
        ap.error('--shard requires one r_NNN')
    path = ROOT / 'census_s03_v35_20261019/shards' / args.shard / 'outputs'
    s02 = [{**r, 'shard': args.shard} for r in read(path / 's02_references.csv')]
    old = [{**r, 'shard': args.shard} for r in read(path / 's03_identity.csv')]
    source_rows = len(old)
    if args.exclude_prior:
        from exclusions import prior_reviews
        excluded, _ = prior_reviews()
        s02 = [r for r in s02 if r['review_id'] not in excluded]
        old = [r for r in old if r['review_id'] not in excluded]
    out, metrics = apply_rows(s02, old)
    metrics.update(source_rows=source_rows, excluded_prior_rows=source_rows-len(old),
                   held_out_unit='review/reference' if args.exclude_prior else 'none')
    dest = HERE / 'results' / args.shard
    dest.mkdir(exist_ok=True)
    write(dest / 's03_identity.csv', out)
    (dest / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
    print(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    main()
