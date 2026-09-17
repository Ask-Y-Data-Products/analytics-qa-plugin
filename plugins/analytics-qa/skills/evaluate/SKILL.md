---
name: evaluate
description: Challenge marketing model and BI claims with independent SQL, DAX, statistical and interaction experiments, preserving reproducible evidence.
---

# Evaluate a validation case

Input: $ARGUMENTS

For multi-component requests, finish one component's baseline/change/reset
experiments before starting the next. Keep a worklist of requested states and
actual evidence. Page navigation and duplicate screenshots do not count as
filter variations. A connected WebView supports real input actions; do not
label that access unavailable because only the screenshot helper was used.

## Stateful Power BI experiments: write a plan, run the cycle runner

Use `${CLAUDE_PLUGIN_ROOT}/scripts/pbi_cycle.py` for every component. You design
the experiment; the runner performs it one observed step at a time and stops on
the first failed receipt. Read its module docstring (`python pbi_cycle.py --help`)
for the plan schema. A plan contains:

- `page`, the slicer titles to record, the date slicer title (or null), the card
  titles and table markers whose numbers matter, the chart titles whose marks
  matter;
- `oracles`: read-only DAX that computes the expected numbers for each state on
  the connected catalog, in the exact filter context of that state (dates,
  members, page filters);
- `steps`: `capture` (with `description` written for the analyst and `expect`
  pointers, e.g. `/cards/Starts` = `{"oracle": "three", "column": "[Starts]"}`,
  `/date_end` = `"7/7/2026"`, `/slicers/Channel` = `"All"`), and these actions:
  - `toggle_member` `{slicer, label, selected}`: check/uncheck one member of a
    dropdown slicer (checkbox list);
  - `select_option` `{slicer, label}`: choose one option of a tile / list / toggle
    slicer (items rendered as `role=option`, e.g. "Paid Ads Spend" / "All Expenses");
  - `set_date` `{slicer, start?, end?}`: type new bounds into a between-dates slicer;
  - `click_mark` `{chart, category}`: click a bar/column by its axis label;
  - `go_to_page` `{name}`.
  Every control the task names must be exercised with one of these; a component
  whose requested change is left "not tested" or "not automatable" is incomplete,
  because the runner covers all Desktop slicer types above. Read
  `pbi_cycle.py --help` before writing the first plan.

Receipt rules the runner enforces, so write the `expect` before the step: every
capture asserts at least one number or caption beyond the five screen guards
(report title, active page, popup/calendar/edit overlay) - those prove the right
screen was open, not that it showed the right number; a capture that follows an
action asserts at least one pointer whose value differs from the previous
capture's expectation for the same pointer; `"allow_weak_receipt": true` is
allowed only with a written `"weak_receipt_reason"`. A weak capture stops the run
after its observation is written, so read that `state.json` for the real numbers
and put them in the plan.

