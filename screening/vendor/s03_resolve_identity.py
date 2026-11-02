"""Step 03 — Is each reference correctly cited? (protocol v2.0 §5 S03; implementation note 05/10/2026)

What:   for every reference, find the work it refers to (the source) and compare the printed citation with it.
Input:  outputs/s02_references.csv (printed identifiers kept apart from JATS hint identifiers; citation_printed)
Output: outputs/s03_identity.csv — one row per reference, printed value next to canonical value for every field.

Principle: matching is tolerant enough to recognise the source of a citation that carries errors; once the source is
found, the comparison is strict. Normalisation only recognises equivalent representations (case, punctuation, DOI
prefix, typographic hyphens, diacritics, umlaut spelling, romanisation of a translated record, name order of an
OpenAlex record); a misspelt name, a wrong year or a wrong identifier is wrong however small.

1. Find the source (first that succeeds):
   a. the citation's own DOI (printed or supplied in the reference data; Crossref, other agencies via doi.org) or
      PMID (PubMed) whose record IDENTIFIES the
      cited work: title agreement >= config.TITLE_SAME, or first author AND year agree;
   b. a JATS hint DOI/PMID (added by PMC or a citation matcher) whose record passes the search-hit test;
   c. a search of the printed citation: Crossref -> PubMed -> OpenAlex, hit must pass the search-hit test
      (accept_hit); among accepted hits the one whose year agrees is preferred (pick).
   Non-journal entries without a printed identifier are not searched (no registry covers them).
2. Compare citation and source, field by field. 1 = wrong, 0 = right, blank = not compared.
     doi_wrong     a printed DOI does not resolve to the source (unregistered, or its record is another work)
     pmid_wrong    same for a printed PMID
     title_wrong   title agreement < TITLE_SAME (not compared when the two titles are in different languages)
     author_wrong  source first-author surname not in the citation's author segment (not compared for group authors;
                   an OpenAlex record can confirm an author but not make one wrong)
     year_wrong    differs from every date of the source by more than 1 year (protocol: ±1 = online vs print)
   A field missing from the citation is never wrong. Journal name is recorded, not compared. JATS hint identifiers
   are never compared: a hint that points elsewhere is reported in hint_note only.
3. State:
     valid       source found, nothing wrong
     corrupted   source found and >= 1 field wrong; OR no source found but a printed DOI/PMID resolves to another
                 identifiable work (record with its own title and author; title and author both disagree)
     unresolved  no source found and no printed identifier contradicts the citation (unresolved_reason says which case)
     pending     a lookup could not run (quota/network): the step is not finished; rerun later (cache keeps the rest)
"""
import html, json, re, sqlite3, subprocess, threading, time, urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import config
from common import read_csv, write_csv, StepLog
import ref_identity as RI

FLAGS = ['doi_wrong', 'pmid_wrong', 'title_wrong', 'author_wrong', 'year_wrong', 'id_points_to_other_work']
# Printed value next to canonical (source) value for every compared field, so errors can be characterised later
# straight from this table. *_match: 1 agrees, 0 wrong, blank not compared (= 1 - *_wrong).
PAIRS = ['printed_first_author', 'canonical_first_author', 'author_match',
         'printed_years', 'canonical_years', 'year_match', 'year_diff',
         'printed_title', 'canonical_title', 'title_score', 'title_match',
         'printed_dois', 'supplied_dois', 'canonical_doi', 'doi_match',
         'printed_pmids', 'supplied_pmids', 'canonical_pmid', 'pmid_match']
FIELDS = (['review_id', 'ref_index', 'ref_rid', 'ref_label', 'kind', 'state'] + FLAGS +
          ['unresolved_reason', 'source_via', 'source_id', 'source_journal'] + PAIRS +
          ['verified_by', 'original_first_author', 'original_years', 'original_title'] +
          ['jats_hint_dois', 'jats_hint_pmids', 'hint_note', 'id_provenance', 'id_checks', 'citation_printed',
           'citation_raw', 'id_note'])

CR_SELECT = 'DOI,title,author,issued,published-print,published-online,created,container-title'

# ---------------------------------------------------------------- HTTP cache for lookups added by this pipeline
_DB = None
_DB_LOCK = threading.Lock()
_BUDGET_LOCK = threading.Lock()


def _db():
    global _DB
    if _DB is None:
        (config.PIPE / 'cache').mkdir(exist_ok=True)
        _DB = sqlite3.connect(str(config.PIPE / 'cache' / 'http.sqlite'), check_same_thread=False)
        _DB.execute('create table if not exists c (k text primary key, v text)')
    return _DB


def cached_get(url, headers=None, budget=None, attempts=3, timeout=30):
    """GET with a permanent cache. budget = counter for metered APIs. Returns None when the call could not be made
    (quota, network), '' for a definite 404/406, else the body."""
    with _DB_LOCK:
        row = _db().execute('select v from c where k=?', (url,)).fetchone()
    if row:
        return row[0]
    if budget is not None:
        with _BUDGET_LOCK:
            if budget.get('blocked') or budget['used'] >= budget['max']:
                return None
            budget['used'] += 1
    # curl: hard wall-clock limit per request; -4 because IPv6 routes to Cloudflare-hosted APIs hang on this network.
    cmd = ['curl', '-4', '-s', '-L', '-m', str(timeout), '-A', RI.UA, '-w', '\n%{http_code}']
    for k, v in (headers or {}).items():
        cmd += ['-H', f'{k}: {v}']
    req = url
    if 'api.openalex.org' in url and OPENALEX_KEY:      # key added to the request only, never to the cache key
        req = url + '&api_key=' + OPENALEX_KEY
    for attempt in range(attempts):
        time.sleep(0.15)
        p = subprocess.run(cmd + [req], capture_output=True, text=True, errors='replace')
        body, _, code = p.stdout.rpartition('\n')
        if code == '200':
            with _DB_LOCK:
                _db().execute('insert or replace into c values (?,?)', (url, body)); _db().commit()
            return body
        if code in ('404', '406'):
            with _DB_LOCK:
                _db().execute('insert or replace into c values (?,?)', (url, '')); _db().commit()
            return ''
        if code == '429' and budget is not None:
            with _BUDGET_LOCK:
                budget['blocked'] = True
            return None
        if attempt + 1 < attempts:
            time.sleep(3 * (attempt + 1))
    return None


def doi_status(d):
    """'registered' / 'not_registered' / 'unknown' (handle lookup failed: never read as not registered)."""
    body = RI.get('https://doi.org/api/handles/' + urllib.parse.quote(d, safe='/'))
    if body == '':
        return 'unknown'
    if body == '__404__':
        return 'not_registered'
    try:
        return 'registered' if json.loads(body).get('responseCode') == 1 else 'not_registered'
    except ValueError:
        return 'unknown'


def _years(m, keys=('issued', 'published-print', 'published-online')):
    ys = set()
    for k in keys:
        dp = (m.get(k) or {}).get('date-parts', [[None]])
        if dp and dp[0] and dp[0][0]:
            ys.add(int(dp[0][0]))
    return ys


def _person(a):
    """-> (family, given, is_group) from a Crossref/CSL author object."""
    fam = a.get('family', '') or ''
    if not fam and (a.get('name') or a.get('literal')):
        return a.get('name') or a.get('literal'), '', True
    return fam, a.get('given', '') or '', False


def cr_record(m):
    fam, giv, grp = _person((m.get('author') or [{}])[0])
    ys = _years(m)
    cr = _years(m, ('created',))
    if ys and cr and min(cr) < min(ys):      # registered before its issue date = early access / online first
        ys |= cr
    doi = (m.get('DOI') or '').lower()
    return {'id': doi, 'doi': doi, 'title': (m.get('title') or [''])[0], 'journal': (m.get('container-title') or [''])[0],
            'containers': list(m.get('container-title') or []),
            'authors': [_person(a)[0] for a in m.get('author') or []],
            'first_author': fam, 'first_given': giv, 'group_author': grp, 'years': ys,
            'year': str(min(ys)) if ys else ''}


def crossref_batch(dois):
    """Same requests as ref_identity.crossref_batch (served from its cache), but keeps all dates and author detail."""
    out = {}
    dois = sorted(set(dois))
    for i in range(0, len(dois), 40):
        chunk = [d for d in dois[i:i + 40] if ',' not in d]
        if not chunk:
            continue
        url = 'https://api.crossref.org/works?' + urllib.parse.urlencode({
            'filter': ','.join('doi:' + d for d in chunk), 'rows': 100, 'mailto': RI.MAILTO, 'select': CR_SELECT})
        d = RI.jget(url)
        for it in (d.get('message') or {}).get('items', []) if isinstance(d, dict) else []:
            r = cr_record(it); out[r['id']] = r
    for d in dois:
        if d not in out and ',' in d:
            j = RI.jget('https://api.crossref.org/works/' + urllib.parse.quote(d, safe='/') + '?mailto=' + RI.MAILTO)
            if isinstance(j, dict) and j.get('message'):
                out[d] = cr_record(j['message'])
    return out


