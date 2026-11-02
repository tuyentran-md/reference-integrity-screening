"""Blinded proportional pilot sampling and XlsxWriter packet, never human scoring."""
import argparse
import csv
import hashlib
import json
import math
import random
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET
import xlsxwriter
from exclusions import prior_reviews
from frozen import load
from registry import Registry
from prepare import HERE, read, write

SEED = 20261025
HEADERS = ['case_id', 'review_id', 'ref_index', 'printed citation text exactly as printed',
           'printed DOI as parsed', 'printed PMID as parsed', 'supplied DOI/PMID as parsed',
           'registry title(s)', 'registry authors', 'registry year(s)', 'registry journal(s)',
           'registry DOI/PMID', 'search link', 'verdict', 'wrong_fields', 'note']


def proportional_sample(rows, n=100, seed=SEED):
    """Hamilton allocation by complete rule_id combination, then SRS within strata."""
    groups = defaultdict(list)
    for row in rows:
        groups[row['rule_id']].append(row)
    total = len(rows)
    if total < n:
        raise ValueError(f'Only {total} eligible new detections; cannot draw {n}')
    quotas = {k: n * len(v) / total for k, v in groups.items()}
    allocation = {k: math.floor(q) for k, q in quotas.items()}
    for key in sorted(groups, key=lambda k: (-(quotas[k] - allocation[k]), k))[:n - sum(allocation.values())]:
        allocation[key] += 1
    rng = random.Random(seed)
    selected = []
    for key in sorted(groups):
        selected += rng.sample(sorted(groups[key], key=lambda r: (r['review_id'], int(r['ref_index']))), allocation[key])
    rng.shuffle(selected)
    return selected, dict(sorted(allocation.items()))


def registry_fields(row, R, S):
    """Factual identifier metadata, including an additional source when needed."""
    own = [('doi', d) for d in S.parse_multi(row['printed_dois']) + S.parse_multi(row['supplied_dois'])]
    own += [('pmid', p) for p in sorted(set(S.parse_multi(row['printed_pmids']) +
                       S.parse_multi(row['supplied_pmids']) + row.get('v36_parsed_pmids', '').split(';'))) if p]
    identifiers = list(dict.fromkeys(own))
    additional = [('doi', row.get('canonical_doi', '')), ('pmid', row.get('canonical_pmid', ''))]
    if row.get('source_id', '').isdigit():
        additional.append(('pmid', row['source_id']))
    for kind, value in additional:
        if value and (kind, value) not in identifiers:
            identifiers.append((kind, value))
    fields = [[] for _ in range(5)]
    for kind, value in identifiers:
        records = R.doi(value) if kind == 'doi' else [R.pmid(value)]
        records = [r for r in records if r]
        prefix = ('' if (kind, value) in own else 'Additional ') + f'{kind.upper()} {value}: '
        if not records:
            fields[0].append(prefix + 'Metadata unavailable in the frozen cache.')
        for rec in records:
            details = rec.get('author_details', [])
            authors = '; '.join((a.get('family', '') + ' ' + a.get('given', '')).strip() for a in details)
            authors = authors or '; '.join(rec.get('authors', [])) or rec.get('first_author', '')
            values = [rec.get('title', ''), authors,
                      '; '.join(map(str, rec.get('years', []))), rec.get('journal', ''),
                      'DOI ' + rec.get('doi', '') + ('; PMID ' + rec['pmid'] if rec.get('pmid') else '')]
            if kind == 'doi':
                handle = getattr(R, 'handle', lambda value: {})(value)
                aliases = [v.get('data', {}).get('value', '') for v in handle.get('values', [])
                           if v.get('type') == 'HS_ALIAS']
                if aliases:
                    values[4] += '; Handle alias: ' + '; '.join(aliases)
            for out, value in zip(fields, values):
                out.append(prefix + value)
    return ['\n\n'.join(x) for x in fields]


def make_values(rows, R, S, prefix):
    """Return only the fixed blinded column whitelist; no machine diagnostics."""
    result = []
    for i, row in enumerate(rows, 1):
        fields = registry_fields(row, R, S)
        link = 'https://scholar.google.com/scholar?q=' + quote_plus(row['printed_title'] or row['citation_printed'])
        printed_pmids = S.parse_multi(row['printed_pmids'])
        supplied_pmids = S.parse_multi(row['supplied_pmids'])
        recovered = [p for p in row.get('v36_parsed_pmids', '').split(';')
                     if p and p not in printed_pmids + supplied_pmids]
        displayed_pmids = json.dumps(list(dict.fromkeys(printed_pmids + recovered))) if recovered else row['printed_pmids']
        result.append([f'{prefix}-{i:03d}', row['review_id'], int(row['ref_index']), row['citation_printed'],
                       row['printed_dois'], displayed_pmids,
                       'DOI ' + row['supplied_dois'] + '\nPMID ' + row['supplied_pmids'],
                       *fields, link, '', '', ''])
    return result


