# Knowledge base

Anonymised know-how from finished reviews, shared between users of this plugin
through one git repository. The `retrospective` skill writes into it; the other
skills read from it. This file is the contract.

## kb.json (in the user's project)

```json
{"repository": "https://github.com/Ask-Y-Data-Products/analytics-qa-knowledge.git",
 "branch": "main",
 "local_path": "kb",
 "redaction": {"replace": {"LaunchLife": "the client", "Acme Colleges": "the client"},
               "allow": ["fact_lead", "dim_date"]},
 "author": "Ask-Y analytics QA team"}
```

- `repository` — the git URL the articles are pushed to. Any git remote works,
  including a path, which is how the tests run.
- `branch` — the branch `sync` checks out and `publish` pushes to (default `main`).
- `local_path` — the clone's location, relative to the project unless absolute.
  `sync` creates it; add it to the project's `.gitignore`.
- `redaction.replace` — exact terms mapped to placeholders. Matching is
  case-insensitive and longest term first, whole-word when the term's edges are
  word characters, so `LaunchLife` and `launchlife` both become `the client`.
- `redaction.allow` — tokens the residual scan must not flag: generic table and
  column names, product words that are not client identities.
- `author` — attribution written into every article's front matter and used as
  the git commit author name. Never an email; commits use the noreply address
  `analytics-qa@users.noreply.github.com`.

## Privacy rules

- The **digest is local-only**. It quotes the analyst's prompts, the commands the
  agent ran, tool errors and case records, and it can contain client material. It
  stays under `<project>/retrospective/<date>/` and is never committed, pasted
  into an article or sent anywhere.
- Only the **approved, redacted article** leaves the machine. Approval is the
  user saying so in chat, recorded as `--approved-by`; the tool refuses to
  publish without `--confirm` and that name.
- `redact` scrubs emails → `[email]`, GUIDs → `[id]`, IPv4 → `[ip]`, paths under
  a user directory (`C:\Users\<name>\…`, `/home/<name>/`, `/Users/<name>/`) →
  `[path]`, phone-like numbers → `[phone]`, and every URL except the knowledge
  base's own `github.com/<org>/<repo>` and `docs.microsoft.com` /
  `learn.microsoft.com` → `[url]`. Code fences are kept, and their contents are
  scrubbed too. `port 9333` and similar are left alone.
- The residual scan then flags what a scrubber cannot decide: two or more
  consecutive capitalised words that are not ordinary BI vocabulary, currency
  amounts, numbers of five or more digits, and any configured replacement term
  that somehow survived (which must be zero). Status is `clean` or `review`.
  **A `review` status is a checklist for a human, not a blocker to route around:**
  fix the draft rather than passing `--accept-residual`.
- No screenshots. An article carries text only.

## Article contract

`articles/<YYYY-MM-DD>-<slug>.md`, YAML front matter then the body:

```yaml
---
title: "Conversion rate that mixes a CRM population with platform conversions"
date: "2026-09-17"
topics: ["conversion-rate", "crm-vs-platform"]
tags: ["daily-series"]
summary: "One sentence for the index and the README table."
author: "Ask-Y analytics QA team"
approved_by: "the analyst who approved publication in chat"
plugin_version: "0.5.0"
source: "retrospective"
anonymized: true
---
```

Body sections (see the skill): Summary, Context, Issues found and how to spot
them (symptom / detection method / root cause / fix), Data-model and DAX design
rules, Detection methods worth reusing, Accelerating the QA, Open questions.

## Repository layout

```
articles/<date>-<slug>.md   the articles; the source of truth
index.json                  {generated_at, articles:[{path,title,date,topics,tags,summary,keywords,words}]}
README.md                   intro (written once), generated article table, topics with counts
topics.json                 copy of the plugin's controlled vocabulary
```

`index.json` and `README.md` are rebuilt from the articles on every publish, so a
hand-edit to either is lost; edit the article. `keywords` are the top 20
distinctive body terms (term frequency with a length boost), used to make search
hits explainable.

## Topics

`plugin/retrospective/topics.json` is the controlled vocabulary (~25 ids with a
label and a description). `publish` refuses an unknown topic and prints the list.
`--tags` is free-form and unvalidated. Add a topic only when several articles
would use it; a one-off belongs in tags.

## Search scoring

`search` loads `index.json` plus each article body from the local clone (running
`sync` first when the clone is missing). Both query and documents are tokenised
by lowercasing, splitting on non-alphanumerics, dropping stopwords and short
tokens, and crudely stemming trailing `s`/`es`/`ing`/`ed`. Scoring is TF-IDF over
weighted fields — title ×3, topics and tags ×3, summary ×2, body ×1 — with
sublinear term frequency and length normalisation per field. `--topic` is an
exact filter applied before scoring. Each hit returns the ~200-character body
window carrying the most query terms, so the agent can judge relevance without
opening the article.

Queries read like a report shape plus a failure mode: `"conversion rate Meta
reporting gaps"`, `"daily leads spike weekday"`, `"campaign window alignment"`.
