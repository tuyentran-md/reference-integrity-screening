"""S02 v2.5 repairs (E4 census, after QC round 1 of 18/10/2026: 18/100 references had a wrong kind, year, title or first author).

Applied by wrapping the frozen ref_identity.parse_ref in memory; the frozen files (and the SHA-checked S03 code) are untouched.
Every rule is a text-shape rule about what is PRINTED, none looks at an external registry:
  years   a year inside a DOI, a URL or an access/"cited" date is not a publication year (structured <year> inside
          <date-in-citation> likewise).
  kind    explicit JATS journal type stays 'journal'. For 'other'/missing types, link text and access dates are removed
          before the non-journal hints are tested, and a printed DOI/PMID or a volume-pages shape counts as journal evidence.
          A place "(State): publisher; year" or an evidence-report shape is a book/report.
  author  a leading '*' or other mark is dropped; lowercase prefixes (el-, al-, van, de ...) belong to the surname; the printed
          first author is preferred to a Google-Scholar link parameter when the text opens with 'Surname Initials' or 'Surname,'.
  title   the guessed title skips an author block even when a name has a non-ASCII letter or ends in 'al.'."""
import re
from lxml import etree
import ref_identity as RI

LETTER = r"[^\W\d_]"
UP = "A-Z\u00c0-\u00d6\u00d8-\u00de\u0100\u0102\u0104\u0106\u010c\u010e\u0110\u0118\u011a\u011e\u0130\u0141\u0143\u0147\u0150\u0152\u0158\u015a\u015e\u0160\u0162\u0164\u016e\u0170\u0178\u0179\u017b\u017d"
PREFIX = r"(?:el|al|de|di|du|da|dos|del|van|von|der|den|le|la|bin|ibn|abu|ter|ten|op|het|[’'`]t)"
DOI_ANY = re.compile(r'(?:https?://(?:dx\.)?doi\.org/|doi\s*:?\s*)?\(?\s*10\.\d{4,9}/\S+|(?<!\d)\d{5,9}/[a-z]{2,}\S*\.(?:19|20)\d\d', re.I)
URL = re.compile(r'(?:https?://|www\.)\S+', re.I)
ACCESS = re.compile(r'\([^()]{0,40}\b(?:last )?accessed\b[^()]{0,20}\)|[\[(]\s*(?:last\s+)?(?:accessed|cited|retrieved|consulted|viewed|updated)\b[^\])]{0,50}[\])]|'
                    r'\b(?:last\s+)?(?:accessed|retrieved|consulted|viewed)\b(?:\s+on)?\s*:?\s*(?:\d{1,2}(?:st|nd|rd|th)?\s+)?'
                    r'(?:[A-Za-z]{3,9}\.?\s+)?(?:\d{1,2},?\s+)?(?:19|20)\d\d', re.I)
AVAIL = re.compile(r'\bavailable\s+(?:online\s+)?(?:from|at)\b\s*:?', re.I)
STATE_PUB = re.compile(r'[A-Z][a-z]+ \([A-Z]{2}\):\s')                      # NLM book place: "Rockville (MD): AHRQ; 2004"
REPORT = re.compile(r'evidence reports?|technology assessments?|\breport (?:no|number)\b|\bno\.\s*\d+\)', re.I)
VOLPAGES = re.compile(r'(?<!\d)\d{1,3}(?:[–-]\d{1,3})?\s*(?:,\s*|\s+|\()\s*\d{1,6}\s*[–-]\s*\d{1,6}|\b(?:19|20)\d\d\s*[;,]\s*\d{1,4}\s*[,(:;]|;\s*\d{1,4}\s*\(|\b\d{1,4}\s*:\s*[eE]?\d+')
COLLAB = re.compile(r'\b(?:investigators?|group|study|collaborat\w*|committee|consortium|trial|network|team|working party|task force)\b', re.I)
PUBLISHER = re.compile(r'(?:[:;,]|\.)\s*(?:Elsevier|Springer(?: Nature)?|Wiley(?:-Blackwell)?|Routledge|Oxford University Press|Cambridge University Press|Academic Press|CRC Press|McGraw[- ]Hill|Saunders|Lippincott(?: Williams & Wilkins)?|Blackwell|Thieme|SAGE|Sage Publications|Taylor & Francis|Karger|Humana Press|Wolters Kluwer|Churchill Livingstone|Mosby|Jones & Bartlett|Guilford Press|Pearson|Palgrave Macmillan)\b\s*[;,.:]?\s*(?:\(?(?:19|20)\d\d)', re.I)
DOI_ISBN = re.compile(r'/(?:97[89]|isbn)', re.I)
NAME_AT_START = re.compile(rf"^(?P<n>(?:{PREFIX}[-\s]*)*[A-Z]{LETTER}+(?:[-'’]{LETTER}+)*)(?=\s+[A-Z]{{1,3}}(?:[\s,.]|$)|,)", re.U)
SKIP_LEAD = re.compile(r'^\s*(?:[\[(]?\d{1,5}[.\])]?[\s•*.▪]+|[\*†‡§#+‘’\'"“”•▪\s]+)+')
NAMEISH = re.compile(rf"^(?:(?:{PREFIX}-)?[{UP}]{LETTER}*(?:[-'’]{LETTER}+)+|(?:{PREFIX}-)?[{UP}]{LETTER}+(?:[-'’]{LETTER}+)*|[A-Z]{{1,3}}|[A-Z]\.?(?:-?[A-Z]\.?){{0,3}}|{PREFIX}|et|al|and|&)$", re.U)

