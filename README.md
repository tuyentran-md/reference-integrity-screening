# reference-integrity-screening

Screening of bibliographic reference integrity in biomedical systematic reviews. Each reference is parsed from
publisher JATS XML, resolved against Crossref, PubMed and doi.org records, and checked for errors in its
identifiers (DOI, PMID), title, authors, journal, year, volume and pages.

Status: pre-release source for independent developer review. Not yet licensed for reuse.

## Layout

`screening/` holds the frozen version 37 rule chain exactly as used for the census and validation analyses.

- `vendor/` - reference parser (S02 v2.5) and identity resolution (S03 v35: base module plus rule patches v31-v35).
- `frozen.py`, `registry.py`, `byline.py` - load the v35 chain with every HTTP entry point replaced by read-only
  lookups in saved response caches; registry subset and JATS author bylines.
- `rules.py` -> `v36b/rules.py` -> `v36c/rules.py` -> `v37/rules.py` - successive rule layers applied after
  work resolution. `v37/rules.py` is the final rule set.
- `run.py`, `jats.py`, `v37/census.py` - apply the rules to one shard, one JATS file, or the full census.
- `packet.py`, `exclusions.py`, `v37/validation.py` - stratified validation sample and blinded rating workbooks.
- `v37/metrics.py`, `v37/prevalence.py`, `v37/census_tables.py`, `v37/std_rates.py`, `v37/h1_trend.py`,
  `v37/element_breakdown.py`, `v37/miss_by_period.py`, `v37/misses_reasons.py` - reported numbers.
- `v37/fig1_diagram.py`, `v37/figures.R` - figures.
- `tests/` - unit tests on synthetic input.

`RELEASE_MANIFEST.json` lists, for every file, its SHA-256 here and in the frozen source. Most files are
byte-identical. Six files differ only by literal substitutions: contact address read from `RIS_MAILTO`, local paths made
relative, a neutral user agent, and wording in comments. No rule or algorithm line was changed; for the
comment-only edits the parsed code is verified unchanged. `v37/census.py` asserts the SHA-256 of `v37/rules.py`.

## Data

Data and caches are not included: census shards, saved registry responses (SQLite), validation workbooks and
ratings. Code paths expect them under the repository root (`census_s03_v35_20261019/`, `code/cache/`,
`pipeline/cache/`, `census_20261013/`) and under `screening/data/` and `screening/v37/validation/`.
Exact reruns come from the saved responses, not from new network calls. Saved request URLs contain the
contact address used at collection time, so an exact replay from the caches requires `RIS_MAILTO` to be set to
that original address (supplied with the caches). With any other value cached lookups miss and reference
states change; this is not a change in the rules.

## Requirements

Python with lxml, XlsxWriter, openpyxl and pytest (recorded run: Python 3.14.4, lxml 6.1.1, XlsxWriter 3.2.9). Figures: R with ggplot2, jsonlite, cowplot
(`figures.R`) and Playwright (`fig1_diagram.py`).

```sh
cd screening
python3 -m pytest
```
