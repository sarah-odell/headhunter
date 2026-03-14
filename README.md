# HASH Recruiting Shortlist Tool (Founder’s Associate Challenge)

A lightweight local web app, backed by a simple Python recruiting engine, that converts a role brief into a candidate pipeline with evidence, fit scoring, outreach drafts, workflow status, and exportable output.

## What this tool does

- Searches public web results (DuckDuckGo HTML endpoint) for role-specific sourcing queries.
- Builds structured candidate cards with:
  - source/evidence link(s)
  - must-have and nice-to-have matches
  - weighted fit score
  - rationale string
  - personalized outreach draft
- Applies a pipeline status automatically:
  - `shortlist` if score >= 0.65
  - `hold` if score >= 0.40
  - `reject` otherwise
- Supports human review updates for `shortlist/hold/reject`.
- Exports the full pipeline to CSV for analysis.
- Supports an offline/air-gapped path with local seed results JSON.
- Provides a local browser UI for running sourcing, reviewing candidates, updating workflow status, and downloading CSV exports.

## Selected role for this submission

This repo includes a role brief for **HASH Full-Stack Engineer** in:

- `role_briefs/hash_full_stack_engineer.json`

The brief is based on HASH's Gem job post for the in-person London/Berlin role.

You can edit this file or add additional role briefs.

## Setup

No third-party packages required:

```bash
python3 --version
```

## Run

### 1) Local web app (recommended)

```bash
python3 src/web_app.py
```

Then open:

```text
http://127.0.0.1:8000
```

The web app lets you:

- choose a role brief
- run sourcing with live web search or deterministic seed data
- review candidate cards
- change `shortlist` / `hold` / `reject`
- download CSV output

### 2) CLI engine

#### Live web sourcing mode

```bash
python3 src/recruiting_tool.py run \
  --brief role_briefs/hash_full_stack_engineer.json \
  --output data/candidates.json \
  --max-results-per-query 10
```

#### Offline seed mode (recommended in restricted environments)

```bash
python3 src/recruiting_tool.py run \
  --brief role_briefs/hash_full_stack_engineer.json \
  --output data/candidates.json \
  --seed-results data/sample_search_results.json
```

Update a candidate workflow status:

```bash
python3 src/recruiting_tool.py review \
  --output data/candidates.json \
  --id <candidate_id> \
  --status shortlist
```

Export CSV:

```bash
python3 src/recruiting_tool.py export \
  --output data/candidates.json \
  --csv data/candidates.csv
```

## Project structure

- `src/recruiting_tool.py` contains the core sourcing, scoring, workflow, and export logic.
- `src/web_app.py` provides the lightweight local web interface.
- `role_briefs/hash_full_stack_engineer.json` contains the HASH Full-Stack Engineer brief.
- `data/sample_search_results.json` contains deterministic seed results for demo and testing.

## Output schema

Each candidate card includes:

- `id`
- `name`
- `headline`
- `source_url`
- `evidence_links`
- `must_have_hits`
- `nice_to_have_hits`
- `fit_score`
- `rationale`
- `status`
- `outreach_draft`

## Notes

- This tool uses public search results and public links.
- The scoring system is intentionally simple and auditable (keyword-evidence weighted scoring).
- If DuckDuckGo response format changes, regex parsing in `ddg_search()` may need adjustment.
- The web app is intentionally local-only and assumes single-user usage on one machine.