_orig = RI.parse_ref


PAGES = re.compile(r'(?<=[\d)\]])\s*:\s*[A-Za-z]{0,2}\d{3,5}(?:\s*[–‐‑−-]\s*[A-Za-z]{0,2}\d{1,5})?|'
                   r'(?<=[:;,)])\s*\d{3,5}\s*[–‐‑−-]\s*\d{2,5}\b|\bpp?\.?\s*\d{1,5}\s*[–‐‑−-]\s*\d{1,5}|'
                   r'(?<=\d)\s*,\s*\d{3,5}\s*[–‐‑−-]\s*\d{1,5}', re.I)


def _strip_links(s):
    s = DOI_ANY.sub(' ', s)
    s = URL.sub(' ', s)
    s = ACCESS.sub(' ', s)
    return AVAIL.sub(' ', s)


def _no_pages(s):
    return PAGES.sub(' ', s)


TC = r"(?:[^.?!]|\.(?=\S)|(?<=\bvs)\.|(?<=\bspp)\.|(?<=\bsp)\.|(?<=\bSt)\.|(?<=\bno)\.|(?<=\bFig)\.)"


def split_segments(s):
    parts = []
    for seg in re.split(r'(?<=[.?!])\s+', s):
        if parts and (re.search(r'(?:^|\s)\d{1,2}\.$', parts[-1]) or re.search(r'\b(?:vs|spp|sp|St|no|Fig|Dr|Mr|Mrs|approx|ca|cf)\.$', parts[-1])):
            parts[-1] += ' ' + seg           # "GRADE guidelines: 3. Rating", "high vs. low"
        elif parts and parts[-1].endswith('?') and len(parts[-1].split()) >= 3 and len(seg.split()) >= 5 \
                and not re.search(r'\b(?:19|20)\d\d\b|\d+\s*[:(]\s*\d', seg[:60]) and not NAMEISH_RUN.match(seg):
            parts[-1] += ' ' + seg           # "Are 6 more accurate than 4? The influence of ..." (a subtitle, not the journal)
        else:
            parts.append(seg)
    return parts


NAMEISH_RUN = re.compile(r"^(?:[A-Z][\w’'-]*\.?\s+){1,6}[A-Z]{1,3}[.,\s]")


def guess_title(raw):
    s = re.sub(r'^\s*[\[(]?\d{1,3}[.\])]?\s+', '', re.sub(r'\bElectronic address\b[^:.]{0,40}:\s*', '', raw))
    s = re.sub(r'^[^\W\d_][\w’\'-]+(?:\s+(?:et al\.?|and|&)\s*(?:[^\W\d_][\w’\'-]+)?)?\s*\((?:19|20)\d\d[a-z]?\)\s+(?=[^\W\d_][\w’\'-]+\s+[A-Z]{1,3}[,\s])', '', s)[:1500]   # 'Page et al. (2020) Page MJ, ...'
    for pat in (r'\((?:19|20)\d\d[a-z]?\)\.?\s*(' + TC + r'{10,300})[.?!]', r'\bet al\.?,?\s*(' + TC + r'{10,300})[.?!]'):
        m = re.search(pat, s)
        if m and len(re.findall(r'[A-Za-z]{3,}', m.group(1))) >= 3:
            return m.group(1)
    m = re.search(r'\((?:19|20)\d\d[a-z]?\)\.?\s*([^.?!(]{4,120})[.?!]', s)          # "Authors (2013) Short title. Journal"
    if m and re.search(r'[A-Za-z]{4,}', m.group(1)) and m.start() < 200:
        return m.group(1)
    prev_etal = prev_authors = False
    parts = split_segments(s)
    for n, seg in enumerate(parts[:6]):
        toks = [t.strip(',;:.') for t in seg.split()]
        toks = [t for t in toks if t]
        if prev_authors and len(toks) >= 2 and not COLLAB.search(seg):   # the segment after an author block is the title
            namey2 = sum(bool(NAMEISH.match(t)) for t in toks) / len(toks) >= 0.8
            if prev_etal or not namey2 or len(toks) >= 4:
                if not namey2 or prev_etal:
                    return seg.rstrip('.?! ')
        if len(toks) < 4:
            continue
        namey = sum(bool(NAMEISH.match(t)) for t in toks) / len(toks) >= 0.8
        if namey and n > 0:      # a later segment is an author block only if it carries initials; a Title-Case title does not
            initials = sum(bool(re.fullmatch(r'[A-Z]{1,3}', t)) for t in toks) / len(toks)
            namey = initials >= 0.25 or seg.rstrip().lower().endswith('et al.')
        if namey:
            prev_etal = seg.rstrip().lower().endswith('et al.'); prev_authors = True
            continue
        return seg.rstrip('.?! ')
    return ''


