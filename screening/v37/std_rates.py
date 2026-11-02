"""Recompute the composition-standardised rates of printed identifiers that belong to another work (2024, 2025,
2026 Jan-Jun) from the frozen v37 census, using the v35 definition (supplementary_material.md, superseded v35):
direct standardisation to the 2024 denominator mix; strata need a non-zero denominator in all three periods.
Publisher groups by review DOI prefix; journals by exact journal string in cohort.csv."""
import csv, json
from collections import defaultdict
from multiprocessing import Pool

from census_tables import CENSUS, COHORT, HERE, shard

PREFIX = {'10.3389': 'Frontiers', '10.3390': 'MDPI', '10.1186': 'BMC', '10.1097': 'Wolters Kluwer', '10.1371': 'PLOS',
          '10.1136': 'BMJ', '10.1002': 'Wiley', '10.1111': 'Wiley', '10.7759': 'Cureus', '10.1007': 'Springer',
          '10.1016': 'Elsevier', '10.2196': 'JMIR', '10.1038': 'Nature', '10.1177': 'SAGE', '10.1093': 'Oxford',
          '10.12688': 'F1000', '10.1080': 'Taylor & Francis', '10.3892': 'Spandidos', '10.1155': 'Hindawi/Wiley'}
YEARS = ('2024', '2025', '2026')


def standardise(cells):
    """cells: {stratum: {year: [num, den]}} -> crude, standardised rates, n strata, coverage of denominators."""
    common = [g for g, c in cells.items() if all(c[y][1] > 0 for y in YEARS)]
    w = {g: cells[g]['2024'][1] for g in common}
    W = sum(w.values())
    out = {'strata': len(common)}
    for y in YEARS:
        out[y] = round(sum(w[g] / W * 1000 * cells[g][y][0] / cells[g][y][1] for g in common), 2)
        out['coverage_' + y] = round(100 * sum(cells[g][y][1] for g in common) / sum(c[y][1] for c in cells.values()), 2)
    return out


def main():
    meta = {r['review_id']: r for r in csv.DictReader(open(COHORT))}
    with Pool(8) as p:
        parts = p.map(shard, sorted(CENSUS.glob('r_*/s03_identity.csv')))
    pub = defaultdict(lambda: {y: [0, 0] for y in YEARS})
    jou = defaultdict(lambda: {y: [0, 0] for y in YEARS})
    crude = {y: [0, 0] for y in YEARS}
    for part in parts:
        for (rid, k), n in part.items():
            m = meta.get(rid)
            if not m or m['year'] not in YEARS or k not in ('wrong_paper_printed', 'printed_verifiable'):
                continue
            i = 0 if k == 'wrong_paper_printed' else 1
            y = m['year']
            g = PREFIX.get(m['doi'].split('/')[0], 'Other') if m['doi'] else 'Other'
            pub[g][y][i] += n; jou[m['journal']][y][i] += n; crude[y][i] += n
    res = {'crude': {y: round(1000 * crude[y][0] / crude[y][1], 2) for y in YEARS},
           'publisher_groups': standardise(pub), 'journals': standardise(jou)}
    (HERE / 'std_rates.json').write_text(json.dumps(res, indent=1) + '\n')
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
