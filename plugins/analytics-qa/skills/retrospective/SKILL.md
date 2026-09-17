---
name: retrospective
description: Review the recent QA sessions and the analyst's inputs, write an anonymised know-how article for the plugin community, get the user's explicit approval, publish it to the knowledge-base repository with topics, and index it for search.
---

# Retrospective: publish what this review taught

Input: $ARGUMENTS

## Where this fits

You are the last step of investigate → evaluate → detect → capture → (regress) → retrospective, and the one that helps everyone else.

What this review taught is not specific to one client: how a dashboard failed, how it was
spotted, how the model should have been built. Turn that into an anonymised article, get
the analyst to approve it, and publish it to the shared knowledge base so the next review,
on any machine, starts from it.


Run this after a capture (the sign-off page exists) or after a regression, or
whenever the user asks for a write-up. It turns one real review into an article
another analyst can use on a different report.

Prerequisite: `kb.json` in the project. If it is missing, ask the user for the
knowledge-base repository URL, the client/product terms to anonymise and the
attribution name, write `kb.json`, then continue:

```json
{"repository": "https://github.com/<org>/<repo>.git", "branch": "main", "local_path": "kb",
 "redaction": {"replace": {"<Client Name>": "the client"}, "allow": ["fact_lead", "dim_date"]},
 "author": "<team or handle, never an email>"}
```

`local_path` is relative to the project and holds the clone `sync` creates.
See [knowledge base](../../references/knowledge-base.md) for the full contract.

## Step 1 — digest the work

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/retrospective.py digest --project . --since-days 30 --out retrospective/<date>/digest.json
```

Read the digest, then read the cases' findings themselves
(`cases/*/case.json`) — the digest trims them. The digest is local evidence and
contains client material: never paste it into an article, a commit or a chat
summary you would publish.

Extract lessons, not narrative:

- For every issue found: **symptom → how it was detected** (which control,
  detector id, DAX/SQL query or lint rule) **→ root cause class → fix or design
  rule**. If a detector flagged it, name the catalog id (`stat.ratio_stability`,
  `lint.date_context_removed`, …).
- For every process lesson: what slowed the work down, and what accelerated it
  (a plan pattern, a probe template, a tool mistake worth avoiding).

## Step 2 — write the draft

Write `retrospective/<date>/draft.md` with these sections:

1. **Title** — the issue class, not the client ("Conversion rate that mixes a
   CRM population with platform conversions").
2. **Summary** — three sentences: what breaks, how it is spotted, what fixes it.
3. **Context** — the report's shape in generic terms only: number of pages,
   visual types, source kinds ("an ad platform and a CRM"). No client, product,
   brand, geography, person, or money figure.
4. **Issues found and how to spot them** — a table: symptom | detection method |
   root cause | fix.
5. **Data-model and DAX design rules** — the modelling that makes the issue
   impossible, stated as rules.
6. **Detection methods worth reusing** — which detectors and statistics, the
   thresholds used, the minimum data they need, and their pitfalls.
7. **Accelerating the QA** — what to automate, which plan patterns worked, the
   common tool mistakes.
8. **Open questions** — what stayed unresolved and what would settle it.

Rules while writing: generic table names are fine (`fact_lead`, `dim_date`);
client-identifying names are not. Replace absolute business figures with
relative or rounded illustrative numbers ("about a third of days", "~2k rows/day"),
never the real totals. Never quote a person, an email, a path containing a user
name, an internal URL, a ticket id or a screenshot. Cite plugin detectors by
their `detectors/catalog.json` id.

## Step 3 — redact

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/retrospective.py redact --in retrospective/<date>/draft.md --config kb.json --project . --out retrospective/<date>/article.md --report retrospective/<date>/article.report.json
```

Read the report. It lists what was replaced, what was scrubbed automatically
(emails, ids, user paths, URLs, ips, phone-like numbers) and every **residual**
item: capitalised names, currency amounts and 5+ digit figures. Fix each residual
item *in the draft* and rerun — do not accept it away. Stop when the status is
`clean`, or when every remaining item is one you can justify in a sentence to the
user (a generic product name, a version number).

## Step 4 — show the user and ask

Present, in chat:

- the absolute path of `article.md`;
- its **Summary** section verbatim;
- the topics you propose, chosen from
  `python ${CLAUDE_PLUGIN_ROOT}/scripts/retrospective.py topics`;
- the redaction report: counts plus every residual item and your justification.

Then ask the user explicitly to (a) review the article, (b) approve publishing it
to the configured repository, and (c) confirm the attribution name. **STOP and
wait.** Only an explicit "approve" / "publish" in chat is approval; "looks good",
"nice write-up" or silence is not. In a headless run there is no user: write the
article and the exact proposed publish command, end with "awaiting approval", and
never publish.

## Step 5 — publish (only after that approval)

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/retrospective.py sync --config kb.json --project .
python ${CLAUDE_PLUGIN_ROOT}/scripts/retrospective.py publish --config kb.json --project . --article retrospective/<date>/article.md --title "<title>" --topics <id1>,<id2> --tags <free,form> --summary "<one sentence>" --approved-by "<name the user confirmed>" --confirm
```

`publish` refuses without `--confirm` and `--approved-by`, refuses an unknown
topic, refuses a report whose status is `review` (unless `--accept-residual`) and
refuses an article that still contains a configured replacement term. It writes
`articles/<date>-<slug>.md` with front matter, rebuilds `index.json` and
`README.md`, commits and pushes. Report the commit and the article URL
(repository URL + `/blob/<branch>/articles/<file>`).

## Step 6 — tell the user how it is used

The other skills search this base before designing tests:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/retrospective.py search --config kb.json --project . --query "conversion rate Meta reporting gaps" --limit 5
```

`investigate` searches it before designing a case, `detect` searches it for the
component's shape before picking detectors. Say so, and mention that an article
can be cited in a case's limitations or findings.
