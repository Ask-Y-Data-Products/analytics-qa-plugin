---
name: regress
description: Revisit an analytics validation case across model changes, source restatements or new reporting dates, distinguishing semantic and rendering regressions.
---

# Regress a validation case

Input: $ARGUMENTS

## Where this fits

You are step 5 of investigate → evaluate → detect → capture → (regress) → retrospective, run when the report, the data or the period changed.

The analyst wants one answer: did this change break anything. Replay the sealed baseline
against the new report, separate what the change was meant to do from what it did by
accident, and keep untouched pages as negative controls. Data moves too: a dashboard that
was right in June can be wrong in August with nobody touching it.


For Power BI targets, apply [Power BI investigation](../../references/powerbi-investigation.md)
to the changed dependency closure and [Power BI testing](../../references/powerbi-testing.md)
for stateful UI replay and period-aware comparisons.

Verify the prior case with `${CLAUDE_PLUGIN_ROOT}/scripts/qa.py verify --case
<baseline>`. Load its claims, procedures, source versions, limitations and review.
Create a new run; preserve IDs. If the previous run is unapproved, call this a
provisional comparison, not a signed-baseline regression.

Create the run with `qa.py init --case <new> --target <component> --project <root>`.

A regression case is built exactly like a baseline review, with the same tools:
replay each baseline plan with `pbi_cycle.py` into `<project>/runs/<component>-regression`,
then attach every replayed component with `qa.py component` (keeping the baseline's
claim ids so the two cases line up), attach the detector runs with `qa.py detect`,
and record the verdicts with `qa.py classify`. Do not write claims or components
into `case.json` yourself: a hand-written case has no scenarios, so the analyst
gets a page with no screenshots and nothing to compare. A component whose replay
could not run is a limitation, recorded as such, not a hand-made claim.
Immediately Read its generated case.json before Write/Edit; script creation does
not satisfy Claude Code's read-before-write requirement.
For an existing unfinished run, call `qa.py inspect --project <root> --case <new>`.
This current-source inventory is mandatory for a project-backed regression; do
not jump directly from prior numerical procedures to a new verdict. Read the
printed visual-to-field map and identify the target visuals. Set
`scope.binding_review` to `{status:"reviewed",visual_ids:[...],evidence:[...]}`
after reviewing their current projections, filters and labels. Unknown rendering
does not make static report bindings unavailable. Inspect changes at both layers.

Audit the prior case's conclusions as well as its hashes. Integrity does not
establish that a prior pass was warranted. If it passed a UI claim using only
engine/static evidence, explicitly correct that classification in the new case,
preserving the original expectation and explaining the coverage correction.
If verification fails, inspect the exact added/missing/changed paths inside that
case. Do not attribute a historical bundle mismatch to changes in the current
project, invent a --force flag, or silently bypass retention verification. Fresh
measurements can proceed in a new case, with the baseline integrity gap explicit.

Record change intent before interpretation. Re-inspect current bindings/code.
Classify conditions before comparing:

* Same inputs: compare implementations on retained identical source versions.
* Same period, restated inputs: rerun the old transformation chain on new inputs
  where feasible. Otherwise quantify source changes without claiming the whole
  difference was caused by code. A CSV extract is not a full retained database.
* New day/window: resolve time bounds once using timezone and source maturity.
  Recompute the expected population/series and reapply accepted properties.
  Never compare new-day revenue against a fixed old-day number.

Replay saved procedures, checking applicability and parameters. Preserve missing
checks as inconclusive, not silently dropped. A fix is proven only under the
original triggering condition, not by the absence of that condition today.

Retain reused exports byte-for-byte with `qa.py retain --baseline <prior> --case
<new> --files <relative-paths>`. Do not reconstruct an old export with the Write
tool. Retention verifies the baseline and records exact-copy provenance, but does
not make old observations current. Resolve database/catalog, active engine port,
period/filter bindings and new output paths before replay; compare recorded
commands with the actual connection metadata in the prior evidence.

For visuals, align state and compare content, interaction and rendering. A new
point, shifted window or valid rescale may change many pixels without a defect;
identical pixels may mean stale data. Use SQL, real DAX, series data, labels,
tooltips and screenshots together. Never reduce visual correctness to pixel diff.

Classify each claim as preserved, expected change pending review, new regression,
defect fixed/still open, or inconclusive; explain numerical contributions and any
unexplained remainder. Use capture to deliver the new report without auto-approval.
Record it with the tool, never by editing the case:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/qa.py classify --case <new> --classifications <file.json>
```

where the file maps each claim id to `{"change_classification": "...", "note": "..."}`.
The command refuses an unknown claim or an unknown value, and lower-cases the
vocabulary for you. Store this in `change_classification`; `status` remains passed, failed or
inconclusive. Preserving an inconclusive claim does not turn it into a pass.

The sign-off page renders `change_classification` literally, so write exactly one
of these six values, in lower case: `preserved`, `expected change pending
review`, `new regression`, `defect fixed`, `still open`, `inconclusive`. It
counts them into a strip under the page header ("Since the reviewed baseline:
2 preserved, 1 new regression"), badges each claim in its expectation table, and
leads every affected component's "Questions for you" with its regressions,
phrased "Since the baseline, <expected> no longer holds: <observed first
sentence>. Is this an intended change?" - so write `expected` and `observed` for
that sentence: plain, one sentence of observation first, no DAX (a technical
wording is replaced by a neutral question and moved into the technical note). A
case whose claims carry no `change_classification` renders exactly as before.
Invoke the capture skill before delivery. Restore and capture the final UI state
before sealing, then validate, seal, render and verify without further evidence writes.

## What the regression delivers

A regression case produces its own pages, built from its own evidence - never by
editing the baseline's:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/findings_report.py --case <new> --out <case-parent>/<new-name>-findings.html --signoff <new-name>-signoff.html
python ${CLAUDE_PLUGIN_ROOT}/scripts/review_form.py --case <new> --out <case-parent>/<new-name>-signoff.html
```

The sign-off page of a regression case **is** the regression report. It opens
with one sentence a manager can act on, derived from the claims you classified as
`new regression`: "One thing broke since the last approved version: the Leads
table now leaves out returning enquiries." That sentence is the first line of the
first claim's `observed`, so write `observed` as one plain sentence of what the
reader would notice - a technical wording is dropped and replaced by the
component's name, which tells the reader far less. When nothing carries `new
regression` the page says "Nothing broke since the last approved version" and, if
anything is still undecided, that some changes still need a decision. Under the
sentence comes the strip counting the classifications, then the components, each
badged with the most serious thing that happened to it; every `new regression`
leads its component's questions. The findings report
beside it is the fix list - what broke, worst first, with what still passes as
the negative control. Produce the how-it-works report as well when the report
definition or the model changed, so the reader can see what the new structure
looks like. Report every path in the final message, and say which baseline case
the comparison is against.