def pm_summary(pmids):
    """Same requests as ref_identity.pm_summary (served from its cache); keeps print + electronic dates."""
    out = {}
    pmids = sorted(set(pmids))
    for i in range(0, len(pmids), 150):
        ids = pmids[i:i + 150]
        d = RI.jget('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?' +
                    urllib.parse.urlencode({'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'json'}))
        res = d.get('result', {}) if isinstance(d, dict) else {}
        for p in ids:
            r = res.get(p)
            if not r or r.get('error'):
                continue
            ys = {int(y) for k in ('pubdate', 'epubdate') for y in re.findall(r'(?:19|20)\d\d', r.get(k, ''))[:1]}
            people = [a for a in (r.get('authors') or []) if a.get('authtype') != 'Editor']
            au = people[0] if people else {}
            name = au.get('name', '') or ''
            grp = au.get('authtype') == 'CollectiveName'
            book = r.get('doctype') in ('book', 'chapter')
            fam = name if grp else re.sub(r'\s+[A-Z]{1,4}$', '', name)
            ini = '' if grp else name[len(fam):].strip()
            out[p] = {'id': p, 'title': r.get('title', '') or r.get('booktitle', ''), 'book': book,
                      'journal': r.get('fulljournalname', '') or r.get('booktitle', ''), 'journal_abbr': r.get('source', ''),
                      'volume': r.get('volume', ''), 'first_page': re.split(r'[-–]', r.get('pages', '') or '')[0],
                      'authors': [re.sub(r'\s+[A-Z]{1,4}$', '', a.get('name', '') or '') for a in people],
                      'first_author': fam, 'first_given': ini, 'group_author': grp, 'years': ys,
                      'year': str(min(ys)) if ys else '',
                      'doi': next((a['value'].lower() for a in r.get('articleids', []) if a.get('idtype') == 'doi'), ''),
                      'pmcid': next((a['value'] for a in r.get('articleids', []) if a.get('idtype') == 'pmc'), '')}
    return out


def csl_record(doi):
    """Metadata for a DOI registered outside Crossref, via doi.org content negotiation (CSL-JSON)."""
    v = cached_get('https://doi.org/' + urllib.parse.quote(doi, safe='/'),
                   headers={'Accept': 'application/vnd.citationstyles.csl+json'}, attempts=1, timeout=3)
    try:
        m = json.loads(v or '')
    except ValueError:
        return None
    if not isinstance(m, dict):
        return None
    fam, giv, grp = _person((m.get('author') or [{}])[0])
    ys = _years(m)
    t = m.get('title') or ''
    return {'id': doi.lower(), 'doi': doi.lower(), 'title': t[0] if isinstance(t, list) else t,
            'journal': m.get('container-title') or '', 'first_author': fam, 'first_given': giv, 'group_author': grp,
            'authors': [_person(a)[0] for a in m.get('author') or [] if isinstance(a, dict)],
            'years': ys, 'year': str(min(ys)) if ys else ''}


# ---------------------------------------------------------------- search (returns the record, not only an ID)
OPENALEX = {'used': 0, 'max': config.OPENALEX_PER_DAY, 'blocked': False}


def _openalex_key():
    f = config.CREDENTIALS / 'openalex.env'
    if f.exists():
        for line in f.read_text().splitlines():
            if line.startswith('OPENALEX_API_KEY='):
                return line.split('=', 1)[1].strip()
    return ''


OPENALEX_KEY = ''  # v36: no credential access or paid API


def typo(a, b):
    """Two surnames a spelling slip apart (Swachuk/Sawchuk, Liberti/Liberati, Udea/Ueda): optimal string alignment
    distance <= 1 (<= 2 for names of 6+ letters). Used to recognise the source, never to excuse the misspelling."""
    a, b = key(a), key(b)
    if min(len(a), len(b)) < 4 or a == b:
        return False
    d = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1] <= (2 if min(len(a), len(b)) >= 6 else 1)


J_SKIP = set('journal of the and for in on de la et und der des du y e revista zeitschrift'.split())


def journal_agrees(rec, e):
    """The printed citation names the record's journal (full name or abbreviation; title words excluded)."""
    rest = RI.norm(e['raw'].replace(e['title'] or '\x00', ' '))
    toks = rest.split()
    for name in (rec.get('journal_abbr', ''), rec.get('journal', '')):
        n = RI.norm(re.sub(r'\s*[:(].*$', '', name or '')).strip()
        if not n:
            continue
        if len(n) >= 3 and f' {n} ' in rest:          # full name or abbreviation as printed (BMJ, JAMA, Lancet)
            return True
        ws = [w for w in n.split() if w not in J_SKIP and not w.isdigit()]
        if not ws:
            continue
        hit = sum(1 for w in ws if any(len(t) >= min(3, len(w)) and (w.startswith(t) or (len(w) >= 3 and t.startswith(w)))
                                       for t in toks))
        if (len(ws) == 1 and hit == 1 and len(ws[0]) >= 5) or (len(ws) >= 2 and hit / len(ws) >= 0.75):
            return True
    return False


def locator_agrees(rec, e):
    """Volume and first page of the record both printed in the citation (PubMed records carry them)."""
    v, p = str(rec.get('volume') or ''), str(rec.get('first_page') or '')
    toks = set(re.findall(r'[A-Za-z]?\d+', e['raw']))
    return bool(v and p and v in toks and p in toks)


def title_cut(rec, e, rev, n_cit):
    """The printed title is the record's title without its subtitle."""
    pt, st = RI.norm(e['title']).strip(), RI.norm(clean_title(rec.get('title', ''))).strip()
    return rev >= 0.9 and n_cit >= 4 and len(pt) >= 20 and st.startswith(pt)


def title_sure(rec, e):
    """Same title both ways, or the printed title is the record's title minus its subtitle."""
    fwd, rev, n_src, n_cit = title_parts(rec.get('title', ''), e)
    return fwd is not None and ((fwd >= 0.9 and (n_cit < 4 or rev >= 0.9)) or title_cut(rec, e, rev, n_cit))


def accept_hit(rec, e):
    """Is this record (a search hit, or the record behind a JATS hint identifier) the work the citation means?
    Tolerant enough to recognise the source of a citation that carries errors; the comparison afterwards stays strict.
      - the title must agree strongly in at least one direction (a printed title cut short still counts) and, for a
        short generic title, in both; a short title that is the same both ways counts only with author and year;
      - first author agrees, is a spelling slip of it, or is the first author's given name printed as a surname
        ("Matthew J" for Page MJ): accepted, whatever the year says;
      - no comparable author: the title in both directions and the year must agree (a book or living document,
        where neither author nor year can be compared: the same title both ways, at least 4 content words);
      - printed first author is someone else: accepted only if the title is the same both ways, the year agrees, and
        the printed name is a co-author of the record or the printed journal is the record's journal
        (a same-title paper by other authors, e.g. a Chinese-language version, is not the source).
    Without title agreement, a record is the source only if author, year, journal, volume and first page all agree
    (a citation whose printed title is cut to a word or two: "Rodríguez M. Tenofovir. World J Gastroenterol 2017;23:7459")."""
    c = compare(rec, e)
    fwd, rev, n_src, n_cit = title_parts(rec.get('title', ''), e)
    if fwd is None:
        return False
    cut = title_cut(rec, e, rev, n_cit)
    # forward agreement (share of the record's title in the citation); reverse agreement alone is not enough: words
    # under 4 letters are dropped, so "Tai Chi and Parkinson's disease: ... trials" would cover a VR review
    ts = 1.0 if cut else fwd
    if ts < config.SEARCH_TITLE_MIN:
        return bool(c['au'] is True and c['yok'] is True and journal_agrees(rec, e) and locator_agrees(rec, e))
    # a short generic title ("Corruption and anti-corruption") can match anything: then the printed title must also
    # be covered by the hit
    specific = n_src >= 5 or (n_src >= 3 and (n_cit < 4 or rev >= config.TITLE_SAME))
    same = fwd >= 0.9 and (n_cit < 4 or rev >= 0.9)      # same title both ways
    if not specific:
        return bool(same and n_src >= 1 and rev >= 0.9 and c['au'] is True and c['yok'] is True)
    # a year that disagrees is reported, but only once the title leaves no doubt: the same both ways, or the printed
    # title is the record's title without its subtitle (a same-author paper on a similar topic is not the source)
    sure = same or cut
    if c['yok'] is False and not sure:
        return False
    cited, giv = key(e['first_author']), (rec.get('first_given') or '').split()
    if (c['au'] is True or (c['au'] is False and (typo(cited, rec.get('first_author', ''))
                                                   or (len(cited) >= 3 and giv and key(giv[0]) == cited)))):
        return True
    if c['au'] is None:
        if not rec.get('first_author') and not rec.get('book'):     # record without author metadata: title must be sure
            return bool((same or cut) and c['yok'] is True)
        return rev >= config.TITLE_SAME and (c['yok'] is True or (c['yok'] is None and same and n_src >= 4))
    coauthor = len(cited) >= 2 and cited in {key(a) for a in (rec.get('authors') or [])[1:]}
    if MEETING.search(e['raw']) and not MEETING.search((rec.get('title') or '') + ' ' + (rec.get('journal') or '')):
        return False   # v2.4: a meeting abstract by its presenter is not the later paper led by a co-author (QC cor064)
    return same and c['yok'] is True and (coauthor or journal_agrees(rec, e))


MEETING = re.compile(r'\bcongress\b|\bannual meeting\b|\bconference\b|\bsymposium\b|\babstracts?\b|\bposter\b', re.I)


def search_crossref_pubmed(e):
    """Same requests as the legacy instrument (so they come from its cache).
    -> (via, rec); ('', None) if nothing accepted; ('incomplete', None) if the Crossref request failed."""
    q = e['raw'][:300]
    body = RI.get('https://api.crossref.org/works?' + urllib.parse.urlencode(
        {'query.bibliographic': q, 'rows': 3, 'mailto': RI.MAILTO, 'select': CR_SELECT}))
    if body == '':
        return 'incomplete', None
    try:
        items = (json.loads(body).get('message') or {}).get('items', [])
    except ValueError:
        items = []
    hits = [('crossref_search', r) for r in map(cr_record, items) if accept_hit(r, e)]
    if not any(compare(r, e)['yok'] for _, r in hits):
        tw = RI.content_words(e['title'])[:5]
        if len(tw) >= 3 and e['first_author']:
            term = f"{RI.skey(e['first_author'])}[1au] AND " + ' AND '.join(f'{w}[tiab]' for w in tw)
            hits += [('pubmed_search', r) for p, r in pm_summary(RI.pm_search(term)).items() if accept_hit(r, e)]
    return pick(hits, e)


def pick(hits, e):
    """Several accepted records: prefer one whose year agrees (a reprint, erratum or software record of the same
    title is not the version cited), then the best title agreement; ties keep registry order."""
    if not hits:
        return '', None
    def rank(h):
        c = compare(h[1], e)
        return (c['yok'] is True, c['au'] is True, c['ts'] or 0)
    return max(hits, key=rank)


def search_openalex(e):
    words = RI.content_words(e['title'])
    q = e['title'] if len(words) >= 3 else e['raw'][:200]
    params = {'search': re.sub(r'[^\w\s-]', ' ', q)[:250], 'per_page': 5, 'mailto': RI.MAILTO,
              'select': 'id,doi,title,publication_year,authorships,primary_location'}
    if e['years']:
        y = max(e['years'])
        params['filter'] = f'publication_year:{y - 1}-{y + 1}'
    v = cached_get('https://api.openalex.org/works?' + urllib.parse.urlencode(params), budget=OPENALEX)
    if v is None:
        return 'incomplete', None
    try:
        res = json.loads(v).get('results', []) if v else []
    except ValueError:
        res = []
    hits = []
    for w in res:
        au = ((w.get('authorships') or [{}])[0].get('author') or {}).get('display_name', '')
        doi = (w.get('doi') or '').replace('https://doi.org/', '').lower()
        y = w.get('publication_year')
        names = [((x.get('author') or {}).get('display_name') or '').split() for x in w.get('authorships') or []]
        rec = {'id': doi or w.get('id', ''), 'doi': doi, 'title': w.get('title') or '',
               'authors': [n[-1] for n in names if n],
               'first_author': au.split()[-1] if au else '', 'first_given': ' '.join(au.split()[:-1]),
               'group_author': False, 'years': {int(y)} if y else set(), 'year': str(y or ''),
               'name_order_unknown': True,     # OpenAlex display names: family name may come first (Chinese names)
               'journal': ((w.get('primary_location') or {}).get('source') or {}).get('display_name', '')}
        if accept_hit(rec, e):
            hits.append(('openalex_search', rec))
    return pick(hits, e)


def search_all(e, pre=None):
    """-> ('found', via, rec) | ('not_found', '', None) | ('incomplete', '', None)."""
    via, rec = pre if pre is not None else search_crossref_pubmed(e)
    if rec and compare(rec, e)['yok'] is not False:
        return 'found', via, rec
    incomplete = via == 'incomplete'
    via2, rec2 = search_openalex(e)          # also when the hit found disagrees on year: look for the cited version
    via, rec = pick([h for h in ((via, rec), (via2, rec2)) if h[1]], e)
    if rec:
        return 'found', via, rec
    return ('incomplete' if incomplete or via2 == 'incomplete' else 'not_found'), '', None


# ---------------------------------------------------------------- comparison (pure functions, unit-tested)
CJK = re.compile(r'[぀-ヿ一-鿿가-힯]')
CYR = re.compile(r'[Ѐ-ӿ]')
TURKISH = re.compile(r'[ğĞşŞİ]')   # a Turkish-language record (field QC 09/10: English title of a Turkish paper)
EN_SW = set('the of and in with for on to from by after versus among an a is are at as or under'.split())
OTHER_SW = set('de la el los las del em para com do da dos das der die und den les des du et une sur di per il '
               'che het een och ve ile na w z i y en al le pour bei mit von zur zum nos nas sobre entre por con '
               'pela pelo dans nel della dei delle im ist eine einer nach aus fur uber una uno ou sa ng'.split())
GROUP = re.compile(r'group|consortium|collaborat|committee|commission|framework|@|investigator|society|association|organi[sz]ation|'
                   r'team|network|council|working|panel|task force|agency|institute|ministry|world health|'
                   r'^journals?$|anonymous|editorial', re.I)   # registry placeholders ("Journals" on a figshare record)


def lang(text):
    t = text or ''
    if TURKISH.search(t):
        return 'other'
    if CJK.search(t):
        return 'cjk'
    if CYR.search(t):
        return 'cyrillic'
    w = re.findall(r'[a-z]+', RI.strip_acc(t).lower())
    en, ot = sum(x in EN_SW for x in w), sum(x in OTHER_SW for x in w)
    return 'other' if (ot >= 2 and ot > en) or (ot >= 1 and en == 0 and len(w) >= 3) else 'en'


TRANSLIT = str.maketrans({'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'ß': 'ss', 'ł': 'l', 'Ł': 'L', 'đ': 'd',
                          'Đ': 'D', 'ı': 'i', 'œ': 'oe', 'Œ': 'OE', 'þ': 'th', 'ð': 'd'})
SYN = [(re.compile(r'severe acute respiratory syndrome coronavirus 2|sars cov 2|coronavirus disease 2019|covid 19|'
                   r'2019 coronavirus disease|2019 novel coronavirus|novel coronavirus 2019|2019 ncov'),
        ' covid '), (re.compile(r'(\w{4,})s\b'), r'\1')]
UMLAUT = str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'Ä': 'Ae', 'Ö': 'Oe', 'Ü': 'Ue'})
REGISTRY = re.compile(r'\b(?:NCT\d{8}|ISRCTN\d+|IRCT\w+|ChiCTR[\w-]+|CTRI/[\d/]+|ACTRN\d+|DRKS\d+|EUCTR[\w-]+)', re.I)


