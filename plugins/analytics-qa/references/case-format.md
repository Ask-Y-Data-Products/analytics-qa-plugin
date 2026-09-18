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
analyst signs, plus the optional `what_it_shows`. Each scenario is
`{id, description, evidence}` where evidence holds
the state's screenshot, observation and receipt (`screen.png`, `state.json`,
`check.json`). Components are created by `qa.py component --case <dir> --run
<pbi_cycle output> --spec <component.json>`, never typed by hand; the validator
requires a description and a screenshot per scenario and known claim IDs. The
sign-off page (`review_form.py`) renders one card per component.
A component spec claim's `layer` is one of interaction, render, engine or
cross-layer; any other value is refused with those four listed.

Plain-language fields: a component may carry `what_it_shows` (one or two
sentences that head its sign-off card) and a claim may carry `question` (the one
sentence the analyst answers). Both come from the component spec, both are
optional, and both are written for the analyst: strict validation refuses one
that reads like DAX — a function call, a `[column]` or `'Table'[Column]`
reference, a `fact_`/`dim_` identifier, a tool word such as oracle or receipt, a
file path — and the error quotes the offending token. The filter never applies
to `definition`, `expected`, `observed` or `source`, which stay technical and
render inside a collapsed block. Without these fields the page falls back to the
definition's first sentence and to `expected`, so historical cases still render.

Declared consistency pairs: a plan may carry
`consistency: [{label, a, b, tolerance}]`, where `a` and `b` are JSON pointers
into the observation (`/cards/Leads`, `/tables/leads`) for a figure that appears
twice on the page. `pbi_cycle.py` evaluates every pair after each capture and
writes `consistency: [{label, form, a, b, value_a, value_b, inputs, status}]`
into `state.json` before it is hashed, mirrors it in the journal's state record,
and counts `inconsistencies`. `status` is consistent, inconsistent or, when a
side is missing or not a number, not_comparable (with `note` saying why - an
abbreviated card such as "$240K" asks for the exact figure through an oracle).
An inconsistent pair does not stop the run: it is a finding the analyst confirms
or rejects on the sign-off page.

Besides that equality (`form: "equality"`), a pair may be derived from other
figures on the same page:

* `{"label": ..., "ratio": ["/cards/Starts", "/cards/Leads"], "equals":
  "/cards/Conversion", "tolerance": 0.1, "percent": true}` - a ratio card against
  the two figures it divides. `percent` scales the 0-1 ratio to the 0-100 card
  before `tolerance` is applied; a zero divider is not_comparable.
* `{"label": ..., "sum": ["/cards/Spend", "/cards/Other Expenses"], "equals":
  "/cards/Total Spend", "tolerance": 1}` - a total against its parts.
* `{"label": "CAC never below 0", "min": 0, "value": "/cards/CAC"}`, or `max` -
  a figure that may not cross a limit.

`value_a` is always the computed left side and `value_b` the target (the figure
it must equal, or the limit), and `inputs` records each pointer's own value, so
the sign-off page can show the arithmetic. `tolerance` is absolute in every form.
A pair that declares none of these four shapes is refused by the runner.

Regression classification: a claim revisited by the regress skill may carry
`change_classification`, written by `qa.py classify`, exactly one of `preserved`, `expected change pending
review`, `new regression`, `defect fixed`, `still open` or `inconclusive`. It is
orthogonal to `status`, which stays passed, failed or inconclusive. The sign-off
page counts the values in a strip under the header, badges each claim, and leads
the component's questions with its `new regression` items. A case whose claims
carry none renders exactly as before.

Run and detector references: every `evidence/runs/<name>/` holding a
`journal.json` must be the `run` of exactly one component, and every
`evidence/detectors/<name>/` must be cited by at least one claim's evidence. An
attached directory that no record accounts for is a validation error — it means
`case.json` was hand-edited instead of built by `qa.py component` / `detect`.
Attachment is atomic: nothing is copied until the spec and receipts validate, and
a copy left over from a refused attempt is removed on the next attach.

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

## Artifacts

A finished review is five files. One lives inside the case; the other four are
written beside it, never into it, and every one of them is derived - regenerating
them changes no evidence.

| File | Question it answers | Written by | Who reads it |
| --- | --- | --- | --- |
| `<case>-how-it-works.html` | How does this BI actually work? | `investigate`, `scripts/lineage_report.py` | an analyst or engineer who inherited the report |
| `<case>-findings.html` | What is wrong with it? | `detect`, `scripts/findings_report.py` | whoever decides what to fix |
| `<case>/report.html` | What is the evidence? | `capture`, `qa.py render` | anyone auditing the record |
| `<case>-signoff.html` | Do you sign? | `capture`, `scripts/review_form.py` | the analyst who signs, and it is the regression report on a regression case |
| `<case>/manifest.json` | Has the evidence changed? | `qa.py seal` | `qa.py verify` |

`lineage_report.py` and `findings_report.py` take `--case` and `--out`, refuse an
output inside the case, work on an unsealed case (reporting the manifest as
`unsealed`), print a JSON summary and stamp the same generator meta tags the
sign-off page does: `<meta name="generator" content="analytics-qa <script>
<version>">` with the case id and manifest digest. `findings_report.py` also
takes `--signoff <relative path or url>`, which turns every item into a link to
that component's card (`#comp-<component id>`) on the sign-off page.

Sealing validates records and hashes evidence; it does NOT prove semantic truth.
Review remains empty until explicit human feedback. A future review revision
records claim/finding IDs, decision, reviewer, confirmation provenance and the
reviewed manifest hash. An LLM finding is not a human decision.

Review records: `review` holds only reviewer decision records, written by
`qa.py review` from an exported decisions file — never by hand and never by the
agent. Each is `{claim_id, decision, reviewer, confirmation, recorded_at,
reviewed_manifest_sha256}` where claim_id is a claim in the case, decision is
accepted, confirmed_defect, unresolved or exception (exception also needs reason,
scope and expires_at) and reviewer is non-empty. Prose, notes or a sign-off
narrative in `review` is a validation error.
