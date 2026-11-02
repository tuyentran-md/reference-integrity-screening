"""S03-v37: sensitivity-first ("bắt chặt") screen. Owner decision 27/10/2026: any wrong or missing
element is an error; over-flagging is acceptable because a human verifies every alert.

Starts from frozen v36c (v35 states, v36 identifier rules, v36c author comparison) and ADDS strict
checks on every resolved reference. It never removes an alert.
  STRICT_AUTHOR     every printed author must match a registry author with the same initials
                    (an omitted middle initial is an error); typography (case, diacritics,
                    punctuation, spacing) is still tolerated.
  TITLE_MISMATCH    printed title must equal a registry title after typography only.
  YEAR_MISMATCH     a printed year must be one of the registry years (print, online, issue).
  VOLUME_MISMATCH   registry volume must appear in the printed citation (when the registry has one).
  PAGE_MISMATCH     registry first page or article number must appear (when the registry has one).
  JOURNAL_MISMATCH  only a clearly different journal: no distinctive word of the registry journal
                    name appears even in abbreviated form (J Pediatr Surg vs N Engl J Med).
                    Abbreviation differences (Journal/J, Pediatric/Ped/P) are never errors.
Unresolved references stay unresolved (reported separately)."""
import html
import re
import unicodedata
import v36c.rules as C

RULES = ('STRICT_AUTHOR', 'TITLE_MISMATCH', 'YEAR_MISMATCH', 'VOLUME_MISMATCH', 'PAGE_MISMATCH', 'JOURNAL_MISMATCH')
GENERIC = set('''journal journals international research review reviews clinical medicine medical science sciences
annals archives american british european society official proceedings bulletin letters reports report advances
current frontiers open acta revista new world global the of and in for on de la le et der und part series section
transactions quarterly communications studies academy association college national'''.split())


def fold(s):
    s = unicodedata.normalize('NFKD', s or '')
    return ''.join(c for c in s if not unicodedata.combining(c)).casefold()


def norm_title(s):
    s = html.unescape(re.sub(r'<[^>]+>', '', s or ''))
    s = s.strip().strip('[]').strip()
    return re.sub(r'[^a-z0-9]', '', fold(s).replace('&', 'and'))


def strict_initials(pg, qg):
    """Absent given names cannot refute; otherwise the initials must be identical."""
    if not pg or not qg:
        return True
    if not ({C.initials(pg), C.initials(pg, True)} & {C.initials(qg), C.initials(qg, True)}):
        return False
    full_p = [w for w in C.words(pg) if len(w) > 1 and not w.isupper()]
    full_q = [w for w in C.words(qg) if len(w) > 1 and not w.isupper()]
    return not (full_p and full_q and C.key(full_p[0]) != C.key(full_q[0]))


def strict_author(row, e, source, byline, S, registry):
    original = C.initials_ok
    C.initials_ok = strict_initials
    try:
        return C.author_check(row, e, source, byline, S, registry)
    finally:
        C.initials_ok = original


def journal_ok(text, names):
    """True when any registry journal name is recognisable in `text`, allowing abbreviations."""
    tokens = re.findall(r'[a-z]+', fold(text))
    joined = re.sub(r'[^a-z]', '', fold(text))
    for name in names:
        words = re.findall(r'[a-z]+', fold(name))
        if not words:
            continue
        if len(words) == 1 and words[0] in tokens:                       # BMJ, JAMA, Lancet, eLife
            return True
        if len(words) > 1 and ''.join(words) in joined and len(''.join(words)) >= 6:
            return True
        distinctive = [w for w in words if w not in GENERIC and len(w) >= 3] or \
                      [w for w in words if len(w) >= 3]
        for w in distinctive:
            if any(w.startswith(t) and (len(t) >= 3 or t == w) for t in tokens):
                return True
    return False


def records_for(row, registry):
    recs = (registry.doi(row['canonical_doi']) if row.get('canonical_doi') else [])
    if row.get('canonical_pmid'):
        recs = recs + [registry.pmid(row['canonical_pmid'])]
    return [r for r in recs if r]


def strict_checks(row, s02, registry, byline, S):
    e = S.entry(s02)
    recs = records_for(row, registry)
    if not recs:
        return [], []
    source = recs[0]
    rules, evidence = [], []
    try:
        hit, note, reasons = strict_author(row, e, source, byline, S, registry)
    except Exception:          # malformed byline: no evidence either way
        hit, note, reasons = [], '', []
    if hit:
        rules.append('STRICT_AUTHOR')
        evidence += reasons
    allrecs = recs + [r for r in C.sources_for(row, e, source, registry, S) if r not in recs]
    titles = {norm_title(r.get('title', '')) for r in allrecs if r.get('title')}
    if e.get('title') and titles and norm_title(e['title']) not in titles:
        rules.append('TITLE_MISMATCH')
        evidence.append(f"title: printed {e['title'][:120]}; registry {source.get('title', '')[:120]}")
    years = {int(y) for r in allrecs for y in r.get('years', [])}
    if e.get('years') and years and not (set(e['years']) & years):
        rules.append('YEAR_MISMATCH')
        evidence.append(f"year: printed {e['years']}; registry {sorted(years)}")
    printed = s02.get('citation_printed', '')
    text = set(re.findall(r'[A-Za-z0-9]+', printed))
    vols = {str(r.get('volume') or '').strip() for r in allrecs} - {''}
    vols = {v for v in vols if v.isdigit()}
    if vols and not any(v in text or v.lstrip('0') in text for v in vols):
        rules.append('VOLUME_MISMATCH')
        evidence.append(f"volume: registry {sorted(vols)} not in printed citation")
    pages = {str(r.get('first_page') or re.split(r'[-–]', r.get('pages') or '')[0]).strip() for r in allrecs} - {''}
    pages = {p for p in pages if re.fullmatch(r'[A-Za-z]?\d+', p)}
    if pages and not any(p in text for p in pages):
        rules.append('PAGE_MISMATCH')
        evidence.append(f"page: registry first page {sorted(pages)} not in printed citation")
    names = [n for r in allrecs if not r.get('book') for n in (r.get('journal', ''), r.get('journal_abbr', '')) if n]
    if names:
        rest, ft = fold(printed), fold(e.get('title', ''))
        cut = rest.find(ft[:40]) if len(ft) >= 20 else -1
        if cut >= 0:
            rest = rest[cut + len(ft):]
        if not journal_ok(rest, names):
            rules.append('JOURNAL_MISMATCH')
            evidence.append(f"journal: registry {names[0][:80]} not recognisable in printed citation")
    return rules, evidence


def apply(row, s02, registry, byline, S):
    from v36c.rules import apply as v36c_apply
    out = v36c_apply(row, s02, registry, byline, S)
    if out['v36_state'] == 'unresolved' or row.get('state') == 'unresolved':
        return out
    rules, evidence = strict_checks(row, s02, registry, byline, S)
    if rules:
        base = [r for r in out['rule_id'].split(';') if r and r != 'V35_RETAIN'] if out['v36_state'] == 'valid' else \
               [r for r in out['rule_id'].split(';') if r]
        out = {**out, 'v36_state': 'corrupted', 'rule_id': ';'.join(base + rules),
               'evidence': ' | '.join(x for x in [out.get('evidence', '')] + evidence if x)}
    return out
