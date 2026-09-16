# Test the recovered Power BI behavior

## Design a discriminating matrix

Start from the investigation, not a universal list of SQL assertions. For each
important claim record the expected property, competing failure, experiment,
required evidence layers and tolerance before interpreting results. Select
tests from the actual dependencies and business risk:

| Risk | Discriminating experiment | Avoid the false oracle |
| --- | --- | --- |
| Filter removed accidentally | Two materially different segments, then combined selection and reset | TREATAS proves engine context, not the slicer wiring |
| Ratio/average error | Segment with unequal denominators; compare numerator and denominator separately at total and segment grain | Do not sum ratios or require total to equal average of displayed rows |
| Join/relationship fan-out | Duplicate and orphan key probes, pre/post-join additive totals, overlapping bridge members | Distinct-count totals can legitimately be less than sum of segments |
| Branching total logic | Leaf, subtotal and grand total using actual grouping/subtotal shape | A filtered ROW is not equivalent to an ISINSCOPE leaf |
| Date logic | First/last included day, empty day, prior-period/fiscal boundary, selected date role | Partial month versus complete month is not like-for-like |
| BLANK handling | No rows, rows with zero denominator, missing dimension member | NULL/BLANK is not zero, "no data" is not a loading failure |
| Field parameter/calculation item | At least one state per materially different measure or date behavior | Static default binding does not cover dynamic choices |
| Security filter | Allowed and excluded populations under real role/identity | Admin results do not validate RLS |
| Visual query reduction | Actual visual query/underlying values, sort and Top N boundary | A 1000-row export is not the full population |

Retain executable DAX and independently derived SQL/Python. Prefer
SUMMARIZECOLUMNS for grouped measures. In ADDCOLUMNS a bare SUM/COUNTROWS does
not transition row context; a measure or CALCULATE does. Check the test's own
segment row counts first. Compare keys, types, missing rows and numeric precision
before declaring equality. A model failure must not be hidden by Python computing
the intended formula instead of executing the real DAX.

For `ALLSELECTED`, retain the outer selection and visual grouping separately.
A month iterator can legitimately return the selection-wide total on each row;
that is not evidence that a date-filtered card broadcasts every month's expense.
Test the card with its actual outer filter context. When only aggregate dated
counts are available, a smaller historical bucket does not prove row deletions:
date reassignment and other restatements can produce the same net count change.

## Observe the actual interface

Identify the WebView by its visible file title, not endpoint order. `pbi.py targets`
returns target IDs and header lines. Pass `--page-id` and `--report-title` to
`pbi.py capture`, or call `pbi.check_report_title` before actions in a custom
procedure. A correct DAX catalog does not establish that a screenshot came from
the same report. Keep the engine-to-report association evidence separately.

Inspect slicer selection configuration before designing its transitions. Legacy
reports can use `isInvertedSelectionMode`: selecting a visible member can exclude
it from All. Record inclusion/exclusion semantics and actual members, not just
the caption "Multiple selections". Synchronized slicers can carry a restriction
onto a different page. A saved multi-selection must be restored exactly, not
cleared to All. Hidden headers may have no usable clear button; do not treat a
forced click on a zero-sized element as a successful reset. Virtualized dropdowns
require inspecting their actual scroll container and Select all control.

Choose a real, discriminating transition the control supports. An explicitly
verified exclusion and restoration can be more useful than an uncompleted
single-selection attempt. Name the scenario after its actual state. Record both
date input values after edits; the existing range can reject an invalid start
before its end is moved. Use one action/observation loop until preconditions and
postconditions are reliable, before batching scenarios.

Use the connected browser or native Desktop tools. Discover current controls;
do not guess DOM selectors. A useful sequence is:

1. Record report/page and baseline state, including hidden filters and bookmark.
2. Capture a full-page screenshot and target visual(s), visible values, selected
   controls, error/loading state and corresponding visual query/data where available.
3. Apply one selection. Verify selected state, then wait for affected content to
   settle. Capture again. Test intended affected and intentionally unaffected visuals.
