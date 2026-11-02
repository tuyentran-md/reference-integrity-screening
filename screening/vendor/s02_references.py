"""Step 02 — References.

What:   extract every reference of every cohort review, exactly as printed.
Input:  outputs/s01_cohort.csv + batch_20261001/jats/<review_id>.xml
Rule:   one row per <ref> in the JATS reference list. Parsing (author, year, title, journal vs non-journal) reuses
        code/ref_identity.parse_ref, so those fields are identical to the 01/10 batch.
        `ref_rid` is the JATS id that in-text and table citations point to (needed in step 04).
Output: outputs/s02_references.csv

Identifier provenance (implementation note 05/10/2026; investigator 05/10: an identifier the article itself supplies and
that points to another work is an error). A JATS <ref> carries identifiers of three kinds:
  printed    part of the citation text a reader sees: preceded by a label ("doi:", "PMID:", "https://doi.org/"),
             the visible text of a DOI/PMID link, or written as free text.
  supplied   in the article's own reference data without a printed label: an unlabelled <pub-id> inside the
             <mixed-citation>, or a <pub-id> of a structured <element-citation>.
  jats hint  added by someone else: PMC (assigning-authority="pmc"), an empty link behind a "PubMed"/"CrossRef"
             button (Wiley's citation matcher), or the identifier of a linked notice ("Erratum in: ...").
Printed and supplied identifiers are the citation's own and can make it wrong in step 03; hints only help find the
source. `citation_printed` is the citation text without supplied/hint identifiers; step 03 compares against it.
"""
import json, re

from lxml import etree

import config
from common import read_csv, write_csv, StepLog
import ref_identity as RI

FIELDS = ['review_id', 'ref_index', 'ref_rid', 'ref_label', 'kind', 'cited_first_author', 'cited_years', 'cited_title',
          'title_source', 'printed_dois', 'printed_pmids', 'supplied_dois', 'supplied_pmids', 'jats_hint_dois',
          'jats_hint_pmids', 'id_provenance',
          'citation_printed', 'citation_raw']

XLINK = '{http://www.w3.org/1999/xlink}href'
LABEL = re.compile(r'(?:\bdoi|\bpmid|\bpubmed(?:\s*id)?|doi\.org/?)\s*[:.]?\s*$', re.I)
HIDDEN_LINKS = ('google-scholar', 'pmcid', 'pmc')
DOI_URL = re.compile(r'doi\.org/(10\.\S+)', re.I)
PUBMED_URL = re.compile(r'(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d{1,9})', re.I)
PRINTED_HOW = ('labelled', 'visible_link', 'free_text')
SUPPLIED_HOW = ('unlabelled', 'element_citation')
# identifiers of a linked notice printed after the citation ("Erratum in: ... doi"), not of the cited work
NOTICE = re.compile(r'(?:erratum in|erratum:|corrigendum|correction in|retraction in|retracted in|expression of concern|'
                    r'comment in|comment on)\b.{0,80}$', re.I)


def _name(el):
    return etree.QName(el).localname


def _id_of(el):
    """-> ('doi'|'pmid', value) for an identifier element, None for anything else."""
    text = ' '.join(''.join(el.itertext()).split())
    if _name(el) == 'pub-id':
        t = {'medline': 'pmid'}.get((el.get('pub-id-type') or '').lower(), (el.get('pub-id-type') or '').lower())
        return (t, text) if t in ('doi', 'pmid') else None
    if _name(el) == 'ext-link':
        t = (el.get('ext-link-type') or '').lower()
        href = el.get(XLINK) or ''
        if t in ('doi', 'pmid'):
            return t, text or href
        if t in ('uri', '') and not text:          # empty URL link: the identifier is only in the href
            m = DOI_URL.search(href) or PUBMED_URL.search(href)
            if m:
                return ('doi' if 'doi.org' in href.lower() else 'pmid'), m.group(1)
    return None