def key(s):
    return RI.skey((s or '').translate(TRANSLIT))


# v2.4: British/American spelling of the same word (amoebiasis/amebiasis, haemorrhage/hemorrhage, tumour/tumor)
BRIT = [(re.compile(r'(?<=[a-z])(?:oe|ae)(?=[a-z])'), 'e'), (re.compile(r'^(?:oe|ae)(?=[a-z])'), 'e'),
        (re.compile(r'our\b'), 'or'), (re.compile(r'(?<=[a-z]{3})is(e|ed|es|ing|ation|ations)\b'), r'iz\1')]


def us_spelling(w):
    for rx, rep in BRIT:
        w = rx.sub(rep, w)
    return w


def words(t):
    n = ' ' + RI.norm((t or '').translate(TRANSLIT)) + ' '
    for rx, rep in SYN:
        n = rx.sub(rep, n)
    n = ' '.join(us_spelling(w) for w in n.split())
    return [w for w in n.split() if len(w) >= 4 and w not in RI.STOP and not w.isdigit()]


TAGS = re.compile(r'<[^>]+>|\(author.?s transl\)', re.I)   # removed without a space: L<scp>ong</scp> -> Long
CONTAINER = re.compile(r'\babstracts\b|open peer commentary|\bsupplement\b|\bproceedings\b|\bcongress\b|'
                       r'\bsymposium\b|annual meeting|scientific sessions|^session\s+\d+', re.I)   # record is a volume, not one work
PLACEHOLDER = re.compile(r'^\W*(?:abstracts?|posters?|oral|e?p?\d+[a-z]?p?)\b[\W\d]*$', re.I)
NO_TITLE_PREFIX = ('10.1101/', '10.21203/', '10.20944/', '10.2139/', '10.5281/', '10.6084/')  # preprints, datasets
TRANSLATED = re.compile(r'\((?:in )?(?:german|french|spanish|portuguese|chinese|japanese|korean|russian|italian|'
                        r'turkish|persian|polish|dutch|czech|hungarian)\)|\b(?:german|french|spanish|portuguese|'
                        r'chinese|japanese|korean|russian|italian|turkish|polish|dutch)\.?\s*$', re.I)


def clean_title(t):
    return TAGS.sub('', t or '')


def near(w, pool):
    """Spelling variant of a long word (judgement/judgment, behaviour/behavior): one edit apart."""
    if len(w) < 7:
        return False
    for x in pool:
        if abs(len(x) - len(w)) <= 1 and x[:3] == w[:3]:
            i = 0
            while i < min(len(x), len(w)) and x[i] == w[i]:
                i += 1
            if x[i + 1:] == w[i + 1:] or x[i:] == w[i + 1:] or x[i + 1:] == w[i:]:
                return True
    return False


