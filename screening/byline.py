"""Read author fields from source JATS, without changing citation text or S02 files."""
from pathlib import Path
from lxml import etree
from frozen import ROOT


def extract(ref):
    """Only explicit author names, never editors or names inferred from the title."""
    groups = ref.xpath('.//person-group[@person-group-type="author"]')
    nodes = [n for g in groups for n in g.xpath('./name|./string-name')]
    if not groups:
        nodes = ref.xpath('.//mixed-citation/name|.//mixed-citation/string-name|'
                          './/element-citation/name|.//element-citation/string-name')
    names = []
    for n in nodes:
        family = ''.join(n.xpath('./surname//text()')).strip()
        given = ''.join(n.xpath('./given-names//text()')).strip()
        if family:
            names.append({'family': family, 'given': given})
    return {'names': names, 'group': bool(ref.xpath('.//collab')),
            'volume': ''.join(ref.xpath('.//volume/text()')).strip(),
            'first_page': ''.join(ref.xpath('.//fpage/text()')).strip()}


def load_bylines(rows):
    """Machine parsing is not individual development inspection."""
    reviews = {}
    for row in rows:
        reviews.setdefault(row['review_id'], []).append(row)
    out = {}
    for review, group in reviews.items():
        shard = group[0].get('shard')
        base = ROOT / 'census_s03_v35_20261019/shards' / shard / 'jats' if shard else ROOT / 'census_v25_20261018/jats'
        path = base / (review + '.xml')
        if not path.exists():
            continue
        tree = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True))
        wanted = {r['ref_rid'] for r in group}
        for ref in tree.xpath('//ref-list/ref'):
            if ref.get('id', '') in wanted:
                out[review, ref.get('id')] = extract(ref)
    return out