def validate_xlsx(path, expected):
    """Saved XML checks cover both blinded views, exact text and blank ratings."""
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    n = len(expected)
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        for name in z.namelist():
            if name.endswith('.xml'):
                ET.fromstring(z.read(name))
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        sheets = wb.findall('s:sheets/s:sheet', ns)
        assert [el.attrib['name'] for el in sheets] == ['Rating', 'Case pages']
        assert all(el.get('state', 'visible') == 'visible' for el in sheets)
        shared = ET.fromstring(z.read('xl/sharedStrings.xml'))
        strings = [''.join(el.itertext()) for el in shared]
        sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        cells = {c.attrib['r']: c for c in sheet.findall('.//s:sheetData/s:row/s:c', ns)}
        def value(addr):
            c = cells.get(addr)
            if c is None:
                return ''
            v = c.find('s:v', ns)
            if v is None:
                return ''
            return strings[int(v.text)] if c.attrib.get('t') == 's' else v.text
        assert [value(f'{chr(65+i)}1') for i in range(16)] == HEADERS
        for i, row in enumerate(expected, 2):
            assert value(f'A{i}') == row[0]
            assert value(f'D{i}') == row[3]
            assert all(value(f'{col}{i}') == '' for col in ('N', 'O', 'P'))
        validations = sheet.findall('.//s:dataValidation', ns)
        assert any(v.attrib['sqref'] == f'N2:N{n+1}' for v in validations)
        for name in ('xl/worksheets/sheet1.xml', 'xl/worksheets/sheet2.xml'):
            root = ET.fromstring(z.read(name))
            assert not root.findall('.//s:f', ns)
        forbidden = ('v36_state', 'rule_id', 'author_note', 'title_score', 'id_checks', 'v36_wrong_fields')
        assert not any(x in HEADERS for x in forbidden)
    return {'xml_files_valid': True, 'cases': n, 'blank_rating_cells': 3*n,
            'exact_citation_strings': n, 'machine_columns_absent': True,
            'visible_blinded_sheets': ['Rating', 'Case pages']}


def write_workbook(path, values):
    """Export a flat rating table and readable printable case pages with no key."""
    if Path(path).exists():
        raise FileExistsError('Refusing to replace an existing potentially rated workbook')
    wb = xlsxwriter.Workbook(path, {'strings_to_formulas': False, 'strings_to_urls': False})
    header = wb.add_format({'font_name': 'Arial', 'font_size': 10, 'bold': True,
                           'bg_color': '#25364A', 'font_color': 'white', 'text_wrap': True,
                           'align': 'center', 'valign': 'vcenter'})
    body = wb.add_format({'font_name': 'Arial', 'font_size': 10, 'text_wrap': True, 'valign': 'top'})
    entry = wb.add_format({'font_name': 'Arial', 'font_size': 10, 'text_wrap': True,
                          'valign': 'top', 'bg_color': '#FFF4CF'})
    label = wb.add_format({'font_name': 'Arial', 'font_size': 10, 'bold': True,
                          'text_wrap': True, 'valign': 'top'})
    n = len(values)
    ws = wb.add_worksheet('Rating')
    ws.hide_gridlines(2)
    ws.freeze_panes(1, 3)
    ws.autofilter(0, 0, n, 15)
    ws.set_row(0, 44)
    widths = [15, 18, 10, 90, 34, 22, 42, 80, 85, 25, 50, 55, 25, 18, 25, 40]
    for j, title in enumerate(HEADERS):
        ws.write_string(0, j, title, header)
        ws.set_column(j, j, widths[j])
    for i, row in enumerate(values, 1):
        for j, value in enumerate(row):
            if len(str(value)) > 32767:
                raise ValueError(f'Excel cell limit exceeded for {row[0]}')
            if j == 2:
                ws.write_number(i, j, value, body)
            elif j == 12:
                ws.write_url(i, j, value, body, 'Search citation')
            elif j >= 13:
                ws.write_blank(i, j, None, entry)
            else:
                ws.write_string(i, j, value, body)
        lines = max(sum(max(1, math.ceil(len(part) / widths[j])) for part in str(value).split('\n'))
                    for j, value in enumerate(row[:12]))
        ws.set_row(i, min(409, max(95, lines * 14 + 8)))
    ws.data_validation(f'N2:N{n+1}', {'validate': 'list', 'source': ['Error', 'Correct', 'Cannot check']})
    ws.set_landscape()
    ws.repeat_rows(0)
    ws.repeat_columns(0, 2)
    ws.print_area(0, 0, n, 15)
    detail = wb.add_worksheet('Case pages')
    detail.hide_gridlines(2)
    detail.set_column('A:A', 19)
    detail.set_column('B:B', 82)
    detail.set_paper(9)
    detail.set_portrait()
    detail.set_margins(.3, .3, .3, .3)
    detail.fit_to_pages(1, 0)
    detail.set_footer('&RPage &P of &N')
    detail.set_header('&LCitation rating packet')
    breaks, positions, r = [], [], 0
    import textwrap
    for row in values:
        if r:
            breaks.append(r)
        start = r
        detail.write(r, 0, row[0], header)
        detail.write(r, 1, f'Review {row[1]}  /  Reference {row[2]}', header)
        detail.set_row(r, 26)
        r += 1
        for j in range(3, 16):
            title = HEADERS[j]
            text = str(row[j])
            # Small continuation rows prevent Excel's maximum row height clipping long bylines.
            chunks = []
            remaining = text
            while len(remaining) > 1400:
                cut = remaining.rfind(' ', 0, 1400) + 1 or 1400
                chunks.append(remaining[:cut])
                remaining = remaining[cut:]
            chunks.append(remaining)
            for k, chunk in enumerate(chunks):
                detail.write(r, 0, row[0] + '\n' + title + (' (continued)' if k else ''), label)
                if j >= 13:
                    detail.write_blank(r, 1, None, entry)
                    detail.set_row(r, 36 if j != 15 else 50)
                    if j == 13:
                        detail.data_validation(r, 1, r, 1, {'validate': 'list', 'source': ['Error', 'Correct', 'Cannot check']})
                elif j == 12:
                    detail.write_url(r, 1, text, body, 'Search citation')
                    detail.set_row(r, 36)
                else:
                    lines = sum(len(textwrap.wrap(paragraph, width=85, break_long_words=True)) or 1
                                for paragraph in chunk.split('\n'))
                    detail.write_string(r, 1, chunk, body)
                    detail.set_row(r, max(50, lines*15+10))
                r += 1
        positions.append({'case_id': row[0], 'start_row': start+1, 'end_row': r})
        detail.set_row(r, 10)
        r += 1
    detail.set_h_pagebreaks(breaks)
    detail.print_area(0, 0, r-1, 1)
    wb.close()
    checks = validate_xlsx(path, values)
    return checks, positions


