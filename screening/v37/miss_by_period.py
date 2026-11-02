"""Post hoc check: share of investigator-rated errors among sampled references with no error detected, and
confirmed share of detected errors, by period of review publication (locked ratings). Writes validation/miss_by_period.json."""
import csv, json
from collections import Counter

from census_tables import COHORT, HERE

V = HERE / 'validation'
year = {r['review_id']: r['year'] for r in csv.DictReader(open(COHORT))}
key = {r['case_id']: r for r in csv.DictReader(open(V / 'validation_key.csv'))}
rat = {r['case_id']: r['verdict'] for r in csv.DictReader(open(V / 'validation_ratings_investigator.csv'))}
period = lambda y: '2016-2019' if y < '2020' else ('2020-2023' if y < '2024' else '2024-2026')
c = Counter((key[k]['sample_stratum'], period(year[key[k]['review_id']]), v) for k, v in rat.items())
out = {s: {p: [c[(s, p, 'Error')], c[(s, p, 'Error')] + c[(s, p, 'Correct')]] for p in ('2016-2019', '2020-2023', '2024-2026')}
       for s in ('valid', 'error_v35', 'error_new_v37')}
(V / 'miss_by_period.json').write_text(json.dumps(out, indent=1) + '\n')
print(json.dumps(out))
