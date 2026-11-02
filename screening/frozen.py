"""Load the local v35 copy with every HTTP/cache entry point made read-only."""
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def readonly(path):
    """Immutable read avoids SQLite journal/SHM writes; reject an active WAL."""
    path = Path(path)
    wal = Path(str(path) + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError(f'Active WAL: cannot read an immutable snapshot: {path}')
    return sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)


def load():
    sys.path.insert(0, str(HERE / 'vendor'))
    import config
    config.PIPE = HERE
    config.OUT = HERE / 'results'
    config.LOGS = HERE / 'results'
    import ref_identity as RI
    import s03_resolve_identity as S
    import s03_v35_patch
    if not getattr(S, '_v36_installed', False):
        s03_v35_patch.install(S)
        S._v36_installed = True
    RI._DB.close()
    RI._DB = readonly(ROOT / 'code/cache/m11_http.sqlite')
    pipeline = readonly(ROOT / 'pipeline/cache/http.sqlite')

    def ri_get(url):
        row = RI._DB.execute('select body from c where url=?', (url,)).fetchone()
        return row[0] if row else ''

    def cached_get(url, **kwargs):
        row = pipeline.execute('select v from c where k=?', (url,)).fetchone()
        return row[0] if row else None

    RI.get = ri_get
    S.cached_get = cached_get
    S.search_openalex = lambda e: S.pick([], e)
    S._offline_pipeline = pipeline
    return S
