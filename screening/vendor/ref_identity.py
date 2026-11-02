#!/usr/bin/env python3
"""M11 reference-identity instrument (JATS ref-list).

Every reference gets exactly one status. "Fabricated" is NEVER assigned by code; the code only
produces the four evidence classes the validation report requires, and a human confirms.

  verified_id            printed/linked DOI or PMID resolves to a record matching the entry
  verified_search        no usable ID; Crossref/PubMed search finds a record matching the entry
  doi_other_ra           DOI registered at doi.org but not Crossref (DataCite, mEDRA, JaLC...); metadata not checked
  id_partial             ID resolves but agreement is partial (e.g. title matches, year off by >1)
  id_metadata_mismatch   ID resolves to a DIFFERENT work (title disagrees and author/year disagree)
  doi_not_registered     DOI string not registered anywhere (doi.org handle API) and no valid PMID
  pmid_invalid           PMID absent from PubMed and no valid DOI
  unresolved             journal-type entry, no ID, no search match
  not_assessed_nonjournal  book/web/guideline/other entry without ID (no deterministic registry)

Caveat to report: DOIs/PMIDs in JATS are often ADDED by the publisher through reference matching,
so an ID-linked entry is partly pre-validated by production; the discriminating classes are the
ID-failure classes and `unresolved`.
"""
import html, json, os, re, sqlite3, subprocess, threading, time, unicodedata, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from lxml import etree

HERE = Path(__file__).resolve().parent
MAILTO = os.environ.get('RIS_MAILTO', 'name@example.org')   # contact for Crossref/NCBI polite use
UA = f'reference-integrity-screening/0.2 (mailto:{MAILTO})'
STOP = set('with from that this versus between after before during into their than were which have '
           'been these those among other about using based study studies patients patient report case '
           'analysis results outcomes outcome review systematic meta clinical'.split())

# ---------------- HTTP with persistent cache + per-host pacing ----------------
_DB = sqlite3.connect('file:' + str(HERE.parent.parent / 'code/cache/m11_http.sqlite') + '?mode=ro&immutable=1', uri=True, check_same_thread=False)
# v36: frozen cache is read-only; cache misses are handled by the offline adapter.
_DBL = threading.Lock()
_PACE = {'eutils.ncbi.nlm.nih.gov': 0.36, 'api.crossref.org': 0.12, 'doi.org': 0.1}
_LAST = {}
_PL = threading.Lock()

def _pace(url):
    host = urllib.parse.urlparse(url).netloc
    gap = _PACE.get(host, 0.2)
    with _PL:
        now = time.time()
        wait = _LAST.get(host, 0) + gap - now
        _LAST[host] = max(now, _LAST.get(host, 0) + gap)
    if wait > 0:
        time.sleep(wait)

def get(url):
    with _DBL:
        row = _DB.execute('select body from c where url=?', (url,)).fetchone()
    if row:
        return row[0]
    body = None
    for attempt in range(4):
        _pace(url)
        p = subprocess.run(['curl', '-s', '-L', '-m', '40', '-A', UA, '-w', '\n%{http_code}', url],
                           capture_output=True, text=True)
        out, _, code = p.stdout.rpartition('\n')
        if code == '200':
            body = out; break
        if code == '404':
            body = '__404__'; break
        time.sleep(2 + 3 * attempt)          # 429/5xx/timeouts: back off, do not cache
    if body is None:
        return ''
    with _DBL:
        _DB.execute('insert or replace into c values (?,?)', (url, body)); _DB.commit()
    return body

def jget(url):
    b = get(url)
    try:
        return json.loads(b)
    except Exception:
        return {}

# ---------------- text helpers ----------------
def strip_acc(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s or '') if not unicodedata.combining(c))

def norm(s):
    s = strip_acc(s).lower()
    s = re.sub(r'([a-z])-\s+([a-z])', r'\1\2', s)
    return ' ' + re.sub(r'[^a-z0-9]+', ' ', s) + ' '

def content_words(t):
    return [w for w in norm(t).split() if len(w) >= 4 and w not in STOP and not w.isdigit()]

def title_score(cand_title, raw):
    """Share of the candidate record's title content-words found in the printed entry."""
    w = content_words(cand_title)
    if not w:
        return None
    r = norm(raw); r0 = r.replace(' ', '')
    return sum(1 for x in w if f' {x} ' in r or x in r0) / len(w)

