"""S03-v36b explainable post-resolution rules; no case identifiers or QC labels."""
import re
import unicodedata
import json
from urllib.parse import urlencode

EXTRA_FIELDS = ['v36_state', 'rule_id', 'author_note', 'evidence',
                'v36_wrong_fields', 'v36_parsed_pmids']
CONFERENCE = re.compile(r'\b(?:conference|congress|meeting|poster|abstracts?|eular)\b|'
                        r'(?:^|[-./])(?:eular|abstracts?)(?:[-./]|$)', re.I)
SPECIAL_ITEM = re.compile(r'^(?:[A-Z]{1,4}\d{2,6}\s+)?(?:correction|erratum|reply|comment|retraction)\b', re.I)


def name_key(value):
    """Case, diacritics, punctuation and particle spacing only."""
    value = unicodedata.normalize('NFKD', value or '').casefold()
    value = value.translate(str.maketrans({'ø': 'o', 'ı': 'i', 'ł': 'l', 'đ': 'd', 'ð': 'd'}))
    return ''.join(c for c in value if c.isalpha() and not unicodedata.combining(c))


def initials(value):
    value = re.sub(r'([a-z])([A-Z])', r'\1 \2', value)
    words = re.findall(r'[^\W\d_]+', value, re.UNICODE)
    words = [w for w in words if w not in {'de', 'da', 'do', 'del'}]
    return ''.join(name_key(w) if len(w) == 1 or w.isupper() else name_key(w)[0] for w in words)


def given_disagrees(printed, source):
    """Abbreviations compare only observable initials; absent given names cannot refute."""
    if not printed or not source:
        return False
    if initials(printed) != initials(source):
        # A shorter initial string may be an ordinary omission of middle initials.
        return not (initials(source).startswith(initials(printed)) or initials(printed).startswith(initials(source)))
    full_p = [w for w in printed.split() if len(name_key(w)) > 1 and not w.isupper()]
    full_s = [w for w in source.split() if len(name_key(w)) > 1 and not w.isupper()]
    return bool(full_p and full_s and name_key(full_p[0]) != name_key(full_s[0]))


def bare_tail(row, S):
    """A detached terminal number after sentence punctuation; exclude DOI/URL suffixes."""
    raw = row.get('citation_printed', '')
    m = re.search(r'[.;]\s+(\d{7,8})\s*\.?\s*$', raw)
    if not m:
        return ''
    before = raw[:m.start()]
    if re.search(r'(?:https?://|10\.\d{4,9}/)\S*$', before, re.I):
        return ''
    existing = S.parse_multi(row.get('printed_pmids', '')) + S.parse_multi(row.get('supplied_pmids', ''))
    return m[1] if m[1] not in existing else ''


PARTICLES = {'da', 'de', 'del', 'della', 'der', 'di', 'do', 'dos', 'du', 'la', 'le', 'van', 'von'}
SUFFIX = re.compile(r'(?:[,\s]+(?:Jr|Sr|II|III|IV|MD|MPH|PhD|LP)\.?)+$', re.I)


def clean_person(p):
    return {**p, 'family': SUFFIX.sub('', p.get('family', '')).strip(),
            'given': re.sub(r'^(?:Dr|Prof)\.?\s+', '', p.get('given', ''), flags=re.I)}


def printed_names(seg, fallback):
    """Recover explicit surname + initials from unstructured comma-separated bylines."""
    names = []
    seg = re.sub(r',\s*((?:[A-Z]\.\s*)+)(?=[,;]|$|\s*\()', r' \1', seg)
    for chunk in re.split(r'[,;]|\bet al\b', seg):
        chunk = re.sub(r'\s*\(?\d{4}\)?.*$', '', chunk).strip(' .')
        chunk = re.sub(r'^[&\s]+', '', chunk)
        match = re.fullmatch(r'(.+?)\s+((?:[A-Z]\.?(?:\s*|[-‐‑–])){1,12})', chunk)
        if match:
            names.append({'family': match[1].strip(), 'given': match[2].strip()})
    return names or [{'family': fallback, 'given': ''}]


def name_agrees(p, q):
    return name_key(p.get('family')) == name_key(q.get('family')) and not given_disagrees(p.get('given', ''), q.get('given', ''))


