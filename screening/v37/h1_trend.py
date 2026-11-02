"""Registered H1/H2 from the frozen v37 census: by year of first publication, frame records (H2), reviews with
references, references, and errors per 1,000 resolved references under two definitions: core fields (DOI, PMID,
first author, year, title; the v35 state, as registered) and any element (v37 final state). Writes h1_trend.json."""
import csv, json
from collections import Counter
from multiprocessing import Pool

from census_tables import CENSUS, COHORT, HERE


def shard(path):
    c = Counter()
    for r in csv.DictReader(open(path, newline='')):
        rid = r['review_id']
        c[(rid, 'ref')] += 1
        c[(rid, 'core_' + r['state'])] += 1
        c[(rid, 'any_' + r['v36_state'])] += 1
    return c


def main():
    meta = {r['review_id']: r['year'] for r in csv.DictReader(open(COHORT))}
    with Pool(8) as p:
        parts = p.map(shard, sorted(CENSUS.glob('r_*/s03_identity.csv')))
    y = {}
    for rid, yr in meta.items():
        y.setdefault(yr, Counter())['records'] += 1
    seen = set()
    for part in parts:
        for (rid, k), n in part.items():
            o = y[meta[rid]]
            if rid not in seen:
                seen.add(rid); o['reviews'] += 1
            o[k] += n
    res = {}
    for yr in sorted(y):
        o = y[yr]
        core_res = o['core_valid'] + o['core_corrupted']; any_res = o['any_valid'] + o['any_corrupted']
        res[yr] = {'records': o['records'], 'reviews': o['reviews'], 'references': o['ref'],
                   'core_errors': o['core_corrupted'], 'core_resolved': core_res,
                   'core_per_1000': round(1000 * o['core_corrupted'] / core_res, 2),
                   'any_errors': o['any_corrupted'], 'any_resolved': any_res,
                   'any_per_1000': round(1000 * o['any_corrupted'] / any_res, 1)}
    (HERE / 'h1_trend.json').write_text(json.dumps(res, indent=1) + '\n')
    for yr, r in res.items():
        print(yr, *r.values())


if __name__ == '__main__':
    main()
