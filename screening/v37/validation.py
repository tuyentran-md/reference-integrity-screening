"""Validation sample for frozen v37 (strict) census (all 120 shards). Blinded, review-held-out.

Strata by FINAL machine state: valid (SRS), error-from-v35, error-new-in-v36c, unresolved.
Sensitivity/specificity/NPV come from the valid stratum (misses) and the error strata
(false alarms) with population weights; each stratum is a simple random sample without
replacement. Never rate selected cases; refuses to overwrite an existing selection."""
import csv
import hashlib
import json
import random
from array import array
from collections import Counter
from prepare import HERE, ROOT, read, write
from exclusions import prior_reviews
from frozen import load
from registry import Registry
import packet
from packet import make_values, needed_pmids, write_workbook, validate_xlsx

_fields = packet.registry_fields


def registry_fields(row, R, S):
    """Packet fields plus volume and pages in the journal cell, for the investigator only."""
    out = _fields(row, R, S)
    extra = []
    for kind, value in [('doi', row.get('canonical_doi', '')), ('pmid', row.get('canonical_pmid', ''))]:
        recs = (R.doi(value) if kind == 'doi' else [R.pmid(value)]) if value else []
        for rec in recs:
            if rec and (rec.get('volume') or rec.get('pages')):
                extra.append(f"{kind.upper()} {value}: volume {rec.get('volume') or '-'}, pages {rec.get('pages') or '-'}")
    if extra:
        out[3] = (out[3] + '\n\n' if out[3] else '') + '\n'.join(extra)
    return out


packet.registry_fields = registry_fields

SEED = 20261029
CENSUS = HERE / 'v37/census'
DEST = HERE / 'v37/validation'
ALLOCATION = {'valid': 2000, 'error_v35': 40, 'error_new_v37': 80, 'unresolved': 40}
BATCH = 365


def stratum(row):
    if row['v36_state'] == 'valid':
        return 'valid'
    if row['v36_state'] == 'unresolved':
        return 'unresolved'
    return 'error_v35' if row['state'] == 'corrupted' else 'error_new_v37'


