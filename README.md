# HASH Recruiting Shortlist Tool (Founder’s Associate Challenge)

A lightweight local web app, backed by a simple Python recruiting engine, that converts a role brief into a candidate pipeline with evidence, fit scoring, London/Berlin eligibility checks, outreach drafts, workflow status, and exportable output.

## Open the web app

Create the local virtual environment and install dependencies:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

Start the app:

```bash
./.venv/bin/python src/web_app.py
```

Then open:

```text
http://localhost:8000
```

If port `8000` is already in use, run:

```bash
./.venv/bin/python src/web_app.py --port 8001
```

Then open:

```text
http://localhost:8001
```

## What this tool does

- Searches public GitHub user results directly for engineering candidates in Berlin and London.
- Builds structured candidate cards with:
  - source/evidence link(s)
  - must-have and nice-to-have matches
  - location eligibility evidence
  - weighted fit score
  - rationale string
  - personalized outreach draft
- Applies a pipeline status automatically:
  - `shortlist` if score >= 0.65
  - `hold` if score >= 0.40
  - `reject` otherwise
- Rejects candidates without public evidence of being based in London or Berlin for this role brief.
- Supports human review updates for `shortlist/hold/reject`.
- Exports the full pipeline to CSV for analysis.
- Supports an offline/air-gapped path with local seed results JSON.
- Provides a local browser UI for running sourcing, reviewing candidates, updating workflow status, and downloading CSV exports.
- Supports both card and table views so 50+ candidates can be reviewed quickly.

## Demo mode

- `data/sample_search_results.json` contains a deterministic GitHub-style seed dataset with 59 candidate leads.
- Running the app in seed mode populates both the cards view and table view with a 50+ candidate pipeline for fast demoing.
- This makes it possible to review the full shortlist workflow without relying on live network search.

## Sourcing approach

- Primary source: public GitHub profiles and repository pages surfaced through role-specific search queries.
- Supporting public sources: personal sites, engineering blogs, or other evidence-rich pages that appear in search results.
- Search mechanism: direct GitHub user search pages plus public profile enrichment, used to keep the tool lightweight and runnable locally without third-party API keys.
- Location gate: this HASH role requires London or Berlin, so the tool explicitly checks for public evidence of either location before allowing a candidate to remain shortlisted.

## Selected role for this submission

This repo includes a role brief for **HASH Full-Stack Engineer** in:

- `role_briefs/hash_full_stack_engineer.json`

The brief is based on HASH's Gem job post for the in-person London/Berlin role.

You can edit this file or add additional role briefs.

## Setup

Create the local virtual environment and install the runtime dependency used for HTTPS certificate verification in live search mode:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

Then confirm Python is available:

```bash
python3 --version
```

## Run

### 1) Local web app (recommended)

The web app lets you:

- choose a role brief
- run GitHub-first live sourcing or deterministic seed data
- review candidate cards
- switch between card and table views for quick comparison
- change `shortlist` / `hold` / `reject`
- download CSV output

Demo mode already includes 59 candidates. For larger live runs, use `20` to `25` results per query in the UI. With the current query set, that is designed to surface 50+ candidates before de-duplication.

### 2) CLI engine

Live GitHub sourcing uses direct GitHub search and profile fetches, so on macOS you should install `requirements.txt` first to avoid SSL certificate issues.

#### Live web sourcing mode

```bash
./.venv/bin/python src/recruiting_tool.py run \
  --brief role_briefs/hash_full_stack_engineer.json \
  --output data/candidates.json \
  --max-results-per-query 20
```

#### Offline seed mode (recommended in restricted environments)

```bash
./.venv/bin/python src/recruiting_tool.py run \
  --brief role_briefs/hash_full_stack_engineer.json \
  --output data/candidates.json \
  --seed-results data/sample_search_results.json
```

Update a candidate workflow status:

```bash
./.venv/bin/python src/recruiting_tool.py review \
  --output data/candidates.json \
  --id <candidate_id> \
  --status shortlist
```

Export CSV:

```bash
./.venv/bin/python src/recruiting_tool.py export \
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
