# Power BI evidence

PBIP points to a Report folder; definition.pbir binds the semantic model.
Support both legacy report.json (nested JSON strings in config/filters) and
PBIR definition/pages/*/visuals/*/visual.json. Semantic models can be model.bim
or TMDL files. Inspect measures, calculated columns, relationships, visual/page/
report filters, interactions and M partitions. Do not assume source column names
are the same as display names or that all visuals use the same measure.

Import models have a second data state. This pilot intentionally imports an
Azure SQL mart export. `powerbi/data/provenance.json` records its SQL, timestamps,
watermark and CSV hash. Compare the actually loaded model to that snapshot and
the current warehouse independently. A fresh CSV does not mean Desktop refreshed.

`python <plugin>/scripts/pbi.py status` discovers local Power BI Analysis Services
instances and any configured browser debug endpoint. Several Desktop windows share one debug
endpoint and each runs its own engine, so tie BOTH ends of the connection to the
window you mean: the WebView target by its title line, and the engine by that
window's own workspace (its process, or the workspace folder created when it
opened). When two copies of a report share one semantic model - a baseline and a
fixed copy, a demo beside the original - every DAX answer is identical on all of
them, so a query can never tell you which instance you reached. Record how you
established both in `identity_evidence`. Use `dax --port <port>
--database <catalog> --file <query.dax> --out <case/evidence/file.json>` to execute actual engine DAX.
Choose the returned model deliberately; ambiguous instances require inspection.
If the project supplies engine_context, read it and verify the model/source hashes.
A semantic-engine harness executes real M/DAX but is not a rendered report; use
the explicit database from that context and retain its limitations in the case.
Examples: EVALUATE ROW("CPA", [CPA]); EVALUATE SUMMARIZECOLUMNS with account/date
keys and the report's actual measures. Filter-context experiments use CALCULATETABLE
and TREATAS. These are engine tests, not evidence a slicer actually applied.

Test the test's filter context. In ADDCOLUMNS, a naked SUM or COUNTROWS does not
automatically turn row context into filter context, while a measure reference
does. Prefer SUMMARIZECOLUMNS for grouped tests, or use explicit CALCULATE around
inline aggregations. Verify segment row counts against SQL before trusting a
generated DAX oracle. Record the actual database/catalog in every replay command;
the first/default model on a local engine is not a stable connection identity.

When a report browser surface is available, inspect it first, identify visuals
and controls, apply a state, verify selection, wait for stable values, and capture
both the complete report and important visuals. Preserve DOM/visible data with
screenshots. Do not use guessed selectors or take successful clicks as evidence.
For Desktop, a browser debugging connection is a local testing option when the
host exposes its WebView. It is not assumed available in every installation.
With multiple Desktop reports sharing an endpoint, pass the verified --page-id
from endpoint discovery. The helper selects that exact CDP target; a matching
URL alone cannot identify the report. Captures retain iframe text for custom
visuals, but nested rendering and its semantics still require inspection.

If only Desktop native UI is accessible, use the host's computer-use tools and
record screenshots/state. If the current CLI session has neither browser nor
computer-use capability, report visual coverage unavailable, complete the static
and DAX investigations, and do not fabricate screenshots or claim rendered tests.
