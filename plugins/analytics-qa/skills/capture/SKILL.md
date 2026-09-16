---
name: capture
description: Assemble a source-to-screen evidence report and the analyst sign-off page, handle analyst feedback, and preserve an explicitly reviewed baseline without inventing approval.
---

# Capture and review

Input: $ARGUMENTS

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

The command copies the run into `evidence/runs/<run>`, turns every captured state
into a scenario with its screenshot and receipt, binds the derived card/table
numbers as facts and records the claims with their receipts. A run whose journal
did not complete, or whose receipts failed, cannot be attached: repair the plan
and rerun into a fresh directory. Do not hand-write scenario lists.

Scenario descriptions come from the plan's step descriptions: write them for the
analyst ("July 1-7: 2,253 leads; the Google line shortens to seven points"), not
for yourself. Keep one component per report page group; five or six components
with three to five states each is a normal review.

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