def boundary_agrees(p, q):
    """Move a field boundary only when all observed surname and given tokens survive."""
    if name_agrees(p, q):
        return True
    pf, pg, qf, qg = (p.get('family', ''), p.get('given', ''), q.get('family', ''), q.get('given', ''))
    # JATS split a compound surname or spaced initials across its two fields.
    if pg and qg and name_key(pf + pg) == name_key(qf + qg):
        return True
    # Registry embeds given names/initials BEFORE the surname. Never just accept
    # containment: every displaced token must be observed in the given initials.
    words = qf.split()
    for cut in range(1, len(words)):
        extra, family = ' '.join(words[:cut]), ' '.join(words[cut:])
        if (name_key(family) == name_key(pf) and pg and extra.replace('.', '').replace(' ', '').isupper()
                and not any(name_key(w) in PARTICLES for w in words[:cut])):
            variants = [qg + ' ' + extra]
            if any(initials(pg) == initials(g) for g in variants):
                return True
    # Portuguese particles placed after given names are still present, not dropped.
    qw = qg.split()
    if qw and name_key(qw[-1]) in PARTICLES:
        if name_key(pf) == name_key(qw[-1] + qf) and not given_disagrees(pg, ' '.join(qw[:-1])):
            return True
    return False


def swap_agrees(p, q):
    """A registry-wide inversion needs multiple independent byline witnesses."""
    family = name_key(p.get('family'))
    words = q.get('given', '').split()
    if words and family == name_key(words[0]):
        return not given_disagrees(p.get('given', ''), ' '.join(words[1:]) + ' ' + q.get('family', ''))
    # Some registries preserve only initials in their inverted given field.
    return (len(name_key(q.get('given'))) == 1 and family
            and family[0] == name_key(q.get('given'))
            and initials(p.get('given', '')) == initials(q.get('family', '')))


def cached_search_source(source, e, registry, S):
    """Recover full bylines from the exact frozen Crossref search response."""
    if source.get('author_details') or not hasattr(S, 'CR_SELECT'):
        return source
    url = 'https://api.crossref.org/works?' + urlencode({
        'query.bibliographic': e['raw'][:300], 'rows': 3,
        'mailto': S.RI.MAILTO, 'select': S.CR_SELECT})
    try:
        items = json.loads(S.RI.get(url) or '{}').get('message', {}).get('items', [])
    except (ValueError, AttributeError):
        return source
    for item in items:
        if (item.get('DOI') or '').lower() == source.get('doi', '') and name_key((item.get('title') or [''])[0]) == name_key(source.get('title')):
            return {**source, **S.cr_record(item), 'type': item.get('type', ''),
                    'author_details': [{'family': a.get('family', '') or a.get('name', ''),
                                        'given': a.get('given', ''), 'group': not a.get('family')}
                                       for a in item.get('author', [])]}
    return source


def work_gate(row, e, source, registry, S):
    if row.get('original_first_author') and row.get('verified_by'):
        return ''
    if not hasattr(S, 'title_agreement') or not source.get('title'):
        return ''
    explicit = source.get('doi') in e.get('dois', []) or source.get('pmid') in e.get('pmids', []) + e.get('hint_pmids', [])
    if (S.title_agreement(source['title'], e) or 0) < (.8 if explicit else .9):
        return 'not_compared_weak_work_match'
    if e.get('kind') == 'journal' and source.get('type') in {'proceedings-article', 'posted-content', 'dataset', 'component'}:
        return 'not_compared_publication_role'
    # A supplied supplement/abstract identifier cannot validate a later full paper.
    if source.get('doi') and e.get('dois') and source['doi'] not in e['dois']:
        for doi in e['dois']:
            for r in registry.doi(doi):
                if CONFERENCE.search(r.get('title', '') + ' ' + r.get('doi', '')):
                    return 'not_compared_publication_role'
    if re.search(r'clinicaltrials|\bnct\d{6,}|inplasy', source.get('doi', '') + ' ' + source.get('title', ''), re.I):
        return 'not_compared_publication_role'
    return ''


