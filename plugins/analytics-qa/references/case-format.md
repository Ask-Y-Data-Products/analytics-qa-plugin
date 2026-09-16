# Case contract

`case.json` is a checkpointed public reasoning/evidence record, not a chat dump.

New cases require schema_version=2, id, target, intent, summary, scope (object),
trace (array), claims, facts, experiments, findings, limitations, review.
Version 1 historical cases remain readable, but cannot establish a new baseline
without the stronger contract below.

Claims: `{id, expected, source, status, observed, evidence}`. Status is passed,
failed or inconclusive. Source identifies the business definition or independent
oracle; evidence is an array of existing paths relative to the case directory.
`observed` explains the measured result and its limits. Retain stable IDs on replay.
Add `layer` (sql, engine, binding, render, interaction, or cross-layer) and
`coverage: {required: [...], observed: [...]}` using those same layer names.
Missing required layers prevent a pass. A broad expectation can remain
inconclusive while narrower child claims pass; identify their parent claim.
Layer and coverage fields are mandatory on every new claim.

New cases also record `state_contract_version: 1`. A passed interaction claim
requires `state_checks: ["evidence/.../check.json", ...]`. Generate these with
`scripts/state_checks.py` from saved observed fields and exact expectations.
Validation rechecks the observation hash, values and receipt status. Include
baseline, discriminating change and reset receipts; a receipt checks only its
listed fields and does not replace screenshot interpretation. Historical cases
without this version marker remain readable, not retroactively certified.

Components: `{id, name, page, definition, claim_ids, scenarios, run}` — the unit the
analyst signs. Each scenario is `{id, description, evidence}` where evidence holds
the state's screenshot, observation and receipt (`screen.png`, `state.json`,
`check.json`). Components are created by `qa.py component --case <dir> --run
<pbi_cycle output> --spec <component.json>`, never typed by hand; the validator
requires a description and a screenshot per scenario and known claim IDs. The
sign-off page (`review_form.py`) renders one card per component.

Detector claims: created by `qa.py detect` from `model_lint.py` output (`--kind lint`)
or a `probes.py` run (`--kind probes --component <id>`). They carry `detector`
(method id from `detectors/catalog.json`), `severity` and, for probes,
`probe_status` (passed / failed / review / inconclusive). Lint claims start
inconclusive with the literal fragment in `observed`; the agent confirms,
dismisses or leaves them as questions. A statistical `review` flag may only
become `passed` with an explanation in `observed`.

Facts: `{id,label,evidence,pointer,value,unit}`. Example:
`{"id":"N1","label":"Account B spend","evidence":"evidence/account.json",
"pointer":"/rows/0/spend","value":"333.00","unit":"USD"}`.
Use real measured values, never the illustration above. The JSON pointer resolves
inside the cited file; value must match exactly, including its JSON type. Keep
critical reported numbers here, and use these facts in the narrative. Derived
numbers need a saved, executed calculation and its result file. The validator
checks facts against evidence; it cannot verify arbitrary prose or business truth.

Experiments: `{id, question, procedure, result, evidence}`. Use procedure as an
object with command(s), parameter/time bindings, state setup and verification,
evaluator and tolerance, plus whether an agent interpretation is required.
Record execution errors distinctly from failed assertions. Examples/results are
typed JSON; save custom analysis programs and actual commands, not just prose.
The `commands` or `command` field is mandatory; a file path alone is not replay.

Findings: `{id, title, explanation, severity, claim_ids, impact, evidence}`.
Trace links: `{from, to, explanation, evidence, confidence}`.
Scope must state period, account/filter population, deployed/source versions,
comparison mode and data completeness. Add unknowns to limitations explicitly.
For an inspected report with available static bindings, scope.binding_review must
record `{status:"reviewed",visual_ids:[...],evidence:[...]}` for the target visuals.
The inventory exposes parsed `visual_bindings` separately from raw source files.
This is a source review, not a claim that the report was deployed or rendered.

Paths: `case.json`, `procedures/*`, `evidence/source/*`, query/DAX JSON and PNG
captures under `evidence/`, derived `report.html`, hash index `manifest.json`.
Preserve baseline artifacts. A changed definition requires new evidence/review.

Sealing validates records and hashes evidence; it does NOT prove semantic truth.
Review remains empty until explicit human feedback. A future review revision
records claim/finding IDs, decision, reviewer, confirmation provenance and the
reviewed manifest hash. An LLM finding is not a human decision.