def skey(s):
    return re.sub(r'[^a-z]', '', strip_acc(s).lower())

def clean_doi(d):
    d = html.unescape(html.unescape((d or '').strip()))
    d = re.sub(r'^(?:https?://)?(?:dx\.)?doi\.org/', '', d, flags=re.I)
    d = re.sub(r'^doi:\s*', '', d, flags=re.I)
    d = d.rstrip('.,;:)]}>\'"')
    if d.count('(') < d.count(')'):
        d = d.rstrip(')')
    return d.lower() if re.match(r'10\.\d{4,9}/\S+', d) else ''

DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>]+', re.I)
PMID_RE = re.compile(r'PMID\s*:?\s*(\d{6,9})', re.I)
CJK = re.compile(r'[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]')
NONWEST = re.compile(r'\b(?:Shenzhen|Zhejiang|Beijing|Shanghai|Guangdong|Sichuan|Jiangsu|Hunan|Hubei|Shandong|Zhonghua|Chin(?:ese)? J|Zhongguo|J Chin|CJPS|Nihon|Rinsho|Vopr|Khirurgiia|Rev Bras|Arq Bras|Acta Cir Bras)\b|\(in (?:Chinese|Japanese|Korean|Russian|Portuguese|Spanish|French|German|Turkish|Persian)\)', re.I)
YEAR_RE = re.compile(r'(?<!\d)(1[89]\d{2}|20[0-3]\d)(?!\d)')

# ---------------- parse one JATS <ref> ----------------
NONJOURNAL_HINT = re.compile(r'(?:\b[A-Z][a-z]+(?:,? [A-Z]{2,})?: [A-Z][^.;:]{2,60}[;,] (?:19|20)\d\d\b)|\b(dispon[ií]vel|disponible|verf[uü]gbar|diss abstr|unpublished|available (?:from|at|online)|accessed|retrieved|https?://|www\.|press|publisher|'
                             r'edition|ed\.|eds\.|in:|guideline|handbook|textbook|world health organization|'
                             r'who\b|report no|thesis|dissertation|conference|proceedings|abstract book)', re.I)
JOURNAL_SHAPE = re.compile(r'(\d+\s*\(\s*[\dS-]+\s*\)|;\s*\d+|\b\d{1,4}\s*:\s*[eE]?\d+|\bvol\.?\s*\d+|\be\d{4,})')

def gs_param(ref, key):
    for el in ref.xpath('.//ext-link[@ext-link-type="google-scholar"]'):
        href = el.get('{http://www.w3.org/1999/xlink}href') or ''
        vals = urllib.parse.parse_qs(href, keep_blank_values=False).get(key)
        if vals:
            return vals
    return []

NAMEISH = re.compile(r"^(?:[A-Z][A-Za-z'’\-]+|[A-Z]{1,3}|[A-Z]\.?(?:-?[A-Z]\.?){0,3}|van|von|de|der|den|del|da|di|le|la|et|al|and|&)$")

def guess_title(raw):
    """Linear-time title guess (no nested quantifiers: an earlier regex backtracked catastrophically
    on long unpunctuated author lists)."""
    s = re.sub(r'^\s*\[?\d{1,3}[.\])]?\s+', '', raw)[:600]
    for pat in (r'\((?:19|20)\d\d[a-z]?\)\.?\s*([^.?!]{10,300})[.?!]', r'\bet al\.?,?\s*([^.?!]{10,300})[.?!]'):
        m = re.search(pat, s)
        if m and len(re.findall(r'[A-Za-z]{3,}', m.group(1))) >= 3:   # not "16(2):179–83"
            return m.group(1)
    for seg in re.split(r'(?<=[.?!])\s+', s)[:6]:
        toks = [t.strip(',;:') for t in seg.split()]
        toks = [t for t in toks if t]
        if len(toks) < 4:
            continue
        if sum(bool(NAMEISH.match(t)) for t in toks) / len(toks) >= 0.8:
            continue                               # author block
        return seg.rstrip('.?! ')
    return ''