def author_check(row, e, source, byline, S):
    """Compare only available personal author evidence after identity resolution."""
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
    names = [clean_person(p) for p in byline.get('names', [])]
    details = [clean_person(q) for q in source.get('author_details', [])]
    if not details:
        # A family-only source cannot support first-author disagreement when
        # the saved registry author set already contains the printed author.
        details = [{'family': f, 'given': ''} for f in source.get('authors', [])]
        details = details or [{'family': source['first_author'], 'given': source.get('first_given', '')}]
    if any(re.search(r'[,;]', q.get('family', '')) for q in details if not q.get('group')):
        return [], 'not_compared_registry_list_in_field', []
    if not names:
        printed = SUFFIX.sub('', e['first_author'])
        if name_key(printed) not in name_key(seg):
            return [], 'not_compared_parsed_name_not_printed', []
        names = printed_names(seg, printed)
    if len(details) == 1 and len((details[0].get('family', '') + ' ' + details[0].get('given', '')).split()) >= 4:
        return [], 'not_compared_registry_merged_name_field', []
    if sum(bool(q.get('given')) and all(len(name_key(w)) == 1 for w in q.get('family', '').split()) for q in details) >= 2:
        return [], 'not_compared_registry_initials_in_family', []
    if any(',' in p.get('family', '') for p in names):
        return [], 'not_compared_printed_list_in_field', []
    # Repair split JATS fields only when the complete registry name is literally
    # present in the printed author segment. Never infer a missing surname token.
    segment_key = name_key(seg)
    complete_names = [(q, name_key(q.get('family', '') + q.get('given', '')), name_key(q.get('family', ''))) for q in details]
    for p in names:
        for q, full, family in complete_names:
            if full and full in segment_key and name_key(p['family'] + p.get('given', '')) in full:
                if name_key(p['family']) != family and family in segment_key:
                    p.update(family=q['family'], given=q.get('given', ''))
                    break
    # A field made entirely of initials provides no observable surname evidence.
    if all(all(len(name_key(w)) == 1 for w in q.get('family', '').split()) for q in details):
        return [], 'not_compared_registry_initials_only', []
    # Consume matches once, including across the byline; no positional reuse.
    pending, available = [], list(enumerate(details))
    for i, p in enumerate(names, 1):
        merged = next(((j, q, k, other) for (j, q), (k, other) in zip(available, available[1:])
                       if name_key(p['family'] + p.get('given', '')) == name_key(q['family'] + q.get('given', '') + other['family'] + other.get('given', ''))), None)
        if merged:
            j, q, k, other = merged
            available.remove((j, q)); available.remove((k, other))
            continue
        hit = next(((j, q) for j, q in available if boundary_agrees(p, q)), None)
        if not hit:
            # A complete unsplit given-first name, with all its initials observed.
            # Hyphenated names remain ambiguous between compound given/family names.
            hit = next(((j, q) for j, q in available
                        if not q.get('given') and len(q.get('family', '').split()) > 1
                        and re.fullmatch(r'[^\W\d_]+', p.get('family', ''))
                        and name_key(q['family'].split()[-1]) == name_key(p['family'])
                        and initials(' '.join(q['family'].split()[:-1])) == initials(p.get('given', ''))), None)
        if hit:
            available.remove(hit)
        else:
            pending.append((i, p))
    if not pending:
        return [], 'set_level_name_agreement', []
    # Swap only on a byline-wide pattern, not a single name or token overlap.
    swaps = [(i, p, q) for i, p in pending for _, q in available if swap_agrees(p, q)]
    short_families = sum(len(name_key(q.get('family'))) <= 5 for _, q in available)
    inverted_initials = all(len(name_key(q.get('given'))) == 1 for _, _, q in swaps)
    if (len(pending) >= 2 and len({i for i, _, _ in swaps}) == len(pending)
            and (short_families >= max(2, len(available) / 2) or inverted_initials)):
        # One-to-one matching also prevents repeated-name self-confirmation.
        used = set()
        for _, p in pending:
            match = next((j for j, q in available if j not in used and swap_agrees(p, q)), None)
            if match is None:
                break
            used.add(match)
        else:
            return [], 'registry_byline_swap_confirmed', []
    notes, reasons = [], []
    for i, p in pending:
        q = next((q for _, q in available if name_key(q.get('family')) == name_key(p.get('family'))), None)
        q = q or next((q for j, q in available if j == i - 1), None)
        q = q or next((q for _, q in available), None)
        if q is None:
            if i > len(details):
                continue
            q = details[i - 1]
        available = [(j, other) for j, other in available if other is not q]
        if q.get('group') or not p.get('family') or not q.get('family'):
            continue
        a, b = name_key(p['family']), name_key(q['family'])
        if a != b:
            if a and a in [name_key(w) for w in q.get('given', '').split()]:
                note = 'given_used_as_surname'
            elif a.startswith(b) or b.startswith(a) or a in b or b in a:
                aw = [name_key(w) for w in p['family'].split()]
                bw = [name_key(w) for w in q['family'].split()]
                extra = [w for w in aw if w not in bw] + [w for w in bw if w not in aw]
                note = 'particle_deviation' if extra and all(w in PARTICLES for w in extra) else 'compound_surname_deviation'
            else:
                note = 'different_surname'
            notes.append(note)
            reasons.append(f"author {i}: printed {p['family']} {p.get('given', '')}; registry {q['family']} {q.get('given', '')}")
        elif given_disagrees(p.get('given', ''), q.get('given', '')):
            notes.append('different_given_name_or_initials')
            reasons.append(f"author {i}: printed {p['family']} {p.get('given', '')}; registry {q['family']} {q.get('given', '')}")
    return (['AUTHOR_STRICT'] if reasons else []), ';'.join(sorted(set(notes))) or 'typographic_or_observable_name_agreement', reasons


