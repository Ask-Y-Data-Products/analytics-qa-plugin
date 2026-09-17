---
name: capture
description: Assemble a source-to-screen evidence report and the analyst sign-off page, handle analyst feedback, and preserve an explicitly reviewed baseline without inventing approval.
---

# Capture and review

Input: $ARGUMENTS

## Rules an unattended run must follow

- Never hand-edit `case.json`. Every structure in it — claims, components,
  scenarios, facts, experiments, findings, review — is written by `qa.py init`,
  `component`, `detect` and `review`. A hand-added or hand-removed record is a
  validation error (an attached run with no component, a review entry that is not
  a reviewer decision), not a shortcut.
- Never hand-write the sign-off page. Only `review_form.py` produces it; it
  stamps `<meta name="generator" content="analytics-qa review_form <version>">`
  with the case id and manifest digest, so a hand-written page is detectable.
- Leave `review` empty during a headless run and say "awaiting review". Filling
  the analyst decision block yourself is fabricated approval.
- Keep every evidence path relative to the case directory
  (`evidence/runs/<run>/state.json`). Absolute paths, empty strings and `/` are
  refused, and the error names the claim, fact or component that holds them.
- When a tool refuses, fix the input and rerun it; do not work around the tool.
  `component` and `detect` validate everything before copying, so a refusal
  leaves nothing behind and the corrected spec attaches on the next try. If a
  copy is left over from an interrupted attempt that no record in `case.json`
  references, the next attach removes it and says so.
- If a step cannot be completed, stop and report the blocker. Do not degrade the
  deliverable to produce something that looks finished.

Read [case format](../../references/case-format.md). Ensure the report includes
business impact, trace, claims, reproducible experiments, actual visual evidence,
and unresolved coverage. Use `${CLAUDE_PLUGIN_ROOT}/scripts/qa.py validate --case
<dir>`, then `seal` and `render`. Open the generated report.html or return its
absolute path. Read the report back and fix broken evidence links.

## Components are the unit the analyst signs

Every reviewable component must be attached with
`python ${CLAUDE_PLUGIN_ROOT}/scripts/qa.py component --case <dir> --run <pbi_cycle
output> --spec <component.json>` BEFORE sealing. The run comes from
`scripts/pbi_cycle.py` (see the evaluate skill); the spec names the component,
its page, a one-paragraph definition, the claims it establishes (each citing the
captured states that prove it) and the report visual IDs it covers:

```json
{"id": "C3", "name": "Contract Velocity: starts and percentile cards", "page": "Contract Velocity",
 "definition": "Starts counts fact_contract rows by contract start date ...",
 "visual_ids": ["d9e6c7b7206716ace6d0"],
 "claims": [{"id": "V1", "expected": "Unchecking one channel changes Starts and the table; re-checking restores them.",
             "observed": "48 -> 17 -> 48 ...", "layer": "interaction",
             "states": ["state1_baseline", "state2_two_channels", "state3_restored"]}],
 "experiments": [{"id": "EXP-V", "question": "...", "result": "..."}]}
```

`layer` is exactly one of `interaction` (a slicer/click changed the numbers and
receipts prove it), `render` (the screen shows the engine's numbers), `engine`
(DAX only) or `cross-layer` (source versus engine comparison). Anything else —
`data`, `sql`, `binding` — is refused with the four accepted values in the
message. The layer sets the required coverage, and `interaction` is the only one
that attaches state-check receipts.

The command copies the run into `evidence/runs/<run>`, turns every captured state
into a scenario with its screenshot and receipt, binds the derived card/table
numbers as facts and records the claims with their receipts. A run whose journal
did not complete, or whose receipts failed, cannot be attached: repair the plan
and rerun into a fresh directory. Do not hand-write scenario lists. The attach is
atomic — the spec, the receipts and the cited states are checked against the
source run first, so a refusal copies nothing and you simply fix the spec and
rerun the same command.

Scenario descriptions come from the plan's step descriptions: write them for the
analyst ("July 1-7: 2,253 leads; the Google line shortens to seven points"), not
for yourself. Keep one component per report page group; five or six components
with three to five states each is a normal review.

## Detectors are part of the review

Before sealing, every component needs the `detect` skill's pass: the model lint
attached once (`qa.py detect --kind lint`) and at least one probe run per
component (`qa.py detect --kind probes --component <id>`), with every review flag
either explained or left as an explicit question. A case whose components carry
only capture claims is incomplete.

## Deliver

Finish every observation, interaction reset and final-state capture BEFORE seal.
Then validate -> seal -> render -> verify. After sealing, only derived rendering
and read-only verification belong in that directory; new evidence needs a new
case. Re-sealing a changed bundle is not a repair. Inspect any verification error's
actual added/missing/changed paths before explaining its cause.

Then generate the analyst sign-off page, which is the deliverable the analyst
actually reads:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/review_form.py --case <sealed-case> --out <case-parent>/<case-name>-signoff.html
```

It shows, per component, the captured Power BI screenshots in story order with
their descriptions, the open defects and business questions, and Sign off /
Reject buttons with a comment. It starts with no decisions, requires reviewer
name and confirmation, and exports decisions pinned to the manifest. Open it in
a headless browser (or read it back) and confirm every screenshot resolves and
each component card shows its scenarios before reporting the path. The final
message must give the absolute paths of report.html and the sign-off page.

Before sealing, audit the report against the inventory and actual result files:
each numerical claim must reproduce from its cited result; forecasts derived
from static formulas must be labeled inferred, never observed in Power BI.
Separate a static binding assertion from runtime measure execution and from
rendered UI behavior. New cases use schema version 2; every claim needs explicit
layer coverage, and every experiment needs an executable replay command.

## Review decisions

Import only an actual reviewer export with `qa.py review --case <sealed-case>
--decisions <export.json> --out <new-review-revision>`, then verify the revision.
This is a local attestation, not an authenticated digital signature. Reviewers
inspect a component, ask for drilldown, dispute a definition, or state decisions
by ID in chat. Run requested follow-up experiments; rerun affected assertions when
the definition changes. Present the exact decision changes before recording approval.

Never fabricate human signoff. During a headless validation run, leave review
empty and say awaiting review. A harness-simulated review must be labeled
simulation and must never become a human-approved baseline.

An accepted baseline consists of specific expectations and replay procedures,
not every observed value. Defects preserve desired behavior and the trigger
needed to reproduce them. Do not alter sealed historical evidence in place.
Sharing/upload needs the user's authorized repository and audience; screenshots
can contain sensitive data.
