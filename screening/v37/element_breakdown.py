"""Design-based prevalence of reference errors by element among resolved references (eligible frame),
from the investigator-locked ratings. Element of a confirmed error: for detected errors, the check(s) that
raised the alert (v37 rule_id; for V35_RETAIN the v35 wrong fields in the evidence); for missed errors in the
machine-valid stratum, the categories in misses_classified.csv. A reference can count under several elements.
Weighted by stratum populations; stratified bootstrap 95% CI (2,000 draws, seed 20261029). Cannot check excluded."""
import csv, json, random, re
from collections import Counter
from prepare import HERE

D = HERE / 'v37/validation'
KEY = {r['case_id']: r for r in csv.DictReader(open(D / 'validation_key.csv'))}
RATED = {r['case_id']: r['verdict'] for r in csv.DictReader(open(D / 'validation_ratings_investigator.csv'))}
MISS = {r['case_id']: [c.strip() for c in r['categories'].split(';')] for r in csv.DictReader(open(D / 'misses_classified.csv'))}
POP = json.load(open(D / 'validation_audit.json'))['populations_eligible']
RESOLVED = ('valid', 'error_v35', 'error_new_v37')
ELEMENTS = ('Authors', 'Title', 'Year', 'Volume', 'Pages', 'Journal', 'DOI or PMID', 'Identifier of another work', 'Unclassified')
RULE = {'STRICT_AUTHOR': 'Authors', 'AUTHOR_STRICT': 'Authors', 'TITLE_MISMATCH': 'Title', 'YEAR_MISMATCH': 'Year',
        'VOLUME_MISMATCH': 'Volume', 'PAGE_MISMATCH': 'Pages', 'JOURNAL_MISMATCH': 'Journal',
        'DOI_TERMINAL_CHARACTER': 'DOI or PMID'}
V35 = {'author': 'Authors', 'title': 'Title', 'year': 'Year', 'doi': 'DOI or PMID', 'pmid': 'DOI or PMID',
       'id_points_to_other_work': 'Identifier of another work'}
MISSCAT = {'middle initial omitted vs fullest record': 'Authors', 'authors missing': 'Authors', 'author name garbled': 'Authors',
           'title': 'Title', 'year': 'Year', 'volume': 'Volume', 'last page': 'Pages', 'page/article no.': 'Pages',
           'journal wrong': 'Journal', 'DOI malformed': 'DOI or PMID', 'unclear': 'Unclassified'}
AUTHOR_SUB = {'middle initial omitted vs fullest record': 'initials', 'authors missing': 'missing', 'author name garbled': 'garbled'}


def elements(k):
    r = KEY[k]
    if r['sample_stratum'] == 'valid':
        return {MISSCAT[c] for c in MISS[k]}
    out = {RULE[x] for x in r['rule_id'].split(';') if x in RULE}
    m = re.search(r'v35 \w+; wrong fields: ([^;]*);', r['evidence'])
    if m:
        out |= {V35[f] for f in m.group(1).split(',') if f in V35}
    if 'Identifier of another work' in out:
        out.add('DOI or PMID')
    return out or {'Unclassified'}


def prev(S, e):
    N = sum(POP[s] for s in RESOLVED)
    return sum(POP[s] * sum(e in x for x in S[s]) / len(S[s]) for s in RESOLVED) / N


def main(boot=2000, seed=20261029):
    S = {s: [elements(k) if RATED[k] == 'Error' else set() for k in RATED
             if KEY[k]['sample_stratum'] == s and RATED[k] in ('Error', 'Correct')] for s in RESOLVED}
    rng = random.Random(seed)
    draws = [{s: [rng.choice(x) for _ in x] for s, x in S.items()} for _ in range(boot)]
    out = {}
    for e in ELEMENTS:
        d = sorted(prev(b, e) for b in draws)
        out[e] = {'prevalence': [round(prev(S, e), 4), round(d[int(.025 * boot)], 4), round(d[int(.975 * boot)], 4)],
                  'n_by_stratum': {s: [sum(e in x for x in S[s]), len(S[s])] for s in RESOLVED}}
    out['author_subtypes_in_missed'] = dict(Counter(AUTHOR_SUB[c] for k in MISS for c in MISS[k] if c in AUTHOR_SUB))
    (D / 'validation_elements.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
