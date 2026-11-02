"""Accuracy of frozen v37 against the investigator-locked ratings (analysis plan in validation_freeze.json).
Weighted by eligible-frame stratum populations; stratified bootstrap 95% CI (2,000 draws)."""
import csv, json, random
from collections import defaultdict, Counter
from prepare import HERE

D = HERE / 'v37/validation'
KEY = {r['case_id']: r for r in csv.DictReader(open(D / 'validation_key.csv'))}
RATED = {r['case_id']: r['verdict'] for r in csv.DictReader(open(D / 'validation_ratings_investigator.csv'))}
POP = json.load(open(D / 'validation_audit.json'))['populations_eligible']
ERR = ('error_v35', 'error_new_v37')


def one(S):
    rate = {s: sum(x) / len(x) for s, x in S.items()}
    TP = sum(POP[s] * rate[s] for s in ERR); FP = sum(POP[s] * (1 - rate[s]) for s in ERR)
    FN = POP['valid'] * rate['valid']; TN = POP['valid'] * (1 - rate['valid'])
    FNu = POP['unresolved'] * rate['unresolved']
    return {'Se': TP / (TP + FN), 'Sp': TN / (TN + FP), 'PPV': TP / (TP + FP), 'NPV': TN / (TN + FN),
            'Se_all_unresolved_missed': TP / (TP + FN + FNu), 'error_prevalence_unresolved': rate['unresolved'],
            'PPV_new_v37': rate['error_new_v37'], 'PPV_v35': rate['error_v35'], 'miss_rate_valid': rate['valid']}


def estimate(verdicts, boot=2000, seed=20261029):
    S = defaultdict(list)
    for k, v in verdicts.items():
        if v in ('Error', 'Correct'):
            S[KEY[k]['sample_stratum']].append(v == 'Error')
    est = one(S)
    rng = random.Random(seed); draws = defaultdict(list)
    for _ in range(boot):
        for m, x in one({s: [rng.choice(x) for _ in x] for s, x in S.items()}).items():
            draws[m].append(x)
    ci = {m: (sorted(x)[int(.025 * boot)], sorted(x)[int(.975 * boot)]) for m, x in draws.items()}
    return est, ci, {s: [sum(x), len(x)] for s, x in S.items()}


def main():
    out = {}
    variants = {'primary (Cannot check excluded)': RATED,
                'Cannot check = Error': {k: ('Error' if v == 'Cannot check' else v) for k, v in RATED.items()},
                'Cannot check = Correct': {k: ('Correct' if v == 'Cannot check' else v) for k, v in RATED.items()}}
    for name, v in variants.items():
        est, ci, n = estimate(v)
        out[name] = {'n_error_of_rated': n, **{m: [round(est[m], 4), round(ci[m][0], 4), round(ci[m][1], 4)] for m in est}}
    # which v37 rules produced true and false alerts (alert strata only)
    rules = defaultdict(Counter)
    for k, v in RATED.items():
        if KEY[k]['sample_stratum'] in ERR and v in ('Error', 'Correct'):
            for r in KEY[k]['rule_id'].split(';'):
                if r: rules[r][v] += 1
    out['alerts_by_rule'] = {r: dict(c) for r, c in sorted(rules.items())}
    out['verdicts'] = dict(Counter(RATED.values()))
    (D / 'validation_metrics.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
