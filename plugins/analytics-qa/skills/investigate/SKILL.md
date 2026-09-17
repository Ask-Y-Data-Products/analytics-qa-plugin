---
name: investigate
description: Reconstruct how a dbt-backed Power BI component works, trace source-to-render behavior, and open an evidence-backed validation case.
---

# Investigate analytics behavior

Input: $ARGUMENTS

## Where this fits

You are step 1 of investigate → evaluate → detect → capture → (regress) → retrospective.

The analyst has a dashboard they need to trust: they built it, an agent built it, it was
rebuilt on another platform, or someone changed one measure. Your job here is to find out
how it actually works before anyone tests it: which visual shows what, which measure feeds
it, which table and warehouse column that measure reads, and which filters reach it. You
end with a case the next skills fill. You prove nothing yet.


Use the user's project and existing connections. This plugin's scripts live at
`${CLAUDE_PLUGIN_ROOT}/scripts`; examples below use `QA` for `python <that path>/qa.py`.
Use the project's virtual environment when present. Do not install or change
the user's data/model without their request. Scope shell operations to this case.

If the project has `kb.json`, search prior know-how before designing:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/retrospective.py search --config kb.json --project . --query "<report domain, visual type, failure mode>"
```

Read the top hits. When an article shaped a test you designed, cite it in the
case's `limitations` or in the relevant finding.

1. Read `pilot.json` (or ask the caller for equivalent connection/target context),
   business definitions and prior cases. Create a new case with `QA init --case
   <dir> --target <component> --project <root>`. This captures the current source
   inventory and prints the visual-to-field map. Resume an unfinished case instead of
   overwriting it. `QA inspect --project <root> --case <dir>` preserves source
   files and yields `evidence/inventory.json`; inspect that inventory selectively.
   Generated dbt artifacts can be gitignored; do not infer their absence from
   Glob/Grep results. Use the inventory's explicit captured paths.
   Immediately use the Read tool on the generated case.json before Write/Edit;
   creating it through a script does not satisfy Claude Code's read-before-write rule.
2. Work backwards from the report visual: report binding and filters -> DAX
   measures/relationships -> M partitions or DirectQuery -> dbt compiled models
   -> sources. Inspect raw SQL and deployed view definitions, not only manifests.
   For Import models, trace and measure refresh/snapshot age separately.
   Review the selected visual's actual field bindings even if no report canvas is
   accessible. A title, measure name and query projection can disagree independently
   of whether the underlying DAX measure is numerically correct.
3. Build `case.json.trace` with `{from,to,explanation,evidence,confidence}` links.
   Confirm critical links through actual values or controlled interactions.
   A source-code reference is static evidence, not proof of the deployed state.
4. State the reporting grain, population, keys, business definition provenance,
   timezone, completeness and important unknowns in `scope`. Propose claims and
   targeted counterexamples. Distinguish source completeness from MAX(date).
5. Continue into the evaluate skill for a full validation request. Do not stop
   at listing files when the user asked for validation.
   Invoke the capture skill before sealing and delivery, including its final
   reset-before-seal and post-render verification steps.

For any Power BI target, read and apply [semantic investigation](../../references/powerbi-investigation.md).
Capture the actual runtime model with `pbi.py model` when accessible, identify its
report association, and record the relevant risks before choosing experiments.
Read [Power BI connection mechanics](../../references/powerbi.md) for actual DAX/render
evidence and [case format](../../references/case-format.md) before saving a case.
Critical distinctions: query success is not correctness; matching totals is not
lineage; inferred definitions are not human approval; zero captured visuals is a
failed observation. Retain unknowns, don't fill them with plausible guesses.
