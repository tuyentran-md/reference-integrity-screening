"""Cache-only registry reader. Frozen databases use immutable read-only handles."""
import json
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit
from frozen import HERE, ROOT, readonly

PREFIX = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?'


class Registry:
    """Expose original records and normalized comparators without live requests."""

    def __init__(self, S):
        self.S = S
        self.doi_db = readonly(ROOT / 'census_20261013/doi_store.sqlite')
        self.dois = {}
        self.pmids = {}
        self.local = sqlite3.connect(HERE / 'data/registry_subset.sqlite')
        self.local.execute('create table if not exists pmid (id text primary key, record text)')
        self.pmids.update((key, json.loads(value)) for key, value in self.local.execute('select id,record from pmid'))
        for record in self.pmids.values():
            for person in record.get('author_details', []):
                family = person.get('family', '')
                last = family.rsplit(' ', 1)[-1]
                if not person.get('given') and ' ' in family and len(last) <= 5 and last.isupper():
                    person.update(family=family[:-(len(last) + 1)], given=last)
                    family = person['family']
                m = re.fullmatch(r'(.+?)\s+([A-Z]{1,5})\s+(?:Jr\.?|Sr\.?|[2-9](?:nd|rd|th)|II|III|IV)', family)
                if m:
                    person.update(family=m[1], given=m[2])
            if record.get('author_details'):
                first = record['author_details'][0]
                record.update(first_author=first['family'], first_given=first['given'])
        self.live_calls = 0
        self.pm_urls_scanned = 0

    def preload_pmids(self, wanted):
        """Index cached NCBI batch URLs, then read only batches with wanted PMIDs."""
        wanted = set(wanted) - set(self.pmids)
        if not wanted:
            return
        db = self.S.RI._DB
        urls = []
        remaining = set(wanted)
        for (url,) in db.execute('select url from c where url>=? and url<?', (PREFIX, PREFIX + '\uffff')):
            self.pm_urls_scanned += 1
            ids = set(parse_qs(urlsplit(url).query).get('id', [''])[0].split(','))
            if ids & remaining:
                urls.append(url)
                remaining -= ids
                if not remaining:
                    break
        for url in urls:
            (body,) = db.execute('select body from c where url=?', (url,)).fetchone()
            try:
                result = json.loads(body).get('result', {})
            except (ValueError, AttributeError):
                continue
            for pmid in sorted(result.keys() - {'uids'}):
                raw = result[pmid]
                if isinstance(raw, dict) and not raw.get('error'):
                    self.pmids[pmid] = self.pubmed_record(pmid, raw)
                    self.local.execute('insert or ignore into pmid values (?,?)', (pmid, json.dumps(self.pmids[pmid])))
        self.local.commit()

    @staticmethod
    def pubmed_record(pmid, raw):
        people = [a for a in raw.get('authors', []) if a.get('authtype') != 'Editor']
        details = []
        for a in people:
            name = a.get('name', '')
            name = re.sub(r'\s+(?:Jr\.?|Sr\.?|[2-9](?:nd|rd|th)|II|III|IV)$', '', name, flags=re.I)
            last = name.rsplit(' ', 1)[-1]
            fam = name[:-(len(last) + 1)] if ' ' in name and len(last) <= 5 and last.isupper() else name
            details.append({'family': fam, 'given': name[len(fam):].strip(),
                            'group': a.get('authtype') == 'CollectiveName'})
        first = details[0] if details else {}
        years = sorted({int(y) for k in ('pubdate', 'epubdate')
                        for y in re.findall(r'(?:19|20)\d\d', raw.get(k, ''))[:1]})
        return {'id': pmid, 'pmid': pmid, 'title': raw.get('title', '') or raw.get('booktitle', ''),
                'doi': next((a['value'].lower() for a in raw.get('articleids', []) if a.get('idtype') == 'doi'), ''),
                'journal': raw.get('fulljournalname', '') or raw.get('booktitle', ''),
                'journal_abbr': raw.get('source', ''), 'years': years, 'year': str(min(years)) if years else '',
                'volume': raw.get('volume', ''), 'pages': raw.get('pages', ''),
                'first_page': re.split(r'[-–]', raw.get('pages', ''))[0],
                'first_author': first.get('family', ''), 'first_given': first.get('given', ''),
                'group_author': first.get('group', False), 'authors': [p['family'] for p in details],
                'author_details': details, 'book': raw.get('doctype') in ('book', 'chapter'),
                'cache_origin': 'code/cache/m11_http.sqlite:NCBI esummary'}

    def doi(self, value):
        value = value.lower()
        if value not in self.dois:
            row = self.doi_db.execute('select items from d where doi=?', (value,)).fetchone()
            items = json.loads(row[0]) if row else []
            records = []
            for item in items:
                rec = self.S.cr_record(item)
                rec['years'] = sorted(rec['years'])
                rec['author_details'] = [{'family': a.get('family', '') or a.get('name', ''),
                                          'given': a.get('given', ''), 'group': not a.get('family')}
                                         for a in item.get('author', [])]
                rec['volume'] = item.get('volume', '')
                rec['pages'] = item.get('page', '')
                rec['type'] = item.get('type', '')
                rec['cache_origin'] = 'census_20261013/doi_store.sqlite'
                records.append(rec)
            if not records:
                rec = self.S.csl_record(value)
                if rec:
                    rec['years'] = sorted(rec.get('years', []))
                    # v35's CSL adapter overwrote the returned DOI with the query.
                    # Keep the registry's actual DOI so truncations cannot self-confirm.
                    from urllib.parse import quote
                    raw = json.loads(self.S.cached_get('https://doi.org/' + quote(value, safe='/')) or '{}')
                    rec['doi'] = (raw.get('DOI') or value).lower()
                    rec['id'] = rec['doi']
                    rec['author_details'] = [{'family': a.get('family', '') or a.get('name', ''),
                                              'given': a.get('given', ''), 'group': not a.get('family')}
                                             for a in raw.get('author', []) if isinstance(a, dict)]
                    rec['cache_origin'] = 'pipeline/cache/http.sqlite:doi.org CSL'
                    records.append(rec)
            self.dois[value] = records
        return self.dois[value]

    def handle(self, value):
        from urllib.parse import quote
        body = self.S.RI.get('https://doi.org/api/handles/' + quote(value, safe='/'))
        if body == '__404__':
            return {'responseCode': 100}
        try:
            return json.loads(body or '{}')
        except ValueError:
            return {}

    def pmid(self, value):
        return self.pmids.get(value)

    def close(self):
        self.local.close()
        self.doi_db.close()
        self.S._offline_pipeline.close()
        self.S.RI._DB.close()