def title_parts(src_title, e):
    """(fwd, rev, n_src_words, n_printed_words): fwd = share of the source title's words in the citation;
    rev = share of the printed title's words in the source title."""
    sw, cw, tw = set(words(clean_title(src_title))), set(words(e['raw'])), set(words(e['title']))
    if not sw:
        return None, 0, 0, len(tw)
    r0 = RI.norm(e['raw']).replace(' ', '')                       # 'short-coming' vs 'shortcoming'
    fwd = sum(1 for x in sw if x in cw or x in r0 or near(x, cw)) / len(sw)
    rev = sum(1 for x in tw if x in sw or near(x, sw)) / len(tw) if tw else 0
    return fwd, rev, len(sw), len(tw)


def main_title(src_title, e):
    """The printed title is the record's title without its subtitle ("The FAB" for "The FAB: a Frontal Assessment
    Battery at bedside", "The ASA's statement on p-values" for "...: Context, Process, and Purpose"). Field QC 09/10."""
    pt = e['title'] or ''
    main = re.split(r'\s*[:.?]\s+|\s*[:?]\s*$', clean_title(src_title or '').strip(), maxsplit=1)[0]
    if not pt or not main or RI.norm(main) == RI.norm(clean_title(src_title or '')):
        return False
    a, b2 = set(words(pt)), set(words(main))
    return (a == b2 and bool(a)) or RI.norm(pt).strip() == RI.norm(main).strip()


def title_agreement(src_title, e):
    """Max of fwd and rev (rev only when the printed title has >= 4 words: handles truncated/abbreviated titles)."""
    fwd, rev, _, n_cit = title_parts(src_title, e)
    if fwd is None:
        return None
    return max(fwd, rev if n_cit >= 4 else 0)


UNPARSED_TITLE = re.compile(r'10\.\d{4,9}/|\bPMC\d{5,}|(?:\b\d+\b.*){4,}')


def title_comparable(rec, e):
    """Titles are compared only when both are the original-language title of the same record type."""
    t = clean_title(rec.get('title', '')).strip()
    raw = e['raw']
    if UNPARSED_TITLE.search(e['title'] or '') and (title_agreement(t, e) or 0) < 0.3:
        return False      # v2.4: step 02 found no title (returned the whole entry) and the entry prints none (QC cor079)
    return not (t.startswith('[') or PLACEHOLDER.match(t) or (rec.get('doi') or '').startswith(NO_TITLE_PREFIX)
                or TRANSLATED.search(raw) or re.search(r'\[[^\]]{20,}\]', raw)
                or lang(t) != lang(e['title'] or raw))


def author_segment(e):
    """Printed text before the title; '' when the citation prints no author (title-first or registry entries)."""
    raw = e['raw']
    if REGISTRY.search(raw[:60]):
        return ''
    t = (e['title'] or '')[:25].lower()
    pos = raw.lower().find(t) if len(t) >= 10 else -1
    seg = raw[:pos] if pos >= 0 else raw[:250]
    seg = re.sub(r'^[\s\[\(]*\d+[\.\)\]]?\s*', '', seg)
    return seg if len(re.sub(r'[^A-Za-zÀ-ÿ]', '', seg)) >= 2 else ''


def name_swapped(seg, fam, giv):
    """East Asian name order printed the other way round: citation 'Weiwei G' for record 'Guo W(eiwei)'."""
    m = re.match(r"\W*([A-Za-zÀ-ÿ'\-]+)[,\s]+((?:[A-Z]\b\.?\s*){1,5})", seg)
    if not m or not fam or not giv:
        return False
    tok, f0 = key(m.group(1))[:1].upper(), key(fam)[:1].upper()
    if tok == f0:                       # same initial letter: a misspelt surname (Swachuk/Sawchuk), not a swap
        return False
    rec_ini = ''.join(w[0] for w in re.split(r'[\s\-.]+', giv) if w).upper() if not giv.isupper() else giv
    return tok in rec_ini and f0 in re.sub(r'[^A-Z]', '', m.group(2))


INITIALS = re.compile(r'^(?:[A-Z]\.?\s*){1,4}$')


def full_name_agrees(seg, fam, giv, cited, rec):
    """v2.4. The printed first author and the registry name the same person in another representation:
    - full name printed "Given Family" ("Dianna Ng", "Danny Yu"): the family name is a whole token of the first author;
    - Iberian double surname stored partly in the given field (given "Mireia Baylina", family "Ferre"; printed "Baylina M");
    Whole tokens only, so a misspelt surname (Swachuk/Sawchuk) is still wrong."""
    first = re.split(r'[,;]|\bet al\b|\s(?:and|&)\s', seg, maxsplit=1)[0]
    first_toks = [key(x) for x in re.split(r'[\s.\-]+', first) if key(x)]
    fam_toks = [key(x) for x in re.split(r'[\s.\-]+', fam) if key(x)]
    if fam_toks and len(first_toks) >= 2 and all(t in first_toks for t in fam_toks):
        return True
    giv_raw = [x for x in re.split(r'[\s.\-]+', giv) if key(x)]
    giv_toks = [key(x) for x in giv_raw]
    if len(giv_toks) >= 2 and len(cited) >= 4 and cited in giv_toks[1:]:
        # the printed initials must be those of the remaining given names only ("Baylina M" for Mireia Baylina Ferre);
        # initials that include the family name ("Selcuk CT" for Tuba Selcuk Can) mean a given name was printed as
        # the surname, which is an author error (QC cor078)
        m = re.search(r'\b' + re.escape(first.strip().split()[0]) + r'[\s,]+((?:[A-Z]\.?\s*){1,4})\b', first) if first.strip() else None
        ini = re.sub(r'[^A-Z]', '', m.group(1)) if m else ''
        rest = ''.join(x[0].upper() for x, k in zip(giv_raw, giv_toks) if k != cited)
        if ini and ini == rest[:len(ini)]:
            return True
    return False


def registry_name(rec):
    """(family, given) of the record's first author. A registry that swapped the fields (family "P. K", given "Sinha")
    is read the right way round: a family name made only of initials next to a given name that is a word."""
    fam, giv = (rec.get('first_author') or '').strip(), (rec.get('first_given') or '').strip()
    if INITIALS.match(fam) and re.fullmatch(r"[A-Z][a-z'\-]{2,}", giv):
        return giv, fam
    return fam, giv


ACRONYM = re.compile(r'^[A-Z][A-Za-z]*[A-Z][A-Za-z]*$')


def corporate(seg):
    """The printed author is an organisation (named, or only as acronyms: "(ASAM); ASoAM", "WHO", "NICE").
    Its credit is not compared with a registry's personal first author (protocol: group authors not compared)."""
    if GROUP.search(seg[:80]):
        return True
    toks = re.findall(r'[A-Za-z]+', seg[:80])
    return bool(toks) and all(ACRONYM.match(t) and len(t) >= 3 and sum(c.isupper() for c in t) >= 3 for t in toks)


def romanised(name):
    """Collapse common romanisation variants of Cyrillic names (ye/e, ya/ia, kh/h, y as soft sign, ii/iy/y endings)."""
    k = key(name)
    for a, b in (('kh', 'h'), ('ye', 'e'), ('yu', 'iu'), ('ya', 'ia'), ('iy', 'i'), ('ii', 'i'), ('y', 'i'), ('ts', 'c')):
        k = k.replace(a, b)
    return k


def persian(name):
    k = key(name)
    for a, b2 in (('ou', 'u'), ('oo', 'u'), ('ee', 'i')):
        k = k.replace(a, b2)
    return k


BOOKISH = re.compile(r"\b(?:manual|handbook|chapter|textbook|monograph)\b|\bed(?:ition|\.)\s|\b\d+(?:st|nd|rd|th)\s+ed\b|"
                     r"\b(?:university|academic)\s+press\b|\bpublishers?\b", re.I)
SERIES_YEAR = re.compile(r'(\bSpine\.?\s*)\(\s*(?:Phila\.?\s*Pa\.?\s*)?1976\s*\)|(\bJ\.?\s*Appl\.?\s*Physiol\.?\s*)\(\s*1985\s*\)', re.I)
EPUB = re.compile(r'epub ahead|ahead of print|in press|online ahead|early view|\[epub\]', re.I)