def parse_ref(ref, idx):
    raw = ' '.join(' '.join(ref.itertext()).split())
    lab = ref.find('./label')
    label = ' '.join(''.join(lab.itertext()).split()) if lab is not None else str(idx)
    cit = None
    for tag in ('element-citation', 'mixed-citation', 'citation', 'nlm-citation'):
        cit = ref.find(f'.//{tag}')
        if cit is not None: break
    ptype = (cit.get('publication-type') or cit.get('citation-type') or '') if cit is not None else ''
    title = ' '.join(''.join(t.itertext()) for t in ref.xpath('.//article-title')).strip()
    if not title:
        title = (gs_param(ref, 'title') or [''])[0]
    title_src = 'structured' if title else 'guess'
    if not title:
        title = guess_title(raw)
    surn = ref.xpath('.//person-group[@person-group-type="author"]//surname/text()') or ref.xpath('.//surname/text()')
    first_author = surn[0] if surn else ''
    if not first_author:
        a = gs_param(ref, 'author')
        first_author = a[0].split()[-1] if a else ''
    if not first_author:
        m = re.match(r"^\s*(?:\[?\d{1,3}[.\])]?\s+)?([A-Z][A-Za-z'’\-]+(?:\s+[a-z]{1,3}\s+[A-Z][A-Za-z'’\-]+)?)", raw)
        first_author = m.group(1) if m else ''
    yrs = [int(y) for y in ref.xpath('.//year/text()') if re.fullmatch(r'\d{4}', (y or '').strip())]
    if not yrs:
        yrs = [int(y) for y in YEAR_RE.findall(raw)]
    dois = []
    for x in ref.xpath('.//pub-id[@pub-id-type="doi"]/text()'):
        dois.append(clean_doi(x))
    for el in ref.xpath('.//ext-link[@ext-link-type="doi"]'):
        dois.append(clean_doi(el.get('{http://www.w3.org/1999/xlink}href') or ''.join(el.itertext())))
    dois = [d for d in dict.fromkeys(dois) if d]
    if not dois:
        # Free-text DOIs only when the XML carries no structured DOI: Frontiers citation-strings glue the
        # PMID onto the DOI text ("10.1108/02683940710733115" + "31861812") while the ext-link is correct.
        dois = [d for d in dict.fromkeys(clean_doi(x) for x in DOI_RE.findall(raw)) if d]
        dois = [d for d in dois if not any(o != d and o.startswith(d) for o in dois)]
    pmids = list(ref.xpath('.//pub-id[@pub-id-type="pmid"]/text()'))
    for el in ref.xpath('.//ext-link[@ext-link-type="pmid"]'):
        pmids.append(el.get('{http://www.w3.org/1999/xlink}href') or ''.join(el.itertext()))
    pmids += PMID_RE.findall(raw)
    pmids = list(dict.fromkeys(p.strip() for p in pmids if re.fullmatch(r'\s*\d{1,9}\s*', p or '')))
    if ptype:
        kind = 'journal' if ptype.lower() in ('journal', 'article', 'journal-article') else 'nonjournal'
    else:
        kind = 'journal' if (JOURNAL_SHAPE.search(raw) and not NONJOURNAL_HINT.search(raw)) else 'nonjournal'
    return {'label': label, 'raw': raw, 'ptype': ptype, 'kind': kind, 'title': title, 'title_src': title_src,
            'first_author': first_author, 'years': sorted(set(yrs)), 'dois': dois, 'pmids': pmids}

# ---------------- registry lookups ----------------
def cr_rec(m):
    yr = ''
    for k in ('published-print', 'published-online', 'issued', 'created'):
        dp = (m.get(k) or {}).get('date-parts', [[None]])
        if dp and dp[0] and dp[0][0]:
            yr = str(dp[0][0]); break
    au = (m.get('author') or [{}])[0]
    return {'id': (m.get('DOI') or '').lower(), 'title': (m.get('title') or [''])[0],
            'journal': (m.get('container-title') or [''])[0], 'year': yr,
            'first_author': au.get('family', '') or au.get('name', '')}

def crossref_batch(dois):
    out = {}
    dois = sorted(set(dois))
    for i in range(0, len(dois), 40):
        chunk = [d for d in dois[i:i + 40] if ',' not in d]
        if not chunk:
            continue
        url = 'https://api.crossref.org/works?' + urllib.parse.urlencode({
            'filter': ','.join('doi:' + d for d in chunk), 'rows': 100, 'mailto': MAILTO,
            'select': 'DOI,title,author,issued,published-print,published-online,created,container-title'})
        d = jget(url)
        for it in (d.get('message') or {}).get('items', []) if isinstance(d, dict) else []:
            r = cr_rec(it); out[r['id']] = r
    for d in dois:                              # DOIs with commas or dropped by the filter: single lookup
        if d not in out and ',' in d:
            j = jget('https://api.crossref.org/works/' + urllib.parse.quote(d, safe='/') + '?mailto=' + MAILTO)
            if isinstance(j, dict) and j.get('message'):
                out[d] = cr_rec(j['message'])
    return out

