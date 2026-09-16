# Choose experiments from the failure mechanism

* Grain: inspect composite business keys, duplicates, join multiplicities,
  unmatched rows. Compare source and modeled counts AND amounts by disjoint
  segments. Account/platform-local IDs are not globally unique.
* Versioned CRM: latest event at the comparison as-of boundary, cancellations,
  test records, arrival versus business date. Use a source query independent of
  staging logic and inspect representative event histories.
  Select the latest event across all statuses before filtering eligibility;
  filtering cancelled records first can revive an earlier confirmed version.
  An explicit historical as-of boundary applies to available history before
  ranking. Confirm the version ordering rule from the business/source contract.
* Aggregation: ratio of sums versus averages; unequal populations are useful
  counterexamples. Distinct counts and ratios are not additive. Zero denominator
  behavior must follow the business definition, not an invented preference.
* Filters: establish expected different populations before using changing numbers
  as an assertion. Inspect effective filter state and source/DAX results. Equal
  numbers can be legitimate. Test reset as well as application.
* Completeness: compare declared loaded-through dates and source population;
  account/file/partition gaps cannot be ruled out by MAX(date).
* Statistical probes: group before profiling when seasonality, account mix or
  reporting maturity differs. `qa.py profile` computes descriptive quantiles and
  numeric parse failures, not a pass/fail. Save custom pandas/scipy scripts when
  diagnostics require distributions, robust residuals or cohort comparisons.
* Counterexamples: opposing segment changes can leave grand totals unchanged.
  New categories, all-NULL slices, empty periods and zero conversions often expose
  errors that the default dashboard state cannot reveal.
* Explanations: show before/after, numerator/denominator, affected keys, disjoint
  reason contributions and remaining unexplained delta. Do not sum overlapping
  reasons or describe a hypothesis as demonstrated cause.

Start with the riskiest claims and expand on contradictions. Record untested
conditions. For a fixture-only edge, explicitly say the deployed UI was not tested.
