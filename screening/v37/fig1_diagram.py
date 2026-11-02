"""Figure 1 of the v37 manuscript, drawn with the diagram-design skill (process + sampling-flow layout).
Counts are read from census_tables.json and validation/; nothing is typed in by hand.
Writes fig1_diagram.html (two SVG panels, 6.8 in wide) and, with Playwright, Figure_1.png / Figure_1.pdf.
Run from v37/:  python3 fig1_diagram.py"""
import csv, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'figures'
PAPER, INK, MUTED, ACCENT, TINT = '#ffffff', '#2d3142', '#4f5d75', '#bf4520', 'rgba(191,69,32,0.08)'
FONT = "Arial, Helvetica, sans-serif"
R = 8
n = lambda x: f'{x:,}'

ct = json.load(open(HERE / 'census_tables.json'))['total']
refs, revs = ct['references'], ct['reviews']
pop = json.load(open(HERE / 'validation/validation_audit.json'))['populations_eligible']
key = {r['case_id']: r['sample_stratum'] for r in csv.DictReader(open(HERE / 'validation/validation_key.csv'))}
rat = {r['case_id']: r['verdict'] for r in csv.DictReader(open(HERE / 'validation/validation_ratings_investigator.csv'))}
cnt = lambda s, v: sum(1 for k, x in rat.items() if key[k] == s and x == v)
tot = lambda s: sum(1 for k in rat if key[k] == s)
frame = sum(pop.values())
EXCL_REVIEWS, EXCL_REFS = 9899, refs - frame      # reviews from validation design; references by subtraction
assert EXCL_REFS == 758592

MARKERS = f'''<marker id="{{p}}-arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{MUTED}"/></marker>
<marker id="{{p}}-arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{ACCENT}"/></marker>'''


def text(x, y, s, size=10, weight=400, fill=INK, anchor='start', spacing=None):
    ls = f' letter-spacing="{spacing}"' if spacing else ''
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-weight="{weight}" '
            f'font-family="{FONT}" text-anchor="{anchor}"{ls}>{s}</text>')


def node(x, y, w, h, lines, focal=False, tag=None):
    """lines: list of (text, kind) with kind in name|sub|rule; y positions run from y+16 in 13-unit steps."""
    stroke = ACCENT if focal else INK
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{PAPER}"/>',
           f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{TINT if focal else PAPER}" stroke="{stroke}" stroke-width="1"/>']
    cy = y + 20
    if tag:
        out.append(f'<rect x="{x + 8}" y="{y + 8}" width="16" height="14" rx="2" fill="none" stroke="{stroke}" stroke-opacity="0.45" stroke-width="0.8"/>')
        out.append(text(x + 16, y + 18.5, tag, 9, 600, stroke, 'middle'))
        cy = y + 30
    for s, kind in lines:
        if kind == 'rule':
            out.append(f'<line x1="{x + 8}" y1="{cy - 9}" x2="{x + w - 8}" y2="{cy - 9}" stroke="{INK}" stroke-opacity="0.2" stroke-width="0.8"/>')
            cy += 8
            continue
        out.append(text(x + w / 2, cy, s, 10 if kind == 'name' else 9, 700 if kind == 'name' else 400,
                        INK if kind == 'name' else MUTED, 'middle'))
        cy += 14
    return '\n'.join(out)


def elbow(x1, y1, x2, y2, yt, accent=False):
    """Down, across at yt, down: quarter-arc corners of radius R; straight when x1 == x2."""
    marker = 'arrow-accent' if accent else 'arrow'
    if x1 == x2:
        d = f'M{x1},{y1} V{y2}'
    else:
        s = 1 if x2 > x1 else -1
        assert abs(x2 - x1) >= 2 * R
        d = (f'M{x1},{y1} V{yt - R} Q{x1},{yt} {x1 + s * R},{yt} H{x2 - s * R} '
             f'Q{x2},{yt} {x2},{yt + R} V{y2}')
    return f'<path d="{d}" fill="none" stroke="{ACCENT if accent else MUTED}" stroke-width="1.2" marker-end="url(#{{p}}-{marker})"/>'


def line(x1, y1, x2, y2, dashed=False):
    da = ' stroke-dasharray="5,4"' if dashed else ''
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{MUTED}" stroke-width="1.2"{da} '
            f'marker-end="url(#{{p}}-arrow)"/>')


def svg(slug, title, desc, h, body):
    inner = MARKERS.replace('{p}', slug) + '\n'
    return (f'<svg viewBox="0 0 680 {h}" xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-labelledby="{slug}-title {slug}-desc" style="width:6.8in;display:block">\n'
            f'<title id="{slug}-title">{title}</title>\n<desc id="{slug}-desc">{desc}</desc>\n'
            f'<defs>{inner}</defs>\n<rect width="100%" height="100%" fill="{PAPER}"/>\n'
            + body.replace('{p}', slug) + '\n</svg>')