Always capture a baseline first, then one discriminating change, then the reset
that restores the baseline values, and expect the baseline numbers again in the
reset capture. Write every step `description` for the analyst, with the numbers
that prove the state ("Google Ads unchecked: 17 starts, median 9; table shows two
rows"); these sentences become the captions under the screenshots on the sign-off
page. Tile/list slicers (a toggle rendered as `role=option` items) are driven with
`select_option`, not `toggle_member`; cards that render abbreviated text such as
"$240K" are compared through `/cards_text/<title>` while the exact number comes
from the DAX oracle. Use `"round": 0` on an oracle reference when the card's
format string rounds the value.

Also look for definitions that silently depend on the clock or on labels:
calculated columns or measures using TODAY()/NOW(), text boxes that say
"Last refresh" but bind a business date, headers whose caption differs from the
bound measure. These are defects or review questions; record them as failed or
inconclusive claims rather than leaving the component at "all passed". Expectations must be discriminating: a total that changes with the
action, both date bounds, the affected slicer caption, and `/charts/<title>/selected_marks`
or `/charts/<title>/highlight_active` when a mark was clicked. Look at the failed
receipt and observation when a step fails, fix the plan (wrong oracle context,
wrong member label, wrong card title), and rerun into a fresh output directory.
Retain the failed run. Do not write your own Playwright when the runner covers
the action; extend a plan instead. Save each plan as `plan.json` inside its run
directory so `qa.py component` can retain it.

Before designing, inspect the page: `pbi.py capture --out <scratch> --page-id ...
--report-title ...` records every visible visual's text, so you learn the exact
card titles, table markers, slicer titles and chart titles the plan must use.
Inspect slicer selection configuration (saved multi-selections,
`isInvertedSelectionMode`, synchronized slicers) before choosing a transition.
Virtualized dropdowns expose only their first visible members; prove an applied
multi-member filter from rendered rows, not from the dropdown.

## Then run the detectors

After a component's capture cycle, run the `detect` skill for it: model lint
once per case, then the failure-mode detectors that apply to the component
(fan-out, additivity, ratio-of-totals, weekday band, changepoints, ratio
stability, campaign window / name churn / day boundary, mix stability). They
answer a different question than the controls: not "does the slicer work" but
"is the number right". Read `${CLAUDE_PLUGIN_ROOT}/detectors/METHODS.md`.

## Oracles and matched context

Before a cross-layer pass, match period, page/visual filters, slicer selections,
grouping, source snapshot and subtotal scope. Cite those matched contexts.
An all-time percentile cannot validate a selected-week screenshot. Do not infer
the semantics of numeric interaction codes; test their effect and classify it
from rendered marks (dimmed marks plus highlight overlays = highlight; changed
row population = filter; unchanged values = no interaction). Inspect nonblank
sample size before explaining null statistics. Do not explain a forecast from
its title: follow the exact bound measure and recursively inspect its formulas.
Large model files are not an access limitation: extract only relevant objects
with a structured parser. Do not pipe away errors or truncate away test results.

Read the existing case and relevant definitions. For every critical claim,
choose a plausible failure and an experiment that could distinguish it from
correct behavior. Read [testing playbook](../../references/experiments.md).
For Power BI, apply the risk-driven matrix and stateful observation process in
[Power BI testing](../../references/powerbi-testing.md). Record which model features
were present, tested, unavailable or out of scope; do not present checklist coverage
as executed evidence.

Save SQL/DAX and one-off Python programs under `procedures/`. Execute SQL via
`python ${CLAUDE_PLUGIN_ROOT}/scripts/qa.py sql --config <pilot.json> --file
<procedure.sql> --out <case/evidence/id.json>` (Azure SQL) or
`scripts/bigquery_evidence.py` (BigQuery, cost-bounded). These exports record
types, completeness, timestamps and executed SQL. Aggregate in the database for
broad checks; never silently treat a limited export as a full relation.

Use the actual Power BI engine and rendered report, not a Python reimplementation
of the report's DAX. See [Power BI](../../references/powerbi.md). A Python oracle
can independently calculate the expected result but cannot substitute for the
observed DAX result. Failed tool calls and unavailable surfaces are inconclusive,
not passes. If the BI is unavailable, complete SQL work and mark visual/DAX gaps.

Separate SQL, semantic-engine, report-binding, rendered-content and interaction
claims. Preserve the original expectation's scope: a claim that a selected account
filters every visual cannot pass from DAX queries or report configuration alone.
Keep that original claim inconclusive, with separately identified passed engine
subclaims. Record each claim's `layer` and `coverage`; required unavailable
layers block pass. Distinguish an absent row from an existing row with a BLANK
aggregate; BLANK is not evidence that the business amount should be zero.

Before reporting, audit every pass against its actual receipt: never count failed
selections, unchanged states or unverified resets as completed coverage.
Distinguish defects in the tested report from errors in an earlier QA report.
A rounded display value is not a different underlying statistic. Re-read the
exact source expression before describing it.

For each experiment record `{id,question,procedure,result,evidence}`; `procedure`
must include the exact replay command, parameters, reset state and comparison
mode. Quantify findings, including metrics and affected slices; show any
unexplained remainder. Separate proven explanations from hypotheses. Add
findings and claim results to case.json, attach each component with
`qa.py component`, then run the capture skill. Do not edit models to make a
test pass during an audit. Limit investigation to agreed scope and budget.
