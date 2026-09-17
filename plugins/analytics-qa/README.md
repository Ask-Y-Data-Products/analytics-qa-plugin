# Measure QA Community Harness (`analytics-qa`)

QA for dashboards, done the way an analyst does it, driven by an agent, signed by
a human. The agent takes a Power BI report apart, tests its numbers against
independent calculations, shows the analyst what it saw, and keeps a sealed record
so the next change can be compared against it. The scripts provide evidence
operations and record integrity, never a universal correctness oracle.

The story, the screenshots and the community knowledge base are in the
[repository README](https://github.com/Ask-Y-Data-Products/measure-qa-harness).

## The pipeline

`investigate → evaluate → detect → capture → (regress) → retrospective`

| Skill | What it does |
| --- | --- |
| `investigate` | Map the component: visual, binding, measure, relationships, partitions, warehouse column. Open the case. Prove nothing yet. |
| `evaluate` | Test it as an analyst would: a figure against another figure, a card against its table, a ratio against its inputs, the same figure on two pages, one filter moved and put back, a stable period and a volatile one. Every step observed on the live report and checked against its own oracle. |
| `detect` | Hunt the failures that survive a working dashboard: fan-out, non-additive members, clock-driven flags, ratio-of-averages, campaign windows, timezone day boundaries, spikes, level shifts, mix changes. |
| `capture` | Seal the evidence and produce the sign-off page the analyst signs. |
| `regress` | Replay the sealed baseline against a changed report, period or model; classify preserved / expected change / new regression / fixed / still open, with untouched pages as negative controls. |
| `retrospective` | Turn the review into an anonymised know-how article and, only with the user's explicit approval, publish it to the shared knowledge base that `investigate` and `detect` search. |

## Run on another project

1. Install Python dependencies from this directory's `requirements.txt` into your
   project environment. Azure SQL evidence uses Azure CLI authentication and an
   installed ODBC driver. Use a read-only database principal for real projects.
   Python 3.13 is the tested runtime. Authenticate Azure CLI outside the agent.
2. Add `pilot.json` to your project using `examples/pbix-project.json` (PBIX + engine) or `examples/bigquery.json` (warehouse helper) as the format.
   Paths are relative to your project, and business definitions are yours.
   Existing dbt `target/manifest.json` and `run_results.json` improve tracing.
3. Open a working copy of the PBIX or PBIP in Power BI Desktop. Preserve the
   imported snapshot; do not refresh unless the test explicitly requires it.
   For browser-based capture in
   this Windows prototype, launch Desktop with the process environment variable
   `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333`.
   Keep the debugging endpoint local and close that test instance when finished.
   Use `scripts/pbi.py targets --endpoint http://127.0.0.1:9333` to identify the
   exact report title and page ID; multiple reports can share the same endpoint.
4. Start `claude --plugin-dir <absolute-path-to-this-plugin>` in your project.
5. Ask `/analytics-qa:investigate Validate the CPA card and trend; investigate,
   evaluate and capture the result in cases/first-review`. Permit read-only
   queries and local evidence-file creation. Review `cases/first-review/report.html`.

Use `/analytics-qa:regress Compare cases/first-review with the current project;
the reporting period has advanced one day` for a subsequent run. State intended
changes. The plugin must not infer that new values should equal historical ones.

## Stateful Power BI experiments

`scripts/pbi_cycle.py --connection <connection.json> --plan <plan.json> --out <dir>`
runs an agent-designed plan (page, slicers, cards, tables, charts, DAX oracles and
capture / toggle_member / set_date / click_mark / go_to_page steps) one observed
step at a time, writing a screenshot, DOM, observation and receipt per state and
stopping on the first failed receipt. `qa.py component --case <dir> --run <out>
--spec <component.json>` attaches the run to the case as a signable component.
`scripts/powerbi_controls.py` holds the observed control mechanics these use.

## Failure-mode detectors

`detectors/catalog.json` and `detectors/METHODS.md` describe the failure modes
the plugin hunts for beyond "does the slicer work": grain and fan-out, ratios
computed as averages, clock-driven date flags, whole-period amounts under
partial filters, attribution lenses, campaign windows and name churn, timezone
day boundaries, weekday-adjusted spikes, level shifts and category-mix shifts.
`scripts/model_lint.py` scans captured model/report metadata; `scripts/probes.py`
runs DAX invariants and the statistics in `scripts/stats_lib.py` on the live
model; `qa.py detect` attaches results as claims. The `detect` skill selects
what applies to each component. Add a detector by adding a catalog entry, a
METHODS section and (for a statistic) a function with a test.

## Review

Inspect claims, trace, experiments and captures in the HTML report. Ask for
drilldown or provide feedback by claim/finding ID in chat. Accepted decisions need
your explicit confirmation. `scripts/review_form.py --case CASE --out review.html`
creates the analyst sign-off page outside the evidence case: per component, the
captured screenshots in story order, defects and open questions, Sign off / Reject
buttons with a comment. Its exported
decisions pin the exact manifest; this is a local attestation, not an authenticated
digital signature. `qa.py review` creates a separate sealed revision
from a decision JSON file; it does not overwrite the original evidence.

## Knowledge base

`scripts/retrospective.py` turns a finished review into an anonymised article
other users of the plugin can search. `digest` summarises the project's recent
Claude Code transcripts, the analyst's prompts and definitions and the cases'
findings into a local file; that digest contains client material and never leaves
the machine. The agent writes a draft, `redact` applies the project's
`kb.json` replacements and scrubs emails, ids, user paths, URLs and phone-like
numbers, then reports every capitalised name, currency amount and large figure it
could not decide about. Only after the user explicitly approves in chat does
`publish` write `articles/<date>-<slug>.md` into the configured git repository and
rebuild `index.json` and `README.md`; it refuses without `--confirm` and
`--approved-by`, on an unknown topic, or while a configured term still appears.
`search` ranks articles with TF-IDF over title, topics, summary and body, so
`investigate` and `detect` can ask "conversion rate Meta reporting gaps" before
designing tests. Topics are the controlled vocabulary in
`retrospective/topics.json`. See [references/knowledge-base.md](references/knowledge-base.md).

## Boundaries

The supplied SQL connectors support Azure SQL with Azure CLI auth and BigQuery
with local gcloud authentication (`scripts/bigquery_evidence.py`, dry-run and
maximum-byte-bounded queries). The agent may
use another existing database/MCP connector, but must preserve executed SQL,
typed results, completeness and provenance in the case; other engines are not
connector-tested here. Power BI local DAX probing requires Windows Desktop's
ADOMD library. Static inspection handles PBIP bindings, BIM/TMDL source files,
legacy report JSON and PBIR files, but does not pretend those establish rendering.
The visual map normalizes legacy `report.json` and modern PBIR projections,
filters, interactions, titles and hidden state. TMDL is retained as source;
runtime DMV discovery supplies the resolved model and dependency metadata.
For a legacy PBIX, `scripts/pbix_snapshot.py --pbix REPORT.pbix --out SNAPSHOT`
extracts only Report/Layout and normalizes bindings. It does not unpack model
data or credentials. Modern unsupported containers need a PBIP/PBIR export.
The live engine remains necessary for DAX, relationships and partition inspection.

## Observed Desktop controls

`scripts/powerbi_controls.py` drives the Desktop WebView through Playwright over the
local CDP endpoint: exact-target connection by title, page navigation, dropdown
member toggles that report side effects, bound-safe date-range edits, edit-overlay
dismissal and a one-call `observe` that writes screen.png, dom.html and a typed
state.json. Pair each observation with `scripts/state_checks.py` receipts. The
helpers read the DOM before and after every action and never infer a state from
the requested action; the agent still interprets the screenshot and the values.

## Power BI investigation logic

The investigation skill follows visual -> query/filter state -> DAX dependency
closure -> relationship paths -> M/storage partitions -> physical SQL/dbt sources.
It requires explicit catalog selection and report association, not the first
running engine. `scripts/pbi.py catalogs --port PORT` lists catalogs;
`scripts/pbi.py model --port PORT --database CATALOG --out CASE/evidence/model`
captures live metadata, refresh state and calculation dependencies.

The Power BI playbooks cover risk-driven tests for context removal, ratio totals,
iterator/context transition, subtotal branches, date roles, orphan/duplicate keys,
many-to-many paths, field parameters, calculation groups, incremental refresh and
RLS. Coverage is conditional: written guidance is not proof those features have
been tested on your model. The demo does not certify arbitrary custom visuals,
remote/composite models, calculation groups or security roles. Inaccessible
features remain explicit gaps. Read the two Power BI playbooks in `references/`
for the investigation and experiment procedures.

`dbt_project` and `definition_file` are optional for a runtime-only investigation;
missing business definitions prevent approval of inferred business meaning.
`powerbi_project` accepts PBIP or a report's `definition.pbir`. Keep referenced
source folders inside the configured project root. Metadata and screenshots can
contain business values or M literals; review the case before sharing it.

Missing surfaces are recorded as coverage gaps. A stable screenshot is not a
visual pass; an inferred formula is not an approved definition; a sealed manifest
is integrity evidence, not proof that an LLM's conclusion is right. This v1 has
no scheduler, Prism upload adapter or production deployment gate.
