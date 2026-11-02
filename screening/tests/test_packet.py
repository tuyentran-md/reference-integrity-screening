"""Exercise packet export on synthetic data only; never inspect pilot case content."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import pytest
import packet
from prepare import HERE, read, write


def test_blinded_packet_xml_and_no_overwrite(monkeypatch):
    class FakeRegistry:
        live_calls = 0
        def __init__(self, unused):
            pass
        def preload_pmids(self, values):
            pass
        def close(self):
            pass
    with tempfile.TemporaryDirectory(dir=HERE / 'results', prefix='synthetic_packet_') as temp:
        root = Path(temp)
        (root / 'results').mkdir()
        for shard, count in [('synthetic_a', 170), ('synthetic_b', 30)]:
            dest = root / 'results' / shard
            dest.mkdir()
            rows = []
            for i in range(count):
                rows.append({'review_id': f'{shard}_{i}', 'ref_index': str(i+1), 'ref_rid': f'R{i}',
                             'shard': shard, 'state': 'valid', 'v36_state': 'corrupted',
                             'rule_id': 'A' if shard.endswith('a') else 'B',
                             'author_note': 'MACHINE_ONLY', 'evidence': 'MACHINE_ONLY',
                             'citation_printed': '=Exact synthetic citation. Do not interpret as a formula.',
                             'printed_title': 'Synthetic citation', 'printed_dois': '', 'printed_pmids': '',
                             'supplied_dois': '', 'supplied_pmids': '', 'v36_parsed_pmids': ''})
            write(dest / 's03_identity.csv', rows)
        monkeypatch.setattr(packet, 'HERE', root)
        monkeypatch.setattr(packet, 'prior_reviews', lambda: (set(), {}))
        monkeypatch.setattr(packet, 'load', lambda: SimpleNamespace(parse_multi=lambda value: []))
        monkeypatch.setattr(packet, 'Registry', FakeRegistry)
        monkeypatch.setattr('sys.argv', ['packet.py', '--shards', 'synthetic_a', 'synthetic_b'])
        packet.main()
        audit = json.loads((root / 'results/packet_audit.json').read_text())
        assert audit['allocation'] == {'A': 85, 'B': 15}
        assert audit['xml_checks']['blank_rating_cells'] == 300
        key = list(read(root / 'pilot_key_100.csv'))
        assert len(key) == 100
        import zipfile
        with zipfile.ZipFile(root / 'pilot_packet_100.xlsx') as z:
            assert b'MACHINE_ONLY' not in z.read('xl/sharedStrings.xml')
        with pytest.raises(FileExistsError):
            packet.main()