def _hidden(el):
    """Elements a reader never sees as part of the citation text (PMC enrichment, link buttons)."""
    if (el.get('assigning-authority') or '').lower() == 'pmc':
        return True
    if _name(el) == 'pub-id':          # other pub-ids (pmcid, pii, publisher-id) are XML metadata, not printed text
        return True
    return _name(el) == 'ext-link' and (el.get('ext-link-type') or '').lower() in HIDDEN_LINKS


HYPHENS = str.maketrans({c: '-' for c in '\u2010\u2011\u2012\u2013\u2212'})


def clean_doi(v):
    """Typographic hyphens (U+2010 etc.) set by the typesetter are the same DOI character, not a different DOI.
    A closing parenthesis that balances one inside the DOI belongs to it ("10.34021/ve.2024.07.02(1)"): the legacy
    cleaner strips every trailing ")" before checking balance, so it is put back here (S03 field QC 09/10/2026)."""
    v = (v or '').translate(HYPHENS)
    d = RI.clean_doi(v)
    while d and d.count('(') > d.count(')') and (d + ')') in v.lower():
        d += ')'
    return d


def _clean(kind, v):
    if kind == 'doi':
        return clean_doi(v)
    v = (v or '').strip()
    return v if re.fullmatch(r'\d{1,9}', v) else ''


def provenance(ref):
    """-> (printed_text, [(kind, value, how)]) for one <ref>.
    Walks the XML in reading order; an identifier element is printed or a hint depending on its markup and on the
    printed text just before it."""
    parts, ids = [], []

    def walk(node, in_ec):
        if node.text:
            parts.append(node.text)
        for ch in node:
            if not isinstance(ch.tag, str):            # comments / processing instructions
                if ch.tail:
                    parts.append(ch.tail)
                continue
            ec = in_ec or _name(ch) == 'element-citation'
            got = _id_of(ch)
            if got:
                kind, raw = got
                text = ' '.join(''.join(ch.itertext()).split())
                if (ch.get('assigning-authority') or '').lower() == 'pmc':
                    how = 'pmc_enriched'
                elif ec:
                    how = 'element_citation'
                elif not text:
                    how = 'link_only'
                elif NOTICE.search(' '.join(' '.join(parts).split())[-100:]):
                    how = 'related_notice'
                elif LABEL.search(' '.join(' '.join(parts).split())[-30:]):
                    how = 'labelled'
                elif _name(ch) == 'ext-link':
                    how = 'visible_link'
                else:
                    how = 'unlabelled'
                if how in PRINTED_HOW:
                    parts.append(text)
                value = _clean(kind, raw)
                if value:
                    ids.append((kind, value, how))
            elif not _hidden(ch):
                walk(ch, ec)
            if ch.tail:
                parts.append(ch.tail)

    walk(ref, False)
    printed_text = ' '.join(' '.join(parts).split())
    # identifiers written as plain text (no markup), including doi.org / PubMed URLs
    for kind, rx in (('doi', RI.DOI_RE), ('pmid', RI.PMID_RE), ('pmid', PUBMED_URL)):
        for m in rx.finditer(printed_text):
            v = clean_doi(m.group(0)) if kind == 'doi' else m.group(1)
            if v:
                ids.append((kind, v, 'related_notice' if NOTICE.search(printed_text[max(0, m.start() - 100):m.start()])
                            else 'free_text'))
    # a marked-up identifier whose value is also written in the citation text is printed (the free-text match can be
    # cut short, e.g. at the "<" of an old SICI DOI)
    low = printed_text.lower()
    for kind, v, how in list(ids):
        if how not in PRINTED_HOW and how != 'related_notice' and len(v) >= 6 and re.search(
                r'(?<![\w.])' + re.escape(v.lower()) + r'(?![\w])', low):
            ids.append((kind, v, 'free_text'))
    return printed_text, ids


