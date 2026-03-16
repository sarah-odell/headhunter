# HASH Recruiting Shortlist Tool

Local recruiting tool for the HASH Full-Stack Engineer role. It sources candidates from public GitHub and supporting public web results, scores candidates against the role requirements, applies a London/Berlin eligibility gate, generates evidence-backed outreach drafts, supports `shortlist` / `hold` / `reject`, and exports the pipeline to CSV.

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

- Sources engineering candidates from public GitHub user search, DuckDuckGo public web search, GitHub profile pages, and linked public websites.
- Enriches candidates with profile evidence, pinned repos, repository listings, repo page summaries, linked websites, and public email when available.
- Caches public fetches locally under `data/.cache` to improve repeat-run speed and reduce rate-limit pressure.
- Builds structured candidate cards with evidence links, evidence snippets, source attribution, ranked fit scores, outreach drafts, and workflow status.
- Adds recruiter-facing summaries like `why this candidate`, per-requirement judgments, review tags, and contact provenance.
- Shows candidates in both card and table views for quick comparison.
- Exports the current pipeline to CSV.

## Live mode vs demo mode

- Live mode: real public GitHub sourcing, public web search support, and profile enrichment.
- Demo mode: deterministic local seed data from [data/sample_search_results.json](/Users/sarahodell/Projects/headhunter/data/sample_search_results.json) for UI walkthroughs.

Demo mode contains 50+ seeded candidates so the full workflow can be reviewed offline. Live mode is the real product path.

## Sourcing methodology

The tool runs multiple public searches for the role to improve recall across:

- Berlin and London
- TypeScript / React-heavy profiles
- broader full-stack / backend profiles

For each candidate, it gathers public evidence from:

- GitHub user search results
- public web search results that surface GitHub profiles
- GitHub profile text
- pinned repositories
- public repositories tab listings
- repository page summaries
- linked personal website, if present
- public email exposed on GitHub or a linked site, if present

`GitHub profiles per search` controls depth per search, not final candidate count. Starting with `5` usually yields about `25` raw results before deduplication.

## Scoring methodology

Scoring is heuristic and evidence-weighted.

- Each requirement in [role_briefs/hash_full_stack_engineer.json](/Users/sarahodell/Projects/headhunter/role_briefs/hash_full_stack_engineer.json) is checked against the candidate’s public evidence.
- Matching is token-aware rather than raw substring-based. Ambiguous aliases like `ts`, `ui`, and `api` are intentionally excluded to reduce false positives.
- Stronger evidence sources carry more weight:
  - `repo` is strongest
  - `profile` is medium
  - `linked site` is medium
  - `public web` is weaker
  - `search` is weakest
- Requirement evidence shown in the UI is an actual matched excerpt, not just a generic repo snippet. Source labels also include provenance such as `repo · Pinned repository summary` or `profile · GitHub profile bio`.
- Repo-level evidence is not limited to repo titles. The tool also pulls repo page summaries and technical markers that strengthen signals around `TypeScript`, `React`, backend work, performance, and open-source activity.
- The tool computes:
  - `must_have_score`
  - `nice_to_have_score`
  - `fit_score`
  - `confidence_score`
- The tool also produces reviewer-facing uncertainty states:
  - `ready`
  - `needs_review`
  - `insufficient_evidence`
- Each requirement is also labeled as `confirmed`, `partial`, or `unknown` from explicit public evidence.
- Candidates are ranked by fit score, then must-have score, nice-to-have score, and confidence.

For this role, London or Berlin is a hard eligibility gate. Candidates without public location evidence for either city are automatically rejected.

Automatic workflow thresholds:

- `shortlist` if `fit_score >= 0.55` and location eligible
- `hold` if `fit_score >= 0.25` and location eligible
- `reject` otherwise

The UI displays these scores as percentages, shows evidence snippets and source labels, and allows manual status changes. Outreach drafts now cite one especially concrete public artifact rather than relying only on generic skill mentions.

The table view is intended for fast recruiter triage. It now surfaces:

- rank
- evidence-base state
- fit score
- requirement judgments for `TypeScript`, `React`, `frontend`, and `backend`
- location state
- a one-line `why this candidate` summary

## Validation and limitations

- A small manual validation fixture lives at [tests/fixtures/validation_profiles.json](/Users/sarahodell/Projects/headhunter/tests/fixtures/validation_profiles.json) to document expected classes of outcomes.
- Missing evidence is treated as `unknown`, not as proof that a candidate lacks a skill.
- This remains a heuristic screening tool. Sparse public profiles, GitHub rate limits, and missing repo metadata can still produce `needs_review` candidates that require human judgment.

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

- [src/recruiting_tool.py](/Users/sarahodell/Projects/headhunter/src/recruiting_tool.py): sourcing, enrichment, scoring, workflow, export
- [src/web_app.py](/Users/sarahodell/Projects/headhunter/src/web_app.py): local web app
- [role_briefs/hash_full_stack_engineer.json](/Users/sarahodell/Projects/headhunter/role_briefs/hash_full_stack_engineer.json): role requirements and weights
- [data/sample_search_results.json](/Users/sarahodell/Projects/headhunter/data/sample_search_results.json): demo seed data

## Notes

- This is a local-only tool intended to run on one machine.
- It uses only public data.
- Candidate scoring is evidence-weighted and heuristic rather than model-generated.
- Missing evidence is treated as `unknown`, not as proof a candidate lacks a skill.
- GitHub may temporarily rate-limit repeated live runs; if so, wait a few minutes and rerun with a smaller per-search count.
