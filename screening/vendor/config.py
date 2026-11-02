"""Fixed settings for the M11 protocol pipeline. Change a value here, never inside a step script.

Every threshold below is a protocol decision. If one changes, note it in README.md (decision log) and rerun
from the step that uses it.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPE = ROOT / 'pipeline'
OUT = PIPE / 'outputs'
LOGS = PIPE / 'logs'

# Development cohort = the 430 SR/MA of the 01/10/2026 batch (JATS already downloaded).
BATCH = ROOT / 'batch_20261001'
COHORT_TYPE = 'srma'

# Legacy instrument (code/ref_identity.py) supplies the registry lookups and their HTTP cache
# (code/cache/m11_http.sqlite), so a rerun reproduces the same answers without hitting the APIs again.
LEGACY_CODE = ROOT / 'code'

# Step 03 — citation correctness (protocol v2.0 §5 S03)
TITLE_SAME = 0.6         # share of the source title's words in the citation: >= this = title agrees; below = title wrong
SEARCH_TITLE_MIN = 0.7   # a search hit must reach this title score (plus author or year agreement) to count as the source
OPENALEX_PER_DAY = 900   # one search costs $0.001; free key budget is $1/day (~1,000 searches) — stay under it
CREDENTIALS = Path.home() / '.config/reference-integrity-screening'   # openalex.env (OPENALEX_API_KEY); never written to cache/logs

# S03 quality check (protocol v2.0 §7): fixed-size random sample; a new round uses SEED + round - 1 and
# excludes rows drawn in earlier rounds, so a fix is never checked on the cases it was tuned on.
SEED = 20261004
QC_S03_CORRUPTED = 100   # all, if fewer
QC_S03_VALID = 50
QC_S03_PASS_CORRUPTED = 0.90
QC_S03_PASS_VALID = 0.95

# S04 quality check (protocol v2.0 §7): 50 corrupted-reference rows per round; round 2 onward excludes every
# row already checked in S04. Round 1 was drawn before this helper was committed; use a fresh fixed seed from round 2.
QC_S04_N = 50
QC_S04_PASS = 0.90
QC_S04_ROUND2_SEED = 20261006
