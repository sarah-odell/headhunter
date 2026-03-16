# HASH Recruiting Shortlist Tool

Lightweight local recruiting tool for the HASH Full-Stack Engineer role. It sources candidates from public GitHub-first signals, ranks them against the role requirements, supports `shortlist` / `hold` / `reject`, generates outreach drafts, and exports the pipeline to CSV.

Web app: [http://localhost:8000](http://localhost:8000)

## Run locally

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python src/web_app.py
```

If port `8000` is busy:

```bash
./.venv/bin/python src/web_app.py --port 8001
```

## What the tool does

- searches public sources appropriate to the role, primarily GitHub user search plus public GitHub profiles
- creates structured candidate cards with evidence links and matched public excerpts
- scores fit against must-haves and nice-to-haves from [role_briefs/hash_full_stack_engineer.json](/Users/sarahodell/Projects/headhunter/role_briefs/hash_full_stack_engineer.json)
- generates ranked candidates and outreach drafts
- supports `shortlist` / `hold` / `reject`
- exports recruiter-friendly CSV output

## Sourcing approach

The tool is GitHub-first because this role is strongly technical and GitHub gives the most direct public evidence for:

- `TypeScript`
- `React`
- frontend/backend scope
- open-source work

Public evidence can come from:

- GitHub user search results
- GitHub profile text
- pinned repositories
- public repository listings
- repository page summaries
- linked personal websites
- supporting public web results when they reinforce the candidate identity

`GitHub profiles per search` controls search depth per query, not final candidate count. Starting with `5` usually yields about `25` raw results before deduplication.

## Scoring and uncertainty

Scoring is deterministic and evidence-weighted.

- matching is token-aware rather than loose substring matching
- ambiguous aliases like `ts`, `ui`, and `api` are intentionally excluded
- stronger evidence sources score higher than weaker ones
- repo-backed evidence is preferred over profile text
- missing evidence is treated as `unknown`, not as proof a candidate lacks a skill

Each important requirement is labeled as:

- `confirmed`
- `partial`
- `unknown`

The UI also surfaces an evidence-base state:

- `Strong evidence base`
- `Partial evidence base`
- `Insufficient public evidence`

For this role, London or Berlin is a hard eligibility gate. Candidates without public location evidence for either city are rejected automatically.

## UI output

Each candidate card includes:

- rank
- fit score
- evidence-base state
- location state
- `why this candidate` summary
- per-requirement evidence with source provenance
- outreach draft

The table view is optimized for quick comparison and shows:

- rank
- status
- evidence-base state
- fit score
- `TypeScript`, `React`, `frontend`, and `backend` judgments
- location state
- one-line candidate summary

## Demo mode vs live mode

- Live mode: real public GitHub sourcing and enrichment
- Demo mode: deterministic local seed data from [data/sample_search_results.json](/Users/sarahodell/Projects/headhunter/data/sample_search_results.json)

Demo mode exists for UI walkthroughs and supports a 50+ candidate workflow offline.

## CSV export

CSV export includes:

- rank
- candidate name
- contact provenance
- evidence-base state
- location state
- `why this candidate`
- review tags
- workflow status

## CLI

Run live sourcing:

```bash
./.venv/bin/python src/recruiting_tool.py run \
  --brief role_briefs/hash_full_stack_engineer.json \
  --output data/candidates.json \
  --max-results-per-query 20
```

Run demo data:

```bash
./.venv/bin/python src/recruiting_tool.py run \
  --brief role_briefs/hash_full_stack_engineer.json \
  --output data/candidates.json \
  --seed-results data/sample_search_results.json
```

Export CSV:

```bash
./.venv/bin/python src/recruiting_tool.py export \
  --output data/candidates.json \
  --csv data/candidates.csv
```

## Limitations

- this is still a heuristic screening tool, not a production recruiting system
- sparse public profiles can produce only partial evidence
- GitHub may rate-limit repeated live runs
- public email is only shown when it is explicitly exposed on GitHub or a linked public site

## Project files

- [src/recruiting_tool.py](/Users/sarahodell/Projects/headhunter/src/recruiting_tool.py): sourcing, enrichment, scoring, export
- [src/web_app.py](/Users/sarahodell/Projects/headhunter/src/web_app.py): local web app
- [role_briefs/hash_full_stack_engineer.json](/Users/sarahodell/Projects/headhunter/role_briefs/hash_full_stack_engineer.json): role requirements and weights
- [tests/fixtures/validation_profiles.json](/Users/sarahodell/Projects/headhunter/tests/fixtures/validation_profiles.json): manual validation fixture
