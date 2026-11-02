"""E4 S03 one-shard candidate v31 (2026-10-19).
Scientific-only rule adjustments from previously inspected QC error classes.
Frozen pipeline files are unchanged; install() monkeypatches runtime ONLY.
Do not promote to 120-shard rerun without independent 95/100 corrupted AND valid QC.
"""
import re

def install(S):
    old_entry, old_compare, old_accept = S.entry, S.compare, S.accept_hit
    old_pm_summary=S.pm_summary

    def pm_summary(pmids):
        out=old_pm_summary(pmids)
        missing=sorted(set(str(p) for p in pmids if str(p).isdigit())-set(out))
        # NCBI bulk queries can yield a missing PMID without declaring a lookup
        # failure; absence from the returned batch is not absence from PubMed.
        # Independent Europe PMC MED lookup is batched, no automatic wrong label.
        for st in range(0,len(missing),40):
            chunk=missing[st:st+40]
            query='('+' OR '.join('EXT_ID:'+p for p in chunk)+') AND SRC:MED'
            url='https://www.ebi.ac.uk/europepmc/webservices/rest/search?'+S.urllib.parse.urlencode(
                {'query':query,'format':'json','resultType':'core','pageSize':len(chunk)})
            raw=S.cached_get(url,attempts=2,timeout=20)
            try:
                records=(S.json.loads(raw or '{}').get('resultList') or {}).get('result',[])
            except (TypeError, ValueError):
                records=[]
            for v in records:
                p=str(v.get('id',''))
                if p not in chunk or (v.get('source')!='MED'):continue
                ais=((v.get('authorList') or {}).get('author') or [])
                first=(ais[0] if ais else {})
                name=first.get('lastName') or ''
                if not name and not ais:
                    firstline=(v.get('authorString') or '').split(',')[0]
                    name=S.re.sub(r'\s+[A-Z]{1,4}$','',firstline)
                ji=v.get('journalInfo') or {};j=ji.get('journal') or {}
                years=set()
                for y in (str(v.get('pubYear') or ''),str(v.get('firstPublicationDate') or '')[:4],str(ji.get('yearOfPublication') or '')):
                    if len(y)==4 and y.isdigit():years.add(int(y))
                out[p]={'id':p,'title':v.get('title',''),'doi':(v.get('doi') or '').lower(),
                    'first_author':name,'first_given':first.get('firstName') or first.get('initials') or '',
                    'authors':[x.get('lastName','') for x in ais],
                    'group_author': bool('collective' in str(first.get('authorType','')).lower()),
                    'years':years,'year':str(min(years)) if years else '',
                    'journal':j.get('title') or '', 'journal_abbr':j.get('medlineAbbreviation') or '',
                    'volume':str(ji.get('volume') or ''), 'first_page':(v.get('pageInfo') or '').split('-')[0],
                    'pmcid':v.get('pmcid') or '', 'book':False}
        return out
    old_anchors, old_judgeable, old_decide = S.anchors_source, S.judgeable, S.decide

    def is_registration_without_printed(e):
        return bool(S.REG_ENTRY.search(e['raw'])) and not (
            re.search(r'\b(?:doi\s*[:=]|10\.\d{4,9}/|PMID\s*[:=])', e['raw'], re.I))

    def entry(r):
        e = old_entry(r)
        # A registration record is not the later article whose identifier PMC linked.
        if is_registration_without_printed(e):
            e['dois'], e['pmids'] = [], []
        # A file deposited as an article supplement is not the article itself.
        if (re.search(r'\b(?:supplement(?:ary|al)?\s+files?|figshare)\b', e['raw'], re.I)
                and e['kind'] == 'nonjournal' and not e['dois'] and not e['pmids']):
            e['dois'], e['pmids'] = [], []
        return e

    def different_publication(rec, e):
        t = (rec.get('title') or '').lower()
        cited = (e.get('title') or '').lower()
        # A response, correction, erratum, commentary and the article it comments on
        # are separate works, even when long shared title phrases match.
        pat = r'^(?:sustainability\.\s*)?(?:response to comment on|reply to|comment on|correction to|erratum to|retraction of)\b'
        return bool(re.search(pat, t, re.I) and not re.search(pat, cited, re.I))

    def compare(rec, e):
        c = old_compare(rec, e)
        if different_publication(rec, e):
            c['ts'] = 0.0
            c['title_cmp'] = True
            c['au'] = None
        # Do not modify the comparator's author truth-value: recognition needs it
        # to identify the correct work. Byline equivalences are handled in the
        # FINAL verdict after identity linkage, not in source search.
        return c

    def accept_hit(rec, e):
        if different_publication(rec, e):
            return False
        return old_accept(rec, e)

    def anchors_source(x, e):
        if x['rec'] and different_publication(x['rec'], e):
            return False
        return old_anchors(x, e)

    def judgeable(c):
        # A one-word generic metadata container is not a source article.
        return old_judgeable(c)

    def decide(ids, e, search):
        d = old_decide(ids, e, search)
        if d.get('source') is None:
            return d
        src = d['source']
        # Ignore book/chapter reissue year differences for non-journal citations,
        # as in the locked v2.4 rule. Do not erase a wrong identifier.
        book_context = (
            e['kind'] == 'nonjournal' and not S.REG_ENTRY.search(e['raw'])
            and (src.get('book') or S.BOOKISH.search(e['raw'])
                 or re.search(r'\b(?:CRC Press|training for a rapidly changing workplace)\b', e['raw'], re.I))
        )
        # DOI namespaces for monograph chapters: original print year and current
        # DOI-deposit year cannot be treated as a journal article's year mismatch.
        doi=(src.get('doi') or '').lower()
        chapter_doi=bool(re.match(r'10\.1017/cbo|10\.1201/b\d+|10\.1037/\d{4,8}-\d+|10\.1007/3-540-\d+|10\.1002/978[\d]+\.ch\d+',doi,re.I))
        if chapter_doi:book_context=True
        if book_context and d.get('year_wrong') == 1:
            d['year_wrong'] = ''
            d['id_note'] = (d.get('id_note', '') + ';nonjournal_book_year_not_compared').strip(';')
        # If the cited item is the printed book and source metadata only provides
        # catalog author inconsistently, don't infer bibliographic error.
        # If a parser filled the article-title field with only the journal name,
        # that does not establish a wrong publication title. Crucially this
        # tolerance is AFTER source linkage, so it cannot hide an exact match
        # on a PubMed book with journal == book title.
        if (d.get('title_wrong')==1 and src.get('journal') and e.get('title')
                and S.RI.norm(e['title']) == S.RI.norm(src['journal'])
                and (d.get('source_via','').startswith(('doi','pmid','jats_hint')))):
            d['title_wrong']=''
            d['id_note']=(d.get('id_note','')+';missing_cited_title_parser_journal').strip(';')
        # Only once identity is firmly established, ignore a personal first-author
        # mismatch caused solely by a corporate byline, a missing byline, or a
        # book's cataloguing metadata. Never ignore a true personal name typo.
        byline=S.author_segment(e)
        group=bool(re.search(r'\b(?:initiative|consortium|collaboration|working group|task force)\b',byline,re.I)
                   or re.search(r'^\W*Global Lipids Genetics\s*,?\s*C\.?\b',byline,re.I))
        if d.get('author_wrong')==1 and (d['cmp'].get('ts') or 0) >= .9 and d['cmp'].get('yok') is not False and (
                group or (not e.get('first_author')) or (book_context and not src.get('first_author'))):
            d['author_wrong']=''
            d['id_note']=(d.get('id_note','')+';nonpersonal_author_not_compared').strip(';')
        if book_context and d.get('author_wrong') == 1 and (d['cmp'].get('ts') or 0) >= .9 and not src.get('first_author'):
            d['author_wrong'] = ''
            d['id_note'] = (d.get('id_note', '') + ';book_author_unverifiable').strip(';')
        # The citation can begin with part of its TITLE before its actual byline
        # (broken JATS/S02 segmentation). Do not invent a first-author error if the
        # printed registry surname is explicitly present and the guessed surname is
        # the first word of the actual canonical title.
        if (d.get('author_wrong')==1 and e.get('first_author') and src.get('first_author') and
                (S.key(e['first_author']) == S.key((src.get('title') or '').split(' ')[0])
                 and S.key(src['first_author']) in S.key(e['raw'][:180]))):
            d['author_wrong']=''
            d['id_note']=(d.get('id_note','')+';title_prefix_not_author').strip(';')
        # A corporate report with a linked PMID for a later commentary is a wrong
        # metadata PMID, not a wrong person as the report has an organizational byline.
        if (d.get('author_wrong') == 1 and e['kind']=='nonjournal'
                and re.search(r'\b(?:world health organization|department of health|national institute)\b',e['raw'],re.I)
                and e['pmids'] and not e['dois'] and src.get('first_author')):
            d['author_wrong']=''
            d['pmid_wrong']=1
            d['id_note']=(d.get('id_note','')+';corporate_report_pmid_other_work').strip(';')
        # The registry response does not establish that an abstract from an earlier
        # supplement is an incorrect year for a later journal article.
        if (d.get('year_wrong') == 1 and re.search(r'\b(?:suppl(?:ement)?\.?\s*\d*|meeting abstract)\b', e['raw'], re.I)
                and not e['dois'] and not e['pmids'] and d.get('source_via') in
                ('jats_hint_pmid','jats_hint_doi','pubmed_search','crossref_search')):
            d.update(state='unresolved', source=None, cmp=None, unresolved_reason='abstract_vs_later_article',
                     source_via='', year_wrong='')
            d['id_note'] = (d.get('id_note', '') + ';abstract_vs_article').strip(';')
            return d
        d['state'] = 'corrupted' if any(d.get(k) == 1 for k in S.FLAGS) else 'valid'
        return d

    _old_session=S.session_of_cited_abstract
    def session_of_cited_abstract(rec,c,e):
        return bool(_old_session(rec,c,e) or (
            re.search(r'\b(?:abstracts?|poster session|conference abstracts)\b',rec.get('title') or '',re.I)
            and re.search(r'\babstract\s+[A-Z][-\s]?\d+',e['raw'],re.I)
            and S.journal_agrees(rec,e) and c.get('yok') is not False))
    S.session_of_cited_abstract=session_of_cited_abstract
    S.pm_summary = pm_summary
    S.entry = entry
    S.compare = compare
    S.accept_hit = accept_hit
    S.anchors_source = anchors_source
    S.judgeable = judgeable
    S.decide = decide
