---
name: detect
description: Select and run failure-mode detectors (model lint, DAX invariants, statistical tests over series) for each reviewed component, and turn the results into claims the analyst sees.
---

# Detect failure modes

Input: $ARGUMENTS

The capture cycles prove that controls do what they say. This skill hunts for
the ways marketing analytics go wrong even when every control works: grain and
join fan-out, ratios computed as averages, date flags driven by the clock,
whole-month amounts under partial filters, attribution lenses, campaign windows,
timezone day boundaries, single-day spikes, level shifts, and mix changes.
Read `${CLAUDE_PLUGIN_ROOT}/detectors/METHODS.md` and
`${CLAUDE_PLUGIN_ROOT}/detectors/catalog.json` once per case.

## 1. Lint the model once

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/model_lint.py --model <case>/evidence/model --report <project>/discovery/report/report.json --out <case>/runs/lint/lint.json
python ${CLAUDE_PLUGIN_ROOT}/scripts/qa.py detect --case <case> --run <case>/runs/lint --kind lint
```

Every lint finding becomes an inconclusive claim with the literal fragment. Go
through them: confirm the ones that are defects with a `dax.assert` probe (for
example: months flagged "completed" must contain data through their last day),
dismiss the ones that are intended with a sentence in `observed`, and leave the
rest as review questions. Do not delete findings.

## 2. Select detectors per component

From the investigation you know each component's measures, relationships, date
role and visual type. When the project has `kb.json`, search the knowledge base
for that shape first — `python ${CLAUDE_PLUGIN_ROOT}/scripts/retrospective.py
search --config kb.json --project . --query "conversion rate Meta reporting
gaps"` — and pick up the detectors and thresholds other reviews found useful.
Then walk `catalog.json` and pick every detector whose `applies_when` matches;
write down why the others do not apply. Typical minimum:

| Component shape | Detectors |
| --- | --- |
| Count by date with a daily chart | stat.weekday_robust_band, stat.changepoints, dax.fan_out, dax.additivity |
| Ratio of two sources (CRM vs platform, conversion) | stat.ratio_stability (with upper_bound when it is a share), stat.ratio_of_totals_vs_mean_of_ratios |
| Table with members and a total | dax.additivity, stat.ratio_of_totals_vs_mean_of_ratios for any ratio column |
| Anything keyed by campaign | dax.campaign_window_alignment, dax.campaign_name_churn, stat.population_stability on campaign mix |
| Facts with a timestamp and a date key | dax.event_day_boundary |
| Durations / future starts | dax.future_or_negative_durations |
| Costs or targets under a date slicer | lint.date_context_removed follow-up dax.assert on a partial range |

## 3. Write the probe plan and run it

One plan per component, saved as `runs/<component>-probes/plan.json`. Every probe
has an `id`, a `method`, the exact `dax` in the component's filter context
(same dates, page filters and members as the captured baseline; for series use
a window of at least eight weeks ending at the import watermark), `params` and a
`question` for the analyst. Templates in the catalog use `<fact>`, `<date_key>`
placeholders: fill them from the model, never guess column names (read
`evidence/model/TMSCHEMA_COLUMNS.json`).

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/probes.py --connection connection.json --plan <run>/plan.json --out <run> --model <case>/evidence/model
python ${CLAUDE_PLUGIN_ROOT}/scripts/qa.py detect --case <case> --run <run> --kind probes --component <component id>
```

Always pass `--model` (the case's model capture, or the discovery model
directory). It checks every `'Table'[Column]` and `[Measure]` in the DAX against
the captured model before the query is sent: a probe that names a column the
model does not have, or still carries a `<fact>` placeholder, comes back
inconclusive with the closest real name and is never executed. Fix the name and
rerun into a fresh run directory. An inconclusive probe caused by a naming error
is not a finding: it must not be attached as one and does not count as coverage
of the failure mode it was meant to test.

`probes.py` writes one evidence file per probe with the query, the result rows,
the method output and a status: passed, failed (dax.assert only), review (a
statistical flag) or inconclusive (too few points, execution error).

## 4. Explain every flag

A `review` result is a lead, not a finding. For each flagged day, shift or
category: query the source for that slice, state the cause you established or
that none was found, and set the claim to failed (defect), passed (explained and
expected) or leave it inconclusive with the question for the analyst. Put the
numbers in `observed`. A component with only passed detector claims and no
explanation of its flagged points is not finished.

An explanation is evidence, not a story. It must cite something in the case: a
DAX/SQL result you ran (for example the inquiry_type of the duplicated keys, the
campaign whose dates are missing, the calendar showing a public holiday), a
definition from `context.md`, or a report text box. Phrases such as "coincides
with a known campaign change" or "likely a budget reduction" without a cited
query are not explanations; leave the claim inconclusive with the exact question
for the analyst instead of turning a flag into a pass.

## 5. What not to do

Do not lower thresholds to make flags disappear. Do not run a statistic on fewer
points than its minimum and call the result clean. Do not treat an empty lint as
proof. Do not skip the detect pass for a component because the capture cycle
passed; the two answer different questions.