def main():
    if (DEST / 'validation_key.csv').exists():
        raise FileExistsError('Validation selection exists; never redraw')
    DEST.mkdir(parents=True, exist_ok=True)
    shards = sorted(p.name for p in CENSUS.iterdir() if p.name.startswith('r_'))
    assert len(shards) == 120
    excluded, _ = prior_reviews()
    inputs = [HERE / 'v37/rules.py', HERE / 'v37/census.py', HERE / 'v37/validation.py', HERE / 'packet.py',
              HERE / 'exclusions.py', HERE / 'registry.py', HERE / 'run.py']
    freeze = {'seed': SEED, 'allocation': ALLOCATION, 'excluded_reviews': len(excluded),
              'dev_inspected_rows': sum(1 for _ in read(HERE / 'dev_inspected_ids.csv')),
              'analysis_plan': {
                  'estimands': 'Se, Sp, PPV, NPV from the resolved states (valid + the two error strata), weighted by eligible-frame populations; also Se over all references with unresolved counted as missed. Primary target Se>=0.80 (owner 27/10: sensitivity first, over-flagging acceptable)',
                  'unresolved': 'reported separately as error prevalence among references the tool cannot assess; not in the sensitivity denominator (n=40 too small); a supplementary seeded unresolved stratum may be added later without redrawing',
                  'error_definition': 'owner 27/10: any wrong or missing element is an error (authors incl. initials, title, year, volume, pages, DOI, PMID); journal only when clearly a different journal, abbreviation differences never count; wrong_fields optional',
                  'cannot_check': 'excluded from the primary analysis; sensitivity analysis counts them all-Error and all-Correct',
                  'confidence_intervals': 'stratified with add-2 adjustment per stratum, or bootstrap; reported as approximate',
                  'frame': 'references in reviews outside the exclusion list (rows_in_excluded_reviews recorded in the audit)'},
              'sha256': {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
    (DEST / 'validation_freeze.json').write_text(json.dumps(freeze, indent=2) + '\n')
    # pass 1: positions of eligible rows per stratum (line index within each shard file)
    pos = {s: {} for s in ALLOCATION}
    total = Counter()
    for shard in shards:
        for s in ALLOCATION:
            pos[s][shard] = array('I')
        with open(CENSUS / shard / 's03_identity.csv', newline='', encoding='utf-8') as f:
            for i, row in enumerate(csv.DictReader(f)):
                total['all'] += 1
                if row['review_id'] in excluded:
                    total['excluded'] += 1
                    continue
                pos[stratum(row)][shard].append(i)
    populations = {s: sum(len(a) for a in pos[s].values()) for s in ALLOCATION}
    rng = random.Random(SEED)
    wanted = {shard: {} for shard in shards}
    for s in sorted(ALLOCATION):
        ordered = [(shard, i) for shard in shards for i in pos[s][shard]]
        for shard, i in rng.sample(ordered, ALLOCATION[s]):
            wanted[shard][i] = s
    # pass 2: fetch the selected rows
    selected = []
    for shard in shards:
        if not wanted[shard]:
            continue
        with open(CENSUS / shard / 's03_identity.csv', newline='', encoding='utf-8') as f:
            for i, row in enumerate(csv.DictReader(f)):
                if i in wanted[shard]:
                    row['shard'] = shard
                    row['_stratum'] = wanted[shard][i]
                    selected.append(row)
    assert len(selected) == sum(ALLOCATION.values())
    assert not ({r['review_id'] for r in selected} & excluded)
    rng.shuffle(selected)
    S = load()
    R = Registry(S)
    R.preload_pmids(needed_pmids(selected, S))
    values = make_values(selected, R, S, 'S37')
    truncated = []
    for i, v in enumerate(values, 1):
        v[0] = f'S37-{i:04d}'
        for j, cell in enumerate(v):
            if isinstance(cell, str) and len(cell) > 32000:
                truncated.append({'case_id': v[0], 'column': j, 'original_length': len(cell)})
                v[j] = cell[:32000] + f'\n[TRUNCATED for Excel: 32000 of {len(cell)} characters shown]'
    keys = []
    for row, value in zip(selected, values):
        s = row['_stratum']
        keys.append({'case_id': value[0], 'seed': SEED, 'shard': row['shard'], 'review_id': row['review_id'],
                     'ref_index': row['ref_index'], 'ref_rid': row['ref_rid'], 'sample_stratum': s,
                     'v35_state': row['state'], 'v36_state': row['v36_state'], 'rule_id': row['rule_id'],
                     'author_note': row['author_note'], 'evidence': row['evidence'],
                     'stratum_population': populations[s], 'stratum_sample': ALLOCATION[s],
                     'inclusion_probability': ALLOCATION[s] / populations[s]})
    write(DEST / 'validation_key.csv', keys)
    files = []
    for b in range(0, len(values), BATCH):
        path = DEST / f'validation_packet_{b // BATCH + 1}.xlsx'
        write_workbook(path, values[b:b + BATCH])
        validate_xlsx(path, values[b:b + BATCH])
        files.append(path.name)
    with open(HERE / 'dev_inspected_ids.csv', newline='', encoding='utf-8') as f:
        fields = next(csv.reader(f))
    with open(HERE / 'dev_inspected_ids.csv', 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        for k in keys:
            w.writerow({'review_id': k['review_id'], 'ref_index': k['ref_index'],
                        'why': 'validation sample (v37 census); never reuse'})
    audit = {'seed': SEED, 'populations_eligible': populations, 'allocation': ALLOCATION, 'rows_total': total['all'],
             'rows_in_excluded_reviews': total['excluded'], 'sampled_reviews': len({r['review_id'] for r in selected}),
             'packets': files, 'truncated_cells': truncated, 'live_network_calls': R.live_calls, 'rated': False}
    (DEST / 'validation_audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    R.close()
    print(json.dumps(audit, indent=2))
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
