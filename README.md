# HASH Recruiting Shortlist Tool

Local GitHub-first recruiting tool for the HASH Full-Stack Engineer role. It searches public GitHub profiles, scores candidates against the role requirements, applies a London/Berlin eligibility gate, generates outreach drafts, supports `shortlist` / `hold` / `reject`, and exports the pipeline to CSV.

Web app: [http://localhost:8000](http://localhost:8000)

## Open the app

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python src/web_app.py
```

Open [http://localhost:8000](http://localhost:8000).

If port `8000` is busy:

```bash
./.venv/bin/python src/web_app.py --port 8001
```

Then open [http://localhost:8001](http://localhost:8001).

## What it does

- Sources engineering candidates from public GitHub user search and public GitHub profile pages.
- Enriches candidates with profile evidence, pinned repos, repository listings, linked websites, and public email when available.
- Builds structured candidate cards with evidence links, match signals, ranked fit scores, outreach drafts, and workflow status.
- Shows candidates in both card and table views for quick comparison.
- Exports the current pipeline to CSV.

## Live mode vs demo mode

- Live mode: real public GitHub sourcing and profile enrichment.
- Demo mode: deterministic local seed data from [data/sample_search_results.json](/Users/sarahodell/Projects/headhunter/data/sample_search_results.json) for UI walkthroughs.

Demo mode contains 50+ seeded candidates so the full workflow can be reviewed offline. Live mode is the real product path.

## Sourcing methodology

The tool runs multiple GitHub searches for the role to improve recall across:

- Berlin and London
- TypeScript / React-heavy profiles
- broader full-stack / backend profiles

For each candidate, it gathers public evidence from:

- GitHub user search results
- GitHub profile text
- pinned repositories
- public repositories tab listings
- linked personal website, if present
- public email exposed on GitHub or a linked site, if present

`GitHub profiles per search` controls depth per search, not final candidate count. Starting with `5` usually yields about `25` raw results before deduplication.

## Scoring methodology

Scoring is heuristic and evidence-weighted.

- Each requirement in [role_briefs/hash_full_stack_engineer.json](/Users/sarahodell/Projects/headhunter/role_briefs/hash_full_stack_engineer.json) is checked against the candidate’s public evidence.
- Stronger evidence sources carry more weight:
  - `repo` is strongest
  - `profile` is medium
  - `linked site` is medium
  - `search` is weakest
- The tool computes:
  - `must_have_score`
  - `nice_to_have_score`
  - `fit_score`
  - `confidence_score`
- Candidates are ranked by fit score.

For this role, London or Berlin is a hard eligibility gate. Candidates without public location evidence for either city are automatically rejected.

Automatic workflow thresholds:

- `shortlist` if `fit_score >= 0.55` and location eligible
- `hold` if `fit_score >= 0.25` and location eligible
- `reject` otherwise

The UI displays these scores as percentages and also allows manual status changes.

## Current role brief

This repo is configured for the HASH Full-Stack Engineer role in:

- [role_briefs/hash_full_stack_engineer.json](/Users/sarahodell/Projects/headhunter/role_briefs/hash_full_stack_engineer.json)

## CLI

Run sourcing:

```bash
./.venv/bin/python src/recruiting_tool.py run \
  --brief role_briefs/hash_full_stack_engineer.json \
  --output data/candidates.json \
  --max-results-per-query 20
```

Run deterministic demo data:

```bash
./.venv/bin/python src/recruiting_tool.py run \
  --brief role_briefs/hash_full_stack_engineer.json \
  --output data/candidates.json \
  --seed-results data/sample_search_results.json
```

Update workflow status:

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

- [src/recruiting_tool.py](/Users/sarahodell/Projects/headhunter/src/recruiting_tool.py): sourcing, scoring, workflow, export
- [src/web_app.py](/Users/sarahodell/Projects/headhunter/src/web_app.py): local web app
- [role_briefs/hash_full_stack_engineer.json](/Users/sarahodell/Projects/headhunter/role_briefs/hash_full_stack_engineer.json): role requirements and weights
- [data/sample_search_results.json](/Users/sarahodell/Projects/headhunter/data/sample_search_results.json): demo seed data

## Notes

- This is a local-only tool intended to run on one machine.
- It uses only public data.
- GitHub may temporarily rate-limit repeated live runs; if so, wait a few minutes and rerun with a smaller per-search count.
