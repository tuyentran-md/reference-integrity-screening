# Data

Derived data from the frozen v37 census of references in MEDLINE-indexed open-access systematic reviews published from 2016 to June 2026. This folder is independent of the code in `screening/`: the code runs without it, and it can be ignored when the code is reused.

The files contain identifiers, screening states and validation verdicts only. Full texts, JATS XML, printed citation strings and saved registry responses are not included, because redistribution permissions vary and they are large.

Licence: [CC BY-NC 4.0](LICENSE). The code in the rest of the repository has its own licence (see `../LICENSE`).

## Files

| File | Rows | Content |
| :-- | --: | :-- |
| `reviews.csv.gz` | 119,342 | One row per review record retrieved from Europe PMC |
| `references/references_<year>.csv.gz` | 7,627,028 in total | One row per parsed reference, split by review publication year |
| `wrong_identifiers.csv.gz` | 12,479 | References whose DOI or PMID leads to a different work, with the identifiers |
| `validation/validation_key.csv` | 2,160 | The stratified random validation sample and the tool result for each case |
| `validation/validation_ratings_investigator.csv` | 2,160 | Final verdict for each case: consensus of the two raters |
| `validation/misses_classified.csv` | 163 | Element(s) wrong in each confirmed error that the tool did not detect |
| `validation/validation_audit.json` | | Seed, stratum populations of the eligible frame and sample allocation |

All CSV files are UTF-8. A reference is identified by `review_id` + `ref_index`. `review_id` is `cs_` followed by the PMID of the review.

## reviews.csv.gz

| Column | Meaning |
| :-- | :-- |
| `review_id`, `pmid`, `pmcid` | Identifiers of the review |
| `year` | Publication year of the review |
| `in_census` | 1 if the review had full text and at least one parsable reference (119,030 reviews) |
| `n_references` | Number of parsed references |
| `development_review` | 1 if the review was used while the tool was developed (9,899 reviews); these are outside the eligible frame of the validation sample |

## references/references_&lt;year&gt;.csv.gz

| Column | Meaning |
| :-- | :-- |
| `review_id`, `ref_index`, `review_year`, `development_review` | As above; `ref_index` is the position of the reference in the review |
| `kind` | `journal` or `nonjournal` |
| `state` | Final tool result: `valid` (no error detected), `corrupted` (error detected) or `unresolved` |
| `v35_state` | Result of the earlier rule layer; together with `state` it defines the two error strata of the validation sample |
| `rule_id` | Final rule(s) that produced the state, separated by `;` |
| `wrong_fields` | Fields recorded as wrong by the identity check (`author`, `title`, `year`, `doi`, `pmid`, `id_points_to_other_work`); checks added by the final rules, such as volume and pages, are named in `rule_id` |
| `doi_wrong`, `pmid_wrong` | 1 if the printed DOI or PMID failed the identifier check |
| `id_points_to_other_work` | 1 if a printed or supplied identifier leads to a different work |
| `has_printed_doi`, `has_printed_pmid` | 1 if the citation carried a DOI or PMID |
| `resolved_via` | How the cited work was identified (`doi`, `pmid`, a JATS link, or a registry search); empty if unresolved |
| `unresolved_reason` | Why the cited work could not be identified |

The rate of wrong-study identifiers per 1,000 references (Results 3.3) counts resolved references with a printed DOI or PMID in the denominator, and in the numerator those with `id_points_to_other_work` = 1 where the wrong identifier was printed (`doi_wrong` = 1 with a printed DOI, or `pmid_wrong` = 1 with a printed PMID).

## wrong_identifiers.csv.gz

`printed_dois` and `printed_pmids` are the identifiers in the citation (JSON lists). `canonical_doi` and `canonical_pmid` belong to the work the citation describes. `id_provenance` gives, for each identifier, whether it appeared as plain text (`free_text`) or only in publisher markup (`link_only`). `title_score` is the similarity between the printed title and the record of the cited work.

## validation/

The eligible frame excludes the 9,899 development reviews. Strata were sampled at random by final tool result (seed 20261029): 2,000 with no error detected, 40 and 80 in the two error strata, and 40 unresolved. `sample_stratum` and `inclusion_probability` in `validation_key.csv` give the design. Verdicts are `Error`, `Correct` or `Cannot check`.

Copy the four files into `screening/v37/validation/` and run `v37/metrics.py`, `v37/prevalence.py` and `v37/element_breakdown.py` to reproduce sensitivity, specificity, predictive values, error prevalence and the element breakdown reported in the article.
