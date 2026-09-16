---
name: regress
description: Revisit an analytics validation case across model changes, source restatements or new reporting dates, distinguishing semantic and rendering regressions.
---

# Regress a validation case

Input: $ARGUMENTS

For Power BI targets, apply [Power BI investigation](../../references/powerbi-investigation.md)
to the changed dependency closure and [Power BI testing](../../references/powerbi-testing.md)
for stateful UI replay and period-aware comparisons.

Verify the prior case with `${CLAUDE_PLUGIN_ROOT}/scripts/qa.py verify --case
<baseline>`. Load its claims, procedures, source versions, limitations and review.
Create a new run; preserve IDs. If the previous run is unapproved, call this a
provisional comparison, not a signed-baseline regression.

Create the run with `qa.py init --case <new> --target <component> --project <root>`.
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
Store this in `change_classification`; `status` remains passed, failed or
inconclusive. Preserving an inconclusive claim does not turn it into a pass.
Invoke the capture skill before delivery. Restore and capture the final UI state
before sealing, then validate, seal, render and verify without further evidence writes.
