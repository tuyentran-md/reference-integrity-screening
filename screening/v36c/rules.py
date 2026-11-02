"""S03-v36c author rule: per-author, multi-source, order-free name matching.

Replaces the v36b author comparison. An author is reported only when the printed
name disagrees with EVERY usable registry record of the same work. Registry
family/given splits are not trusted blindly: an alternative split is tried only
where the registry split is evidently unreliable (lone-initial family field,
Chinese pinyin names, or an unsplit full name). Investigator policy stays strict:
a dropped particle or dropped surname part is still a deviation.
No case identifiers or QC labels are used.
"""
import difflib
import re
import unicodedata

from v36b import rules as B

EXTRA_FIELDS = B.EXTRA_FIELDS
PARTICLES = B.PARTICLES | {'des', 'den', 'ten', 'ter', 'dello', 'degli'}
HONORIFIC = re.compile(r'\b(?:Prof|Dr|Doctor|Mr|Mrs|Ms|Sir|Dame)\b\.?\s*', re.I)
SUFFIX = re.compile(r'(?:[,\s]+(?:Jr|Sr|II|III|IV|MD|MPH|PhD|LP|J[uú]nior|FRCS|MSc|BSc|RN)\.?)+$', re.I)
PREPRINT = re.compile(r'\b(?:medrxiv|biorxiv|arxiv|preprint|research square|ssrn|preprints\.org)\b', re.I)
LATIN = re.compile(r'[A-Za-zÀ-ɏ]')
NON_LATIN = re.compile(r'[^\W\d_A-Za-zÀ-ɏḀ-ỿ]')
_INIT = r'(?:zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw])?'
_FINAL = r'(?:iang|iong|uang|ueng|ang|eng|ing|ong|uai|uan|ian|iao|van|ai|ei|ao|ou|an|en|er|in|un|ui|ua|uo|ia|ie|iu|ve|a|o|e|i|u|v)'
PINYIN = re.compile(rf'^(?:{_INIT}{_FINAL}){{1,3}}$')
# Most frequent Chinese surnames (pinyin). Used only to tell which field of a
# Chinese name a registry or citation treats as the surname.
CN_SURNAMES = set('''wang li zhang liu chen yang huang zhao wu zhou xu sun ma zhu hu guo he gao lin luo
zheng liang xie song tang han feng deng cao peng zeng xiao tian dong yuan pan yu jiang cai du ye cheng
wei su lv lu ding ren shen yao cui zhong tan fan jin shi liao jia xia fu fang bai zou meng xiong qin qiu
yin xue yan duan lei hou long tao gu mao hao gong shao wan qian dai mo kong xiang chang niu kang
ou gou ni zhan ge hong mu bao yi wen chu geng mei lan ji pei rao bi lai shu ai qi yue nie zhan cen'''.split())


def key(value):
    return B.name_key(value)


def demojibake(value):
    """Undo a cp1250 name read as cp1252 (e.g. ć shown as æ); keep the original if it fails."""
    out = {value}
    for enc, dec in (('cp1252', 'cp1250'), ('cp1252', 'utf-8'), ('latin-1', 'utf-8')):
        try:
            fixed = value.encode(enc).decode(dec)
        except (UnicodeError, LookupError):
            continue
        out.add(fixed)
    return out


def words(value):
    return re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)?", value or '', re.UNICODE)


def initials(value, keep_particles=False):
    """Initials of a given-name field. A short all-caps word (MA, JFB) is a run of
    initials; a long all-caps word (SAFWAN) is a name. Hyphenated names give one
    initial per part."""
    value = re.sub(r'([a-z])([A-Z])', r'\1 \2', value or '')
    out = ''
    for w in words(value.replace('-', ' ').replace('‐', ' ')):
        if not keep_particles and key(w) in PARTICLES and w.islower():
            continue
        if w.isupper() and len(w) <= 3:
            out += key(w)
        else:
            out += key(w)[:1]
    return out