INIT_FIRST_RUN = re.compile(r"^(?:(?:[A-Z]\.\s*-?\s*)+[^\W\d_][\w’'-]+(?:\s[^\W\d_][\w’'-]+)?,\s*(?:and\s+|&\s+)?)+")


def chapter_title(raw):
    m = re.search(r'\bIn:?\s', raw)
    pre = re.sub(r'^\s*[\[(]?\d{1,3}[.\])]?\s+', '', raw[:m.start()]) if m else ''
    segs = [x.strip(' .') for x in split_segments(pre) if x.strip(' .')]
    segs = [x for x in segs if not re.match(r'^(?:[^\W\d_][\w’\'-]*\s+)?[^\W\d_][\w’\'-]+(?:\s[A-Z]{1,3})?(?:,\s*(?:[^\W\d_][\w’\'-]*\s+)?[^\W\d_][\w’\'-]+\s[A-Z]{1,3})*(?:,\s*et al)?$', x) or len(x.split()) >= 4]
    cand = [x for x in segs[1:] if not re.fullmatch(r'(?:19|20)\d\d', x)]
    return cand[-1] if cand else ''


def strip_journal_tail(title, raw):
    """'... A Global Perspective Humanities and Social Sciences Communications' + '. Human Soc Sci Commun . ( 2024 )'."""
    i = raw.find(title)
    if i < 0:
        return title
    m = re.match(r'\s*\.?\s*([A-Z][\w&.-]*(?:\s+[A-Za-z][\w&.-]*){0,5})\s*\.?\s*[\[(;,]?\s*(?:\(?\s*)?(?:19|20)\d\d', raw[i + len(title):])
    if not m:
        return title
    abbr = [a.strip('.') for a in m.group(1).split() if a.strip('.')]
    if len(abbr) < 2:
        return title  # single-word journal names (e.g., Diabetes) may also end the title
    words = title.split()
    stop = {'and', 'of', 'the', 'for', '&', 'in', 'on'}
    for k in range(len(abbr), len(abbr) + 4):
        tail = words[-k:] if k <= len(words) else None
        if not tail or k >= len(words):
            continue
        core = [w for w in tail if w.lower() not in stop]
        if len(core) == len(abbr) and all(w[0].isupper() for w in core) and all(w.lower().startswith(a.lower()[:3]) for w, a in zip(core, abbr)):
            return ' '.join(words[:-k])
    return title


def strip_author_run(t):
    m = re.match(r'^(?:[A-Z][a-z]+\s+){1,2}(?:[A-Z]{2}|[A-Z]-[A-Z])\.\s+(?=[A-Z][a-z])', t)
    if m and len(t[m.end():].split()) >= 4:
        t = t[m.end():]
    """'Prokopez CR, Cesoni OM, Vallejos M Prevalence of x' -> 'Prevalence of x': drop a leading author list."""
    m = INIT_FIRST_RUN.match(t)
    if m and len(t[m.end():].split()) >= 3:
        t = t[m.end():]
    m = re.match(r"^[^\W\d_][\w’'-]+,\s+(?=[A-Z][a-z])", t)       # "Graves, The use of ..." (last name of a cut list)
    if m and re.match(r'^\s*(?:The|A|An|Effect|Effects|Use|Role|Impact)\b', t[m.end():]):
        t = t[m.end():]
    toks = t.split()
    k = 0
    while k < len(toks) and NAMEISH.match(toks[k].strip(',;:.')) and not toks[k].endswith(':'):
        k += 1
    ini = [i for i in range(k) if re.fullmatch(r'[A-Z]{1,3}', toks[i].strip(',;:.'))]
    names = [i for i in range(k) if len(toks[i].strip(',;:.')) >= 2 and not re.fullmatch(r'[A-Z]{1,3}', toks[i].strip(',;:.')) and toks[i][0].isupper() and toks[i].strip(',;:.') not in ('And', 'Of', 'The')]
    if len(ini) >= 2 and names and names[0] < ini[-1] and ini[-1] + 1 < len(toks) and ini[0] < 8:
        rest = ' '.join(toks[ini[-1] + 1:])
        if len(rest.split()) >= 3:
            return rest
    return t


PARTICLE = re.compile(r"^(?:(?:aan|de|den|der|van|von|ten|ter|te|da|di|du|del|della|dos|das|le|la|el|al|op|het|[’'`]t|d[’'])[\s-]*)+$", re.I)
ORG = re.compile(r'\b(?:Organi[sz]ation|Commission|College|Association|Society|Institute|Agency|Ministry|Department|Council|'
                 r'Foundation|Cent(?:er|re)s?|Committee|Federation|Union|Office|Bank|Programme|Program|Service|Board|Authority|'
                 r'Academy|Alliance|Coalition|Forum|Fund|Collaboration|Network|Team|Group|Bureau|Administration|Government|'
                 r'Consortium|Registry|Trust|Survey|Statistics|Unit|Nations)\b')