def compare(rec, e):
    """Compare one registry record with the printed citation. au/yok: True agree, False disagree, None not compared."""
    ts = title_agreement(rec.get('title', ''), e)
    printed_title = len(RI.content_words(e['title'])) >= 3 and e['title'].lower()[:25] in e['raw'].lower()
    title_cmp = (ts is not None
                 and (printed_title or e['title_src'] == 'structured' or ts >= 0.3)
                 and title_comparable(rec, e))
    au = None
    fam, giv = registry_name(rec)
    seg = author_segment(e)
    if seg and (re.fullmatch(r'\W*anon(?:ymous)?\W*', seg, re.I) or
                (len(RI.norm(seg).strip()) >= 6 and RI.norm(clean_title(rec.get('title', ''))).strip().startswith(RI.norm(seg).strip()))):
        seg = ''      # v2.4 r3: no author printed ("Anon"; title-first "GRADE guidelines: 18 ..."): not compared (QC r2)
    latin = not re.search(r'[\u0370-\u03ff\u0400-\u04ff\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]', fam)
    if (re.search(r'[A-Za-z\u00c0-\u024f]', fam) and seg and latin and not rec.get('group_author') and not rec.get('book') and not GROUP.search(fam)
            and not corporate(seg) and '\ufffd' not in fam):
        cited = key(e['first_author'])
        fams = {fam, fam.translate(UMLAUT)}                       # Mühlbacher / Muehlbacher
        toks = [key(x) for f in fams for x in re.split(r'[\s\-]+', f) if key(x)] or [key(fam)]
        sk = key(seg)
        seg_toks = [key(x) for x in re.split(r'[\s,;.\-]+', seg) if key(x)]
        fam_toks = [key(x) for x in re.split(r'[\s,;.\-]+', fam) if key(x)]
        au = (any((len(t) >= 3 and t in sk) or t == cited for t in toks) or key(fam) == cited
              # a short or initial-like surname ("Ng", "P. K") as whole tokens at the head of the author segment
              or (bool(fam_toks) and seg_toks[:len(fam_toks)] == fam_toks)
              or (len(key(giv)) >= 2 and key(giv) == cited) or name_swapped(seg, fam, giv)
              # equivalent representations, not spelling: an OpenAlex name whose order is unknown ("Liu Min"), and
              # romanisation of a PubMed record translated from Russian etc. (Vorob'eva / Vorobyeva)
              or (rec.get('name_order_unknown') and bool(giv) and key(giv.split()[0]) == cited)
              or (clean_title(rec.get('title', '')).strip().startswith('[') and len(cited) >= 4
                  and romanised(fam) == romanised(e['first_author']))
              # Persian romanisation: ou / oo / u for one vowel (Soltanipour / Soltanipoor)
              or (len(cited) >= 6 and persian(fam) == persian(e['first_author']))
              # v2.4 (QC 11/10): equivalent representations of the same name, not spelling slips
              or full_name_agrees(seg, fam, giv, cited, rec))
    yok = None
    ys = rec.get('years') or ({int(rec['year'])} if str(rec.get('year', '')).isdigit() else set())
    # years printed anywhere in the citation except inside page ranges (S02 can pick a journal-series year,
    # e.g. "J Appl Physiol (1985) 2014")
    txt = re.sub(r'(?:doi:?\s*)?10\.\d{4,9}/\S+|https?://\S+', ' ', e['raw'], flags=re.I)   # not years inside DOIs/URLs
    txt = SERIES_YEAR.sub(' ', txt)          # v2.4: "Spine (Phila Pa 1976)", "J Appl Physiol (1985)" name the journal
    acc = re.findall(r'(?:cited|accessed|retrieved|viewed)[^.;\]\)]{0,40}', txt, re.I)        # access dates
    acc_years = {int(y) for a in acc for y in re.findall(r'(?:19|20)\d\d', a)}
    for a in acc:
        txt = txt.replace(a, ' ')
    free = {int(y) for y in re.findall(r'(?<!\d)((?:1[89]|20)\d\d)(?!\d)', txt)}
    e = {**e, 'years': [y for y in e['years'] if y in free]}
    cited = (set(e['years']) - acc_years) | {int(m.group(1)) for m in
                                             re.finditer(r'(?<![\d\-–])(1[89]\d\d|20[0-3]\d)(?![\d\-–])', txt)}
    # reported printed year: an element-citation's <year>, not free numbers (volume/pages "36 9 1972 1976")
    shown = (set(e['years']) - acc_years) if e.get('title_src') == 'structured' and e['years'] else cited
    # living documents (StatPearls, Bookshelf books re-issued under one PMID): year not compared
    living = rec.get('book') or 'statpearls' in (e['raw'] + ' ' + (rec.get('journal') or '')).lower()
    # v2.4 r3: a year written into the DOI itself ("jpn-2011-0458") that matches the printed year shows the registry
    # date is a later deposit (QC r2 cor071)
    doi_years = {int(y) for y in re.findall(r'(?<!\d)((?:19|20)\d\d)(?!\d)', rec.get('doi') or '')} \
        if (rec.get('doi') or '').lower() in {d.lower() for d in e.get('dois') or []} else set()   # the citation's own DOI
    if ys and cited and not living:
        if doi_years & set(cited):
            yok = True
        elif EPUB.search(e['raw']):          # cited at epub / in press: an earlier printed year is not wrong
            yok = any(c <= a + 1 for a in ys for c in cited)
        else:
            yok = any(abs(a - c) <= 1 for a in ys for c in cited)
    if CONTAINER.search(clean_title(rec.get('title', ''))) and not CONTAINER.search(e['title'] or ''):
        title_cmp, au = False, None            # a supplement/proceedings record cannot confirm or refute one item
    ydiff = min((abs(a - c) for a in ys for c in cited), default=None) if yok is not None else None
    # OpenAlex author lists can be reordered or incomplete: a disagreement still rules a search hit out, but it never
    # makes a printed author wrong (au_refutes = False)
    return {'ts': ts, 'title_cmp': title_cmp, 'au': au, 'au_refutes': not rec.get('name_order_unknown'), 'yok': yok,
            'years_cited': sorted(shown or cited),
            'years_source': sorted(ys), 'ydiff': ydiff}


def judgeable(c):
    """A record can show that an identifier is wrong only if its title or its author can be compared at all
    (not: container volumes, records without metadata, non-Latin-script records)."""
    return bool(c['title_cmp'] or c['au'] is not None)


def identifies(c):
    return bool((c['title_cmp'] and c['ts'] >= config.TITLE_SAME) or (c['au'] and c['yok']))


def title_contradicts(c, e):
    """A substantive printed title (>= 4 content words) that the record's title does not share (score < 0.3):
    the record is another paper, whatever author and year say (field QC 09/10: a DOI of the same author's other
    paper was reported as a wrong title)."""
    return bool(c['title_cmp'] and c['ts'] is not None and c['ts'] < 0.3
                and len(RI.content_words(e['title'] or '')) >= 4 and not UNPARSED_TITLE.search(e['title'] or ''))


PREPRINT_DOI = ('10.1101/', '10.21203/', '10.20944/', '10.2139/', '10.48550/', '10.31234/', '10.31219/')


def preprint_version(x, e):
    """v2.4 (investigator 12/10): a preprint and its journal publication are one work. A preprint cited with the identifier of
    the published article, or an article cited with its preprint DOI, is not a citation error when the titles agree."""
    if not x.get('rec') or not x.get('cmp') or (x['cmp'].get('ts') or 0) < config.TITLE_SAME:
        return False
    return bool(PREPRINT.search(e['raw'])) or (x['rec'].get('doi') or '').lower().startswith(PREPRINT_DOI)


def anchors_source(x, e):
    """The citation's own identifier names the cited work. Its record must identify the work, and a year that
    disagrees is taken as a citation error only if the author agrees and the title leaves no doubt; otherwise the
    identifier may point to another version (a reprint, a later Cochrane update, another edition) or another paper by
    the same author, and the source is looked for by search instead."""
    c = x['cmp']
    if x['status'] != 'record' or not c:
        return False
    book = bool(BOOKISH.search(e['raw'])) and c['au'] is True and (c['ts'] or 0) >= config.TITLE_SAME
    return (x['status'] == 'record' and identifies(c) and not title_contradicts(c, e)
            # v2.4 r3: a book's DOI names the work across editions and online re-issues (QC r2 cor055)
            and not (c['yok'] is False and not (c['au'] is True and title_sure(x['rec'], e)) and not book))


def other_work(rec, c, e=None):
    """The record is a different identifiable work: it has its own title and author, and both disagree; or its title
    contradicts a substantive printed title (the same author's other paper)."""
    has_work = len(RI.content_words(rec.get('title', ''))) >= 2 and bool(rec.get('first_author'))
    return bool(has_work and c['title_cmp'] and ((c['ts'] < config.TITLE_SAME and c['au'] is False)
                                                 or (e is not None and title_contradicts(c, e))))


SQUEEZE = str.maketrans({c: '-' for c in '\u2010\u2011\u2012\u2013\u2212'})


def doi_slip(bad, raw, src_doi, own):
    """An unregistered printed DOI that is the source's DOI with typesetting noise: a trailing "/", a space inside,
    the next token glued on (".PMID:...", "|y"). The source DOI must be printed in the citation, ending where a DOI
    can end (so "cureus.1350313" for "cureus.13503" stays a wrong DOI). Field QC 09/10/2026."""
    if not src_doi:
        return False
    cs = re.sub(r'\s+', '', (raw or '').translate(SQUEEZE))          # case kept: a glued next author "656Kendrick"
    sq = cs.lower()
    bounded = any(not sq[m.end():m.end() + 1].isalnum() or re.match(r'[A-Z][a-z]{2,}', cs[m.end():m.end() + 3])
                  for m in re.finditer(re.escape(src_doi), sq))
    bad = bad.lower()
    return bounded and (bad.startswith(src_doi) or src_doi.startswith(bad) or src_doi in own)