def initials_ok(pg, qg):
    """Absent given names cannot refute; a shorter initial run may omit middle names."""
    if not pg or not qg:
        return True
    if not any(a and b and (a.startswith(b) or b.startswith(a))
               for a in {initials(pg), initials(pg, True)} for b in {initials(qg), initials(qg, True)}):
        return False
    full_p = [w for w in words(pg) if len(w) > 1 and not w.isupper()]
    full_q = [w for w in words(qg) if len(w) > 1 and not w.isupper()]
    return not (full_p and full_q and key(full_p[0]) != key(full_q[0]))


def clean(p, printed=False):
    family = SUFFIX.sub('', HONORIFIC.sub('', p.get('family', '') or '')).strip(' ,.')
    given = SUFFIX.sub('', HONORIFIC.sub('', p.get('given', '') or '')).strip(' ,')
    given = re.sub(r'\s*\([^)]*\)\s*', ' ', given).strip()          # nickname: "C. (Kate) M."
    if printed:
        # Initials glued to the surname ("CervantesA").
        m = re.fullmatch(r'(.*[a-zà-ÿ])([A-Z]{1,3})', family)
        if m and not given:
            family, given = m[1], m[2]
        # A particle parked at the end of the printed given field belongs to the surname.
        gw = given.split()
        lead = []
        while gw and gw[-1].islower() and key(gw[-1]) in PARTICLES:
            lead.insert(0, gw.pop())
        if lead:
            family, given = ' '.join(lead + [family]), ' '.join(gw)
    else:
        # Registry initials stored before the surname ("E. Anderson" + "Ikeokwu").
        m = re.fullmatch(r'((?:[A-Z]\.?\s+)+)(\S{2,}.*)', family)
        if m and given:
            family, given = m[2], (given + ' ' + m[1]).strip()
    return {'family': family, 'given': given, 'group': p.get('group', False)}


def family_equal(a, b):
    """Exact surname after typography; a registry U+FFFD matches one or two letters."""
    if '�' in b:
        pattern = ''.join('.{1,2}' if ch == '�' else re.escape(key(ch)) for ch in b if ch == '�' or key(ch))
        return bool(re.fullmatch(pattern, key(a)))
    return key(a) == key(b) and bool(key(a))


def pinyin(value):
    ws = [key(w) for w in words(value.replace('-', ' '))]
    return bool(ws) and all(PINYIN.match(w) for w in ws)


def splits(q, swapped_byline=False):
    """Candidate (family, given) readings of one registry person."""
    fam, giv = q['family'], q['given']
    yield fam, giv
    gw = giv.split()
    if gw and gw[-1].islower() and key(gw[-1]) in PARTICLES:     # "Lislei Maia de" + "Góis"
        yield gw[-1] + ' ' + fam, ' '.join(gw[:-1])
        yield fam, ' '.join(gw[:-1])
    if giv and len(key(fam)) == 1:                                 # lone initial in family field
        yield giv, fam
    if giv and swapped_byline:
        yield giv, fam
        if len(gw) > 1:                                            # "Liu Zhong" + "Ping" = Liu Zhong-Ping
            yield gw[0], ' '.join(gw[1:]) + ' ' + fam
    fw = fam.split()
    if not giv and len(fw) > 1 and pinyin(fam):
        # Unsplit Chinese name: the surname is whichever end is a common surname.
        if key(fw[0]) in CN_SURNAMES:
            yield fw[0], ' '.join(fw[1:])
        if key(fw[-1]) in CN_SURNAMES and key(fw[0]) not in CN_SURNAMES:
            yield fw[-1], ' '.join(fw[:-1])
    elif not giv and len(fw) > 1:
        for cut in range(1, len(fw)):
            yield ' '.join(fw[cut:]), ' '.join(fw[:cut])      # given-first full name
            yield ' '.join(fw[:cut]), ' '.join(fw[cut:])      # family-first full name