ORG_WORD = re.compile(r"^(?:U\.\s?[SK]\.|[A-Z][\w’'&.-]*|of|and|for|the|on|in|&|de|du|la|des|et|für|und|del|y|e)$")
PREPRINT = re.compile(r'\[\s*preprint\s*\]|\bpreprint\b|\b(?:bio|med|chem|psy|soc)rxiv\b|\barxiv\b|research square|\bssrn\b|preprints\.org', re.I)
CONF = re.compile(r'\b(?:International|Annual|National|World|European|Asian)\s+(?:Conference|Congress|Symposium|Workshop)\b|\bConference on\b|\bProceedings of (?:the )?\d+(?:st|nd|rd|th)\b|\bProceedings of [A-Z]{3,}\b', re.I)
THESIS = re.compile(r'\b(?:M\.?\s?S\.?c?|Ph\.?\s?D|MD|MPH|master(?:\'?s)?|doctoral|bachelor(?:\'?s)?)\s*(?:thesis|dissertation)\b|\b(?:thesis|dissertation)\b', re.I)
PREPRINT_DOI = ('10.21203/', '10.20944/', '10.2139/', '10.48550/', '10.31234/', '10.31219/')
MEDRXIV_DOI = re.compile(r'^10\.1101/(?:19|20)\d\d\.\d\d\.\d\d\.')
BOOK_YEAR = re.compile(r'\([A-Z]{2}\):\s[^;]{3,150};\s*((?:19|20)\d\d)')        # NLM book: "Rockville (MD): AHRQ; 2004"
GLUED_YEAR = re.compile(r'(?<!\d)((?:19|20)\d\d)(?=\d{1,8}\s*[–-]\s*\d)')          # "Clin Infect Dis 200847634–41"
GLUED_YEAR2 = re.compile(r'(?<![\d.])((?:19|20)\d\d)(?=\d{1,3}[eE]?\d{2,6}(?:\s*[–-]\s*\d+)?(?:\s*[.(\])]|\s*$))')   # "201395e104"
ACCESS_LEAD = re.compile(r'^\s*[\[(]+\s*(?:accessed|cited|retrieved|viewed|updated)', re.I)


def _txt(el):
    return ' '.join(''.join(el.itertext()).split()) if el is not None else ''


PART = r"(?:(?i:van|von|de|del|della|der|den|di|du|da|dos|das|le|la|el|al|op|het|ter|ten|aan|los|las|bin|ibn|abu|[’'`]t)[\s-]+|(?i:d)[’'])"
NAME1 = rf"[{UP}]{LETTER}*(?:[-'’‐][{UP}]?{LETTER}+)*{LETTER}"
NAME2 = rf"[{UP}][a-z\u00df-\u00f6\u00f8-\u00ff]{LETTER}*(?:[-'’‐][{UP}]?{LETTER}+)*"
AFTER = rf"(?=\s+[{UP}]{{1,3}}(?:[\s,;.]|$)|\s*,|\s+et al)"
SPELLED = re.compile(rf"^[^,]{{1,60}},\s*[{UP}]{LETTER}{{3,}}\s+[{UP}]{LETTER}{{3,}}(?:\s+[{UP}]\.?)?\s*[,.;]", re.U)


def printed_author(lead):
    """The first author as the text prints it, preserving compound surnames."""
    mcompound = re.match(rf'^([{UP}]{LETTER}+(?:[-’]{LETTER}+)?)\s+([{UP}]{LETTER}+(?:[-’]{LETTER}+)?)\s+[A-Z]{{1,4}}\s*,', lead)
    if mcompound:
        return mcompound.group(1) + ' ' + mcompound.group(2)
    rest_spelled = bool(SPELLED.match(lead))            # "Stewart Nicola A., Chantrey Jonquille, ..." = Surname Given
    for k in (3, 2, 1, 0):
        if k and rest_spelled:
            continue
        m = re.match(rf"^((?:{PART})*{NAME1}(?:\s+{NAME2}){{0,{k}}}){AFTER}", lead, re.U)
        if m and m.group(1)[0].isalpha():
            return m.group(1)
    if rest_spelled:
        m = re.match(rf"^({NAME1})\s+[{UP}]{LETTER}{{3,}}", lead, re.U)
        if m:
            return m.group(1)
    m = re.match(rf"^(?:[{UP}]\.\s*-?\s*)+([{UP}]{LETTER}*(?:[-'’‐]{LETTER}+)*{LETTER})(?=\s*,|\s+and\b|\s+&)", lead, re.U)   # "E. Magosso, C. Cuppini"
    if m:
        return m.group(1)
    # Full surname particles after the first word, including hyphenated Dutch surnames.
    m = re.match(r'^([^,;.]{3,96}?)\s+[A-Z]{1,5}(?=\s*[,;.]|\s+et al\b)', lead)
    if m and len(m.group(1).split()) <= 9 and not re.search(r'\b(?:et al|and|&|20\d\d)\b', m.group(1), re.I):
        return m.group(1)
    return ''