# ------------------------------------------------------------------ panel a
XS = [6, 178, 350, 522]
steps = [('1', 'Retrieve', ['JATS XML full text', 'from Europe PMC']),
         ('2', 'Parse', ['authors, title, journal,', 'year, volume, pages,', 'DOI and PMID']),
         ('3', 'Identify the work', ['own DOI or PMID, then', 'publisher identifiers, then', 'search of Crossref, PubMed,', 'OpenAlex']),
         ('4', 'Compare', ['every element with all', 'saved registry records', 'of the work'])]
a = [text(6, 14, 'a', 12, 700), text(24, 14, f'{n(refs)} references in {n(revs)} systematic reviews and meta-analyses', 10, 400, MUTED)]
a += [line(XS[i] + 152, 88, XS[i + 1], 88) for i in range(3)]
a += [elbow(560, 136, 254, 184, 152), elbow(598, 136, 426, 184, 168), elbow(636, 136, 636, 184, 0)]
for i, (tag, name, sub) in enumerate(steps):
    a.append(node(XS[i], 40, 152, 96, [(name, 'name')] + [(s, 'sub') for s in sub], focal=(i == 3), tag=tag))
res = [('No error detected', ct['valid']), ('Error detected', ct['corrupted']), ('Unresolved', ct['unresolved'])]
for i, (name, v) in enumerate(res):
    a.append(node(XS[i + 1], 184, 152, 52, [(name, 'name'), (f'{n(v)} ({100 * v / refs:.1f}%)', 'sub')]))
a.append(text(6, 214, 'RESULT FOR EACH REFERENCE', 9, 400, MUTED, spacing='0.08em'))
panel_a = svg('fig1a', 'Steps of the reference-checking tool',
              'Four steps from retrieving a review full text to comparing each reference element with registry records, ending in three results per reference.',
              248, '\n'.join(a))

# ------------------------------------------------------------------ panel b
strata = [('valid', 'No error detected', ''), ('error_v35', 'Error detected', 'identifier and core fields'),
          ('error_new_v37', 'Error detected', 'element checks only'), ('unresolved', 'Unresolved', '')]
b = [text(6, 14, 'b', 12, 700), text(24, 14, 'Validation design', 10, 400, MUTED)]
b += [line(375, 58, 470, 58, dashed=True), line(265, 86, 265, 118)]
b += [elbow(199, 166, 82, 216, 190), elbow(243, 166, 243, 216, 0), elbow(287, 166, 426, 216, 200), elbow(331, 166, 598, 216, 184)]
b.append(node(155, 30, 220, 56, [(f'{n(refs)} references', 'name'), (f'in {n(revs)} reviews', 'sub')]))
b.append(node(470, 30, 204, 56, [(f'Excluded: {n(EXCL_REVIEWS)} reviews with', 'sub'), ('a reference inspected during', 'sub'),
                                 (f'development ({n(EXCL_REFS)} references)', 'sub')]))
b.append(node(155, 118, 220, 48, [('Eligible frame', 'name'), (f'{n(frame)} references', 'sub')], focal=True))
for i, (s, name, qual) in enumerate(strata):
    lines = [(name, 'name'), (qual or ' ', 'sub'), (f'{n(pop[s])} in frame', 'sub'), ('', 'rule'),
             (f'Sampled {n(tot(s))}', 'name'),
             (f'Error {n(cnt(s, "Error"))} · Correct {n(cnt(s, "Correct"))}', 'sub')]
    if cnt(s, 'Cannot check'):
        lines.append((f'Cannot check {cnt(s, "Cannot check")}', 'sub'))
    b.append(node(XS[i], 216, 152, 112, lines))
b.append(text(6, 344, "Stratified random sample, seed 20261029. The investigator rated each reference blind to the tool's result.", 9, 400, MUTED))
panel_b = svg('fig1b', 'Design of the validation study',
              'Reviews inspected during development were excluded; the eligible frame was split into four strata of the tool result and a random sample of each was rated by the investigator.',
              352, '\n'.join(b))

HTML = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Figure 1 reference-checking tool</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{background:{PAPER};font-family:{FONT}}}
.fig{{width:6.8in}} .gap{{height:0.12in}}</style></head>
<body><div class="fig" id="fig">{panel_a}<div class="gap"></div>{panel_b}</div></body></html>'''
(HERE / 'fig1_diagram.html').write_text(HTML)

if __name__ == '__main__':
    from playwright.sync_api import sync_playwright
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(device_scale_factor=300 / 96, viewport={'width': 760, 'height': 800})
        pg.goto((HERE / 'fig1_diagram.html').as_uri())
        box = pg.locator('#fig')
        box.screenshot(path=str(OUT / 'Figure_1.png'))
        h = pg.evaluate('document.getElementById("fig").getBoundingClientRect().height')
        pg.pdf(path=str(OUT / 'Figure_1.pdf'), width='6.8in', height=f'{h / 96:.3f}in', print_background=True,
               margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        br.close()
    print('ok', h / 96)
