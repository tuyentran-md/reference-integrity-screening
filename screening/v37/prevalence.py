"""Design-based prevalence of reference errors among resolved references in the eligible frame,
from the investigator-locked ratings (independent of tool sensitivity). Weighted by stratum
populations; stratified bootstrap 95% CI (2,000 draws, seed 20261029). Cannot check excluded."""
import csv, json, random
from prepare import HERE

D = HERE / 'v37/validation'
KEY = {r['case_id']: r for r in csv.DictReader(open(D / 'validation_key.csv'))}
RATED = {r['case_id']: r['verdict'] for r in csv.DictReader(open(D / 'validation_ratings_investigator.csv'))}
POP = json.load(open(D / 'validation_audit.json'))['populations_eligible']
RESOLVED = ('valid', 'error_v35', 'error_new_v37')


def prev(S):
    return sum(POP[s] * sum(x) / len(x) for s, x in S.items()) / sum(POP[s] for s in S)


def main(boot=2000, seed=20261029):
    S = {s: [RATED[k] == 'Error' for k in RATED
             if KEY[k]['sample_stratum'] == s and RATED[k] in ('Error', 'Correct')] for s in RESOLVED}
    rng = random.Random(seed)
    d = sorted(prev({s: [rng.choice(x) for _ in x] for s, x in S.items()}) for _ in range(boot))
    out = {'frame': 'resolved references, eligible frame', 'N': sum(POP[s] for s in RESOLVED),
           'prevalence_any_error': [round(prev(S), 4), round(d[int(.025 * boot)], 4), round(d[int(.975 * boot)], 4)],
           'n_error_of_rated': {s: [sum(x), len(x)] for s, x in S.items()}}
    (D / 'validation_prevalence.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