def byline_swapped(people):
    """A registry byline stores Chinese names given-first when its 'given' fields hold
    the common surnames and its 'family' fields do not."""
    if not people or not all(pinyin(q['family']) and pinyin(q['given']) for q in people if q['given']):
        return False
    first = lambda v: key((v.split() or [''])[0].split('-')[0])
    g = sum(first(q['given']) in CN_SURNAMES and key(q['family']) not in CN_SURNAMES for q in people)
    f = sum(key(q['family']) in CN_SURNAMES and first(q['given']) not in CN_SURNAMES for q in people)
    return g >= 2 and g > f


def byline_initial_swapped(people):
    """Chinese names stored as given name + surname initial ("Xiaoyu Q")."""
    named = [q for q in people if q['given']]
    return (len(named) >= 2 and all(pinyin(q['family']) and len(key(q['given'])) <= 3 for q in named)
            and sum(key(q['family']) not in CN_SURNAMES for q in named) > len(named) / 2)


def printed_variants(p):
    yield p
    for fam in demojibake(p['family']) - {p['family']}:
        yield {**p, 'family': fam}
    # Initials-first printed name ("MA Williams"): JATS or parser put initials as surname.
    if re.fullmatch(r'[A-Z]{1,3}\.?', p['family'] or '') and p['given'] and not re.fullmatch(r'(?:[A-Z]\.?\s*)+', p['given']):
        yield {**p, 'family': p['given'], 'given': p['family']}


def person_match(p, q, swapped_byline=False, initial_swapped=False):
    for pv in printed_variants(p):
        for fam, giv in splits(q, swapped_byline):
            if family_equal(pv['family'], fam) and initials_ok(pv['given'], giv):
                return True
            if pv['given'] and key(pv['family'] + pv['given']) == key(fam + initials(giv)):
                return True
            # Printed surname field also holds the given name ("Aoki Maki" + "K.").
            pw, fw = pv['family'].split(), fam.split()
            if len(pw) > len(fw) and [key(w) for w in pw[:len(fw)]] == [key(w) for w in fw]:
                extra = [key(w) for w in pw[len(fw):]]
                gw = [w for w in words(giv) if key(w) not in extra]
                if all(x in [key(w) for w in words(giv)] for x in extra) and initials_ok(pv['given'], ' '.join(gw)):
                    return True
        if initial_swapped and q['given']:
            g, f = key(q['given']), key(q['family'])
            if key(pv['family']).startswith(g) and initials(pv['given'])[:1] == f[:1]:
                return True
    return False


def printed_names(seg, fallback):
    """Surname + initials from an unstructured byline; '&'/'and' separate authors."""
    seg = re.sub(r'\s+(?:&|and)\s+', ', ', seg)
    seg = re.sub(r',\s*((?:[A-Z]\.\s*)+)(?=[,;]|$|\s*\()', r' \1', seg)
    names = []
    for chunk in re.split(r'[,;]|\bet al\b', seg):
        chunk = re.sub(r'\s*\(?\d{4}\)?.*$', '', chunk).strip(' .')
        if not chunk:
            continue
        m = re.fullmatch(r'(.+?)\s+((?:[A-Z]\.?(?:\s*|[-‐‑–])){1,12})', chunk)
        if m:
            names.append({'family': m[1].strip(), 'given': m[2].strip()})
            continue
        m = re.fullmatch(r'((?:[A-Z]\.?\s*){1,4})\s+([^\W\d_][^\d]*)', chunk)
        if m and len(m[2]) > 1:
            names.append({'family': m[2].strip(), 'given': m[1].strip()})
    return names or [{'family': fallback, 'given': ''}]


