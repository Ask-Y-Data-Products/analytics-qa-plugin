# Analytics QA plugin for Claude Code

Investigate, test, capture and regress analytics components — Power BI reports
backed by dbt / BigQuery / Azure SQL — with evidence an analyst can sign.

The agent reconstructs how a component is produced (source → semantic model →
visual), designs baseline / change / reset experiments, runs them against the
live Power BI Desktop report one observed step at a time, and assembles a sealed
evidence case plus a **sign-off page**: per component, the captured screenshots in
story order with the observed numbers, the open defects and questions, and
Sign off / Reject buttons. Later runs compare a changed report against that
evidence and explain what changed and why.

## Install

Inside Claude Code:

```text
/plugin marketplace add Ask-Y-Data-Products/analytics-qa-plugin
/plugin install analytics-qa@ask-y-analytics-qa
```

Or from a checkout:

```powershell
git clone https://github.com/Ask-Y-Data-Products/analytics-qa-plugin.git
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r plugins\analytics-qa\requirements.txt
claude --plugin-dir <checkout>\plugins\analytics-qa
```

Windows with Power BI Desktop is required for the Power BI parts (ADOMD model
queries and the report WebView). Start Desktop with
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333` and open a
working copy of the report; never refresh or save the copy under test.

Full install and first-run walkthrough, including the project folder layout and
what a passing result looks like: the project repository's
`docs/INSTALL_AND_TEST.md` (Ask-Y-Data-Products/analytics-qa).

## Skills

| Skill | Purpose |
| --- | --- |
| `/analytics-qa:investigate` | Bind the component to its visuals, measures, relationships, partitions and physical sources; open a case |
| `/analytics-qa:evaluate` | Design discriminating experiments; run them with `scripts/pbi_cycle.py` plans and DAX oracles; classify interactions from rendered marks |
| `/analytics-qa:capture` | Attach components (`qa.py component`), validate, seal, render, and generate the sign-off page (`review_form.py`) |
| `/analytics-qa:regress` | Replay a sealed baseline against a changed report or period; classify preserved / regressed / expected changes with negative controls |

## Scripts

| Script | Role |
| --- | --- |
| `scripts/pbi.py` | Engine status, browser targets, catalogs, model metadata, read-only DAX, query replay, page capture |
| `scripts/pbi_cycle.py` | Plan-driven UI cycle: capture / toggle_member / select_option / set_date / click_mark / go_to_page, receipts per state |
| `scripts/powerbi_controls.py` | Observed WebView control mechanics (dropdowns, tile slicers, date inputs, page tabs, overlays) |
| `scripts/qa.py` | Case lifecycle: init, inspect, component, validate, seal, verify, render, review, retain |
| `scripts/state_checks.py` | Hash-bound typed receipts for captured observations |
| `scripts/review_form.py` | The analyst sign-off page |
| `scripts/pbix_snapshot.py`, `powerbi_inventory.py` | Legacy PBIX layout extraction and visual/field binding map |
| `scripts/bigquery_evidence.py`, `connections.py` | Cost-bounded BigQuery and read-only Azure SQL evidence queries |
| `scripts/compare.py` | Typed keyed comparison of evidence exports |

Reference playbooks live in `plugins/analytics-qa/references/`.

## What it does not do

It does not decide business truth: a sealed manifest proves integrity, a passing
receipt proves the recorded fields, and only the analyst's exported decisions are
a sign-off (a local attestation, not an authenticated signature). It does not
refresh, save or edit reports or data. Prism/GitHub publishing of cases is not
built in.

MIT licensed. Version: see `plugins/analytics-qa/.claude-plugin/plugin.json`.