4. Compare those outputs to the engine at the same filter and date context.
   Inspect titles, units, axes, ordering, clipping, tooltips and blank presentation.
5. Reset, verify reset state and confirm the baseline values return. A successful
   click or stable DOM is not proof that the intended state was reached.

The helper's capture result is an observation, never an automatic visual verdict.
Review the screenshot itself. A canvas can exist while charts show errors. Text
stability can occur before data loads. Native dialogs are outside WebView captures.
Retain rejected/failed observations instead of replacing them with clean ones.

`pbi.py capture` can take one scoped text selection with `--click-text` and
`--click-scope` (a selector discovered from the current DOM). It records per-visual
text and checked/selected states, plus screenshot and DOM. Use a fresh directory
per state. For multi-step/complex slicers use the host browser tools or a recorded
case-specific Playwright procedure, with explicit preconditions and reset steps.
Do not claim the helper supports arbitrary custom visuals or automatically
understands their interaction semantics.

`scripts/powerbi_controls.py` supplies the observed mechanics for Desktop WebViews:
`connect_page` (exact target + title), `go_to_page`/`active_page` (real page tabs,
first text line only), `toggle_member` (open, set one visible checkbox, re-read
every visible member and report `other_members_changed`, close, settle),
`set_date_range` (bound-safe edit order, both inputs asserted, calendar closed with
Escape), `dismiss_edit_overlays` (Desktop text-formatting toolbars intercept clicks),
and `observe` (screen.png, dom.html and a typed state.json with title, active page,
slicer captions, both dates, selected cells and every visible visual's text).
Dropdowns are virtualized: only the first visible members exist in the DOM, so prove
an applied multi-member filter from rendered rows, not from the popup alone. A
selected bar is shown by dimming the others (fill-opacity 0.4) and adding
`highlight` overlays; there is no `selected` class on the mark. Derive discriminating
values (`visual_value`, `table_total`) from the saved observation and feed them to
`check_state`, so the receipt and the screenshot describe the same capture.

Persist a read-only observation immediately after each meaningful transition.
Use `state_checks.check_state(observation_file, expected_pointer_map, receipt)`
to enforce the preconditions and postconditions that make the experiment valid.
Do not continue a batch after a failed check. A later capture cannot repair an
unverified earlier selection. Clear expectations and exact captured values are
both part of the evidence; these receipts do not automatically establish business
correctness or replace inspecting the screenshots.

## Regress meaning, not pixels

Keep three modes separate and record which one each comparison uses:

- **Same inputs, changed logic:** fix source snapshot, parameters, model version,
  identity and selections. Replay identical scenarios; explain changes in measure,
  relationship, binding or formatting. Without retained inputs this attribution
  is incomplete, even when the calendar period matches.
- **Same period, restated inputs:** reconcile changed source rows first. Split
  late arrivals, updates, deletions and definition changes. Recompute expected
  aggregates and explain the remaining delta; do not absorb everything into tolerance.
- **New period/data:** preserve business invariants and replay equivalent contexts.
  Compare mature overlapping periods after accounting for restatements; test the
  new day's coverage, freshness, joins, blanks and ratios independently. Compare
  similar weekday/maturity cohorts for anomaly triage, not automatic correctness.

For statistical work state sample size, excluded partial periods and baseline
window. Use robust median/MAD or quantiles for skewed spend; apply minimum-volume
guards to rates and disclose multiple comparisons. An anomaly is a review lead,
not proof of a defect, and normal-looking data can still be wrong.

For visuals align page, selection, identity, bookmark, viewport and period mode.
Compare data points and semantic presentation first. A new point and rescaled
axis can be correct; unchanged pixels after an expected refresh can be stale.
Keep screenshots as supporting evidence rather than a binary pixel-diff gate.
Replay the original triggering slices for previously fixed findings. Preserve
prior claim IDs or explicit ancestry when splitting claims; never silently
transfer approval to a broader or different claim.
