---
name: detect
description: Select and run failure-mode detectors (model lint, DAX invariants, statistical tests over series) for each reviewed component, and turn the results into claims the analyst sees.
---

# Detect failure modes

Input: $ARGUMENTS

## Where this fits

You are step 3 of investigate → evaluate → detect → capture → (regress) → retrospective.

Evaluate asks "does the control work". You ask "is the number right anyway". These are the
failures that survive a working dashboard: double counting through a join, members that do
not add up, a date flag driven by the clock, a ratio computed as an average of ratios,
events attributed outside their campaign window, a timezone that moves a day, a spike or a
level shift nobody explained, a channel mix that quietly changed.


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

## 6. Produce the findings report

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/findings_report.py --case <case> --out <case-parent>/<case-name>-findings.html --signoff <case-name>-signoff.html
```

This is the artifact of this step, and it is the page a fixer reads: what is
wrong with the report, worst first. It reads `case.json` only - every failed
claim and finding as a defect card, every inconclusive claim as an open question
grouped by component, the model lint grouped by detector and folded away as the
least urgent section, and a compact table of what was checked and passed, so the
reader sees the scope and not only the problems. Every defect and open question
shows its evidence before its words: the screenshots of the situations behind it
(the captures its claim cites, or its component's first and last situation when
it cites none), then what we saw, why it matters, where, and the analyst question
as the closing line under a short heading. `--signoff` is the file name the
capture skill will write beside the case; every item then links to that
component's card there, and every item carries a stable `#item-<claim id>` anchor
the deck and the sign-off page can link to. Report the absolute path. Run it
again after any claim changes, and run it even when nothing failed: "nothing
found, here is what was checked" is a result the analyst needs to see.

Because the page is built out of the case, a defect is only as readable as what
you wrote into the claim. Every failed claim and every `findings[]` entry needs:

- a plain **question** an analyst would ask ("Are there contracts with a
  non-positive contract value?"), because it becomes the card's heading. A
  question written for engineers is replaced by a neutral fallback heading and
  the real wording drops into the technical note, which is a worse page.
- an **impact** sentence on the finding, in the reader's terms ("Anyone reading
  the monthly chart under a narrowed date range sees the whole year"), because it
  becomes the "Why it matters" line. Without it the card states a number and
  leaves the reader to guess whether it matters.

The observation itself stays exact - "Observed 534 == expected 0" is rewritten
for the reader as "We measured 534 where the check expects 0", so keep writing
the precise form. Defects are ordered by severity and then by the size of the
number involved, so record `severity` and, for a detector claim, its `hits`.