def corporate_byline(lead):
    # An institutional byline with a date or parenthesised acronym precedes the title.
    m = re.match(r'^(.{4,90}?)\s+\((?:19|20)\d{2}\)\s+', lead)
    if m and ORG.search(m.group(1)):
        return m.group(1).strip(' .')
    m = re.match(r'^(.{4,90}?)\s+\([A-Z]{2,8}\)\s*[.]', lead)
    if m and ORG.search(m.group(1)):
        return m.group(1).strip(' .')
    m = re.match(r'^([A-Z]{2,12})\s*:\s+(?=[A-Z])', lead)
    if m:
        return m.group(1)
    m = re.match(r'^([A-Z][A-Za-z]{2,12})\.\s+(?=[A-Z])', lead)
    if m and len(re.findall(r'[A-Z]', m.group(1))) >= 2:
        return m.group(1)   # institutional acronym e.g. mixed-case or all capitals
    m = re.match(r'^([A-Z]{3,12})\s+(?=[A-Z][a-z])', lead)
    if m:
        return m.group(1)   # abbreviation before an unpunctuated report title
    pre = re.match(r'(?:U\.\s?[SK]\.|US|UK)\s+(?=[A-Z])', lead)
    head, body = (pre.group(0), lead[pre.end():]) if pre else ('', lead)
    seg = head + re.split(r'\s+\(|\s*[.:;,]\s', body + ' ', maxsplit=1)[0].strip(' .')
    if re.fullmatch(r'[A-Z]{2,}(?:/[A-Z]{2,})+', seg):
        return seg                                     # "WHO/UNICEF"
    ws = seg.split()
    last = max((i for i, x in enumerate(ws) if ORG.search(x) and ORG_WORD.match(x)), default=-1)
    if last >= 1 and all(ORG_WORD.match(x) for x in ws[:last + 1]) and ws[0][0].isupper() and not re.match(r'[A-Z]{1,3}$', ws[last + 1] if last + 1 < len(ws) else ''):
        return ' '.join(ws[:last + 1])
    w = seg.split()
    if 1 <= len(w) <= 12 and ORG.search(seg) and all(ORG_WORD.match(x) for x in w) and w[0][0].isupper():
        return seg
    return ''