def doi_registered(d):
    j = jget('https://doi.org/api/handles/' + urllib.parse.quote(d, safe='/'))
    return j.get('responseCode') == 1

def pm_summary(pmids):
    out = {}
    pmids = sorted(set(pmids))
    for i in range(0, len(pmids), 150):
        ids = pmids[i:i + 150]
        d = jget('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?' +
                 urllib.parse.urlencode({'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'json'}))
        res = d.get('result', {}) if isinstance(d, dict) else {}
        for p in ids:
            r = res.get(p)
            if not r or r.get('error'):
                continue
            out[p] = {'id': p, 'title': r.get('title', ''), 'journal': r.get('fulljournalname', ''),
                      'year': (re.findall(r'\d{4}', r.get('pubdate', '')) or [''])[0],
                      'first_author': ((r.get('authors') or [{'name': ''}])[0].get('name', '') or '').split(' ')[0],
                      'doi': next((a['value'] for a in r.get('articleids', []) if a.get('idtype') == 'doi'), '')}
    return out

def pm_search(term, n=5):
    d = jget('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?' +
             urllib.parse.urlencode({'db': 'pubmed', 'term': term, 'retmax': n, 'retmode': 'json'}))
    return (d.get('esearchresult') or {}).get('idlist', []) if isinstance(d, dict) else []

# ---------------- agreement ----------------
def agree(rec, e):
    """Compare a registry record with a printed entry -> ('match'|'partial'|'mismatch', detail)."""
    ts = title_score(rec.get('title', ''), e['raw'])
    fa = skey(rec.get('first_author', ''))
    au = None
    if fa and len(fa) >= 2:
        au = fa in skey(e['raw'][:250]) or (bool(e['first_author']) and skey(e['first_author']) == fa)
    yok = None
    if rec.get('year') and e['years']:
        yok = any(abs(int(rec['year']) - y) <= 1 for y in e['years'])
    detail = f"title={'NA' if ts is None else f'{ts:.2f}'} author={au} year={yok}"
    printed_title = len(content_words(e['title'])) >= 3 and e['title'].lower()[:25] in e['raw'].lower()
    if ts is None or (not printed_title and e['title_src'] != 'structured' and ts < 0.3):
        # no title on one side (registry record without title, or chemistry/physics style entries
        # "Mater. Lett. 2016;184:197"): judge on author+year only; never call it a mismatch on that basis
        detail += ' [no-title rule]'
        return ('match' if au and yok else 'partial'), detail
    if ts >= 0.9 and au:                       # same work; printed year off (online vs print, typo)
        return 'match', detail
    if ts >= 0.6 and au is not False and yok is not False:
        return 'match', detail
    if ts >= 0.85 and yok is not False:        # near-exact title: author miss is usually particle/order parsing
        return 'match', detail
    if ts >= 0.6 or (ts >= 0.3 and (au or yok)) or (au and yok):
        return 'partial', detail
    return 'mismatch', detail

def search_match(e):
    """Deterministic search for an ID-less journal entry. A candidate must pass agree()=='match'
    with title evidence >= 0.7 so that a generic title cannot validate a nonexistent entry."""
    q = e['raw'][:300]
    d = jget('https://api.crossref.org/works?' + urllib.parse.urlencode(
        {'query.bibliographic': q, 'rows': 3, 'mailto': MAILTO,
         'select': 'DOI,title,author,issued,published-print,published-online,created,container-title'}))
    for it in (d.get('message') or {}).get('items', []) if isinstance(d, dict) else []:
        r = cr_rec(it)
        st, det = agree(r, e)
        ts = title_score(r['title'], e['raw'])
        if st == 'match' and ts is not None and ts >= 0.7:
            return 'crossref', r['id'], det
    tw = content_words(e['title'])[:5]
    if len(tw) >= 3 and e['first_author']:
        term = f"{skey(e['first_author'])}[1au] AND " + ' AND '.join(f'{w}[tiab]' for w in tw)
        for p, r in pm_summary(pm_search(term)).items():
            st, det = agree(r, e)
            ts = title_score(r['title'], e['raw'])
            if st == 'match' and ts is not None and ts >= 0.7:
                return 'pubmed', p, det
    return '', '', ''

# ---------------- classify ----------------
def classify(entries):
    """entries: list of parse_ref dicts (one article or many). Adds status/evidence in place."""
    crx = crossref_batch([d for e in entries for d in e['dois']])
    pmx = pm_summary([p for e in entries for p in e['pmids']])
    need_search = []
    for e in entries:
        ev = []
        best = None                           # best outcome over the entry's IDs
        rank = {'match': 3, 'partial': 2, 'other_ra': 2, 'mismatch': 1, 'not_found': 0}
        for d in e['dois']:
            if d in crx:
                st, det = agree(crx[d], e); ev.append(f'doi {d}: {st} ({det}; cr="{crx[d]["title"][:80]}")')
            elif doi_registered(d):
                st = 'other_ra'; ev.append(f'doi {d}: registered non-Crossref')
            else:
                st = 'not_found'; ev.append(f'doi {d}: NOT registered')
            best = st if best is None or rank[st] > rank[best] else best
        doi_best = best
        for p in e['pmids']:
            if p in pmx:
                st, det = agree(pmx[p], e); ev.append(f'pmid {p}: {st} ({det}; pm="{pmx[p]["title"][:80]}")')
            else:
                st = 'not_found'; ev.append(f'pmid {p}: not in PubMed')
            best = st if best is None or rank[st] > rank[best] else best
        if best == 'match':
            status = 'verified_id'
        elif best == 'other_ra':
            status = 'doi_other_ra'
        elif best == 'partial':
            status = 'id_partial'
        elif best == 'mismatch':
            status = 'id_metadata_mismatch'
        elif best == 'not_found':
            status = 'doi_not_registered' if doi_best == 'not_found' else 'pmid_invalid'
        else:
            status = None
        e['status'] = status
        e['evidence'] = ' | '.join(ev)
        e['id_conflict'] = best == 'match' and any((': mismatch' in x) or ('NOT registered' in x) or ('not in PubMed' in x) for x in ev)
        e['non_western_source'] = bool(CJK.search(e['raw']) or NONWEST.search(e['raw']))
        if status is None:
            if e['kind'] == 'journal':
                need_search.append(e)
            else:
                e['status'] = 'not_assessed_nonjournal'
    def _s(e):
        src, rid, det = search_match(e)
        if src:
            e['status'] = 'verified_search'; e['evidence'] = f'search {src} {rid}: match ({det})'
        else:
            e['status'] = 'unresolved'; e['evidence'] = 'crossref bibliographic + pubmed author/title: no match'
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(_s, need_search))
    return entries

def scan_refs(xml_path):
    root = etree.parse(str(xml_path), etree.XMLParser(recover=True, huge_tree=True)).getroot()
    refs = root.xpath('.//back//ref-list//ref') or root.xpath('.//ref-list//ref')
    return [parse_ref(r, i) for i, r in enumerate(refs, 1)]

STATUSES = ['verified_id', 'verified_search', 'doi_other_ra', 'id_partial', 'id_metadata_mismatch',
            'doi_not_registered', 'pmid_invalid', 'unresolved', 'not_assessed_nonjournal']

def summarize(entries):
    from collections import Counter
    c = Counter(e['status'] for e in entries)
    return {'refs_total': len(entries),
            'refs_journal': sum(e['kind'] == 'journal' for e in entries),
            'refs_with_id': sum(bool(e['dois'] or e['pmids']) for e in entries),
            **{f'ref_{s}': c.get(s, 0) for s in STATUSES},
            'invalid_doi_n': c.get('doi_not_registered', 0),
            'metadata_mismatch_reference_n': c.get('id_metadata_mismatch', 0),
            'unresolved_reference_n': c.get('unresolved', 0)}

if __name__ == '__main__':
    import sys, csv
    w = None
    for p in sys.argv[1:]:
        es = classify(scan_refs(p))
        for e in es:
            row = {'file': Path(p).name, **{k: (';'.join(map(str, v)) if isinstance(v, list) else v) for k, v in e.items()}}
            if w is None:
                w = csv.DictWriter(sys.stdout, fieldnames=list(row)); w.writeheader()
            w.writerow(row)
