"""Extract ledger inputs by exact review_id/ref_rid; never modify the ledger."""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QC = ROOT / 'census_v25_20261018/qc'


def read(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        yield from csv.DictReader(stream)


def write(path, rows, fields=None):
    rows = list(rows)
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    ledger = list(read(QC / 'S03_v35_QC_FINAL_20261020.csv'))
    keys = {(r['review_id'], r['ref_rid']): r for r in ledger}
    locations = {}
    for name in ('S03_v35_census_machine_key_seed20261031.csv',
                 'S03_v35_census_machine_key_validext_seed20261032.csv'):
        for row in read(QC / name):
            locations[row['review_id'], row['ref_rid']] = row['shard']
    groups = defaultdict(set)
    for key in keys:
        groups[locations[key]].add(key)
    collected = {'s02_references': [], 's03_identity': []}
    for shard, wanted in sorted(groups.items()):
        for name in collected:
            path = ROOT / 'census_s03_v35_20261019/shards' / shard / 'outputs' / (name + '.csv')
            for row in read(path):
                key = row['review_id'], row['ref_rid']
                if key in wanted:
                    collected[name].append({**row, 'shard': shard, 'qc_id': keys[key]['qc_id']})
        print('extracted', shard, len(wanted), flush=True)
    for name, rows in collected.items():
        assert len(rows) == len(keys) == 760
        assert len({(r['review_id'], r['ref_rid']) for r in rows}) == 760
        write(HERE / 'data' / ('ledger_' + name + '.csv'), rows)
    existing = list(read(HERE / 'dev_inspected_ids.csv')) if (HERE / 'dev_inspected_ids.csv').exists() else []
    logged = [
        {'review_id': r['review_id'], 'ref_index': r['ref_index'],
         'why': '760-row locked ledger: development/regression input; conservatively exclude all'}
        for r in collected['s02_references']]
    combined = {(r['review_id'], r['ref_index']): r for r in logged}
    combined.update({(r['review_id'], r['ref_index']): r for r in existing})
    write(HERE / 'dev_inspected_ids.csv', combined.values())
    used = set(groups) | {f'r_{i:03d}' for i in range(11)}
    audit = {'prior_pilots': [f'r_{i:03d}' for i in range(11)],
             'ledger_shards': sorted(groups),
             'fully_untouched_shards': sorted({f'r_{i:03d}' for i in range(120)} - used),
             'ledger_rows': len(ledger), 'ledger_qc': dict(Counter(r['QC_final_verdict'] for r in ledger))}
    (HERE / 'data/shard_exposure.json').write_text(json.dumps(audit, indent=2) + '\n')
    print('READY', json.dumps(audit))


if __name__ == '__main__':
    main()
