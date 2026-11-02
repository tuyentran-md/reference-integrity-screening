"""S03-v36 explainable post-resolution rules; no case identifiers or QC labels."""
import re
import unicodedata

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
    words = re.findall(r'[^\W\d_]+', value, re.UNICODE)
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


def author_check(row, e, source, byline, S):
    """Compare only available personal author evidence after identity resolution."""
    seg = S.author_segment(e)
    if byline.get('group') or source.get('group_author') or S.corporate(seg):
        return [], 'not_compared_group', []
    if not seg or not source.get('first_author') or not e.get('first_author'):
        return [], 'not_compared_missing', []
    if source.get('book'):
        return [], 'not_compared_book_catalogue_v35', []
    names = byline.get('names') or []
    details = source.get('author_details') or [{'family': source['first_author'], 'given': source.get('first_given', '')}]
    if not names:
        # S02 identifies only the family field. Don't infer later authors from free text.
        printed = re.sub(r'\s+(?:Jr\.?|Sr\.?|II|III|IV)$', '', e['first_author'], flags=re.I)
        if name_key(printed) not in name_key(seg):
            return [], 'not_compared_parsed_name_not_printed', []
        names = [{'family': printed, 'given': ''}]
        # A full "Given Family" prefix or joined surname+initial is parser segmentation.
        fam, giv = details[0].get('family', ''), details[0].get('given', '')
        if name_key(printed) == name_key(fam) + initials(giv) and initials(giv):
            names = [{'family': fam, 'given': initials(giv)}]
        elif giv and name_key(printed) in (name_key(giv + fam), name_key(fam + giv)):
            names = [{'family': fam, 'given': giv}]
    notes, reasons = [], []
    for i, p in enumerate(names, 1):
        # Missing authors and registry author order do not establish a name deviation.
        q = next((q for q in details if name_key(q.get('family')) == name_key(p.get('family'))
                  and not given_disagrees(p.get('given', ''), q.get('given', ''))), None)
        q = q or next((q for q in details if name_key(q.get('family')) == name_key(p.get('family'))), None)
        if q is None:
            if i > len(details):
                continue
            q = details[i - 1]
        if q.get('group') or not p.get('family') or not q.get('family'):
            continue
        a, b = name_key(p['family']), name_key(q['family'])
        if a != b:
            if a and a in [name_key(w) for w in q.get('given', '').split()]:
                note = 'given_used_as_surname'
            elif a.startswith(b) or b.startswith(a):
                note = 'surname_truncation_or_extension'
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


def apply(row, s02, registry, byline, S):
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
    # Frozen original-publication verification outranks a conflicting registry byline.
    if row.get('original_first_author') and row.get('verified_by'):
        source.update(first_author=row['original_first_author'], author_details=[{'family': row['original_first_author'], 'given': ''}])
    rules, note, reasons = author_check(row, e, source, byline, S)
    if rules and source.get('doi'):
        for alternative in registry.doi(source['doi']):
            if alternative.get('doi') == source['doi'] and name_key(alternative.get('title')) == name_key(source['title']):
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