def split_ids(ids):
    """Printed wins: a value printed anywhere in the citation is printed, even if PMC also added it.
    A free-text DOI that only extends a marked-up DOI (text glued onto it, e.g. a PMID) is that marked-up DOI."""
    marked = {v.lower() for k, v, how in ids if k == 'doi' and how != 'free_text'}
    out = {'doi': {}, 'pmid': {}}
    for k, v, how in ids:
        # free text glued onto a marked-up DOI, or cut short at a "<" (SICI): the marked-up DOI stands for it
        if k == 'doi' and how == 'free_text' and any(v.lower() != m and (v.lower().startswith(m) or (
                m.startswith(v.lower()) and m[len(v)] == '<')) for m in marked):
            continue
        key = v.lower() if k == 'doi' else v
        prev = out[k].get(key)
        if prev is None or (how in PRINTED_HOW and prev[1] not in PRINTED_HOW):
            out[k][key] = (v, how)
    res = {}
    for k in ('doi', 'pmid'):
        res[f'printed_{k}s'] = [v for v, how in out[k].values() if how in PRINTED_HOW]
        res[f'supplied_{k}s'] = [v for v, how in out[k].values() if how in SUPPLIED_HOW]
        res[f'jats_hint_{k}s'] = [v for v, how in out[k].values() if how not in PRINTED_HOW + SUPPLIED_HOW]
    res['id_provenance'] = ' | '.join(f'{k}:{v}={how}' for k in ('doi', 'pmid') for v, how in out[k].values())
    return res


def js(xs):
    # JSON avoids corrupting old SICI/Wiley DOIs that legitimately contain semicolons.
    return json.dumps(xs, ensure_ascii=False) if xs else ''


def main():
    log = StepLog('s02', __file__)
    cohort = read_csv(config.OUT / 's01_cohort.csv')
    rows, no_refs = [], []
    for c in cohort:
        if c['jats_missing'] == '1':
            continue
        root = etree.parse(str(config.BATCH / 'jats' / f"{c['review_id']}.xml"),
                           etree.XMLParser(recover=True, huge_tree=True)).getroot()
        refs = root.xpath('.//back//ref-list//ref') or root.xpath('.//ref-list//ref')
        if not refs:
            no_refs.append(c['review_id'])
        for i, ref in enumerate(refs, 1):
            e = RI.parse_ref(ref, i)
            printed_text, ids = provenance(ref)
            p = split_ids(ids)
            rows.append({'review_id': c['review_id'], 'ref_index': i, 'ref_rid': ref.get('id') or f'_idx{i}',
                         'ref_label': e['label'], 'kind': e['kind'], 'cited_first_author': e['first_author'],
                         'cited_years': ';'.join(map(str, e['years'])), 'cited_title': e['title'],
                         'title_source': e['title_src'],
                         **{k: js(p[k]) for k in ('printed_dois', 'printed_pmids', 'supplied_dois', 'supplied_pmids',
                                                  'jats_hint_dois', 'jats_hint_pmids')},
                         'id_provenance': p['id_provenance'], 'citation_printed': printed_text,
                         'citation_raw': e['raw']})
    write_csv(config.OUT / 's02_references.csv', rows, FIELDS)
    how = {}
    for r in rows:
        for x in filter(None, r['id_provenance'].split(' | ')):
            k = x.split(':', 1)[0] + ':' + x.rsplit('=', 1)[1]
            how[k] = how.get(k, 0) + 1
    log.done(reviews_in=len(cohort), reviews_without_refs=no_refs, rows_out=len(rows),
             journal=sum(r['kind'] == 'journal' for r in rows), nonjournal=sum(r['kind'] == 'nonjournal' for r in rows),
             with_printed_doi=sum(bool(r['printed_dois']) for r in rows),
             with_printed_pmid=sum(bool(r['printed_pmids']) for r in rows),
             with_supplied_doi=sum(bool(r['supplied_dois']) for r in rows),
             with_supplied_pmid=sum(bool(r['supplied_pmids']) for r in rows),
             with_hint_doi_only=sum(bool(r['jats_hint_dois']) and not (r['printed_dois'] or r['supplied_dois'])
                                    for r in rows),
             with_hint_pmid_only=sum(bool(r['jats_hint_pmids']) and not (r['printed_pmids'] or r['supplied_pmids'])
                                     for r in rows),
             id_provenance=dict(sorted(how.items())))


if __name__ == '__main__':
    main()
