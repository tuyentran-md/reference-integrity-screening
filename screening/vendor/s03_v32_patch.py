"""E4 S03 candidate v32. Scoped rule-class fixes on top of v31; no census writes.
Regression/development cases are NOT a valid QC gate.
"""
import re
from urllib.parse import unquote

INSTITUTION = re.compile(
    r"\b(?:American|Global|International|European|National|World|Royal|United Nations)\s+"
    r"(?:Congress|Alliance|Association|College|Society|Organization|Organisation|Academy|Institute|Committee|Council)\b", re.I
)
DATA_RESOURCE = re.compile(r"\b(?:data and code for|dataset (?:and|&|for)|data repository|software and code for)\b", re.I)
SOFTWARE_DOI = re.compile(r"^10\.32614/cran\.package\.[a-z][\w.\-]*$", re.I)

def install(S):
    import s03_v31_patch
    s03_v31_patch.install(S)
    base_entry, base_decide = S.entry, S.decide
    base_corporate, base_anchors, base_accept = S.corporate, S.anchors_source, S.accept_hit

    def entry(r):
        e = base_entry(r)
        # DOI percent-escapes encode valid canonical DOI characters, not a misspelling.
        e['dois'] = list(dict.fromkeys(unquote(d).lower() for d in e['dois']))
        e['hint_dois'] = list(dict.fromkeys(unquote(d).lower() for d in e['hint_dois']))
        # A repository deposit with its own citation is not the journal article whose data it holds.
        if (DATA_RESOURCE.search(e['raw']) and
                any(d.startswith('10.17605/osf.io/') for d in e['dois'])):
            e['kind'] = 'nonjournal'
            e['_resource'] = 'osf_dataset_code'
        return e

    def corporate(segment):
        return base_corporate(segment) or bool(INSTITUTION.search(segment[:160]))

    def anchors_source(x, e):
        if x.get('_prefer_exact_hint'):
            return False
        return base_anchors(x, e)

    def decide(ids, e, search):
        # A printed DOI and an independent PMC hint can resolve to distinct publications
        # by the SAME authors/year. The exact substantive title match must outrank the
        # weaker title overlap; a wrong journal/volume does not turn that DOI into the source.
        hints = [h for h in ids if not h['printed'] and h['status'] == 'record'
                 and h.get('cmp') and h['cmp'].get('title_cmp')
                 and (h['cmp'].get('ts') or 0) >= .98 and h['cmp'].get('au') is True
                 and h['cmp'].get('yok') is True]
        for x in ids:
            if not (x['printed'] and x['type']=='doi' and x['status']=='record' and x.get('cmp')):
                continue
            c = x['cmp']
            if (c['title_cmp'] and 0.5 < (c.get('ts') or 0) < .9
                    and c.get('au') is True and c.get('yok') is True
                    and len(S.RI.content_words(e.get('title') or '')) >= 6
                    and any((h['rec'].get('doi') or '').lower() !=
                            (x['rec'].get('doi') or '').lower() for h in hints)):
                x['_prefer_exact_hint'] = True
        d = base_decide(ids, e, search)
        src = d.get('source')
        if src is None:
            return d
        cmp = d['cmp'] or {}
        own = [x for x in ids if x['printed']]
        # Two registries may describe the same work but disagree on the deposited
        # year. Match DOI identity by title and first author; NEVER suppress a wrong
        # year in the citation, another-work DOI, or different Cochrane versions.
        if d.get('doi_wrong') == 1 and cmp.get('yok') is True:
            doi_candidates = [x for x in own if x['type']=='doi']
            if (doi_candidates and all(
                x['status']=='record' and x.get('cmp') and
                x['cmp'].get('title_cmp') and (x['cmp'].get('ts') or 0) >= .98 and
                x['cmp'].get('au') is True and
                (cmp.get('ts') or 0)>=.98 and
                S.key(x['rec'].get('first_author') or '') == S.key(src.get('first_author') or '') and
                not S.COCHRANE.match(x['id']) for x in doi_candidates)):
                d['doi_wrong']=0
                d['id_note']=(d.get('id_note','')+';same_work_registry_year_difference').strip(';')
        # Identifiers of versioned software releases refer to one software package.
        if (d.get('year_wrong') == 1 and SOFTWARE_DOI.match((src.get('doi') or ''))
                and (cmp.get('ts') or 0) >= .95 and cmp.get('au') is True
                and any(x['type']=='doi' and x['id'].lower()==src['doi'].lower() for x in own)):
            d['year_wrong']=''
            d['id_note']=(d.get('id_note','')+';versioned_software_year_not_compared').strip(';')
        # Preprint -> journal: version year not comparable when identical research title,
        # same authors and the printed citation explicitly names the journal venue.
        if (d.get('year_wrong') == 1 and
                (src.get('doi') or '').lower().startswith(S.PREPRINT_DOI) and
                (cmp.get('ts') or 0) >= .95 and cmp.get('au') is True and
                re.search(r'(?:\bJ\.\s|\bRes\.\s|\bJournal\b|\bBMJ\b|\bLancet\b|\bNature\b|\bScience\b)',e['raw'],re.I)):
            d['year_wrong']=''
            d['id_note']=(d.get('id_note','')+';preprint_journal_version_year').strip(';')
        # An OSF dataset/code citation MUST NOT be judged by the associated article's
        # Crossref record. Without independent identity of the repository item, preserve
        # uncertainty rather than manufacture an erroneous DOI or an approved valid one.
        if (e.get('_resource')=='osf_dataset_code'
                and d.get('doi_wrong') == 1 and
                not any(x['type']=='doi' and x['status']=='record' and
                        x.get('cmp') and (x['cmp'].get('ts') or 0)>=.95 and
                        x['cmp'].get('au') is True for x in own)):
            d.update(state='unresolved',source=None,cmp=None,
                     unresolved_reason='repository_item_identity_unverified',
                     source_via='',doi_wrong='',id_points_to_other_work='')
            d['id_note']=(d.get('id_note','')+';osf_resource_not_journal_article').strip(';')
            return d
        # A DOI that is missing from registries is not automatically fraudulent.
        # If an independent PMID/hint identifies the SAME journal work, but an
        # alternative DOI is unregistered and has not been verified at the original
        # publisher, classification is genuinely uncertain. This is NOT a valid
        # verdict and is not credited toward either precision gate.
        alias = [x for x in own if x['type']=='doi' and x['status']=='not_registered']
        if (d.get('doi_wrong') == 1 and len(alias)==1 and len([x for x in own if x['type']=='doi'])==1
                and d.get('source_via','').startswith(('jats_hint_pmid','pmid'))
                and cmp.get('title_cmp') and (cmp.get('ts') or 0)>=.98
                and cmp.get('au') is True and cmp.get('yok') is True
                and S.journal_agrees(src,e)
                and not any(d.get(k)==1 for k in S.FLAGS if k!='doi_wrong')):
            d.update(state='unresolved',source=None,cmp=None,
                     unresolved_reason='unregistered_doi_alias_requires_publisher',
                     source_via='',doi_wrong='')
            d['id_note']=(d.get('id_note','')+';unregistered_doi_alias_unverified').strip(';')
            return d
        d['state'] = 'corrupted' if any(d.get(k)==1 for k in S.FLAGS) else 'valid'
        return d

    S.entry=entry
    S.corporate=corporate
    S.anchors_source=anchors_source
    S.decide=decide
