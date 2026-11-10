---
license: other
license_name: upstream-terms-preserved
license_link: LICENSE.md
task_categories:
- tabular-classification
configs:
- config_name: default
  data_files:
  - split: train
    path: extract/public/train.csv
  - split: validation
    path: extract/public/eval.csv
  - split: test
    path: extract/private/holdout.csv
---

# Will an existing U.S. bank branch close within the next 12 months? Forward branch closure from the FDIC Summary of Deposits

Will an existing FDIC-insured bank branch close within the next 12 months?

Derived from Federal Deposit Insurance Corporation (FDIC). This is not its official product.

Prediction time: June 30 of survey year t, at the SOD branch snapshot date. The public SOD file is released later; this retrospective task treats the June 30 values as decision time measurements and does not claim real time availability. The institution community bank flag is not part of the shipped feature set because the selected SOD vintages carry no nonmissing values for it.

Evaluation scope: `retrospective_research`. Public validation is development data. The downloadable labeled holdout is public; it is not cryptographically sealed. No final holdout benchmark score is claimed.

## Measured baseline

The existing runner was used unchanged on public validation. See `evaluation.json` for rows, controls, per-year results and limitations.

```json
{
  "full_model": {
    "auc": 0.7291396394680473,
    "brier": 0.04005977457598382
  },
  "majority_prior": {
    "auc": 0.5,
    "brier": 0.041169370095473085
  },
  "calendar_identity": {
    "auc": 0.5179224126161044,
    "brier": 0.042655080660513385
  }
}
```

## Limitations

- SOD is an annual June 30 snapshot published with a lag; this is a retrospective task and does not prove historical real-time availability.
- Change code 721 captures reported branch closures; bank failures, charter consolidations, relocations, and some merger-related exits may be coded differently or not appear as 721, so the label can undercount economic closures.
- Branch-year rows are clustered within banks and local markets; use bank-grouped or clustered evaluation and do not treat rows as independent.
- SOD includes multiple office and service types, and service-type definitions and reporting changed over 2012-2022.
- Branch deposits may be estimated or consolidated, as indicated by BRCENM, weakening deposit-based features.
- FDIC data covers FDIC-insured institutions and insured U.S. branches of foreign banks, not credit unions or noninsured lenders.
- History events are current-vintage and may be revised; the target uses event effective dates rather than publication dates.
- The public-domain basis is U.S. federal statutory status under 17 U.S.C. § 105, not a worldwide dedication; third-party content and trademarks are excluded, and the data must not imply FDIC endorsement.
- No causal interpretation: closure is a strategic bank decision, not a random outcome.
- SOD is an annual June 30 snapshot and the public file is released later, so this is retrospective research and not a real time forecasting claim.
- The history API is a current vintage administrative record and may be revised; the target uses event effective dates rather than publication dates.
- Reported 721 branch closures may undercount economic closures such as bank failures, charter consolidations, relocations, and some merger related exits.
- Branch year rows are clustered within banks and local markets and should be evaluated with bank grouped or clustered methods.
- SOD service type and field definitions may change over 2012 through 2022.
- No verified SOD publication lag observations were available; the feature lag field is zero to denote the June 30 decision instant, not a measured zero-day public release. The actual publication lag remains unverified.
- Presence of ORG_ROLE_CDE and the exact branch role code BR are assumed from the frozen target; the builder fails with the observed source headers if either is absent.
- The FDIC community bank flag is not usable in the selected SOD vintages: requesting CB returns no nonmissing values for survey years 2012 through 2022, and the institutions endpoint returns only CERT and ID records, so the previously planned CB feature is omitted with recorded evidence instead of being shipped as an empty column.
- Label derived group rates are not shipped and training rates are not used as features, so no causal training rate feature is exposed.
- Closure event dates are label-source values and are not shipped; conservative positive day clusters use the branch establishment date, a decision-time date.
- No uncertainty interval; observed differences do not establish universal headroom

## Loading and reproduction

Verify all downloaded files with `verify_dataset.py`. CSV splits live under `extract/`; `meta.json` declares the target and identifiers. `MANIFEST.json` binds data, source snapshot, code and measured evidence.

To reconstruct without network source drift, extract `source-snapshot.tar.gz` into a new cache directory, install `reproduction/requirements.txt` in an isolated Python environment, then run `python reproduction/dataset_run.py --output NEW_EMPTY_DIRECTORY --source-cache CACHE_DIRECTORY`. Compare the qualification artifact digest with this manifest. This performs full validation and baseline evaluation and may need worker resources.

See `LICENSE.md` for source terms and preserved attribution.
