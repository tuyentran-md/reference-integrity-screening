"""Synthetic tests specify general rule boundaries, not human case ratings."""
import copy
from types import SimpleNamespace
import pytest
from rules import name_key, given_disagrees, author_check, bare_tail, same_work
from packet import proportional_sample


def test_typographic_names_only():
    for a, b in [('García', 'Garcia'), ('van der Waals', 'Vanderwaals'),
                 ('O’Neil', "O'Neil"), ('Østbye', 'Ostbye'), ('Ayık', 'Ayik')]:
        assert name_key(a) == name_key(b)
    assert name_key('Gorji') != name_key('Heidari Gorji')
    assert name_key('Ali') != name_key('Heidari Gorji')
    assert name_key('Smith') != name_key('Smyth')


def test_given_initials_and_full_names():
    assert not given_disagrees('C. A.', 'Cheryl Ann')
    assert not given_disagrees('J', 'John Joseph')
    assert given_disagrees('H', 'Mohammed')
    assert given_disagrees('John', 'James')


S = SimpleNamespace(author_segment=lambda e: e['raw'].split('. Title')[0],
                    corporate=lambda text: 'Consortium' in text)


def test_any_available_author_not_just_first():
    e = {'raw': 'Smith A, Jones B. Title', 'first_author': 'Smith'}
    source = {'first_author': 'Smith', 'author_details': [
        {'family': 'Smith', 'given': 'A'}, {'family': 'Jones', 'given': 'B'}]}
    byline = {'names': [{'family': 'Smith', 'given': 'A'}, {'family': 'Jonse', 'given': 'B'}]}
    rules, note, _ = author_check({}, e, source, byline, S)
    assert rules == ['AUTHOR_STRICT'] and note == 'different_surname'


def test_duplicate_surnames_choose_compatible_given_names():
    e = {'raw': 'Li B, Li A. Title', 'first_author': 'Li'}
    source = {'first_author': 'Li', 'author_details': [
        {'family': 'Li', 'given': 'A'}, {'family': 'Li', 'given': 'B'}]}
    names = {'names': [{'family': 'Li', 'given': 'B'}, {'family': 'Li', 'given': 'A'}]}
    assert not author_check({}, e, source, names, S)[0]


def test_group_and_missing_authors_are_not_flagged():
    assert author_check({}, {'raw': 'Consortium. Title', 'first_author': 'Consortium'},
                        {'first_author': 'Smith'}, {'group': True}, S)[1] == 'not_compared_group'
    assert author_check({}, {'raw': '. Title', 'first_author': ''},
                        {'first_author': 'Smith'}, {}, S)[1] == 'not_compared_missing'


def test_missing_and_reordered_authors_do_not_create_name_errors():
    e = {'raw': 'Jones B. Title', 'first_author': 'Jones'}
    source = {'first_author': 'Smith', 'author_details': [
        {'family': 'Smith', 'given': 'A'}, {'family': 'Jones', 'given': 'B'}]}
    assert not author_check({}, e, source, {'names': [{'family': 'Jones', 'given': 'B'}]}, S)[0]


def test_terminal_number_is_not_a_doi_or_page_suffix():
    s = SimpleNamespace(parse_multi=lambda value: [])
    assert bare_tail({'citation_printed': 'Author. Title. Journal 2000;1:10. 12345678'}, s) == '12345678'
    for text in ('Title. Journal 2000;1:12345678', 'Title. https://x.org/12345678',
                 'Title. 10.1234/12345678', 'Title. 12345678-9'):
        assert not bare_tail({'citation_printed': text}, s)


def test_alias_fingerprint_rejects_separate_publications():
    import html
    s = SimpleNamespace(html=html)
    a = {'title': 'A Study', 'first_author': 'Smith', 'years': [2020], 'doi': '10.1/a', 'journal': 'Journal of Examples'}
    b = {**a, 'doi': '10.2/b'}
    assert same_work(a, b, s)
    assert not same_work(a, {**b, 'doi': '10.2/abstract.1'}, s)
    assert not same_work(a, {**b, 'years': [2022]}, s)
    assert not same_work(a, {**b, 'first_author': 'Smyth'}, s)
    assert not same_work(a, {**b, 'journal': 'Different Journal'}, s)
    assert not same_work(a, {**b, 'book': True}, s)


def test_proportional_sampling_is_fixed_and_order_independent():
    rows = [{'rule_id': 'A' if i < 170 else 'B', 'review_id': f'R{i:03d}', 'ref_index': '1'} for i in range(200)]
    selected, allocation = proportional_sample(rows)
    other, other_allocation = proportional_sample(list(reversed(rows)))
    assert allocation == {'A': 85, 'B': 15} == other_allocation
    assert selected == other
    assert len({r['review_id'] for r in selected}) == 100
    with pytest.raises(ValueError):
        proportional_sample(rows[:99])


def test_identifier_absence_alias_and_other_error_boundaries():
    import html
    from rules import apply
    target = {'id': '10.1234/trial+', 'doi': '10.1234/trial+', 'title': 'Exact trial title',
              'first_author': 'Smith', 'first_given': 'A', 'years': [2020], 'journal': 'Journal'}
    entry = {'raw': 'Smith A. Exact trial title. Journal 2020;1:2.', 'first_author': 'Smith',
             'title': target['title'], 'dois': ['10.1234/trial'], 'pmids': [], 'hint_pmids': ['12345678']}
    flags = ['doi_wrong', 'pmid_wrong', 'title_wrong', 'author_wrong', 'year_wrong', 'id_points_to_other_work']
    s = SimpleNamespace(entry=lambda row: entry, FLAGS=flags, html=html,
                        author_segment=lambda e: 'Smith A', corporate=lambda seg: False,
                        parse_multi=lambda text: [])
    row = {'state': 'valid', 'source_via': 'doi', 'source_id': target['doi'],
           'source_journal': 'Journal', 'canonical_title': target['title'],
           'canonical_first_author': 'Smith', 'canonical_doi': target['doi'],
           'canonical_pmid': '12345678', 'canonical_years': '2020', 'printed_first_author': 'Smith',
           **{f: '0' for f in flags}}
    class FakeRegistry:
        response = {}
        alias = False
        def doi(self, value):
            return [target] if value == target['doi'] or self.alias else []
        def pmid(self, value):
            return {**target, 'pmid': value}
        def handle(self, value):
            return self.response
    registry = FakeRegistry()
    assert apply(row, {}, registry, {}, s)['v36_state'] == 'valid'
    registry.response = {'responseCode': 100}
    assert apply(row, {}, registry, {}, s)['rule_id'] == 'DOI_TERMINAL_CHARACTER'
    registry.alias = True
    registry.response = {'responseCode': 1}
    alias = apply(row, {}, registry, {}, s)
    assert alias['v36_state'] == 'valid'
    assert alias['rule_id'] == 'DOI_ALIAS_SAME_WORK'
    other = apply({**row, 'state': 'corrupted', 'title_wrong': '1'}, {}, registry, {}, s)
    assert other['v36_state'] == 'corrupted'
    assert other['v36_wrong_fields'] == 'title'
    unresolved = apply({**row, 'state': 'unresolved', 'unresolved_reason': 'not_found'}, {}, registry, {}, s)
    assert unresolved['v36_state'] == 'unresolved'