PREPRINT = re.compile(r'medrxiv|biorxiv|research square|researchsquare|preprints?\b|ssrn|arxiv|\bposted\b', re.I)
EDITION = re.compile(r'\(\s*\d+(?:st|nd|rd|th)\s+ed|\bedition\b', re.I)
REG_ENTRY = re.compile(r'\bPROSPERO\b|\bCRD\s?\d{8,}', re.I)
COCHRANE = re.compile(r'^(10\.1002/14651858\.cd\d+)(?:\.pub\d+)?$', re.I)


def cochrane_version(e, rec):
    """A Cochrane review exists in several versions (.pub2, .pub3 ...), each with its own year and author list. If the
    printed year disagrees with the record found, the version whose year agrees is the one cited (field QC 09/10)."""
    m = COCHRANE.match((rec or {}).get('doi') or '')
    if not m:
        return None
    cands = [m.group(1).lower()] + [f'{m.group(1).lower()}.pub{i}' for i in range(2, 9)]
    found = crossref_batch(cands)
    for d in cands:
        r = found.get(d)
        if r and compare({**r, 'doi': d}, e)['yok'] is True and title_sure({**r, 'doi': d}, e):
            return {**r, 'doi': d, 'id': d}
    return None


def b(x):
    return '' if x is None else int(bool(x))


SUPPL_PAGE = re.compile(r'\b(?:[ivx]{1,4}|S|e)\d{1,4}\b|\bsuppl', re.I)


def session_of_cited_abstract(rec, c, e):
    """v2.4 r3. A meeting abstract cited with the DOI of the session/section that holds it ("Cell Biology and Signaling",
    Neuro-Oncology iv7-iv25, for an abstract on iv8; holdout ESHRE "Session 47"): short record title, same journal and year,
    supplement-style page in the citation. Not wrong (investigator 06/10, holdout cor042; QC r2 cor040)."""
    short = len(RI.content_words(clean_title(rec.get('title', '')))) <= 4 or bool(CONTAINER.search(rec.get('title', '')))
    if re.search(r'editorial|erratum|correction|corrigendum|notice|preface|foreword|introduction|retraction',
                 rec.get('title', ''), re.I):
        return False          # another item of the supplement, not the section holding the abstract (seen cor097)
    return bool(short and c['yok'] is True and SUPPL_PAGE.search(e['raw']) and journal_agrees(rec, e))


def item_in_cited_container(rec, c, e):
    """v2.4. The citation names a book or proceedings (as its title) and its DOI is that of an item inside it (a chapter,
    a proceedings paper, a column): the record's container title is the printed title, and author or year agree
    (QC #12 Cho EMNLP, #21 Fleiss chapter 18, #50 Murray chapter). Or the record is a short section/column heading
    by the same authors in the printed journal and year (#64 "Biotechnology Update")."""
    pt = e['title'] or ''
    if len(RI.content_words(pt)) >= 2:
        for ct in rec.get('containers') or []:
            if title_agreement(ct, e) is not None and set(words(ct)) and set(words(pt)) and \
                    len(set(words(ct)) & set(words(pt))) / len(set(words(pt))) >= 0.85 and (c['au'] or c['yok']):
                return True
    heading = len(RI.content_words(clean_title(rec.get('title', '')))) <= 2
    return bool(heading and c['au'] is True and c['yok'] is True)



def decide(ids, e, search):
    """ids: [{type: 'doi'|'pmid', id, printed: bool, status: 'record'|'not_registered'|'no_metadata'|'unknown', rec, cmp}].
    printed = the citation's own identifier (printed, or supplied in the article's reference data; step 02);
    otherwise a JATS hint added by PMC or a publisher's citation matcher.
    search: callable -> ('found', via, rec) | ('not_found'|'incomplete', '', None); called only when needed.

    Only the citation's own identifiers can be wrong. A hint can name the source only if its record passes the same test as a
    search hit (accept_hit); it never makes a citation wrong. Hint records that disagree are reported in hint_note."""
    out = {k: '' for k in FLAGS}
    out.update(state='', unresolved_reason='', source_via='', source=None, cmp=None, anchors=[], hint_note='',
               verified_by='', original={})
    if e.get('multi_work'):
        out.update(state='unresolved', unresolved_reason='multi_work_entry')     # v2.4: several works in one entry
        return out
    printed = [x for x in ids if x['printed']]
    hints = [x for x in ids if not x['printed']]
    if not printed and REG_ENTRY.search(e['raw']):
        out.update(state='unresolved', unresolved_reason='nonjournal_no_id')   # v2.4 r3: a registration (QC r2 cor038)
        return out
    anchors = [x for x in printed if anchors_source(x, e)]
    hint_anchors = [x for x in hints if x['status'] == 'record' and accept_hit(x['rec'], e)]
    best = lambda xs: max(xs, key=lambda x: ((x['cmp']['ts'] or 0) + (x['cmp']['au'] is True) + (x['cmp']['yok'] is True)))
    if anchors:
        a = best(anchors)
        out.update(source_via=a['type'], source=a['rec'], cmp=a['cmp'])
    elif hint_anchors:
        a = best(hint_anchors)
        out.update(source_via='jats_hint_' + a['type'], source=a['rec'], cmp=a['cmp'])
    else:
        if not printed and (e['kind'] != 'journal' or REG_ENTRY.search(e['raw'])):
            out.update(state='unresolved', unresolved_reason='nonjournal_no_id')
            return out
        st, via, rec = search()
        if st == 'incomplete':
            out.update(state='pending', unresolved_reason='search_incomplete')
            return out
        if st == 'found':
            out.update(source_via=via, source=rec, cmp=compare(rec, e))
    notes = []
    if out['source'] is not None and out['cmp']['yok'] is False:
        cv = cochrane_version(e, out['source'])
        if cv:
            out.update(source=cv, cmp=compare(cv, e), source_via=out['source_via'] + '+cochrane_version')
            notes.append('cochrane_version')
        elif not anchors and not journal_agrees(out['source'], e) and not locator_agrees(out['source'], e):
            # found by search or hint, other year, other journal: a report, abstract, preprint or reprint of the
            # same title, not the item cited (field QC 09/10: PROSPERO/report/reprint matched to an article)
            out.update(source=None, cmp=None, source_via='')
            notes.append('search_hit_other_version')
    anchors = anchors + hint_anchors
    out['anchors'] = anchors
    src_doi = ((out['source'] or {}).get('doi') or '').lower()
    out['hint_note'] = ' | '.join(
        f"{x['type']}:{x['id']}=" + ('other_work' if x['status'] == 'record' and other_work(x['rec'], x['cmp']) else
                                     'agrees' if x in hint_anchors or (x['cmp'] and identifies(x['cmp']))
                                     or (x['type'] == 'doi' and x['id'].lower() == src_doi)
                                     else x['status'] if x['status'] != 'record' else 'not_confirmed')
        for x in hints)
    same_work = {(r.get('doi') or '').lower() for r in [out['source']] + [x['rec'] for x in anchors] if r} - {''}
    contra = [x for x in printed if x['status'] == 'record' and other_work(x['rec'], x['cmp'], e)
              and (x['rec'].get('doi') or '').lower() not in same_work
              and not session_of_cited_abstract(x['rec'], x['cmp'], e)       # v2.4 r3 (QC r2 cor040)
              and not item_in_cited_container(x['rec'], x['cmp'], e)
              and not preprint_version(x, e)]                                 # investigator 12/10: preprint = same work
    if (out['source'] is not None and anchors and out['cmp']['yok'] is False and out['source_via'] in ('doi', 'pmid')
            and not any(x['cmp'] and x['cmp']['yok'] is True for x in ids)
            and (e['kind'] != 'journal' or REG_ENTRY.search(e['raw']))
            and not (BOOKISH.search(e['raw']) and not REG_ENTRY.search(e['raw']))   # v2.4 r3: a book's editions
            and not any(preprint_version(x, e) for x in anchors)):                  # investigator 12/10: preprint = same work
        # a registration or report cited with the identifier of the later journal article (field QC 09/10)
        a = next(x for x in anchors if x['rec'] is out['source'])
        out[f"{a['type']}_wrong"] = 1
        out.update(state='corrupted', id_points_to_other_work=0)
        notes.append('id_points_to_other_version')
        out['id_note'] = ';'.join(notes)
        return out
    out['id_note'] = ';'.join(notes)
    if out['source'] is None:
        if contra:
            out.update(state='corrupted', id_points_to_other_work=1)
            for t in ('doi', 'pmid'):
                if any(x['type'] == t for x in contra):
                    out[f'{t}_wrong'] = 1
        elif any(x['status'] == 'unknown' for x in printed):
            out.update(state='pending', unresolved_reason='lookup_incomplete')
        else:
            out.update(state='unresolved', unresolved_reason='id_unverifiable' if printed else 'not_found')
        return out

    c = out['cmp']
    for t in ('doi', 'pmid'):
        mine = [x for x in printed if x['type'] == t]
        if not mine:
            continue
        wrong = False
        for x in mine:
            if x['status'] == 'record' and judgeable(x['cmp']):
                # another work, or another version of it; a PubMed and a Crossref record of one DOI are the same work
                # even when their dates differ
                same_doi = bool(src_doi) and (x['rec'].get('doi') or '').lower() == src_doi
                inside = item_in_cited_container(x['rec'], x['cmp'], e) or session_of_cited_abstract(x['rec'], x['cmp'], e)
                if inside:
                    notes.append('doi_item_in_cited_container')
                pv = preprint_version(x, e)
                if pv:
                    notes.append('preprint_version')         # investigator 12/10: preprint and article are one work
                wrong |= not (anchors_source(x, e) or same_doi or inside or pv)
                if 'cochrane_version' in notes and COCHRANE.match(x['id'] or '') and x['id'].lower() != src_doi:
                    wrong = True       # v2.4 r3: DOI of another Cochrane version than the one cited (investigator 06/10; QC r2 val048)
            elif x['status'] == 'not_registered':
                if t == 'doi' and src_doi and x['id'].lower() == src_doi:
                    notes.append('doi_unregistered_official')    # the journal's own DOI, never registered (investigator 09/10)
                elif t == 'doi' and doi_slip(x['id'], e['raw'], src_doi, {d.lower() for d in e['dois']}):
                    notes.append('doi_format_slip')
                else:
                    wrong = True
            elif x['status'] == 'no_metadata' and t == 'doi' and src_doi:
                # v2.4: a registered DOI whose metadata cannot be read is wrong only when the source found is in the
                # printed journal; otherwise the search may have found another publication of the work (QC #73, #96)
                if x['id'].lower() != src_doi and journal_agrees(out['source'] or {}, e):
                    wrong = True
                elif x['id'].lower() != src_doi:
                    notes.append('doi_no_metadata_unverifiable')
        out[f'{t}_wrong'] = int(wrong)
    out['id_points_to_other_work'] = int(bool(contra))
    # Several records of the same work (PubMed, Crossref) can differ; a field is wrong only if none of them agrees.
    cs = [c] + [x['cmp'] for x in anchors if x['cmp'] is not c]
    translated = any(clean_title((x['rec'] or {}).get('title', '')).strip().startswith('[') for x in ids)
    tc = [] if translated else [x for x in cs if x['title_cmp']]       # non-English original: translations vary
    out['title_wrong'] = int(all(x['ts'] < config.TITLE_SAME for x in tc)) if tc else ''
    if out['title_wrong'] == 1 and any(main_title((r or {}).get('title', ''), e)
                                       for r in [out['source']] + [x['rec'] for x in anchors]):
        out['title_wrong'] = 0                           # subtitle omitted, nothing else differs
    ac = [x['au'] for x in cs if x['au'] is True or (x['au'] is False and x.get('au_refutes', True))]
    out['author_wrong'] = int(not any(ac)) if ac else ''
    yc = [x['yok'] for x in cs if x['yok'] is not None]
    out['year_wrong'] = int(not any(yc)) if yc else ''
    src = out['source'] or {}
    if out['year_wrong'] == 1 and not any(out[k] == 1 for k in ('doi_wrong', 'pmid_wrong')) and \
            BOOKISH.search(e['raw']) and not REG_ENTRY.search(e['raw']) \
            and not PREPRINT.search(e['raw']) and not COCHRANE.match(src.get('doi') or ''):
        # v2.4 r3: a book, manual or chapter exists in editions, printings and online re-issues with their own years
        # (QC r2 cor025 JBI manual 2017 vs 2020 edition; cor055 NYU Press); its year is not judged
        out['year_wrong'] = ''
        notes.append('book_year_not_compared')
    if out['year_wrong'] == 1:
        pre_src = (src.get('doi') or '').startswith(NO_TITLE_PREFIX)
        if bool(PREPRINT.search(e['raw'])) != pre_src or EDITION.search(src.get('title', '')):
            out['year_wrong'] = ''                       # another version of the work: preprint/article, edition
            notes.append('version_year_not_compared')
    out['id_note'] = ';'.join(notes)
    out['state'] = 'corrupted' if any(out[k] == 1 for k in FLAGS) else 'valid'
    return out


