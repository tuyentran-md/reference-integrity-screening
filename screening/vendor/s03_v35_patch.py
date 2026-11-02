"""E4 S03 v35 isolated source identity safeguards, building on v34.
Inspected r005 examples are regression data, NEVER independent QC.
"""
import re

def install(S):
    import s03_v34_patch
    s03_v34_patch.install(S)
    old_compare,old_decide=S.compare,S.decide

    def compare(rec,e):
        c=old_compare(rec,e)
        title=e.get('title') or ''
        raw=e.get('raw') or ''
        # Do not silently regard years as unreported just because S02's title
        # differs typographically from the printed source. In that case, retain
        # explicitly parsed S02 years for a conservative provisional comparison.
        if len(title)<12 or raw.lower().find(title.lower())<0:
            ys=sorted(set(int(y) for y in e.get('years',[]) if 1800<=int(y)<=2030))
            if ys and not c.get('years_cited'):
                src=rec.get('years') or ({int(rec['year'])} if str(rec.get('year','')).isdigit() else set())
                c['years_cited']=ys
                if src and not rec.get('book'):
                    c['yok']=any(abs(a-b)<=1 for a in src for b in ys)
                    c['ydiff']=min(abs(a-b) for a in src for b in ys)
        return c

    def decide(ids,e,search):
        d=old_decide(ids,e,search)
        src=d.get('source')
        if not src:return d
        c=d.get('cmp') or {}
        printed_dois=[x for x in ids if x['printed'] and x['type']=='doi']
        # A printed DOI can identify a different publication by the same authors,
        # in the same year, with a high-overlap but not identical title. Prefer
        # a separately resolved FULL-title match in the PRINTED journal.
        old_title=c.get('ts') or 0
        if (d['state']=='valid' and printed_dois and .62<=old_title<.91
                and c.get('au') is True and c.get('yok') is True
                and not S.journal_agrees(src,e)):
            via, target=S.search_crossref_pubmed(e)
            if target and (target.get('doi') or '').lower() != (src.get('doi') or '').lower():
                cc=S.compare(target,e)
                if (cc.get('title_cmp') and (cc.get('ts') or 0)>=.98
                        and cc.get('au') is True and cc.get('yok') is True
                        and S.journal_agrees(target,e)):
                    d.update(source=target,cmp=cc,source_via=via+'+printed_venue',
                             doi_wrong=1,id_points_to_other_work=1)
                    d['id_note']=(d.get('id_note','')+';doi_other_publication_same_topic').strip(';')
                    d['state']='corrupted'
                    return d
        # Older primary article cited with a DOI that resolves to a much later
        # republished version. Metadata alone cannot adjudicate the DOI version:
        # don't mistake source-deposit year for a wrong cited publication year.
        if (d.get('year_wrong')==1 and c.get('au') is True
                and (c.get('ts') or 0)>=.95 and c.get('yok') is False
                and c.get('years_cited') and c.get('years_source')
                and min(c['years_source'])-max(c['years_cited'])>=8
                and not S.journal_agrees(src,e)
                and not any(d.get(k)==1 for k in S.FLAGS if k!='year_wrong')):
            via, original=S.search_crossref_pubmed(e)
            if original and (original.get('doi') or '').lower()!=(src.get('doi') or '').lower():
                co=S.compare(original,e)
                if (co.get('title_cmp') and (co.get('ts') or 0)>=.95
                        and co.get('au') is True and co.get('yok') is True):
                    d.update(state='unresolved',source=None,cmp=None,source_via='',
                             year_wrong='',unresolved_reason='original_vs_reprint_identifier')
                    d['id_note']=(d.get('id_note','')+';verified_original_and_later_reprint_dois').strip(';')
                    return d
        return d

    S.compare=compare
    S.decide=decide
