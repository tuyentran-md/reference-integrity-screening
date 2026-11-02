from v37.rules import journal_ok, strict_initials, norm_title


def test_omitted_middle_initial_is_error():
    assert not strict_initials('J', 'JM')
    assert not strict_initials('J', 'J Martin')
    assert strict_initials('JM', 'J Martin')
    assert strict_initials('J.M.', 'JM')
    assert strict_initials('', 'JM')          # absent given name cannot refute


def test_journal_abbreviations_are_not_errors():
    assert journal_ok('J Pediatr Surg. 2019;54:1-5.', ['Journal of Pediatric Surgery'])
    assert journal_ok('N Engl J Med 2020; 382: 1', ['The New England journal of medicine', 'N Engl J Med'])
    assert journal_ok('BMJ 2021;372:n71', ['BMJ'])
    assert journal_ok('Sci Rep. 2020;10:1', ['Scientific Reports'])
    assert journal_ok('PLoS One 10(5): e0124500', ['PLoS ONE'])


def test_clearly_different_journal_is_error():
    assert not journal_ok('N Engl J Med 2020; 382: 1', ['Journal of Pediatric Surgery', 'J Pediatr Surg'])
    assert not journal_ok('J Pediatr Surg 2019;54:1', ['The New England journal of medicine', 'N Engl J Med'])


def test_title_typography_only():
    assert norm_title('Nutrition and the <i>circadian</i> system.') == norm_title('Nutrition & the circadian system')
    assert norm_title('[Effect of fluoxetine].') == norm_title('Effect of Fluoxetine')
    assert norm_title('acetylchonesterase inhibitors') != norm_title('acetylcholinesterase inhibitors')