# ---------------------------------------------------------------- the original publication
# Registries are resolvers; when one disagrees with the citation, the original publication decides (investigator, 05/10/2026):
# the source's own full text in PMC, else the citation_* metadata of the publisher's article page. Fetched only for
# references with a wrong title, author or year, cached like every other request.
BROWSER = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'


def pmc_original(pmcid):
    from lxml import etree
    v = cached_get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?' +
                   urllib.parse.urlencode({'db': 'pmc', 'id': pmcid.replace('PMC', '')}), attempts=2)
    if not v:
        return None
    try:
        root = etree.fromstring(v.encode(), etree.XMLParser(recover=True, huge_tree=True))
    except (etree.XMLSyntaxError, ValueError):
        return None
    meta = root.find('.//article-meta')
    if meta is None:
        return None
    names = meta.xpath('.//contrib[@contrib-type="author"]/name')
    groups = meta.xpath('.//contrib[@contrib-type="author"]/collab')
    fam = names[0].findtext('surname') or '' if names else ''
    giv = names[0].findtext('given-names') or '' if names else ''
    title = ' '.join(''.join(t.itertext()) for t in meta.xpath('.//title-group/article-title')).strip()
    years = {int(y) for y in meta.xpath('.//pub-date/year/text()') if y.strip().isdigit()}
    return {'via': 'pmc_fulltext', 'first_author': fam, 'first_given': giv, 'title': title, 'years': years,
            'authors': [n.findtext('surname') or '' for n in names], 'group_author': not names and bool(groups)}


def publisher_original(doi):
    v = cached_get('https://doi.org/' + urllib.parse.quote(doi, safe='/') + '#citation_meta',
                   headers={'User-Agent': BROWSER}, attempts=1, timeout=20)
    if not v:
        return None
    meta = {}
    for m in re.finditer(r'<meta\s+[^>]*>', v, re.I):
        tag = m.group(0)
        n = re.search(r'name="([^"]+)"', tag, re.I)
        c = re.search(r'content="([^"]*)"', tag, re.I)
        if n and c:
            meta.setdefault(n.group(1).lower(), []).append(html.unescape(c.group(1)).strip())
    authors = meta.get('citation_author') or meta.get('dc.creator') or []
    if not authors:
        return None
    a = authors[0]
    fam, giv = ([x.strip() for x in a.split(',', 1)] if ',' in a else (a.split()[-1], ' '.join(a.split()[:-1])))
    years = {int(y) for k in ('citation_publication_date', 'citation_date', 'citation_online_date', 'dc.date')
             for x in meta.get(k, []) for y in re.findall(r'(?:19|20)\d\d', x)[:1]}
    return {'via': 'publisher_page', 'first_author': fam, 'first_given': giv, 'authors': [x.split(',')[0] for x in authors],
            'title': (meta.get('citation_title') or meta.get('dc.title') or [''])[0], 'years': years,
            'group_author': False}


def pmcid_of(doi):
    v = cached_get('https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?' +
                   urllib.parse.urlencode({'ids': doi, 'format': 'json', 'tool': 'm11', 'email': RI.MAILTO}), attempts=2)
    try:
        return next((r.get('pmcid', '') for r in json.loads(v or '{}').get('records', [])), '')
    except ValueError:
        return ''


def verify_original(d, e):
    """A wrong title, author or year is checked against the original publication; the original decides that field.
    d['verified_by'] says what decided: pmc_fulltext / publisher_page / registry (original not reachable)."""
    fields = [k for k in ('title_wrong', 'author_wrong', 'year_wrong') if d[k] == 1]
    if not fields or not d['source']:
        return d
    src = d['source']
    recs = [src] + [x['rec'] for x in d['anchors']]
    doi = next((r.get('doi') for r in recs if r.get('doi')), '')
    pmcid = next((r.get('pmcid') for r in recs if r.get('pmcid')), '') or (pmcid_of(doi) if doi else '')
    orig = (pmc_original(pmcid) if pmcid else None) or (publisher_original(doi) if doi else None)
    d['verified_by'] = orig['via'] if orig else 'registry'
    d['original'] = orig or {}
    if not orig:
        return d
    c = compare(orig, e)
    agrees = {'title_wrong': c['title_cmp'] and c['ts'] is not None and c['ts'] >= config.TITLE_SAME,
              'author_wrong': c['au'] is True, 'year_wrong': c['yok'] is True}
    for k in fields:
        if agrees[k]:
            d[k] = 0
    d['state'] = 'corrupted' if any(d[k] == 1 for k in FLAGS) else 'valid'
    return d


# ---------------------------------------------------------------- run
def parse_multi(v):
    """Read step-02 list fields (JSON; semicolon fallback for older files)."""
    if not v:
        return []
    if v.lstrip().startswith('['):
        try:
            return [str(x) for x in json.loads(v)]
        except (ValueError, TypeError):
            pass
    return [x for x in v.split(';') if x]


MULTI_WORK = re.compile(r'^\W*\d*\W*a\)\s.+?[;.,]\s*b\)\s', re.S)      # "a) Ulbrich ...; b) Ulbrich ...; c) ..."
LEGAL_DEPOSIT = re.compile(r'\bD/\d{4}/[\d./]+')                        # Belgian legal deposit "D/2009/10.273/29"