def parse_ref(ref, idx):
    e = _orig(ref, idx)
    raw = e['raw']
    ptype = (e['ptype'] or '').lower()
    clean = _strip_links(raw)
    # A date of citation outside brackets is still NOT a publication year.
    clean = re.sub(r'\bCited\s+(?:on\s+)?(?:19|20)\d{2}\s+[A-Za-z]{3,9}\s+\d{1,2}\b', ' ', clean, flags=re.I)
    clean = re.split(r'\b(?:Erratum in|Correction in|Corrigendum in|Retraction in|Retracted in|Comment in|Update in)\b', clean, flags=re.I)[0]
    for el in ref.xpath('.//date-in-citation|.//access-date'):        # an access date is not a publication date
        t = _txt(el)
        if t:
            clean = clean.replace(t, ' ')
    lead = re.sub(r'^[A-Z]{1,2}\d{1,5}[.)]?\s+', '', SKIP_LEAD.sub('', raw))

    # ---- first author: structured first contributor; otherwise the printed byline
    first = ref.xpath('(.//person-group[@person-group-type="author"]/*[self::name or self::collab or self::string-name])[1]') \
        or ref.xpath('(.//*[self::name or self::collab or self::string-name][not(ancestor::person-group[@person-group-type!="author"])])[1]')
    old_fa = e['first_author'].lstrip('*†‡§#').strip()
    pa = printed_author(lead)
    org = corporate_byline(lead)
    if first and etree.QName(first[0]).localname == 'collab':
        fa = _txt(first[0])
    elif first and first[0].find('.//surname') is not None:
        sur = _txt(first[0].find('.//surname'))
        # the printed form wins when it is the same surname with its particles or letters the XML split off
        fa = pa if pa and RI.skey(pa).endswith(RI.skey(sur)) and RI.skey(sur) else sur
    elif org:
        fa = org
    elif pa:
        fa = pa
    elif old_fa and ' ' not in old_fa and RI.skey(lead).startswith(RI.skey(old_fa)) and (
            re.match(r'\W*' + re.escape(old_fa) + r'(?:\s+[A-Z]{1,3}\b|\s*,|\s+et al)', lead) or re.fullmatch(r'[A-Z]{2,8}', old_fa)
            or len(re.split(r'(?<=[.?!])\s+', lead, maxsplit=1)[0].split()) > 7):
        fa = old_fa
    else:
        fa = ''
    if not fa and not first and not org:
        # A publisher can be explicitly cited as an unstructured corporate author.
        mi = re.match(r'^([A-Z][A-Za-z]{4,28})[.]\s+(?=[A-Z])', lead)
        url = re.search(r'https?://(?:www[.])?([^/]+)/', raw)
        if mi and url and RI.skey(mi.group(1)) in RI.skey(url.group(1).split('.')[0]):
            fa = mi.group(1)
    if not fa and not first and not org:        # last resort, as the frozen parser: the first capitalised word of the byline
        m0 = re.match(rf"^([{UP}]{LETTER}+(?:[-'’]{LETTER}+)*)(?=[\s,])", lead, re.U)
        seg0 = re.split(r'(?<=[.?!])\s+', lead, maxsplit=1)[0]
        if m0 and re.match(r'^[\s,;]*[A-Z]{1,5}\b(?:[\s.,;]|$)|^[\s,;]*et al', seg0[len(m0.group(1)):]):
            fa = m0.group(1)
    # A title-first webpage has no byline; do not invent an author from its first word.
    if not first and (ptype == 'webpage' and ref.xpath('.//article-title')
                      or (not pa and not org and re.search(r'\bReference Source\b', raw, re.I))):
        fa = ''
    title_first = False
    if not fa and not first:
        seg = re.split(r'(?<=[.?!])\s+', lead, maxsplit=1)
        rest = seg[1] if len(seg) > 1 else ''
        s0 = seg[0]
        connector = re.search(r'\s(?:and|of|in|for|the|on|to|with)\s', s0) and not re.search(r'(?:^|\s)[A-Z]{1,3}[,.\s]', s0 + ' ')
        if 2 <= len(s0.split()) <= 7 and (ACCESS_LEAD.match(rest) or re.match(r'(?i)available|https?://|www\.', rest) or connector):
            title_first = True          # web page cited by its title: "Obesity and Overweight. [(accessed ...)]. Available online"
    e['first_author'] = fa.strip(' .,;')

    # An explicitly personal full-name byline on behalf of a working group.
    m_onbehalf = re.match(r'^([A-Z][a-z]+)\s+[A-Z]\s+([A-Z][a-z]+)\s+on behalf of\b', lead)
    if m_onbehalf:
        e['first_author'] = m_onbehalf.group(2)
    # ---- title
    src = ref.find('.//source')
    qchap = re.search(r'[“"]\s*([^”"]{10,300}?)[,.]?\s*[”"]\s*,?\s*in:?\s', raw)
    chap = ref.find('.//chapter-title')
    if chap is None:
        chap = ref.find('.//part-title')
    if qchap and not ref.xpath('.//article-title'):
        e['title'] = qchap.group(1).strip(' ,.')
    elif not ref.xpath('.//article-title') and chap is not None and _txt(chap):
        e['title'] = _txt(chap)                       # a chapter is cited by its own title, not the book's
    elif (not ref.xpath('.//article-title') and src is not None and _txt(src) and re.search(r'\bIn:?\s', raw)
          and raw.find(_txt(src)) > raw.find(re.search(r'\bIn:?\s', raw).group(0)) and chapter_title(raw)):
        e['title'] = chapter_title(raw)
    elif not ref.xpath('.//article-title') and ptype not in ('journal', 'article', 'journal-article') and src is not None and _txt(src):
        t = _txt(src)
        com = _txt(ref.find('.//comment')) if ref.find('.//comment') is not None else ''
        com = URL.sub('', com).strip()
        e['title'] = com if ((ptype == 'webpage' or len(t.split()) <= 2) and len(com.split()) >= 3) else t
    elif (not ref.xpath('.//article-title') and ptype not in ('journal', 'article', 'journal-article') and src is None
          and ref.find('.//comment') is not None and len(URL.sub('', _txt(ref.find('.//comment'))).split()) >= 3):
        e['title'] = re.split(r'\s*;\s*', URL.sub('', _txt(ref.find('.//comment'))).strip())[0].strip(' .')
    elif not fa and not first and re.search(r'\bReference Source\b', raw, re.I):
        e['title'] = re.split(r'(?<=[.?!])\s+', lead, maxsplit=1)[0].rstrip('.?! ')
    elif title_first:
        e['title'] = re.split(r'(?<=[.?!])\s+', lead, maxsplit=1)[0].rstrip('.?! ')
    elif e['title_src'] == 'guess':
        e['title'] = guess_title(raw)
    if org and fa == org and e['title_src'] == 'guess' and ref.find('.//source') is None:   # corporate byline: the title is the next printed segment
        rest = re.sub(r'^\s*Electronic address\b[^:.]{0,40}:\s*', '', URL.sub(' ', lead[len(org):]).lstrip(' .:,;'))
        rest = re.sub(r'^\(\s*(?:[A-Z]{2,}|(?:19|20)\d\d)\s*\)\s*', '', rest)
        seg = re.split(r'(?<=[a-z0-9)\]])\s*\.(?:\s+|$)', rest, maxsplit=1)[0].strip(' .')
        if len(seg.split()) >= 2 and not ACCESS_LEAD.match(seg):
            e['title'] = seg
    if e['title'] and e['title_src'] == 'structured' and not ref.xpath('.//article-title') and len(e['title']) >= 8:
        j = raw.find(e['title'])
        if j >= 0 and re.match(r'\s*(?:&|;)\s*\S', raw[j + len(e['title']):]) and not re.match(r'\s*;\s*Proceedings\b', raw[j + len(e['title']):], re.I):
            g = guess_title(raw)
            if g.startswith(e['title']) and len(g) > len(e['title']):
                e['title'] = g                          # the Google-Scholar link parameter was cut at '&'
    if m_onbehalf and not ref.xpath('.//article-title'):
        after_group = re.search(r'\bon behalf of [^.]+[.]\s+(.+?)(?:,\s*World Health Organization Collaborating|[.]\s+(?:https?://|chrome-extension://)|$)', raw, re.I)
        if after_group:
            e['title'] = after_group.group(1).strip(' .,')
    if e['title_src'] == 'guess' and e['first_author'] and re.search(r'(?i)(?:accessed|https?://|/[^\s]*checklist)', e['title']):
        rest = lead[len(e['first_author']):].lstrip(' .;:,')
        candidate = re.split(r'[.]\s+', rest, maxsplit=1)[0].strip(' .')
        if 2 <= len(candidate.split()) <= 15 and not re.search(r'https?://|www\.', candidate):
            e['title'] = candidate
    if re.search(r'https?://|www\.', e['title']):          # a URL is never the title: guess again without links
        e['title'] = guess_title(URL.sub(' ', raw))
    if e['first_author'] and len(e['first_author']) >= 3 and e['title'].startswith(e['first_author']) and re.match(r'[\s.,:;(]', e['title'][len(e['first_author']):len(e['first_author']) + 1] or '.'):
        rest = re.sub(r'^[\s.,:;]*(?:\(\s*(?:19|20)\d\d[a-z]?\s*\))?[\s.,:;]*', '', e['title'][len(e['first_author']):])
        if len(rest.split()) >= 2:
            e['title'] = rest                          # "World Health Organization ( 2023 ) Anaemia in ..." -> "Anaemia in ..."
    e['title'] = re.sub(r'^\s*Electronic address\b[^:]{0,40}:\s*', '', e['title'])
    if ref.xpath('.//article-title'):
        # Do not damage an explicit JATS article title with author-list heuristics.
        e['title'] = strip_journal_tail(_txt(ref.xpath('.//article-title')[0]), raw)  # source JATS may contain journal name at end
    else:
        e['title'] = strip_journal_tail(strip_author_run(e['title']).lstrip(' :;,.-–'), raw)
    if not ref.xpath('.//article-title') and re.search(r'\b\d{1,2}(?:st|nd|rd|th)\s+edition[.]', raw, re.I):
        mbook = re.search(r'\b\d{1,2}(?:st|nd|rd|th)\s+edition[.].{0,130}?(?:19|20)\d{2}[.]\s+([^.;]{8,130})[.]?$', raw, re.I)
        if mbook:
            e['title'] = mbook.group(1).strip(' .')

    if re.match(r'(?i)\s*(?:available\b|\(?\s*accessed\b)', e['title']):
        segs = [x.strip(' .') for x in re.split(r'(?<=[^\s.]{2})\.\s+', URL.sub(' ', lead))]
        segs = [x for x in segs if len(x.split()) >= 2 and x != e['first_author'] and not re.match(r'(?i)(?:available|\(?\s*accessed|https?:|www\.)', x)]
        t0 = segs[0] if segs else ''
        if e['first_author'] and t0.startswith(e['first_author']) and len(t0) > len(e['first_author']) + 5:
            t0 = t0[len(e['first_author']):].lstrip(' ,.:;')
        e['title'] = t0
    if ACCESS_LEAD.match(e['title']) or not re.search(r'[^\W\d_]{3,}', e['title']):
        e['title'] = ''               # metadata only ("2020, 2020(18), 2730–2734") or an access date: no title printed
    if e['title_src'] == 'guess' and len(e['title'].split()) >= 4:
        tail = re.search(r'[\s,;]+\(?((?:19|20)\d\d)\)?$', e['title'])
        if tail and re.sub(r'[\s.,;]+$', '', clean).endswith(tail.group(1)):
            e['title'] = e['title'][:tail.start()]      # the citation's closing year, not part of the title

    # ---- years: structured <year> outside an access date, otherwise printed years outside DOIs/URLs/access dates/pages
    sy = [int(y) for y in ref.xpath('.//year[not(ancestor::date-in-citation) and not(ancestor::access-date)]/text()')
          if re.fullmatch(r'\d{4}', (y or '').strip())]
    body = clean.replace(e['title'], ' ') if len(e['title']) >= 15 else clean
    ys = {int(y) for y in RI.YEAR_RE.findall(_no_pages(body))}
    if not ys:      # the guessed title may have swallowed the closing year: take it back only from the end of the title
        ys = {int(y) for y in re.findall(r'(?<!\d)((?:19|20)\d\d)(?!\d)[\s.,;)]*$', e['title'])}
    bm = BOOK_YEAR.search(clean)
    if bm:
        ys = {int(bm.group(1))}
    if not ys:
        ys = {int(y) for y in GLUED_YEAR.findall(clean)} or {int(y) for y in GLUED_YEAR2.findall(clean)}
    if len(ys) >= 2:
        mby = re.search(r'\bet al\s*\(\s*((?:19|20)\d{2})\s*\)\s+', clean, re.I)
        if mby and int(mby.group(1)) in ys:
            ys = {int(mby.group(1))}  # journal title may itself contain an historical year
    if len(ys) > 1:
        pubyear = re.search(r'\b((?:19|20)\d{2})\s*;\s*\d{1,4}(?:\s*\(\s*\d{1,4}\s*\))?\s*:', clean)
        if pubyear and int(pubyear.group(1)) in ys:
            ys = {int(pubyear.group(1))}  # bibliographic year/volume/issue/page outranks a conference group year
    e['years'] = sorted(set(sy)) if sy else sorted(ys)

    # ---- kind
    preprint = PREPRINT.search(_strip_links(raw)) or any(d.startswith(PREPRINT_DOI) or MEDRXIV_DOI.match(d) for d in e['dois'])
    strong_journal = bool(re.search(r'(?:19|20)\d\d\s*;\s*\d+\s*(?:\(\s*[\w-]+\s*\))?\s*:', clean))
    nt0 = clean.replace(e['title'], ' ') if len(e['title']) >= 15 else clean
    degree = re.search(r'\b(?:M\.?\s?S\.?c?|Ph\.?\s?D|MD|MPH|master(?:\'?s)?|doctoral|bachelor(?:\'?s)?)\.?\s*(?:thesis|dissertation)\b', clean, re.I)
    if preprint or (CONF.search(nt0) and not strong_journal) or degree or THESIS.search(nt0) or re.search(r'\bProceedings of (?:the )?\d+(?:st|nd|rd|th)\b|\bin:?\s+Proceedings\b', clean, re.I):   # a preprint, conference paper or thesis, whatever the XML type says
        e['kind'] = 'nonjournal'
    elif org and fa == org and not (e['dois'] or e['pmids'] or strong_journal):
        e['kind'] = 'nonjournal'                  # a corporate report without any journal trace
    elif ptype == 'journal' or ptype in ('article', 'journal-article'):
        e['kind'] = 'journal'
    elif ptype in ('', 'other', 'unknown', 'list') or e['kind'] == 'journal':
        nt = clean.replace(e['title'], ' ') if len(e['title']) >= 15 else clean      # 'guidelines'/'edition' inside a journal title is not a book hint
        if strong_journal and len(e['title']) >= 15 and e['title'] in clean:
            nt = clean.split(e['title'], 1)[-1]  # use the source/container portion, not the conference affiliation before the title
        nt = re.sub(r'\bIn:\s*(?=[^.]{0,60}\bjournal\b)|\((?:English|Engl\.?\s*)\s*Edition\)|\(Engl\.? Ed\.?\)', ' ', nt, flags=re.I)
        nt = re.sub(r'\b(?:19|20)\d{2}/\d{2}/\d{2}\s+ed[.]', ' ', nt, flags=re.I)
        if org and fa == org and strong_journal:
            nt = nt.replace(org, ' ')            # an organisation as author of a journal article ("World Health Organization ... Vaccine. 2017;35(43):5753")
        bad = RI.NONJOURNAL_HINT.search(nt) or STATE_PUB.search(nt) or REPORT.search(nt) or re.search(r'Database of Abstracts of Reviews', nt) or (PUBLISHER.search(nt) and not strong_journal)
        has_doi = bool(e['dois']) and not any(DOI_ISBN.search(d) for d in e['dois'])
        good = RI.JOURNAL_SHAPE.search(clean) or VOLPAGES.search(clean) or has_doi or bool(e['pmids'])
        e['kind'] = 'journal' if (good and not bad) else 'nonjournal'
    return e


def install_provenance(M):
    """A DOI that appears only inside a non-doi.org URL path (medrxiv.org/content/10.1101/...v1) is not a printed DOI."""
    orig = M.provenance

    def provenance(ref):
        text, ids = orig(ref)
        in_url, alone = set(), set()
        for tok in text.split():
            ds = {M.clean_doi(x) for x in RI.DOI_RE.findall(tok)}
            if re.match(r'(?i)(?:https?://|www\.)', tok) and 'doi.org' not in tok.lower():
                in_url |= ds
            else:
                alone |= ds
        only = {d for d in in_url - alone if d}
        ids = [(k, v, 'url_path' if (k == 'doi' and how == 'free_text' and v.lower() in only) else how) for k, v, how in ids]
        return text, ids
    M.provenance = provenance


def install():
    RI.parse_ref = parse_ref
    RI.guess_title = guess_title
