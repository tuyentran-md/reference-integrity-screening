"""Synthetic policy checks for the v36c author matcher (no real case data)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import v36c.rules as C


def m(pf, pg, qf, qg, swapped=False, iswapped=False):
    return C.person_match(C.clean({'family': pf, 'given': pg}, True), C.clean({'family': qf, 'given': qg}), swapped, iswapped)


def test_typography_tolerated():
    assert m('Peng', 'Y D', 'Peng', 'YD')
    assert m('Hull', 'M', 'Hüll', 'Prof. Dr. Michael')
    assert m('Netto', 'J', 'Netto Júnior', 'J')
    assert m('Lessells', 'C. M.', 'Lessells', 'C. (Kate) M.')
    assert m('Moreira', 'L.D.S.G.', 'Moreira', 'Laís de Souza Gouveia')
    assert m('Obeidat', 'SM', 'OBEIDAT', 'SAFWAN M.')
    assert m('Savoviæ', 'J', 'Savović', 'Jelena')
    assert m('Góis', 'LM', 'G�is', 'Lislei Maia de')


def test_real_deviations_still_flagged():
    assert not m('Silva', 'CF', 'da Silva', 'Cristiane França')      # dropped particle
    assert not m('Møller', 'SH', 'Hedlund Møller', 'S')             # dropped surname part
    assert not m('Nasim', 'Z', 'Zakibakhsh', 'Nasim')                # given name used as surname
    assert not m('Leung', 'SK', 'Leung', 'SS')                       # different initials
    assert not m('Sevdalls', 'N', 'Sevdalis', 'N')                   # misspelling
    assert not m('Ming-Xi', 'X', 'Xue Ming-Xi', '')                  # Chinese given name as surname


def test_registry_split_artefacts():
    assert m('Nithin', 'R', 'R', 'Nithin')                           # lone initial in family field
    assert m('Han', 'S', 'Si-Yi', 'Han', swapped=True)               # registry byline given-first
    assert not m('Han', 'S', 'Si-Yi', 'Han', swapped=False)
    assert m('Qin', 'X', 'Xiaoyu', 'Q', iswapped=True)
    assert m('Aoki Maki', 'K.', 'Aoki', 'Maki K.')


def test_byline_swap_detection():
    swapped = [{'family': 'Si-Yi', 'given': 'Han'}, {'family': 'Hai-Bo', 'given': 'Yu'}, {'family': 'Wei-Zheng', 'given': 'Zhong'}]
    normal = [{'family': 'Xue', 'given': 'Ming-Xi'}, {'family': 'Chen', 'given': 'Zhi-Bin'}, {'family': 'Wang', 'given': 'Wei-Ming'}]
    persian = [{'family': 'Zakibakhsh', 'given': 'Nasim'}, {'family': 'Basharpoor', 'given': 'Sajjad'}]
    assert C.byline_swapped(swapped)
    assert not C.byline_swapped(normal)
    assert not C.byline_swapped(persian)


def test_printed_parser():
    names = C.printed_names('Jafar, Z. S., Ahmed, A., Mirza, M. J. & Ahtesham, K.', 'Jafar')
    assert [n['family'] for n in names] == ['Jafar', 'Ahmed', 'Mirza', 'Ahtesham']