def drop_prefixes(ds):
    """v2.4 r3: "doi:10.3389/fpsyg. 2021.773049" yields "10.3389/fpsyg" next to the full DOI; only the full one is a claim."""
    low = [d.lower() for d in ds]
    return [d for d, l in zip(ds, low) if not any(o != l and o.startswith(l) for o in low)]


def entry(r):
    """raw = the citation as printed (step 02 removed hint identifiers), so nothing PMC added can agree or disagree."""
    raw = r['citation_printed'] or ''
    deposit = ''.join(LEGAL_DEPOSIT.findall(raw)).lower()
    urls = [u.lower() for u in re.findall(r'https?://\S+', raw) if not re.match(r'https?://(?:dx\.)?doi\.org/', u, re.I)]
    bare = re.sub(r'https?://\S+', ' ', raw).lower()
    # v2.4: not a DOI claim: a legal deposit number (QC cor020); a DOI-like string seen only inside a publisher/
    # preprint-server URL ("search.informit.org/doi/abs/10.3316/...", investigator 06/10 holdout cor091)
    keep = lambda d: not (deposit and d.lower() in deposit) and not (any(d.lower() in u for u in urls) and d.lower() not in bare)
    return {'raw': raw, 'multi_work': bool(MULTI_WORK.match(raw)), 'title': r['cited_title'], 'title_src': r['title_source'],
            'first_author': r['cited_first_author'], 'kind': r['kind'],
            'years': [int(y) for y in r['cited_years'].split(';') if y],
            # the citation's own identifiers: printed, or supplied in the article's reference data (step 02)
            'dois': drop_prefixes([d for d in parse_multi(r['printed_dois']) + parse_multi(r['supplied_dois']) if keep(d)]),
            'pmids': parse_multi(r['printed_pmids']) + parse_multi(r['supplied_pmids']),
            'hint_dois': parse_multi(r['jats_hint_dois']), 'hint_pmids': parse_multi(r['jats_hint_pmids'])}


def lookup_ids(e, pmx, crx):
    ids = []
    for printed, pmids, dois in ((True, e['pmids'], e['dois']), (False, e['hint_pmids'], e['hint_dois'])):
        for p in pmids:
            if p in pmx:
                ids.append({'type': 'pmid', 'id': p, 'printed': printed, 'status': 'record', 'rec': {**pmx[p], 'id': p}})
            else:
                ids.append({'type': 'pmid', 'id': p, 'printed': printed, 'status': 'not_registered', 'rec': None})
        for d in dois:
            if d in crx:
                ids.append({'type': 'doi', 'id': d, 'printed': printed, 'status': 'record', 'rec': {**crx[d], 'doi': d}})
                continue
            st = doi_status(d)
            rec = csl_record(d) if st == 'registered' else None
            if rec and (rec['title'] or rec['first_author']):
                ids.append({'type': 'doi', 'id': d, 'printed': printed, 'status': 'record', 'rec': rec})
            else:
                ids.append({'type': 'doi', 'id': d, 'printed': printed, 'rec': None,
                            'status': 'no_metadata' if st == 'registered' else st})
    for x in ids:
        x['cmp'] = compare(x['rec'], e) if x['rec'] else None
    return ids


def fmt_ids(ids):
    out = []
    for x in ids:
        s = f"{'' if x['printed'] else 'hint:'}{x['type']}:{x['id']}={x['status']}"
        if x['cmp']:
            c = x['cmp']
            t = 'NA' if c['ts'] is None else round(c['ts'], 2)
            s += f"(t={t}{'' if c['title_cmp'] else '*'},a={b(c['au'])},y={b(c['yok'])})"
        out.append(s)
    return ' | '.join(out)


def pairs(r, d):
    """Printed vs canonical values of one decided reference (see PAIRS)."""
    src, c = d['source'] or {}, d['cmp']
    recs = [src] + [x['rec'] for x in d['anchors'] if x['rec'] is not src]
    cmps = ([c] if c else []) + [x['cmp'] for x in d['anchors'] if x['cmp'] is not c]
    canon_years = sorted({y for x in cmps for y in x['years_source']})
    diffs = [x['ydiff'] for x in cmps if x['ydiff'] is not None]
    via_pmid = d['source_via'] in ('pmid', 'pubmed_search', 'jats_hint_pmid')
    canon_pmid = src.get('id', '') if via_pmid else next((x['id'] for x in d['anchors'] if x['type'] == 'pmid'), '')
    canon_doi = next((x.get('doi', '') for x in recs if x.get('doi')), '')
    match = lambda k: '' if d[k] == '' else 1 - d[k]
    return {'printed_first_author': r['cited_first_author'], 'canonical_first_author': src.get('first_author', ''),
            'author_match': match('author_wrong'),
            'printed_years': ';'.join(map(str, c['years_cited'])) if c else r['cited_years'],
            'canonical_years': ';'.join(map(str, canon_years)), 'year_match': match('year_wrong'),
            'year_diff': min(diffs) if diffs else '',
            'printed_title': r['cited_title'], 'canonical_title': src.get('title', ''),
            'title_score': '' if not c or c['ts'] is None else round(c['ts'], 3), 'title_match': match('title_wrong'),
            'printed_dois': r['printed_dois'], 'supplied_dois': r['supplied_dois'], 'canonical_doi': canon_doi,
            'doi_match': match('doi_wrong'), 'printed_pmids': r['printed_pmids'], 'supplied_pmids': r['supplied_pmids'],
            'canonical_pmid': canon_pmid, 'pmid_match': match('pmid_wrong')}


def main(keys=None, out_path=None):
    """keys: optional set of (review_id, ref_rid) to run only those references (regression harness);
    out_path: write there instead of outputs/s03_identity.csv (no step log for a subset run)."""
    log = StepLog('s03', __file__) if keys is None else None
    refs = read_csv(config.OUT / 's02_references.csv')
    if keys is not None:
        refs = [r for r in refs if (r['review_id'], r['ref_rid']) in keys]
    E = [entry(r) for r in refs]

    # Registry lookups batched per review (as the 01/10 batch did, so they come from its cache).
    ids_all, cur = [], None
    for r, e in zip(refs, E):
        if r['review_id'] != cur:
            cur = r['review_id']
            group = [x for y, x in zip(refs, E) if y['review_id'] == cur]
            crx = crossref_batch([d for x in group for d in x['dois'] + x['hint_dois']])
            pmx = pm_summary([p for x in group for p in x['pmids'] + x['hint_pmids']])
        ids_all.append(lookup_ids(e, pmx, crx))
    print('id lookups done', flush=True)

    # Pre-run the Crossref -> PubMed search (4 workers) for every reference that will need a search.
    need = [i for i, (e, ids) in enumerate(zip(E, ids_all))
            if not any(anchors_source(x, E[i]) for x in ids if x['printed'])
            and not any(x['status'] == 'record' and accept_hit(x['rec'], e) for x in ids if not x['printed'])
            and (any(x['printed'] for x in ids) or e['kind'] == 'journal')]
    print(f'references needing a search: {len(need)}', flush=True)
    with ThreadPoolExecutor(max_workers=4) as ex:
        pre = dict(zip(need, ex.map(lambda i: search_crossref_pubmed(E[i]), need)))
    print('crossref/pubmed search done', flush=True)

    out = []
    for i, (r, e, ids) in enumerate(zip(refs, E, ids_all)):
        d = verify_original(decide(ids, e, lambda: search_all(e, pre.get(i))), e)
        out.append({**{k: r[k] for k in ('review_id', 'ref_index', 'ref_rid', 'ref_label', 'kind', 'jats_hint_dois',
                                         'jats_hint_pmids', 'id_provenance', 'citation_printed', 'citation_raw')},
                    **{k: d[k] for k in FLAGS}, 'state': d['state'], 'unresolved_reason': d['unresolved_reason'],
                    'source_via': d['source_via'], 'source_id': (d['source'] or {}).get('id', ''),
                    'source_journal': (d['source'] or {}).get('journal', ''), 'hint_note': d['hint_note'],
                    'verified_by': d['verified_by'], 'original_first_author': d['original'].get('first_author', ''),
                    'original_years': ';'.join(map(str, sorted(d['original'].get('years') or []))),
                    'original_title': d['original'].get('title', ''),
                    **pairs(r, d), 'id_checks': fmt_ids(ids), 'id_note': d.get('id_note', '')})
    write_csv(out_path or config.OUT / 's03_identity.csv', out, FIELDS)
    if log is None:
        return out
    cor = [r for r in out if r['state'] == 'corrupted']
    log.done(rows_in=len(refs), rows_out=len(out), finished=not any(r['state'] == 'pending' for r in out),
             openalex_calls=OPENALEX['used'], openalex_blocked=OPENALEX['blocked'],
             state=dict(Counter(r['state'] for r in out)),
             unresolved_reason=dict(Counter(r['unresolved_reason'] for r in out if r['unresolved_reason'])),
             source_via=dict(Counter(r['source_via'] for r in out if r['source_via'])),
             corrupted_fields={k: sum(1 for r in cor if r[k] == 1) for k in FLAGS})


if __name__ == '__main__':
    main()
