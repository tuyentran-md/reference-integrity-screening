"""Conservative QC exclusion inventory, read without examining individual citations."""
import csv
import json
from prepare import ROOT, HERE, read


def prior_reviews():
    excluded = set()
    sources = {}
    for folder in ('census_v25_20261018/qc', 'pipeline/qc', 'analysis_v24/qc',
                   'analysis_20261010/qc', 'holdout_20261005/qc', 'pilot/qc'):
        for path in sorted((ROOT / folder).glob('*.csv')):
            count = 0
            for row in read(path):
                review = row.get('review_id') or ''
                if review:
                    excluded.add(review)
                    count += 1
            if count:
                sources[str(path.relative_to(ROOT))] = count
    for folder in ('analysis_v24', 'analysis_20261010', 'holdout_20261005', 'pipeline', 'batch_20261001'):
        path = ROOT / folder / 'outputs/s01_cohort.csv'
        if path.exists():
            for row in read(path):
                if row.get('pmid'):
                    excluded.add('cs_' + row['pmid'])
    for row in read(HERE / 'dev_inspected_ids.csv'):
        excluded.add(row['review_id'])
    return excluded, sources


def audit():
    excluded, sources = prior_reviews()
    return {'excluded_reviews': len(excluded), 'sources': sources,
            'policy': 'Exclude complete reviews with known prior QC or development exposure; not proof of untouched shards.'}


if __name__ == '__main__':
    (HERE / 'data/exclusion_audit.json').write_text(json.dumps(audit(), indent=2) + '\n')
    print(json.dumps(audit(), indent=2))
