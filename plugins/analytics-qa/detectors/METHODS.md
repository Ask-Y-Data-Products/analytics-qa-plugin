# Detector methods

How each failure-mode detector works, when it applies, how to read its result,
and what to record. `catalog.json` is the machine-readable index; the `detect`
skill selects from it. Three kinds:

- **lint** — deterministic scan of captured model metadata / report layout
  (`scripts/model_lint.py`). Fast, runs once per case, produces review leads.
- **dax** — an invariant evaluated on the live model (`scripts/probes.py`,
  `dax.*` methods). Passes or fails on a stated expectation.
- **stat** — a statistical method over a series exported from the model
  (`scripts/probes.py`, `stat.*` methods backed by `scripts/stats_lib.py`).
  Flags are review leads, never verdicts.

A detector result becomes a claim through `qa.py detect`: passed → passed,
failed → failed, review / inconclusive → inconclusive with the flagged points in
`observed`. The agent must add the explanation; a flag without an explanation
stays inconclusive on the sign-off page.

## Adding a detector

1. Add an entry to `catalog.json` (id, kind, domain, applies_when, claim, and a
   DAX `template` for `dax.assert`-style invariants).
2. Add a section below: when it applies, how to instantiate, how to read it.
3. For a new statistic, add a pure function to `stats_lib.py` with a unit test
   and a branch in `probes.run_method`.

---

## lint.volatile_time_in_column (time, high)

**When**: a calculated column calls TODAY()/NOW(). Typical: `is_current_month`,
`is_completed_month_this_year`, `is_last_30_days`.
**Why it fails**: calculated columns are evaluated at process/recalc time, not
per query. On a stale import the flag drifts: a "completed month" column
evaluated on the 16th of September marks July and August complete although the
import ends July 20. Desktop may also recalculate on open, so the same file
shows different flags on different days.
**Read**: every hit is a claim: "flag X reflects data completeness". Test it with
`dax.assert`: months flagged complete must have rows through their last day.

## lint.volatile_time_in_measure (time, medium)

**When**: MTD/YTD/pacing/projection measures use TODAY(). **Why**: values change
day to day without data changes, and a stale import produces empty recent
windows (a projection collapses to YTD). **Read**: record the clock dependency;
capture the run date in the case; compare against the import watermark.

## lint.date_context_removed (time, medium)

**When**: ALL / ALLSELECTED / REMOVEFILTERS on the date table. **Why**: under a
partial-period slicer the measure can add a whole month (expenses, targets) or
all time (a REMOVEFILTERS injected by mistake). **Read**: instantiate
`dax.assert` for a partial range (for example the 1st–13th) and compare with the
full month; state the intended policy as a question if none is documented.

## lint.raw_division / lint.format_hides_precision / lint.stale_query_name / lint.freshness_label_on_business_date / lint.multiple_relationship_paths

Presentation and structure leads. Raw `/` produces infinity on empty slices;
integer formats hide 11.5 → 12; a stale query name shows "Inquiries" over a
column bound to `New Inquiries`; a "Last refresh" label bound to MAX(business
date) changes with slicers; two relationship paths (first/last touch) mean the
active one silently wins. Each hit is a claim about labelling or robustness,
usually `layer: cross-layer`, evidence: the lint file plus the rendered screen.

## dax.fan_out (grain, high)

**When**: a fact joined through a relationship or bridge; sums that could double.
**Instantiate**: one ROW with base rows, rows after traversing the path, distinct
business keys, e.g.
`ROW("Rows", COUNTROWS(fact), "Joined", SUMX(fact, COUNTROWS(RELATEDTABLE(dim))), "Keys", DISTINCTCOUNT(fact[id]))`.
**Read**: multiplier ≠ 1 or duplicate rows > 0 → failed claim with counts; the
synthetic pilot's spend doubled (10,878 → 20,538) exactly this way.

## dax.additivity (grain, high)

**When**: a breakdown next to a total. **Instantiate**: a member/value query plus
a total query. **Read**: remainder ≠ 0 means dropped BLANK members, double
counting, or a visual-level filter; report the remainder and which side is larger.

## dax.assert (generic)

Any invariant expressible as one DAX value: outside-window campaign events,
orphan keys (`COUNTROWS(fact) - COUNTROWS(fact joined to dim)`), zero-denominator
days, negative durations, future-dated events, timezone day mismatches. Use
`operator` and `tolerance`. The templates in `catalog.json`
(`dax.campaign_window_alignment`, `dax.campaign_name_churn`,
`dax.event_day_boundary`, `dax.future_or_negative_durations`) are `dax.assert`
probes with the placeholders `<fact>`, `<date_key>`, `<timestamp>`, `<duration>`,
`<name_column>` filled in from the model.

## stat.ratio_of_totals_vs_mean_of_ratios (ratios)

**When**: CPA/CPL/ROAS/conversion shown per member and in total. **Read**: the
gap between sum/sum and the mean of member ratios; if the displayed total equals
the mean of ratios, the total is wrong (the synthetic CPA fault). Informational
by itself; pair it with a `dax.assert` on the displayed total.

## stat.ratio_stability (ratios / time)

**When**: a daily ratio of two differently sourced populations (CRM leads over
platform leads, conversions over clicks) or a share that must stay in [0,1].
**Params**: `minimum_denominator` (volume guard), `upper_bound` (hard bound),
IQR band on the guarded days. **Read**: `bound` flags are structural (more CRM
leads than platform leads on a day means attribution or matching, not a rate);
`band` flags are review leads. Low-volume days are listed, never flagged.

## stat.weekday_robust_band (time)

**When**: any daily series with weekly seasonality. Each weekday is compared
with its own peers (median/MAD), so a Sunday is not judged against Mondays.
**Read**: flagged days with their weekday median and z; explain each from the
source (campaign launch, import gap, duplicate load) before signing. This is
what should catch a July-7-style spike automatically.

## stat.changepoints (time)

Binary segmentation on the mean with a robust scale; returns dates where the
level shifts by more than `minimum_shift_sigma`. **Read**: each shift must map
to a known event (tracking change, campaign start, definition change). Unknown
shifts are inconclusive claims, not defects.

## stat.population_stability (mix)

PSI between two periods' category mix (channel, campaign, province). **Read**:
PSI above 0.25 or any new / vanished category is a review lead; renamed
campaigns show up as one vanished and one new category, which is the usual cause
of "campaign X disappeared" tickets.

## Campaign and time alignment (campaign_time)

The three templates cover the recurring problems when time-stamped events meet
campaign metadata: events attributed to a campaign before its start or after its
end (window alignment), one campaign key with several names across sources
(name churn), and a date key that does not match the calendar date of the event
timestamp in the reporting timezone (day boundary). For the day boundary run the
template twice: with offset 0 (UTC) and with the reporting-timezone offset (for
example -4 for Toronto in summer). A key that matches UTC but not local time was
cut at UTC midnight and shifts evening events to the next day. All are `dax.assert` probes
expected to be zero; a non-zero count is a failed claim with the count and the
share of the population, and the agent should list the top offending campaigns
in `observed`.