def usable(details):
    """A registry byline is unusable as name evidence when its fields are malformed."""
    people = [q for q in details if not q.get('group')]
    if not people:
        return False
    if any(re.search(r'[,;]', q['family']) for q in people):
        return False
    if len(people) == 1 and len((people[0]['family'] + ' ' + people[0]['given']).split()) >= 4:
        return False
    if all(len(key(q['family'])) <= 1 for q in people):
        return False
    if sum(bool(q['given']) and all(len(key(w)) == 1 for w in q['family'].split()) for q in people) >= 2:
        return False
    return True


def sources_for(row, e, source, registry, S):
    """All cached records of the same work with an author list."""
    found, seen = [], set()

    def add(rec):
        if not rec or not rec.get('author_details'):
            return
        tag = (rec.get('cache_origin', ''), rec.get('doi', ''), rec.get('pmid', ''), len(rec['author_details']))
        if tag in seen:
            return
        same_doi = bool(rec.get('doi')) and rec.get('doi') == source.get('doi')
        if not same_doi and B.work_gate(row, e, rec, registry, S):
            return
        if rec is not source and not same_doi and key(rec.get('title')) != key(source.get('title')):
            if (S.title_agreement(rec.get('title', ''), e) or 0) < .9:
                return
        seen.add(tag)
        found.append(rec)

    add(source)
    if source.get('doi'):
        for rec in registry.doi(source['doi']):
            add(rec)
        for rec in getattr(registry, '_by_doi', {}).get(source['doi'], []):
            add(rec)
    for pmid in set(e.get('pmids', []) + e.get('hint_pmids', []) + [row.get('canonical_pmid', '')]):
        rec = registry.pmid(pmid) if pmid else None
        if rec and (not source.get('doi') or not rec.get('doi') or rec['doi'] == source['doi']):
            add(rec)
    for doi in e.get('dois', []):
        for rec in registry.doi(doi):
            if not source.get('doi') or rec.get('doi') == source['doi']:
                add(rec)
    return found


def counterpart(p, people):
    """Does the printed person resemble someone in this registry byline at all?"""
    a = key(p['family'])
    for q in people:
        b = key(q['family'])
        tokens = [key(w) for w in words(q['family'] + ' ' + q['given'])]
        if a in tokens or (a and b and (a in b or b in a)) or difflib.SequenceMatcher(None, a, b).ratio() >= .5:
            return True
        if initials(p['given'])[:1] == b[:1] and key(q['given'])[:1] == a[:1]:
            return True
    return False


