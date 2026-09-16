# Reconstruct a Power BI component

Use this playbook when the target is a Power BI visual, page, measure or model.
The deliverable is a supported explanation of what the component calculates and
why, plus experiments against its weakest assumptions. Do not mechanically test
every model object: follow the target's dependency closure and affected siblings.

## 1. Establish identity and access

Identify the report, page and visual by stable object ID, not title alone. A
screenshot is a locator: match its title, position, fields and values to a live
visual; record ambiguity instead of choosing the nearest-looking chart.

Read PBIP -> definition.pbir -> byPath or byConnection. A thin report can point
to a remote model with no local model files. A PBIX can be investigated through
its open Desktop engine. Use `pbix_snapshot.py --pbix REPORT.pbix --out SNAPSHOT`
for a legacy Report/Layout container; unsupported containers need a PBIP/PBIR
export. This reads only report bindings, not the binary semantic model or secrets.
Never pretend PBIX is a JSON file.

Run `pbi.py status`, then `pbi.py catalogs --port PORT` for candidate engines.
Choose an explicit catalog for **every** model or DAX command. Ports change on
reopen. `pbi.py model --port PORT --database CATALOG --out CASE/evidence/model`
captures tables, columns, measures, relationships, partitions, expressions,
hierarchies, calculation groups/items, roles, permissions and engine dependencies.
An unsupported rowset is an access gap, not evidence that the feature is absent.

Associate the catalog with the report using the Desktop external-tools connection
identity when available, or the opened report path plus runtime model structure,
partition source, and discriminating visible values. Record the evidence and
confidence in `scope.powerbi_identity`. Model names and matching grand totals alone
are insufficient. A separate test harness must remain labeled as a harness.
Do not refresh, change roles, export sensitive data, or modify the user's report
merely to improve coverage without authority for that action.

## 2. Recover the actual visual query and state

Inspect `evidence/inventory.json.visual_bindings` and the cited source files.
Both PBIR and legacy bindings include fields, titles and filter configuration.
Legacy queryRef names can survive measure renames: resolve prototypeQuery Select
expressions and live queries before declaring a reference missing. Legacy page
interaction overrides may live in config.relationships, not visualInteractions.
The raw numeric enum is evidence; prove its effect through actual selections.
For the target retain these together:

- Projection roles: axis/category, value, legend, tooltip, small multiples;
  column aggregation, implicit measure, explicit measure or visual calculation.
- Report/page/visual filters, slicer selections, synchronized hidden slicers,
  relative date reference time, include/exclude, Top N and "show items with no data".
- Sort, drill level, field parameter selection, calculation item, bookmark,
  interaction overrides and cross-highlight versus cross-filter behavior.
- Units, format overrides, conditional formatting, dynamic title, axis range,
  hidden state and tooltip meaning. A correct value with the wrong unit is wrong.

For complex visuals, use Performance Analyzer's copied DAX query or a supported
query-capture tool. Retain the original visual query and replay it against the
identified catalog. Its filters, TOPN, subtotal flags, grouping and data reduction
matter. Then simplify one feature at a time to localize the discrepancy. A query
reconstructed by the agent is a hypothesis until checked against the actual
visual output. Never invent an unavailable Performance Analyzer export.
`pbi.py queries --port PORT --database CATALOG --out CASE/evidence/queries.json`
can retain recent DAX commands from live engine sessions. Capture before/after a
controlled action and correlate changed filters, grouping and visible results.
This is best-effort last-command discovery, not a full trace or automatic visual
attribution. Query caching may leave old commands. Replaying a visual query with
multiple EVALUATE statements produces multiple `result_sets`; inspect all of them,
not just the first count/metadata result exposed through the compatibility `rows`.
Replay without retyping: `pbi.py replay --capture queries.json --query-index N
--port PORT --database CATALOG --out CASE/evidence/replayed.json` uses the exact
captured query and records its provenance. Indexes are zero-based. If a generated
query fails, compare its text to the capture before blaming an unsupported
function or engine. A misspelled function is a test defect, not a platform limit.

## 3. Trace the semantic calculation

Follow each measure reference recursively, using engine dependencies to locate
objects and exact DAX to understand semantics. Capture calculated columns/tables,
calculation items and dynamic format strings where they affect the target.
Do not infer dependency closure with a regex alone: names in strings/comments,
unqualified measure names and dynamic branches make it unreliable.

For each formula explain:

- Its base population and numerator/denominator, including eligibility,
  attribution, deduplication and currency/timezone decisions.
- Which filters it keeps, removes, replaces or adds. Examine CALCULATE,
  ALL/ALLSELECTED/REMOVEFILTERS, KEEPFILTERS, TREATAS and CROSSFILTER in context.
- Its evaluation grain: iterator row context and context transition, ratio of
  totals versus average of ratios, distinct-count overlap, semi-additive dates.
- Branches using ISINSCOPE/HASONEVALUE/SELECTEDVALUE, BLANK/zero behavior,
  alternate denominators and total/subtotal behavior.
- USERELATIONSHIP and date role, calendar completeness, fiscal periods,
  time-intelligence windows and calculation-group precedence/SELECTEDMEASURE.

A suspicious function is not a defect. Percent-of-total deliberately removes a
filter; account CPA usually must retain it. Turn that distinction into a claim
grounded in the business definition, not a blanket lint rule.

## 4. Trace filter propagation and storage to sources

Build the relevant relationship paths, recording active/inactive state,
cardinality, cross-filter/security direction, bridge tables and disconnected
selectors. Prove the expected path with a small slice. Check one-side key
uniqueness and unmatched fact keys, including the automatic blank member.
Many-to-many and bidirectional paths require a discriminating overlapping-key
example, not just a diagram review. Include automatically generated date tables.

Read actual partition modes and M/shared expressions. Follow merges, appends,
renames, type conversions, grouping, parameters, native queries and dataflows
back to concrete source objects. Distinguish Import, DirectQuery, Dual and
calculated tables; a report connection is not a storage-mode declaration.
For incremental refresh record partition ranges and refresh times; MAX(date)
does not prove old partitions refreshed. Inspect query folding only when needed
to explain population, timing or refresh behavior, not as a correctness oracle.

At SQL/dbt boundaries match physical server/database/schema/relation, compiled
SQL and deployed definition. Trace source keys and joins; compare pre/post-join
row counts and additive measures at the proper grain. Record broken links such
as inaccessible dataflows explicitly. Never infer lineage just because names match.

RLS/OLS claims need tests under the relevant identity/role, including an excluded
population. An unrestricted Desktop/admin query cannot establish user visibility.
If impersonation or service access is unavailable, keep security claims unverified.

## 5. Produce a bounded investigation artifact

In `scope` record identity, period/timezone, input/refresh state, selected context,
grain, definition provenance and `powerbi_risks`: an array of feature, relevance,
evidence, planned experiment or reason it is out of scope. Use stable claim IDs.
Add source-to-screen trace edges with evidence and confidence. Read
[Power BI experiments](powerbi-testing.md) to challenge the recovered behavior.
"No roles found" is supported only by a successful roles inventory; it is not
interchangeable with "roles not inspected".

References: [PBIP report format](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report),
[engine dependencies](https://learn.microsoft.com/en-us/openspecs/sql_server_protocols/ms-ssas/a59d3ced-51f1-4f26-a373-1faefb2fc278).