def same_work(a, b, S):
    """Alias evidence needs identical title, first author and compatible publication role."""
    title = lambda r: re.sub(r'[^\w]', '', S.html.unescape(r.get('title', '')).casefold())
    if not title(a) or title(a) != title(b) or name_key(a.get('first_author')) != name_key(b.get('first_author')):
        return False
    if bool(CONFERENCE.search(a.get('doi', '') + ' ' + a.get('title', ''))) != bool(CONFERENCE.search(b.get('doi', '') + ' ' + b.get('title', ''))):
        return False
    if bool(SPECIAL_ITEM.search(a.get('title', ''))) != bool(SPECIAL_ITEM.search(b.get('title', ''))):
        return False
    # Reprints/editions in different venues are not aliases on title alone.
    ja = name_key(S.html.unescape(a.get('journal', '')).split(':')[0])
    jb = name_key(S.html.unescape(b.get('journal', '')).split(':')[0])
    if not ja or not jb or ja != jb or a.get('book') or b.get('book'):
        return False
    ya, yb = set(a.get('years', [])), set(b.get('years', []))
    return bool(ya & yb) and not (a.get('volume') and b.get('volume') and a['volume'] != b['volume'])


def _apply(row, s02, registry, byline, S):
    """Preserve v35 columns; append an auditable new three-state decision."""
    result = {**row, **dict.fromkeys(EXTRA_FIELDS, '')}
    result['v36_state'] = row['state']
    result['rule_id'] = 'V35_RETAIN'
    e = S.entry(s02)
    if row['state'] == 'unresolved':
        result.update(author_note='not_compared_unresolved', evidence='v35 unresolved: ' + row['unresolved_reason'])
        return result
    source = {
        'title': row['canonical_title'], 'first_author': row['canonical_first_author'],
        'doi': row['canonical_doi'], 'years': [int(y) for y in row['canonical_years'].split(';') if y],
        'journal': row['source_journal']}
    # Prefer the record that actually provided v35's source, retaining its full byline.
    cached = registry.pmid(row['source_id']) if row['source_id'].isdigit() else None
    candidates = registry.doi(row['source_id']) if row['source_id'].startswith('10.') else []
    cached = cached or next((r for r in candidates if name_key(r.get('title')) == name_key(source['title'])), None)
    if not cached and source['doi']:
        cached = next((r for r in registry.doi(source['doi']) if name_key(r.get('title')) == name_key(source['title'])), None)
    if cached:
        source = {**source, **cached}
    source = cached_search_source(source, e, registry, S)
    # Frozen original-publication verification outranks a conflicting registry byline.
    if row.get('original_first_author') and row.get('verified_by'):
        source.update(first_author=row['original_first_author'], author_details=[{'family': row['original_first_author'], 'given': ''}])
    gate = work_gate(row, e, source, registry, S)
    rules, note, reasons = ([], gate, []) if gate else author_check(row, e, source, byline, S)
    if rules and source.get('doi'):
        if not hasattr(registry, '_by_doi'):
            registry._by_doi = {}
            for record in getattr(registry, 'pmids', {}).values():
                registry._by_doi.setdefault(record.get('doi'), []).append(record)
        for alternative in registry.doi(source['doi']) + registry._by_doi.get(source['doi'], []):
            if alternative.get('doi') == source['doi'] and name_key(alternative.get('title')) == name_key(source['title']):
                if work_gate(row, e, alternative, registry, S):
                    continue
                alt_rules, alt_note, alt_reasons = author_check(row, e, alternative, byline, S)
                if not alt_rules and not alt_note.startswith('not_compared'):
                    rules, note, reasons = [], 'registry_byline_disagreement_match_confirmed', []
                    break
    wrong = [k.removesuffix('_wrong') for k in S.FLAGS if row.get(k) == '1']
    if rules:
        wrong.append('author')
    pmids = [registry.pmid(p) for p in set(e['pmids'] + e['hint_pmids'] + [row['canonical_pmid']])]
    pmids = [r for r in pmids if r]
    aliases = []
    for doi in e['dois']:
        recs = registry.doi(doi)
        canonical = [r for r in pmids if r.get('doi') and r['doi'] != doi and same_work(r, source, S)]
        # A missing/extra terminal DOI character is not typography. Require an
        # independently recorded DOI and no cached record registered AS the printed DOI.
        for target in canonical:
            good = target['doi']
            if (doi.startswith(good) or good.startswith(doi)) and doi != good:
                registered_as_printed = any(r.get('doi') == doi for r in recs)
                handle = registry.handle(doi)
                if not registered_as_printed and handle.get('responseCode') == 100:
                    rules.append('DOI_TERMINAL_CHARACTER'); wrong.append('doi')
                    reasons.append(f'printed DOI {doi}; independent PubMed DOI {good}; no cache record registered as printed string')
        if recs and canonical and all(any(same_work(r, target, S) for target in canonical) for r in recs):
            aliases.append(doi)
    # Opposed conference/article roles are different publications even with one title.
    doi_records = [r for doi in e['dois'] for r in registry.doi(doi)]
    for a in doi_records:
        for b in pmids:
            if (b.get('pmid') in e['pmids'] and a.get('doi') != b.get('doi')
                    and bool(CONFERENCE.search(a.get('doi', '') + ' ' + a.get('title', '')))
                    != bool(CONFERENCE.search(b.get('doi', '') + ' ' + b.get('title', '')))
                    and (S.title_agreement(a.get('title', ''), e) or 0) >= .95
                    and (S.title_agreement(b.get('title', ''), e) or 0) >= .95
                    and name_key(a.get('first_author')) == name_key(b.get('first_author'))
                    and b.get('volume') and byline.get('volume') and b['volume'] != byline['volume']):
                rules.append('IDENTIFIER_PUBLICATION_CONFLICT'); wrong.append('doi/pmid')
                reasons.append(f"citation contains conference/article identifiers {a.get('doi')} and PMID {b['pmid']} (DOI {b.get('doi')}); printed JATS volume {byline['volume']} vs PubMed volume {b['volume']}")
    bare = bare_tail(s02, S)
    result['v36_parsed_pmids'] = ';'.join(e['pmids'])
    if bare:
        rec = registry.pmid(bare)
        if rec:
            c = S.compare(rec, e)
            if (c.get('title_cmp') and name_key(rec.get('title')) != name_key(source.get('title')) and c.get('au') is False
                    and c.get('yok') is False and rec.get('doi') and source.get('doi')
                    and rec['doi'] != source['doi']):
                rules.append('BARE_PMID_OTHER_WORK'); wrong.append('pmid')
                result['v36_parsed_pmids'] = ';'.join(dict.fromkeys(e['pmids'] + [bare]))
                reasons.append(f"detached terminal PMID {bare} resolves to {rec['first_author']} ({rec['year']}), {rec['title']}")
        else:
            reasons.append(f'detached terminal number {bare}: no cached PMID record; not classified as an identifier')
    if aliases and len(aliases) == len(e['dois']):
        wrong = [field for field in wrong if field != 'doi']
        if row.get('pmid_wrong') != '1':
            wrong = [field for field in wrong if field != 'id_points_to_other_work']
    if rules:
        result['v36_state'] = 'corrupted'
        result['rule_id'] = ';'.join(dict.fromkeys(rules))
    elif aliases:
        reasons.append('same-work cached DOI alias: ' + ', '.join(aliases) + '; registry/source DOI ' + source.get('doi', ''))
        # Remove only a DOI-only false alarm when every printed DOI is independently
        # supported as the same work. Never erase another wrong field or unresolved state.
        other = [k for k in S.FLAGS if k not in ('doi_wrong', 'id_points_to_other_work') and row.get(k) == '1']
        if row.get('doi_wrong') == '1' and len(aliases) == len(e['dois']) and not other:
            result['v36_state'] = 'valid'
            wrong = []
            result['rule_id'] = 'DOI_ALIAS_SAME_WORK'
            reasons.append('all printed DOIs resolve in cache to the same title, author and publication year as the independently identified work')
        else:
            result['rule_id'] = 'DOI_ALIAS_SAME_WORK' if row['state'] == 'valid' else 'V35_RETAIN'
    result['author_note'] = note
    result['evidence'] = ' | '.join(reasons) or ('v35 ' + row['state'] + '; wrong fields: ' + (','.join(wrong) or 'none') + '; printed/source: ' + row['printed_first_author'] + ' / ' + row['canonical_first_author'])
    result['v36_wrong_fields'] = ';'.join(dict.fromkeys(wrong))
    return result


def apply(row, s02, registry, byline, S):
    """Precision refinement of frozen v36 candidates; do not expand candidate recall."""
    from rules import apply as v36_apply
    baseline = v36_apply(row, s02, registry, byline, S)
    if 'AUTHOR_STRICT' not in baseline['rule_id'].split(';'):
        return baseline
    candidate = _apply(row, s02, registry, byline, S)
    return candidate