def author_check(row, e, source, byline, S, registry):
    seg = S.author_segment(e)
    title_start = e.get('raw', '').lower().find(source.get('title', '')[:25].lower()) if len(source.get('title', '')) >= 25 else -1
    if title_start > 0:
        seg = re.sub(r'^\s*\d+[.)]?\s*', '', e['raw'][:title_start])
    if byline.get('group') or source.get('group_author') or S.corporate(seg):
        return [], 'not_compared_group', []
    if not seg or not source.get('first_author') or not e.get('first_author'):
        return [], 'not_compared_missing', []
    if source.get('book'):
        return [], 'not_compared_book_catalogue_v35', []
    if PREPRINT.search(e.get('raw', '')) and source.get('type', 'journal-article') != 'posted-content' and not re.search(r'10\.1101/', ' '.join(e.get('dois', []))):
        return [], 'not_compared_preprint_vs_article', []
    names = [clean(p, printed=True) for p in byline.get('names', [])]
    if not names:
        printed = B.SUFFIX.sub('', e['first_author'])
        if key(printed) not in key(seg):
            return [], 'not_compared_parsed_name_not_printed', []
        names = [clean(p, printed=True) for p in printed_names(seg, printed)]
    if any(',' in p['family'] for p in names):
        return [], 'not_compared_printed_list_in_field', []
    records = sources_for(row, e, source, registry, S)
    bylines = []
    for rec in records:
        details = [clean(q) for q in rec['author_details']]
        if not usable(details):
            continue
        people = [q for q in details if not q.get('group')]
        if len(people) == 1 and len(names) >= 3:
            continue          # registry lists only one (e.g. corresponding) author
        if all(NON_LATIN.search(q['family'] + q['given']) and not LATIN.search(q['family'] + q['given']) for q in people):
            continue
        bylines.append(people)
    if not bylines:
        return [], 'not_compared_no_usable_registry_byline', []
    segment_key = key(seg)
    # Per-author consensus: matched if ANY usable record has this person (one-to-one per record).
    matched = [False] * len(names)
    for people in bylines:
        swapped, iswapped = byline_swapped(people), byline_initial_swapped(people)
        free = list(people)
        for i, p in enumerate(names):
            hit = next((q for q in free if person_match(p, q, swapped, iswapped)), None)
            if hit is None:
                # JATS split one printed author into two fields, or merged two authors.
                hit = next((q for q in free if key(q['family'] + q['given']) in segment_key
                            and key(q['family']) in segment_key and p['family']
                            and all(key(w) in [key(x) for x in words(q['family'])] for w in words(p['family']))), None)
            if hit is None:
                pair = next(((q, r) for q, r in zip(free, free[1:])
                             if key(p['family'] + p['given']) == key(q['family'] + q['given'] + r['family'] + r['given'])), None)
                if pair:
                    free.remove(pair[1])
                    hit = pair[0]
            if hit is not None:
                free.remove(hit)
                matched[i] = True
    pending = [(i, p) for i, p in enumerate(names, 1) if not matched[i - 1] and p['family']]
    if not pending:
        return [], 'set_level_name_agreement', []
    if len(names) > 1 and len(pending) == len(names) and not any(
            counterpart(p, people) for _, p in pending for people in bylines):
        # No printed person resembles anyone in any record: a different author list
        # (another version or work), not a spelling deviation of one name.
        return [], 'not_compared_no_counterpart_byline', []
    primary = bylines[0]
    notes, reasons = [], []
    for i, p in pending:
        # Evidence pairing: the most similar registry person, never an arbitrary one.
        q = max(primary, key=lambda q: difflib.SequenceMatcher(None, key(p['family']), key(q['family'])).ratio())
        a, b = key(p['family']), key(q['family'])
        if a != b:
            if a and a in [key(w) for w in q['given'].split()]:
                note = 'given_used_as_surname'
            elif a in b or b in a:
                aw = [key(w) for w in p['family'].split()]
                bw = [key(w) for w in q['family'].split()]
                extra = [w for w in aw if w not in bw] + [w for w in bw if w not in aw]
                note = 'particle_deviation' if extra and all(w in PARTICLES for w in extra) else 'compound_surname_deviation'
            else:
                note = 'different_surname'
        else:
            note = 'different_given_name_or_initials'
        notes.append(note)
        reasons.append(f"author {i}: printed {p['family']} {p['given']}; registry {q['family']} {q['given']}")
    return ['AUTHOR_STRICT'], ';'.join(sorted(set(notes))), reasons


def _apply(row, s02, registry, byline, S):
    """v36b flow with the author comparison replaced; DOI/PMID rules unchanged."""
    original = B.author_check
    holder = {}

    def replacement(row_, e, source, byline_, S_):
        # v36b calls author_check for the primary source and for each alternative.
        # v36c consults all sources at once, so only the first call decides.
        if 'result' not in holder:
            holder['result'] = author_check(row_, e, source, byline_, S_, registry)
            return holder['result']
        return holder['result']

    B.author_check = replacement
    try:
        return B._apply(row, s02, registry, byline, S)
    finally:
        B.author_check = original


def apply(row, s02, registry, byline, S):
    """Precision refinement of frozen v36 candidates; never expands candidate recall."""
    from rules import apply as v36_apply
    baseline = v36_apply(row, s02, registry, byline, S)
    if 'AUTHOR_STRICT' not in baseline['rule_id'].split(';'):
        return baseline
    return _apply(row, s02, registry, byline, S)