def needed_pmids(rows, S):
    return [p for row in rows for p in S.parse_multi(row['printed_pmids']) +
            S.parse_multi(row['supplied_pmids']) + row.get('v36_parsed_pmids', '').split(';') +
            [row.get('canonical_pmid', ''), row.get('source_id', '') if row.get('source_id', '').isdigit() else ''] if p]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--shards', nargs=2, required=True)
    args = ap.parse_args()
    path, key_path = HERE / 'pilot_packet_100.xlsx', HERE / 'pilot_key_100.csv'
    if path.exists() or key_path.exists():
        raise FileExistsError('Packet/key already exist; refusing to overwrite possibly rated files')
    snapshot = HERE / 'data/pilot_exclusions_snapshot.json'
    if snapshot.exists():
        frozen = json.loads(snapshot.read_text())
        excluded, sources = set(frozen['reviews']), frozen['sources']
    else:
        excluded, sources = prior_reviews()
    pool = []
    for shard in args.shards:
        pool += [r for r in read(HERE / 'results' / shard / 's03_identity.csv')
                 if r['state'] == 'valid' and r['v36_state'] == 'corrupted' and r['review_id'] not in excluded]
    selected, allocation = proportional_sample(pool)
    S = load()
    R = Registry(S)
    R.preload_pmids(needed_pmids(selected, S))
    values = make_values(selected, R, S, 'P36')
    checks, positions = write_workbook(path, values)
    keys = [{'case_id': value[0], 'seed': SEED, 'shard': row['shard'],
             'review_id': row['review_id'], 'ref_index': row['ref_index'], 'ref_rid': row['ref_rid'],
             'v35_state': row['state'], 'v36_state': row['v36_state'], 'rule_id': row['rule_id'],
             'author_note': row['author_note'], 'evidence': row['evidence'],
             'stratum_population': sum(r['rule_id'] == row['rule_id'] for r in pool),
             'stratum_sample': allocation[row['rule_id']]} for row, value in zip(selected, values)]
    write(key_path, keys)
    assert not ({row['review_id'] for row in selected} & excluded)
    audit = {'seed': SEED, 'method': 'Hamilton proportional allocation, SRS within complete rule_id strata, shuffled case order',
             'holdout_unit': 'review', 'eligible_new_detections': len(pool), 'allocation': allocation,
             'eligible_rule_counts': dict(Counter(r['rule_id'] for r in pool)),
             'excluded_reviews': len(excluded), 'exclusion_sources': sources,
             'xml_checks': checks, 'case_page_rows': positions, 'live_network_calls': R.live_calls,
             'packet_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (HERE / 'results/packet_audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    R.close()
    print(json.dumps({k: audit[k] for k in ('seed', 'holdout_unit', 'eligible_new_detections', 'allocation', 'xml_checks')}, indent=2))


if __name__ == '__main__':
    main()
