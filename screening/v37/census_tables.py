"""Census-level tables for the v37 manuscript: states, reviews with >=1 error and wrong-paper identifiers by year.
Reads the frozen v37 census (state = v35, v36_state = v37 final) and census_20261013/cohort.csv for review year."""
import csv, json, sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENSUS = HERE / 'census'
COHORT = HERE.parents[1] / 'census_20261013/cohort.csv'
csv.field_size_limit(sys.maxsize)


def shard(path):
    refs = Counter(); rev_err = defaultdict(bool); rev_seen = set()
    rows = Counter()
    for r in csv.DictReader(open(path, newline='')):
        rid = r['review_id']; rev_seen.add(rid)
        st = r['v36_state']
        rows[(rid, st)] += 1
        pd = r['printed_dois'] not in ('', '[]'); pp = r['printed_pmids'] not in ('', '[]')
        if st != 'unresolved' and (pd or pp):
            rows[(rid, 'printed_verifiable')] += 1
            # the identifier that belongs to another work is itself printed (not markup-only)
            if r['id_points_to_other_work'] == '1' and ((r['doi_wrong'] == '1' and pd) or (r['pmid_wrong'] == '1' and pp)):
                rows[(rid, 'wrong_paper_printed')] += 1
        if r['id_points_to_other_work'] == '1':
            rows[(rid, 'wrong_paper_any')] += 1
    return rows


def main():
    year = {r['review_id']: r['year'] for r in csv.DictReader(open(COHORT))}
    with Pool(8) as p:
        parts = p.map(shard, sorted(CENSUS.glob('r_*/s03_identity.csv')))
    per_rev = defaultdict(Counter)
    for part in parts:
        for (rid, k), n in part.items():
            per_rev[rid][k] += n
    out = defaultdict(Counter)
    for rid, c in per_rev.items():
        y = year.get(rid, 'missing_year')
        for key in (y, 'total'):
            o = out[key]
            o['reviews'] += 1; o['reviews_with_error'] += c['corrupted'] > 0
            o['reviews_with_wrong_paper_printed'] += c['wrong_paper_printed'] > 0
            for k in ('valid', 'corrupted', 'unresolved', 'printed_verifiable', 'wrong_paper_printed', 'wrong_paper_any'):
                o[k] += c[k]
    res = {}
    for y, o in sorted(out.items()):
        refs = o['valid'] + o['corrupted'] + o['unresolved']
        res[y] = {**o, 'references': refs,
                  'pct_reviews_with_error': round(100 * o['reviews_with_error'] / o['reviews'], 1),
                  'wrong_paper_per_1000_printed': round(1000 * o['wrong_paper_printed'] / o['printed_verifiable'], 2) if o['printed_verifiable'] else None}
    res['cohort_records'] = len(year)
    (HERE / 'census_tables.json').write_text(json.dumps(res, indent=1) + '\n')
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
