"""E4 S03 isolated v34 candidate: contextual publication year and source-edition matching.
Never edit the frozen S02 or the census. All inspected QC cases are development evidence.
"""
import re

GROUP_INITIALS = re.compile(r"\b(?:GlobalSurg|COVIDSurg|EuroSurg)\s+C\.?\b", re.I)
REPRINT_TAG = re.compile(r"\b(?:republished\s+article|republication|reprinted\s+article)\b", re.I)
JOURNAL_SERIES = (
    re.compile(r"\bJ\.?\s*Appl\.?\s*Physiol\.?\s*\(?\s*1985\s*\)?", re.I),
    re.compile(r"\bRev\.?\s*Assoc\.?\s*Med\.?\s*Bras\.?\s*\(?\s*1992\s*\)?", re.I),
)

def years_in_citation(e):
    """Only printed publication-year candidates OUTSIDE the printed title/identifiers.
    Missing a year is not a wrong year; never infer date from the work title or pages.
    """
    raw=e.get("raw") or ""
    title=e.get("title") or ""
    start=raw.lower().find(title.lower()) if len(title)>=12 else -1
    if start>=0:
        raw=raw[:start] + " " * len(title) + raw[start+len(title):]
    else:
        # Do not interpret digits in an unlocatable title: uncertain year.
        return []
    raw = re.sub(r"https?://\S+|(?:doi\s*[:=]\s*)?10\.\d{4,9}/\S+",
                 " ", raw, flags=re.I)
    raw = re.sub(r"(?:cited|accessed|retrieved|viewed)\s+[^.;)\]]{0,60}",
                 " ", raw, flags=re.I)
    # Page ranges often look like years, including glued "...2018271978–1986".
    raw = re.sub(r"\d{3,5}\s*[–\-]\s*\d{2,5}", " ", raw)
    for pat in JOURNAL_SERIES:
        if pat.search(raw):
            raw = pat.sub(" ", raw)
            # Some JATS references repeat the journal series year as a purported
            # publication date, before volume/page, e.g. "(1985) . 1985 ; 102".
            raw = re.sub(r"(?<!\d)(?:1985|1992)\s*;\s*\d{1,3}\b", " ", raw)
    yrs = [int(m.group()) for m in re.finditer(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)",raw)]
    return sorted(set(yrs))

def install(S):
    import s03_v33_patch
    s03_v33_patch.install(S)
    old_compare, old_corporate, old_decide=S.compare,S.corporate,S.decide

    def corporate(seg):
        return old_corporate(seg) or bool(GROUP_INITIALS.search(seg))

    def compare(rec,e):
        c=old_compare(rec,e)
        clean_years=years_in_citation(e)
        # The original S03's year scan also reads dates from titles, page ranges,
        # journal disambiguators, and JATS year parsed from those locations.
        # Use the independently delimited publication years only.
        if clean_years!=c.get('years_cited',[]):
            src_years = rec.get('years') or (
                {int(rec['year'])} if str(rec.get('year','')).isdigit() else set())
            c['years_cited'] = clean_years
            c['years_source'] = sorted(src_years)
            if not clean_years or not src_years or rec.get('book'):
                c['yok']=None;c['ydiff']=None
            else:
                c['yok']=any(abs(a-b)<=1 for a in src_years for b in clean_years)
                c['ydiff']=min(abs(a-b) for a in src_years for b in clean_years)
        return c

    def decide(ids,e,search):
        d=old_decide(ids,e,search)
        src=d.get('source')
        if not src:return d
        c=d.get('cmp') or {}
        # A journal's explicit "republished article" can point to a later paper
        # with same words/author but different volume/year. Source-search for the
        # ORIGINAL; only accept when title, printed year and author agree and the
        # source journal matches. Do not overlook a truly printed contradictory PMID.
        if (d.get('year_wrong')==1 and c.get('yok') is False
                and REPRINT_TAG.search(src.get('title') or '')
                and c.get('au') is True and c.get('ts',0)>=.9
                and not re.search(r'\bPMID\s*[:=]\s*\d+',e['raw'],re.I)):
            via, orig = S.search_crossref_pubmed(e)
            if orig:
                co=S.compare(orig,e)
                if (co.get('title_cmp') and (co.get('ts') or 0)>=.95
                        and co.get('au') is True and co.get('yok') is True
                        and S.journal_agrees(orig,e)):
                    d.update(source=orig,cmp=co,source_via=via+'+original_version')
                    d['year_wrong']=0
                    d['id_note']=(d.get('id_note','')+';original_article_precedes_republication').strip(';')
        # A decades-old exact work with newer digital deposit metadata has an
        # UNVERIFIED original print year, not enough evidence for "wrong year".
        # The official publisher issue date must be checked; never auto-pass it.
        src=d.get('source') or {}
        cc=d.get('cmp') or {}
        cite_years=cc.get('years_cited') or []
        pub_years=cc.get('years_source') or []
        if (d.get('year_wrong')==1 and
                (src.get('doi') or '').lower().startswith('10.1159/')
                and len(cite_years)==1 and pub_years
                and min(pub_years)-cite_years[0]>=5
                and (cc.get('ts') or 0)>=.95 and cc.get('au') is True
                and not any(d.get(k)==1 for k in S.FLAGS if k!='year_wrong')):
            d.update(state='unresolved',source=None,cmp=None,year_wrong='',
                     source_via='',unresolved_reason='original_print_year_needs_publisher_issue')
            d['id_note']=(d.get('id_note','')+';publisher_backfile_year_unverified').strip(';')
            return d
        d['state']='corrupted' if any(d.get(k)==1 for k in S.FLAGS) else 'valid'
        return d

    S.corporate=corporate
    S.compare=compare
    S.decide=decide
