"""S02-v2.5 -> cache-only copied v35 -> v36 for a single local JATS file."""
import argparse
import json
from pathlib import Path
from lxml import etree
from frozen import HERE, load
from registry import Registry
from byline import extract
from prepare import write
from rules import apply, bare_tail


def parse_file(path, review_id, S):
    import s02_references as P
    import s02_v25
    s02_v25.install()
    if not getattr(P, '_v36_provenance', False):
        s02_v25.install_provenance(P)
        P._v36_provenance = True
    tree = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True))
    rows, bylines = [], {}
    for i, ref in enumerate(tree.xpath('//ref-list/ref'), 1):
        e = S.RI.parse_ref(ref, i)
        text, ids = P.provenance(ref)
        p = P.split_ids(ids)
        row = {'review_id': review_id, 'ref_index': str(i), 'ref_rid': ref.get('id') or f'_idx{i}',
               'ref_label': e['label'], 'kind': e['kind'], 'cited_first_author': e['first_author'],
               'cited_years': ';'.join(map(str, e['years'])), 'cited_title': e['title'],
               'title_source': e['title_src'], 'citation_printed': text, 'citation_raw': e['raw'],
               'id_provenance': p['id_provenance'],
               **{k: P.js(p[k]) for k in ('printed_dois', 'printed_pmids', 'supplied_dois',
                                        'supplied_pmids', 'jats_hint_dois', 'jats_hint_pmids')}}
        rows.append(row)
        bylines[row['review_id'], row['ref_rid']] = extract(ref)
    return rows, bylines


def run_file(path, review_id):
    S = load()
    registry = Registry(S)
    rows, bylines = parse_file(path, review_id, S)
    E = [S.entry(r) for r in rows]
    registry.preload_pmids([p for e in E for p in e['pmids'] + e['hint_pmids']] +
                           [bare_tail(r, S) for r in rows if bare_tail(r, S)])
    # All baseline search functions remain available, but can only read exact cached requests.
    # A missing PMID response must be unknown, never evidence that the PMID is invalid.
    outputs = []
    for row, e in zip(rows, E):
        crx = {rec['doi']: rec for d in e['dois'] + e['hint_dois'] for rec in registry.doi(d)}
        ids = S.lookup_ids(e, registry.pmids, crx)
        for item in ids:
            if item['type'] == 'pmid' and not item['rec']:
                item['status'] = 'unknown'
        decision = S.verify_original(S.decide(ids, e, lambda: S.search_all(e)), e)
        if decision['state'] == 'pending' or any(x['printed'] and x['status'] == 'unknown' for x in ids):
            decision.update(state='unresolved', unresolved_reason='offline_cache_miss')
        old = {**dict.fromkeys(S.FIELDS, ''), **row,
               **{k: decision[k] for k in S.FLAGS}, 'state': decision['state'],
               'unresolved_reason': decision['unresolved_reason'], 'source_via': decision['source_via'],
               'source_id': (decision['source'] or {}).get('id', ''),
               'source_journal': (decision['source'] or {}).get('journal', ''),
               **S.pairs(row, decision), 'id_checks': S.fmt_ids(ids), 'id_note': decision.get('id_note', '')}
        outputs.append(apply(old, row, registry, bylines[row['review_id'], row['ref_rid']], S))
    registry.close()
    return rows, outputs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('jats', type=Path)
    ap.add_argument('--review-id', required=True)
    ap.add_argument('--name', default='single_jats', help='Output directory name inside results')
    args = ap.parse_args()
    if not args.name.replace('_', '').replace('-', '').isalnum():
        ap.error('Invalid --name')
    rows, out = run_file(args.jats, args.review_id)
    dest = HERE / 'results' / args.name
    dest.mkdir(exist_ok=True)
    write(dest / 's02_references.csv', rows)
    write(dest / 's03_identity.csv', out)
    print(json.dumps({'references': len(rows), 'live_network_calls': 0, 'output': str(dest)}))


if __name__ == '__main__':
    main()
