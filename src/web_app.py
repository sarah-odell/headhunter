#!/usr/bin/env python3
"""Local web UI for the recruiting shortlist tool."""

from __future__ import annotations

import argparse
import json
from html import escape, unescape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from recruiting_tool import (
    build_candidates,
    cards_to_csv_text,
    export_csv,
    load_cards,
    load_role_brief,
    save_cards,
    update_card_status,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BRIEF = ROOT / "role_briefs" / "hash_full_stack_engineer.json"
DEFAULT_OUTPUT = ROOT / "data" / "candidates.json"
DEFAULT_CSV = ROOT / "data" / "candidates.csv"
DEFAULT_SEED = ROOT / "data" / "sample_search_results.json"
DEFAULT_MAX_RESULTS = 20
STATUS_OPTIONS = ("shortlist", "hold", "reject")
EVIDENCE_FILTERS = {"all", "full", "partial", "degraded"}
LOCATION_FILTERS = {"all", "confirmed", "inferred", "unknown"}
REQUIREMENT_FILTERS = {"all", "typescript", "react", "frontend", "backend"}


def available_briefs() -> list[Path]:
    return sorted((ROOT / "role_briefs").glob("*.json"))


def status_counts(cards: list) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_OPTIONS}
    for card in cards:
        counts[card.status] = counts.get(card.status, 0) + 1
    return counts


def normalize_status_filter(raw_value: str) -> str:
    return raw_value if raw_value in {"all", *STATUS_OPTIONS} else "all"


def normalize_evidence_filter(raw_value: str) -> str:
    return raw_value if raw_value in EVIDENCE_FILTERS else "all"


def normalize_location_filter(raw_value: str) -> str:
    return raw_value if raw_value in LOCATION_FILTERS else "all"


def normalize_requirement_filter(raw_value: str) -> str:
    return raw_value if raw_value in REQUIREMENT_FILTERS else "all"


def percent_label(value: float) -> str:
    return f"{round(value * 100):d}%"


def display_text(value: str) -> str:
    return escape(unescape(value))


def evidence_level(value: float) -> str:
    if value >= 0.85:
        return "High confidence"
    if value >= 0.55:
        return "Moderate confidence"
    if value > 0:
        return "Low confidence"
    return "No evidence"


def review_state_label(value: str) -> str:
    labels = {
        "ready": "Strong evidence base",
        "needs_review": "Partial evidence base",
        "insufficient_evidence": "Insufficient public evidence",
    }
    return labels.get(value or "needs_review", "Partial evidence base")


def requirement_judgment(value: float) -> str:
    if value >= 0.85:
        return "Confirmed"
    if value > 0:
        return "Partial"
    return "Unknown"


def requirement_checkmark(value: float) -> str:
    return "✓" if value >= 0.85 else ""


def enrichment_state_label(value: str) -> str:
    labels = {
        "full": "Full enrichment",
        "partial": "Partial enrichment",
        "degraded": "Degraded enrichment",
    }
    return labels.get(value or "full", "Full enrichment")


def render_page(
    brief_path: Path,
    output_path: Path,
    csv_path: Path,
    seed_path: Path | None,
    max_results: int,
    cards: list,
    view_mode: str = "cards",
    status_filter: str = "all",
    evidence_filter: str = "all",
    location_filter: str = "all",
    requirement_filter: str = "all",
    queue_index: int = 0,
    message: str = "",
    error: str = "",
) -> str:
    brief = load_role_brief(brief_path)
    counts = status_counts(cards)
    filtered_cards = [
        card for card in cards
        if (status_filter == "all" or card.status == status_filter)
        and (evidence_filter == "all" or getattr(card, "enrichment_state", "full") == evidence_filter)
        and (location_filter == "all" or getattr(card, "location_state", "unknown") == location_filter)
        and (
            requirement_filter == "all"
            or getattr(card, "requirement_judgments", {}).get(requirement_filter.title() if requirement_filter in {"typescript", "react"} else requirement_filter, "unknown") != "unknown"
        )
    ]
    ranked_cards = sorted(
        filtered_cards,
        key=lambda card: (card.fit_score, card.must_have_score, card.nice_to_have_score, card.confidence_score),
        reverse=True,
    )
    location_targets = brief.get("location_targets", [])
    is_demo_mode = seed_path is not None
    mode_label = "Demo data" if is_demo_mode else "Live GitHub results"
    mode_note = (
        "Synthetic seed candidates for UI walkthroughs. These are not real sourced profiles."
        if is_demo_mode
        else "Fresh public GitHub search and profile enrichment. These are real sourced profiles."
    )
    base_query = {
        "brief": str(brief_path.relative_to(ROOT)),
        "seed": str(seed_path.relative_to(ROOT)) if seed_path else "",
        "max_results": str(max_results),
        "evidence": evidence_filter,
        "location": location_filter,
        "requirement": requirement_filter,
        "load": "1" if cards else "",
    }
    role_options = []
    for option in available_briefs():
        selected = " selected" if option == brief_path else ""
        option_brief = load_role_brief(option)
        role_options.append(
            f'<option value="{escape(str(option.relative_to(ROOT)))}"{selected}>{escape(option_brief["role_name"])}</option>'
        )

    summary_cards = "".join(
        f"""
        <a class="summary-card spotlight-card workflow-filter {'active' if status_filter == status else ''}" href="/?{urlencode(base_query | {'view': view_mode, 'status': status})}">
          <span class="eyebrow-label">Workflow</span>
          <strong>{counts.get(status, 0)}</strong>
          <span class="summary-caption">{status.title()} candidates</span>
        </a>
        """
        for status in STATUS_OPTIONS
    )

    candidate_cards = []
    candidate_rows = []
    for rank, card in enumerate(ranked_cards, start=1):
        location_pill = ", ".join(card.location_hits) if card.location_hits else "Location unknown"
        found_via_text = ", ".join(card.found_via) if getattr(card, "found_via", None) else "GitHub user search"
        email_html = (
            f'<a class="ghost-link meta-link" href="mailto:{escape(card.email)}">{escape(card.email)}</a>'
            if card.email
            else '<span class="candidate-location-note">No public email on GitHub</span>'
        )
        contact_note = display_text(getattr(card, "contact_source", "") or "No public contact provenance")
        evidence_records = getattr(card, "evidence_records", []) or []
        evidence_items = "".join(
            f'<li><strong>{display_text(record.get("label", "Evidence"))}</strong><span class="evidence-snippet">{display_text(record.get("snippet", ""))}</span><a href="{escape(record.get("url", ""))}" target="_blank" rel="noreferrer">Open source</a></li>'
            for record in evidence_records[:5]
        )
        visible_requirements = list(brief["must_haves"])
        github_work_rows = "".join(
            f"""
            <div class="signal-row">
              <div class="signal-head">
                <span class="signal-label">{display_text(key)}</span>
                <span class="signal-chip">{requirement_judgment(card.requirement_scores.get(key, 0.0))}</span>
              </div>
              <div class="signal-meta">{display_text(card.requirement_sources.get(key, "") or "No explicit source evidence")}</div>
            <div class="signal-evidence">{display_text(card.requirement_evidence.get(key, "") or "No explicit public evidence found.")}</div>
            </div>
            """
            for key in visible_requirements
        )
        status_form = "".join(
            f"""
            <button type="submit" name="status" value="{status}" class="status-button status-{status} {'active' if card.status == status else ''}">
              {status.title()}
            </button>
            """
            for status in STATUS_OPTIONS
        )
        candidate_cards.append(
            f"""
            <article class="candidate-card spotlight-card reveal">
              <div class="candidate-head">
                <div>
                  <div class="candidate-meta">
                    <span class="pill">#{rank}</span>
                    <span class="pill pill-status">{escape(card.status.title())}</span>
                    <span class="pill">{display_text(review_state_label(getattr(card, "review_state", "needs_review") or "needs_review"))}</span>
                    <span class="pill">{display_text(enrichment_state_label(getattr(card, "enrichment_state", "full") or "full"))}</span>
                    <span class="pill">{display_text(location_pill)}</span>
                  </div>
                  <h3>{display_text(card.name)}</h3>
                  <p class="headline">{display_text(card.headline)}</p>
                </div>
                <div class="score-stack">
                  <span class="eyebrow-label">Fit score</span>
                  <div class="score-pill">{percent_label(card.fit_score)}</div>
                </div>
              </div>
              <div class="candidate-topline">
                <div class="candidate-link-row">
                  <a class="ghost-link meta-link" href="{escape(card.source_url)}" target="_blank" rel="noreferrer">Open GitHub profile</a>
                  {email_html}
                </div>
                <div class="candidate-inline-meta">
                  <span class="candidate-location-note">Found via {display_text(found_via_text)}</span>
                </div>
              </div>
              <p class="candidate-location-note">{display_text(getattr(card, "reviewer_note", "") or "Public evidence is still heuristic and should be reviewed.")}</p>
              <p class="candidate-location-note">{display_text(getattr(card, "why_summary", "") or "Review the requirement evidence below for the strongest public support.")}</p>
              <div class="score-breakdown">
                <div class="breakdown-item">
                  <span class="eyebrow-label">Must-haves</span>
                  <strong>{percent_label(card.must_have_score)}</strong>
                </div>
                <div class="breakdown-item">
                  <span class="eyebrow-label">Nice-to-haves</span>
                  <strong>{percent_label(card.nice_to_have_score)}</strong>
                </div>
                <div class="breakdown-item">
                  <span class="eyebrow-label">Weighted total</span>
                  <strong>{percent_label(card.fit_score)}</strong>
                </div>
              </div>
              <section class="signal-panel">
                <h4>Requirement evidence</h4>
                <div class="signal-grid">{github_work_rows}</div>
              </section>
              <details class="detail-panel">
                <summary>Evidence and source notes</summary>
                <div class="detail-body">
                  <p class="candidate-location-note">Public contact source: {contact_note}</p>
                  <ul class="evidence-list">{evidence_items or "".join(f'<li><a href="{escape(link)}" target="_blank" rel="noreferrer">{escape(link)}</a></li>' for link in card.evidence_links)}</ul>
                </div>
              </details>
              <details class="detail-panel">
                <summary>Outreach draft</summary>
                <div class="detail-body">
                  <pre>{display_text(card.outreach_draft)}</pre>
                </div>
              </details>
              <form method="post" action="/review" class="review-form">
                <input type="hidden" name="candidate_id" value="{escape(card.id)}">
                <input type="hidden" name="brief" value="{escape(str(brief_path.relative_to(ROOT)))}">
                <input type="hidden" name="seed" value="{escape(str(seed_path.relative_to(ROOT))) if seed_path else ''}">
                <input type="hidden" name="max_results" value="{max_results}">
                <input type="hidden" name="view" value="{escape(view_mode)}">
                <input type="hidden" name="status_filter" value="{escape(status_filter)}">
                <input type="hidden" name="evidence_filter" value="{escape(evidence_filter)}">
                <input type="hidden" name="location_filter" value="{escape(location_filter)}">
                <input type="hidden" name="requirement_filter" value="{escape(requirement_filter)}">
                <input type="hidden" name="queue_index" value="{queue_index}">
                {status_form}
              </form>
            </article>
            """
        )
        candidate_rows.append(
            f"""
            <tr>
              <td class="col-rank">#{rank}</td>
              <td class="col-candidate">
                <div class="table-name">{display_text(card.name)}</div>
                <a class="table-link" href="{escape(card.source_url)}" target="_blank" rel="noreferrer">GitHub</a>
                <div class="table-subtle">{display_text(found_via_text)}</div>
              </td>
              <td class="col-status">{escape(card.status.title())}</td>
              <td class="col-status">{display_text(review_state_label(getattr(card, "review_state", "needs_review") or "needs_review"))}<div class="table-subtle">{display_text(enrichment_state_label(getattr(card, "enrichment_state", "full") or "full"))}</div></td>
              <td class="col-score">{percent_label(card.fit_score)}</td>
              <td class="col-score">{display_text(requirement_checkmark(card.requirement_scores.get("TypeScript", 0.0)))}</td>
              <td class="col-score">{display_text(requirement_checkmark(card.requirement_scores.get("React", 0.0)))}</td>
              <td class="col-score">{display_text(requirement_checkmark(card.requirement_scores.get("frontend", 0.0)))}</td>
              <td class="col-score">{display_text(requirement_checkmark(card.requirement_scores.get("backend", 0.0)))}</td>
              <td class="col-location">{display_text(", ".join(card.location_hits) if card.location_hits else "No match")}<div class="table-subtle">{display_text((getattr(card, 'location_state', 'unknown') or 'unknown').title())}</div></td>
              <td class="col-skills">{display_text(getattr(card, "why_summary", "") or "No summary")}</td>
            </tr>
            """
        )

    message_html = f'<div class="banner success">{escape(message)}</div>' if message else ""
    error_html = f'<div class="banner error">{escape(error)}</div>' if error else ""
    seed_value = escape(str(seed_path.relative_to(ROOT))) if seed_path else ""
    seed_checked = " checked" if seed_path else ""
    must_have_list = "".join(f"<li>{escape(item)}</li>" for item in brief["must_haves"])
    nice_to_have_list = "".join(f"<li>{escape(item)}</li>" for item in brief["nice_to_haves"])
    location_label = " / ".join(location_targets) if location_targets else "No location gate"
    cards_tab_class = "active" if view_mode == "cards" else ""
    table_tab_class = "active" if view_mode == "table" else ""
    queue_tab_class = "active" if view_mode == "queue" else ""
    filter_label = "All candidates" if status_filter == "all" else f"{status_filter.title()} only"
    view_query = urlencode(base_query | {"status": status_filter})
    queue_index = max(0, min(queue_index, max(len(ranked_cards) - 1, 0)))
    queue_card = ranked_cards[queue_index] if ranked_cards else None
    queue_status_form = (
        "".join(
            f"""
            <button type="submit" name="status" value="{status}" class="status-button status-{status} {'active' if queue_card and queue_card.status == status else ''}">
              {status.title()}
            </button>
            """
            for status in STATUS_OPTIONS
        )
        if queue_card
        else ""
    )
    queue_view_html = (
        f"""
        <section class="table-shell spotlight-card reveal queue-shell">
          <div class="section-head">
            <div>
              <p class="eyebrow">Review queue</p>
              <h3>{display_text(queue_card.name)}</h3>
            </div>
            <span class="pill">{queue_index + 1} / {len(ranked_cards)}</span>
          </div>
          <div class="queue-controls">
            <div class="queue-nav">
              <a class="secondary queue-nav-button" href="/?{urlencode(base_query | {'status': status_filter, 'view': 'queue', 'queue': str(max(queue_index - 1, 0))})}">Previous</a>
              <a class="secondary queue-nav-button" href="/?{urlencode(base_query | {'status': status_filter, 'view': 'queue', 'queue': str(min(queue_index + 1, max(len(ranked_cards) - 1, 0)))})}">Next</a>
            </div>
            <form method="post" action="/review" class="review-form queue-review-form">
              <input type="hidden" name="candidate_id" value="{escape(queue_card.id) if queue_card else ''}">
              <input type="hidden" name="brief" value="{escape(str(brief_path.relative_to(ROOT)))}">
              <input type="hidden" name="seed" value="{escape(str(seed_path.relative_to(ROOT))) if seed_path else ''}">
              <input type="hidden" name="max_results" value="{max_results}">
              <input type="hidden" name="view" value="queue">
              <input type="hidden" name="status_filter" value="{escape(status_filter)}">
              <input type="hidden" name="evidence_filter" value="{escape(evidence_filter)}">
              <input type="hidden" name="location_filter" value="{escape(location_filter)}">
              <input type="hidden" name="requirement_filter" value="{escape(requirement_filter)}">
              <input type="hidden" name="queue_index" value="{queue_index}">
              {queue_status_form}
            </form>
          </div>
          <div class="queue-card">{candidate_cards[queue_index] if queue_card else '<div class="candidate-card empty-state spotlight-card"><p class="lede">No candidates loaded yet. Run live GitHub sourcing or enable seed data to populate the pipeline.</p></div>'}</div>
        </section>
        """
        if ranked_cards
        else '<div class="candidate-card empty-state spotlight-card"><p class="lede">No candidates loaded yet. Run live GitHub sourcing or enable seed data to populate the pipeline.</p></div>'
    )
    cards_view_html = (
        ''.join(candidate_cards)
        if candidate_cards
        else '<div class="candidate-card empty-state spotlight-card"><p class="lede">No candidates loaded yet. Run live GitHub sourcing or enable seed data to populate the pipeline.</p></div>'
    )
    table_view_html = (
        f"""
        <section class="table-shell spotlight-card reveal">
          <div class="section-head">
            <div>
              <p class="eyebrow">Candidate table</p>
              <h3>All candidates</h3>
            </div>
            <span class="pill">{len(ranked_cards)} rows</span>
          </div>
          <div class="table-wrap">
            <table class="candidate-table">
              <thead>
                <tr>
                  <th class="col-rank">Rank</th>
                  <th class="col-candidate">Candidate</th>
                  <th class="col-status">Status</th>
                  <th class="col-status">Evidence base</th>
                  <th class="col-score">Score</th>
                  <th class="col-score">TS</th>
                  <th class="col-score">React</th>
                  <th class="col-score">FE</th>
                  <th class="col-score">BE</th>
                  <th class="col-location">Location</th>
                  <th class="col-skills">Why this candidate</th>
                </tr>
              </thead>
              <tbody>
                {''.join(candidate_rows)}
              </tbody>
            </table>
          </div>
        </section>
        """
        if candidate_rows
        else '<div class="candidate-card empty-state spotlight-card"><p class="lede">No candidates loaded yet. Run live GitHub sourcing or enable seed data to populate the pipeline.</p></div>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HASH Recruiting Tool</title>
  <style>
    :root {{
      --background-deep: #020203;
      --background-base: #050506;
      --background-elevated: #0a0a0c;
      --surface: rgba(255, 255, 255, 0.05);
      --surface-hover: rgba(255, 255, 255, 0.08);
      --foreground: #EDEDEF;
      --foreground-muted: #8A8F98;
      --foreground-subtle: rgba(255, 255, 255, 0.6);
      --accent: #5E6AD2;
      --accent-bright: #6872D9;
      --accent-glow: rgba(94, 106, 210, 0.3);
      --border-default: rgba(255, 255, 255, 0.06);
      --border-hover: rgba(255, 255, 255, 0.1);
      --border-accent: rgba(94, 106, 210, 0.3);
      --shadow-card: 0 0 0 1px rgba(255,255,255,0.06), 0 2px 20px rgba(0,0,0,0.4), 0 0 40px rgba(0,0,0,0.2);
      --shadow-card-hover: 0 0 0 1px rgba(255,255,255,0.1), 0 10px 44px rgba(0,0,0,0.55), 0 0 80px rgba(94,106,210,0.14);
      --shadow-accent: 0 0 0 1px rgba(94,106,210,0.5), 0 4px 12px rgba(94,106,210,0.3), inset 0 1px 0 0 rgba(255,255,255,0.18);
      --radius-lg: 16px;
      --radius-md: 12px;
      --radius-sm: 10px;
      --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
    }}
    * {{ box-sizing: border-box; }}
    html {{ color-scheme: dark; }}
    body {{
      margin: 0;
      font-family: "Inter", "Geist Sans", system-ui, sans-serif;
      color: var(--foreground);
      background:
        radial-gradient(ellipse at top, #0a0a0f 0%, #050506 50%, #020203 100%);
      min-height: 100vh;
      overflow-x: hidden;
    }}
    body::before,
    body::after {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: -2;
    }}
    body::before {{
      background:
        radial-gradient(circle at 18% 18%, rgba(94,106,210,0.22), transparent 30%),
        radial-gradient(circle at 82% 24%, rgba(122,92,255,0.14), transparent 24%),
        radial-gradient(circle at 50% 88%, rgba(94,106,210,0.12), transparent 30%);
      filter: blur(20px);
      animation: float 10s ease-in-out infinite;
    }}
    body::after {{
      background-image:
        linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);
      background-size: 64px 64px;
      opacity: 0.28;
      mask-image: linear-gradient(to bottom, rgba(255,255,255,0.6), transparent 88%);
      z-index: -1;
    }}
    a {{
      color: var(--foreground);
      text-decoration: none;
      transition: color 200ms var(--ease-out-expo);
    }}
    a:hover {{ color: var(--accent-bright); }}
    .shell {{
      position: relative;
      max-width: 1280px;
      margin: 0 auto;
      padding: 28px 20px 96px;
    }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 18px;
      margin-bottom: 28px;
      border: 1px solid var(--border-default);
      border-radius: var(--radius-lg);
      background: linear-gradient(to bottom, rgba(255,255,255,0.05), rgba(255,255,255,0.02));
      backdrop-filter: blur(16px);
      box-shadow: var(--shadow-card);
    }}
    .brand {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .brand strong {{
      font-size: 14px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--foreground-subtle);
    }}
    .brand span {{
      color: var(--foreground-muted);
      font-size: 14px;
    }}
    .page-header {{
      display: grid;
      gap: 10px;
      margin: 8px 0 20px;
    }}
    .hero {{
      position: relative;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 24px;
      align-items: stretch;
      margin-bottom: 28px;
    }}
    .control-panel,
    .summary-card,
    .brief-card,
    .candidate-card,
    .info-panel,
    .outreach-panel,
    .toolbar {{
      position: relative;
      overflow: hidden;
      background: linear-gradient(to bottom, rgba(255,255,255,0.08), rgba(255,255,255,0.03));
      border: 1px solid var(--border-default);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-card);
      backdrop-filter: blur(16px);
      transition: transform 240ms var(--ease-out-expo), box-shadow 240ms var(--ease-out-expo), border-color 240ms var(--ease-out-expo), background 240ms var(--ease-out-expo);
    }}
    .spotlight-card::before {{
      content: "";
      position: absolute;
      inset: 0;
      background: radial-gradient(300px circle at var(--mouse-x, 50%) var(--mouse-y, 50%), rgba(94,106,210,0.16), transparent 60%);
      opacity: 0;
      transition: opacity 240ms var(--ease-out-expo);
      pointer-events: none;
    }}
    .spotlight-card:hover {{
      transform: translateY(-4px);
      border-color: var(--border-hover);
      box-shadow: var(--shadow-card-hover);
      background: linear-gradient(to bottom, rgba(255,255,255,0.09), rgba(255,255,255,0.04));
    }}
    .spotlight-card:hover::before {{
      opacity: 1;
    }}
    .page-header h1 {{
      margin: 0;
      font-size: clamp(1.7rem, 3vw, 2.3rem);
      line-height: 1.1;
      letter-spacing: -0.04em;
      font-weight: 600;
      max-width: none;
      background: linear-gradient(to bottom, rgba(255,255,255,1), rgba(255,255,255,0.72));
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
    }}
    .eyebrow,
    .eyebrow-label {{
      text-transform: uppercase;
      letter-spacing: 0.18em;
      font-size: 12px;
      font-family: "SFMono-Regular", Menlo, monospace;
      color: var(--foreground-subtle);
    }}
    .eyebrow {{
      margin: 0 0 14px;
      color: rgba(104,114,217,0.9);
    }}
    h2, h3, h4 {{
      margin: 0;
      line-height: 1.1;
      letter-spacing: -0.02em;
      font-weight: 600;
    }}
    .lede {{
      font-size: 0.92rem;
      line-height: 1.5;
      color: var(--foreground-muted);
      max-width: 52ch;
      margin: 0;
    }}
    .hero-stats {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .control-panel {{
      padding: 24px;
      height: 100%;
    }}
    .form-grid {{
      display: grid;
      gap: 16px;
    }}
    label {{
      display: grid;
      gap: 8px;
      font-size: 13px;
      color: var(--foreground-muted);
      font-weight: 500;
    }}
    .field-note {{
      margin: -2px 0 0;
      font-size: 12px;
      line-height: 1.5;
      color: var(--foreground-subtle);
    }}
    select, input[type="number"], input[type="text"] {{
      width: 100%;
      padding: 13px 14px;
      border-radius: var(--radius-sm);
      border: 1px solid rgba(255,255,255,0.1);
      background: #0f0f12;
      color: var(--foreground);
      font-size: 15px;
      transition: border-color 200ms var(--ease-out-expo), box-shadow 200ms var(--ease-out-expo), background 200ms var(--ease-out-expo);
    }}
    select:focus, input:focus, button:focus, a:focus {{
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(94,106,210,0.35);
    }}
    .checkbox {{
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--foreground-muted);
    }}
    .checkbox input {{
      width: auto;
      accent-color: var(--accent);
    }}
    .button-row {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 8px;
    }}
    button {{
      cursor: pointer;
      border: 0;
      border-radius: 10px;
      padding: 12px 16px;
      min-height: 46px;
      font-size: 15px;
      line-height: 1.2;
      font-weight: 600;
      color: var(--foreground);
      transition: transform 200ms var(--ease-out-expo), background 200ms var(--ease-out-expo), box-shadow 200ms var(--ease-out-expo), color 200ms var(--ease-out-expo);
    }}
    .primary {{
      background: var(--accent);
      box-shadow: var(--shadow-accent);
    }}
    .primary:hover {{
      background: var(--accent-bright);
      transform: translateY(-1px);
    }}
    .secondary,
    .ghost-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 12px 16px;
      border-radius: 10px;
      background: rgba(255,255,255,0.05);
      color: var(--foreground);
      border: 1px solid var(--border-default);
      box-shadow: inset 0 1px 0 0 rgba(255,255,255,0.08);
      min-height: 46px;
      font-size: 15px;
      font-weight: 600;
      line-height: 1.2;
      text-decoration: none;
    }}
    .secondary:hover,
    .ghost-link:hover {{
      background: rgba(255,255,255,0.08);
    }}
    .hero-gradient {{
      background: linear-gradient(90deg, #5E6AD2, #8e98ff, #5E6AD2);
      background-size: 200% auto;
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      animation: shimmer 8s linear infinite;
    }}
    .workspace {{
      display: grid;
      gap: 24px;
      margin-top: 28px;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      padding: 16px 18px;
      align-items: center;
    }}
    .toolbar-copy {{
      display: grid;
      gap: 6px;
    }}
    .toolbar-copy h2 {{
      font-size: clamp(1.15rem, 2vw, 1.5rem);
    }}
    .toolbar-copy p,
    .summary-caption,
    .headline,
    li,
    pre,
    .toolbar-meta,
    .toolbar-side p {{
      color: var(--foreground-muted);
      line-height: 1.65;
      font-size: 15px;
    }}
    .toolbar-side {{
      display: grid;
      gap: 10px;
      align-content: start;
      justify-items: end;
    }}
    .toolbar-pills,
    .candidate-meta {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 32px;
      padding: 0 12px;
      border-radius: 999px;
      border: 1px solid var(--border-default);
      background: rgba(255,255,255,0.04);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--foreground-subtle);
      font-weight: 600;
    }}
    .pill-status {{
      border-color: var(--border-accent);
      color: rgba(237,237,239,0.9);
    }}
    .pill-demo {{
      color: #f6ddb0;
      border-color: rgba(173, 124, 36, 0.3);
    }}
    .pill-live {{
      color: #c2f0e3;
      border-color: rgba(31, 107, 93, 0.3);
    }}
    .pill.active {{
      color: var(--foreground);
      border-color: var(--border-accent);
      background: rgba(94,106,210,0.12);
    }}
    .pill-location.eligible {{
      color: #d2e8ff;
      border-color: rgba(105, 161, 255, 0.25);
    }}
    .pill-location.ineligible {{
      color: #f1b3b3;
      border-color: rgba(255, 92, 92, 0.25);
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 16px;
      align-items: stretch;
    }}
    .workflow-group {{
      display: contents;
    }}
    .summary-card {{
      padding: 20px;
      min-height: 128px;
      display: grid;
      gap: 8px;
      align-content: start;
      height: 100%;
    }}
    .summary-card strong {{
      font-size: 2rem;
      line-height: 1;
      letter-spacing: -0.03em;
    }}
    .workflow-filter.active {{
      border-color: var(--border-accent);
      box-shadow: var(--shadow-card-hover);
      background: linear-gradient(to bottom, rgba(94,106,210,0.16), rgba(255,255,255,0.04));
    }}
    .candidate-toolbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .toolbar-filters {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 0 0 16px;
    }}
    .queue-nav {{
      display: flex;
      gap: 12px;
      margin: 0;
      flex-wrap: wrap;
    }}
    .queue-controls {{
      display: grid;
      gap: 16px;
      margin: 8px 0 26px;
      padding: 18px;
      border-radius: 14px;
      background: linear-gradient(to bottom, rgba(255,255,255,0.04), rgba(255,255,255,0.02));
      border: 1px solid var(--border-default);
      box-shadow: var(--shadow-card);
    }}
    .queue-review-form {{
      margin: 0;
    }}
    .queue-nav-button {{
      min-width: 132px;
    }}
    .workspace-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.55fr);
      gap: 24px;
      align-items: start;
    }}
    .view-switcher {{
      display: inline-flex;
      gap: 8px;
      padding: 4px;
      border-radius: 999px;
      background: rgba(255,255,255,0.04);
      border: 1px solid var(--border-default);
      width: fit-content;
    }}
    .view-tab {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 34px;
      padding: 0 14px;
      border-radius: 999px;
      color: var(--foreground-muted);
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.02em;
      transition: background 200ms var(--ease-out-expo), color 200ms var(--ease-out-expo), box-shadow 200ms var(--ease-out-expo);
    }}
    .toolbar-note {{
      margin: 0;
      font-size: 13px;
      color: var(--foreground-subtle);
    }}
    .hero .toolbar {{
      height: 100%;
      align-content: start;
      grid-template-columns: 1fr;
    }}
    .hero .toolbar-side {{
      justify-items: start;
    }}
    .view-tab.active {{
      background: rgba(94,106,210,0.18);
      color: var(--foreground);
      box-shadow: inset 0 0 0 1px rgba(94,106,210,0.25);
    }}
    .candidate-list {{
      display: grid;
      gap: 18px;
      min-width: 0;
    }}
    .candidate-card {{
      padding: 24px;
    }}
    .candidate-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 156px;
      gap: 18px;
      align-items: start;
      margin-bottom: 6px;
    }}
    .candidate-head h3 {{
      font-size: clamp(1.35rem, 2vw, 1.7rem);
      margin-top: 16px;
      margin-bottom: 10px;
    }}
    .score-stack {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      width: 156px;
      min-width: 156px;
      justify-self: end;
    }}
    .score-pill {{
      min-width: 84px;
      height: 56px;
      box-sizing: border-box;
      padding: 14px 16px;
      border-radius: 14px;
      text-align: center;
      background: linear-gradient(to bottom, rgba(94,106,210,0.9), rgba(94,106,210,0.7));
      color: #fff;
      font-size: 1.3rem;
      font-weight: 700;
      box-shadow: var(--shadow-accent);
    }}
    .score-stack .eyebrow-label {{
      white-space: nowrap;
      line-height: 1;
      margin: 0;
    }}
    .candidate-topline {{
      display: grid;
      gap: 12px;
      margin: 0 0 16px;
    }}
    .candidate-link-row {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .candidate-inline-meta {{
      display: flex;
      align-items: center;
      gap: 0;
      flex-wrap: wrap;
    }}
    .candidate-inline-meta > * + *::before {{
      content: "|";
      margin: 0 10px;
      color: var(--foreground-subtle);
    }}
    .candidate-location-note {{
      color: var(--foreground-muted);
      font-size: 14px;
    }}
    .meta-link {{
      min-height: 38px;
      display: inline-flex;
      align-items: center;
    }}
    .score-breakdown {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}
    .breakdown-item {{
      padding: 14px;
      border-radius: 12px;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--border-default);
      display: grid;
      gap: 8px;
    }}
    .breakdown-item strong {{
      font-size: 1.05rem;
      color: var(--foreground);
      letter-spacing: -0.02em;
    }}
    .match-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin: 18px 0;
    }}
    .match-panel {{
      padding: 16px;
      border-radius: 12px;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--border-default);
      display: grid;
      gap: 12px;
    }}
    .match-panel h4 {{
      font-size: 0.95rem;
    }}
    .signal-panel {{
      margin: 18px 0 0;
      padding: 16px;
      border-radius: 12px;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--border-default);
      display: grid;
      gap: 12px;
    }}
    .signal-panel h4 {{
      font-size: 0.95rem;
      margin: 0;
    }}
    .signal-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .signal-row {{
      display: grid;
      gap: 6px;
      padding: 14px;
      border-radius: 12px;
      background: rgba(8, 8, 12, 0.72);
      border: 1px solid var(--border-default);
    }}
    .signal-head {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .signal-label {{
      font-weight: 600;
      color: var(--foreground);
    }}
    .signal-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      border: 1px solid var(--border-default);
      background: rgba(255,255,255,0.04);
      color: var(--foreground-subtle);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .signal-meta {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--foreground-subtle);
    }}
    .signal-evidence {{
      color: var(--foreground-muted);
      font-size: 13px;
      line-height: 1.5;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 3;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .match-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .match-badge,
    .match-empty {{
      display: inline-flex;
      flex-direction: column;
      align-items: flex-start;
      justify-content: flex-start;
      gap: 4px;
      min-height: 44px;
      max-width: min(100%, 280px);
      padding: 9px 12px 7px;
      border-radius: 12px;
      background: rgba(94,106,210,0.12);
      border: 1px solid rgba(94,106,210,0.22);
      color: var(--foreground);
      font-size: 13px;
      line-height: 1.2;
      text-align: left;
      box-sizing: border-box;
    }}
    .match-badge.subdued {{
      background: rgba(255,255,255,0.05);
      border-color: var(--border-default);
      color: var(--foreground-muted);
    }}
    .match-badge small {{
      font-size: 11px;
      color: var(--foreground-subtle);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      text-align: left;
    }}
    .match-empty {{
      background: rgba(255,255,255,0.03);
      border-color: var(--border-default);
      color: var(--foreground-muted);
    }}
    .info-panel,
    .outreach-panel,
    .brief-card {{
      padding: 18px;
    }}
    .info-panel-wide {{
      grid-column: span 3;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 12px;
    }}
    .brief-card {{
      display: grid;
      gap: 20px;
      position: sticky;
      top: 24px;
      align-self: start;
      max-height: calc(100vh - 48px);
      overflow: auto;
    }}
    .table-shell {{
      padding: 20px;
      min-width: 0;
    }}
    .table-wrap {{
      width: 100%;
      max-width: 100%;
      overflow-x: auto;
      border-radius: 14px;
      border: 1px solid var(--border-default);
      background: rgba(8, 8, 12, 0.72);
    }}
    .candidate-table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
      table-layout: fixed;
    }}
    .candidate-table th,
    .candidate-table td {{
      padding: 14px 16px;
      text-align: left;
      vertical-align: top;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      font-size: 14px;
      line-height: 1.5;
    }}
    .candidate-table th {{
      font-family: "SFMono-Regular", Menlo, monospace;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--foreground-subtle);
      background: rgba(255,255,255,0.03);
      position: sticky;
      top: 0;
    }}
    .candidate-table tbody tr:hover {{
      background: rgba(255,255,255,0.03);
    }}
    .table-name {{
      color: var(--foreground);
      font-weight: 600;
      margin-bottom: 6px;
    }}
    .table-link {{
      color: var(--accent-bright);
      font-size: 13px;
    }}
    .table-subtle {{
      margin-top: 4px;
      color: var(--foreground-subtle);
      font-size: 12px;
    }}
    .col-rank {{
      width: 6%;
      white-space: nowrap;
    }}
    .col-candidate {{
      width: 16%;
    }}
    .col-status {{
      width: 9%;
    }}
    .col-score {{
      width: 7%;
      white-space: nowrap;
    }}
    .col-location {{
      width: 13%;
    }}
    .col-skills {{
      width: 19%;
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
    }}
    .evidence-list {{
      display: grid;
      gap: 12px;
      padding-left: 18px;
    }}
    .evidence-list li {{
      display: grid;
      gap: 6px;
    }}
    .evidence-snippet {{
      color: var(--foreground-muted);
      font-size: 14px;
      line-height: 1.5;
    }}
    .brief-columns {{
      display: grid;
      gap: 18px;
    }}
    .brief-columns section {{
      display: grid;
      gap: 12px;
    }}
    pre {{
      white-space: pre-wrap;
      background: rgba(8, 8, 12, 0.92);
      padding: 14px;
      border-radius: 14px;
      border: 1px solid var(--border-default);
      font: 14px/1.55 "SFMono-Regular", Menlo, monospace;
      margin: 0;
      color: rgba(237,237,239,0.85);
    }}
    .detail-panel {{
      border: 1px solid var(--border-default);
      border-radius: 14px;
      background: rgba(255,255,255,0.025);
      margin-top: 12px;
      overflow: hidden;
    }}
    .detail-panel summary {{
      list-style: none;
      cursor: pointer;
      padding: 14px 16px;
      font-weight: 600;
      color: var(--foreground);
    }}
    .detail-panel summary::-webkit-details-marker {{
      display: none;
    }}
    .detail-panel[open] summary {{
      border-bottom: 1px solid var(--border-default);
      background: rgba(255,255,255,0.03);
    }}
    .detail-body {{
      padding: 14px 16px 16px;
    }}
    .review-form {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }}
    .status-button {{
      background: rgba(255,255,255,0.04);
      border: 1px solid var(--border-default);
      box-shadow: inset 0 1px 0 0 rgba(255,255,255,0.08);
      min-height: 48px;
      min-width: 124px;
      justify-content: center;
      font-weight: 700;
    }}
    .status-button.active {{
      background: var(--accent);
      color: #fff;
      box-shadow: var(--shadow-accent);
    }}
    .queue-review-form .status-button {{
      flex: 1 1 0;
      min-width: 140px;
      min-height: 54px;
      border-radius: 14px;
      font-size: 15px;
      transition: transform 180ms var(--ease-out-expo), border-color 180ms var(--ease-out-expo), background 180ms var(--ease-out-expo);
    }}
    .queue-review-form .status-button:hover,
    .queue-nav-button:hover {{
      transform: translateY(-1px);
    }}
    .queue-review-form .status-shortlist {{
      border-color: rgba(55, 195, 129, 0.28);
      background: rgba(55, 195, 129, 0.09);
    }}
    .queue-review-form .status-hold {{
      border-color: rgba(255, 196, 87, 0.28);
      background: rgba(255, 196, 87, 0.08);
    }}
    .queue-review-form .status-reject {{
      border-color: rgba(255, 107, 107, 0.24);
      background: rgba(255, 107, 107, 0.08);
    }}
    .banner {{
      margin-bottom: 16px;
      padding: 12px 14px;
      border-radius: 12px;
      font: 600 14px/1.4 "Inter", system-ui, sans-serif;
      border: 1px solid transparent;
    }}
    .mode-banner {{
      background: rgba(255,255,255,0.05);
      color: var(--foreground);
      border-color: var(--border-default);
    }}
    .mode-banner.demo {{
      background: rgba(173, 124, 36, 0.16);
      border-color: rgba(173, 124, 36, 0.3);
      color: #f6ddb0;
    }}
    .mode-banner.live {{
      background: rgba(31, 107, 93, 0.16);
      border-color: rgba(31, 107, 93, 0.3);
      color: #c2f0e3;
    }}
    .success {{
      background: rgba(31, 107, 93, 0.18);
      color: #baf3df;
      border-color: rgba(31, 107, 93, 0.32);
    }}
    .error {{
      background: rgba(153, 47, 47, 0.18);
      color: #ffd1d1;
      border-color: rgba(153, 47, 47, 0.34);
    }}
    .empty-state {{
      padding: 28px;
      min-height: 220px;
      display: grid;
      place-items: center;
      text-align: center;
    }}
    .reveal {{
      opacity: 0;
      transform: translateY(24px) scale(0.98);
      animation: reveal 600ms var(--ease-out-expo) forwards;
    }}
    .candidate-card:nth-child(2) {{ animation-delay: 80ms; }}
    .candidate-card:nth-child(3) {{ animation-delay: 160ms; }}
    @keyframes float {{
      0%, 100% {{ transform: translateY(0) rotate(0deg); }}
      50% {{ transform: translateY(-20px) rotate(1deg); }}
    }}
    @keyframes shimmer {{
      0% {{ background-position: 0% 50%; }}
      100% {{ background-position: 200% 50%; }}
    }}
    @keyframes reveal {{
      to {{
        opacity: 1;
        transform: translateY(0) scale(1);
      }}
    }}
    @media (max-width: 1120px) {{
      .hero,
      .toolbar,
      .workspace-grid {{
        grid-template-columns: 1fr;
      }}
      .summary-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .brief-card {{
        position: static;
      }}
    }}
    @media (max-width: 760px) {{
      .shell {{
        padding: 16px 14px 72px;
      }}
      .control-panel,
      .toolbar,
      .brief-card,
      .candidate-card {{
        padding: 20px;
      }}
      .page-header h1 {{
        font-size: clamp(2.6rem, 16vw, 4.2rem);
      }}
      .hero-stats,
      .summary-grid,
      .score-breakdown,
      .match-grid,
      .signal-grid {{
        grid-template-columns: 1fr;
      }}
      .candidate-head,
      .section-head,
      .topbar {{
        flex-direction: column;
        align-items: flex-start;
      }}
      .score-stack {{
        justify-items: start;
      }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{
        animation: none !important;
        transition: none !important;
        scroll-behavior: auto !important;
      }}
      .reveal {{
        opacity: 1;
        transform: none;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    {message_html}
    {error_html}
    <header class="topbar spotlight-card">
      <div class="brand">
        <strong>HASH Recruiting Tool</strong>
        <span>Local GitHub-first sourcing for engineering candidates</span>
      </div>
      <a class="secondary" href="/export?{urlencode({'output': str(output_path.relative_to(ROOT)), 'csv': str(csv_path.relative_to(ROOT))})}">Download CSV</a>
    </header>

    <section class="page-header" id="hero">
      <h1>Recruiting <span class="hero-gradient">Shortlist</span></h1>
      <p class="lede">
        GitHub-first sourcing with weighted scoring and a London/Berlin gate.
      </p>
      <div class="hero-stats">
        <span class="pill">Source: GitHub</span>
        <span class="pill">Location: {escape(location_label)}</span>
      </div>
    </section>

    <section class="hero">
      <form class="control-panel spotlight-card" method="post" action="/run" id="run-form">
        <div class="section-head">
          <div>
            <p class="eyebrow">Run sourcing</p>
            <h2>Pipeline controls</h2>
          </div>
          <span class="pill">Local-only</span>
        </div>
        <div class="form-grid">
          <input type="hidden" name="view" value="{escape(view_mode)}">
          <input type="hidden" name="status_filter" value="{escape(status_filter)}">
          <input type="hidden" name="evidence_filter" value="{escape(evidence_filter)}">
          <input type="hidden" name="location_filter" value="{escape(location_filter)}">
          <input type="hidden" name="requirement_filter" value="{escape(requirement_filter)}">
          <input type="hidden" name="queue" value="{queue_index}">
          <label>
            Role brief
            <select name="brief">{''.join(role_options)}</select>
          </label>
          <p class="field-note">GitHub-first sourcing with weighted scoring and a hard London/Berlin screen.</p>
          <label>
            GitHub profiles per search
            <input type="number" name="max_results" min="1" max="25" value="{max_results}">
          </label>
          <p class="field-note">The tool runs multiple searches for the role, so total raw results will be higher than this number. Start with 20 for about 60 raw results.</p>
          <p class="field-note">Live sourcing can take 10-20 seconds while public profiles and repos are enriched.</p>
          <label class="checkbox">
            <input type="checkbox" name="use_seed"{seed_checked}>
            Use local seed results for deterministic demo data
          </label>
          <label>
            Seed results path
            <input type="text" name="seed" value="{seed_value}" placeholder="data/sample_search_results.json">
          </label>
          <div class="button-row">
            <button class="primary" type="submit" id="run-button">Run GitHub sourcing</button>
            <a class="secondary" href="/export?{urlencode({'output': str(output_path.relative_to(ROOT)), 'csv': str(csv_path.relative_to(ROOT))})}">Export current CSV</a>
          </div>
        </div>
      </form>

    </section>

    <section class="workspace">
      <section class="summary-grid">
        <div class="workflow-group">
          {summary_cards}
          <a class="summary-card spotlight-card workflow-filter {'active' if status_filter == 'all' else ''}" href="/?{urlencode(base_query | {'view': view_mode, 'status': 'all'})}">
            <span class="eyebrow-label">Workflow</span>
            <strong>{len(cards)}</strong>
            <span class="summary-caption">All candidates</span>
          </a>
        </div>
        <a class="summary-card spotlight-card" href="/?{urlencode(base_query | {'view': view_mode, 'status': status_filter})}">
          <span class="eyebrow-label">Top score</span>
          <strong>{percent_label(max((card.fit_score for card in filtered_cards), default=0))}</strong>
          <span class="summary-caption">Showing {escape(filter_label.lower())}</span>
        </a>
      </section>

      <section class="workspace-grid">
        <section class="candidate-list">
          <div class="candidate-toolbar">
            <div class="toolbar-group">
              <p class="toolbar-label">View</p>
              <nav class="view-switcher" aria-label="Candidate views">
                <a class="view-tab {cards_tab_class}" href="/?{view_query}&view=cards">Cards</a>
                <a class="view-tab {table_tab_class}" href="/?{view_query}&view=table">Table</a>
                <a class="view-tab {queue_tab_class}" href="/?{view_query}&view=queue">Queue</a>
              </nav>
            </div>
            <p class="toolbar-note">Showing {escape(filter_label.lower())}</p>
          </div>
          <div class="toolbar-group">
            <p class="toolbar-label">Filters</p>
            <div class="toolbar-filters">
              <a class="pill {'active' if evidence_filter == 'all' else ''}" href="/?{urlencode(base_query | {'status': status_filter, 'view': view_mode, 'evidence': 'all'})}">All evidence</a>
              <a class="pill {'active' if evidence_filter == 'full' else ''}" href="/?{urlencode(base_query | {'status': status_filter, 'view': view_mode, 'evidence': 'full'})}">Full enrichment</a>
              <a class="pill {'active' if evidence_filter == 'partial' else ''}" href="/?{urlencode(base_query | {'status': status_filter, 'view': view_mode, 'evidence': 'partial'})}">Partial enrichment</a>
              <a class="pill {'active' if location_filter == 'confirmed' else ''}" href="/?{urlencode(base_query | {'status': status_filter, 'view': view_mode, 'location': 'confirmed'})}">Confirmed location</a>
              <a class="pill {'active' if location_filter == 'inferred' else ''}" href="/?{urlencode(base_query | {'status': status_filter, 'view': view_mode, 'location': 'inferred'})}">Inferred location</a>
              <a class="pill {'active' if requirement_filter == 'backend' else ''}" href="/?{urlencode(base_query | {'status': status_filter, 'view': view_mode, 'requirement': 'backend'})}">Backend evidence</a>
              <a class="pill {'active' if requirement_filter == 'frontend' else ''}" href="/?{urlencode(base_query | {'status': status_filter, 'view': view_mode, 'requirement': 'frontend'})}">Frontend evidence</a>
            </div>
          </div>
          {cards_view_html if view_mode == "cards" else table_view_html if view_mode == "table" else queue_view_html}
        </section>

        <aside class="brief-card spotlight-card">
          <div class="section-head">
            <div>
              <p class="eyebrow">Role brief</p>
              <h3>Scoring inputs</h3>
            </div>
            <span class="pill">Explicit criteria</span>
          </div>
          <div class="brief-columns">
            <section>
              <h4>Must-haves</h4>
              <ul>{must_have_list}</ul>
            </section>
            <section>
              <h4>Nice-to-haves</h4>
              <ul>{nice_to_have_list}</ul>
            </section>
            <section>
              <h4>Location gate</h4>
              <ul>{"".join(f"<li>{escape(item)}</li>" for item in location_targets) or "<li>No location targets set</li>"}</ul>
            </section>
          </div>
        </aside>
      </section>
    </section>
  </main>
  <script>
    const cards = document.querySelectorAll('.spotlight-card');
    for (const card of cards) {{
      card.addEventListener('pointermove', (event) => {{
        const rect = card.getBoundingClientRect();
        const x = ((event.clientX - rect.left) / rect.width) * 100;
        const y = ((event.clientY - rect.top) / rect.height) * 100;
        card.style.setProperty('--mouse-x', `${{x}}%`);
        card.style.setProperty('--mouse-y', `${{y}}%`);
      }});
    }}

    const hero = document.getElementById('hero');
    const runForm = document.getElementById('run-form');
    const runButton = document.getElementById('run-button');
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (hero && !prefersReducedMotion) {{
      const updateHero = () => {{
        const offset = Math.min(window.scrollY / 600, 1);
        hero.style.opacity = String(1 - offset * 0.2);
        hero.style.transform = `translateY(${{offset * 20}}px) scale(${{1 - offset * 0.03}})`;
      }};
      updateHero();
      window.addEventListener('scroll', updateHero, {{ passive: true }});
    }}

    if (runForm && runButton) {{
      runForm.addEventListener('submit', () => {{
        runButton.disabled = true;
        runButton.textContent = 'Running...';
      }});
    }}
  </script>
</body>
</html>"""


class RecruitingHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.handle_index(parse_qs(parsed.query))
            return
        if parsed.path == "/export":
            self.handle_export(parse_qs(parsed.query))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length).decode("utf-8")
        form = {key: values[-1] for key, values in parse_qs(payload).items()}

        if parsed.path == "/run":
            self.handle_run(form)
            return
        if parsed.path == "/review":
            self.handle_review(form)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_index(self, query: dict[str, list[str]]) -> None:
        brief_path = self.resolve_path(query.get("brief", [str(DEFAULT_BRIEF.relative_to(ROOT))])[-1], DEFAULT_BRIEF)
        output_path = self.resolve_path(query.get("output", [str(DEFAULT_OUTPUT.relative_to(ROOT))])[-1], DEFAULT_OUTPUT)
        csv_path = self.resolve_path(query.get("csv", [str(DEFAULT_CSV.relative_to(ROOT))])[-1], DEFAULT_CSV)
        seed_value = query.get("seed", [""])[-1]
        seed_path = self.resolve_path(seed_value, DEFAULT_SEED) if seed_value else None
        max_results = int(query.get("max_results", [str(DEFAULT_MAX_RESULTS)])[-1])
        view_mode = query.get("view", ["cards"])[-1]
        status_filter = normalize_status_filter(query.get("status", ["all"])[-1])
        evidence_filter = normalize_evidence_filter(query.get("evidence", ["all"])[-1])
        location_filter = normalize_location_filter(query.get("location", ["all"])[-1])
        requirement_filter = normalize_requirement_filter(query.get("requirement", ["all"])[-1])
        queue_index = int(query.get("queue", ["0"])[-1] or "0")
        should_load_cards = query.get("load", [""])[-1] == "1"
        cards = load_cards(output_path) if should_load_cards and output_path.exists() else []
        page = render_page(
            brief_path=brief_path,
            output_path=output_path,
            csv_path=csv_path,
            seed_path=seed_path,
            max_results=max_results,
            cards=cards,
            view_mode=view_mode if view_mode in {"cards", "table", "queue"} else "cards",
            status_filter=status_filter,
            evidence_filter=evidence_filter,
            location_filter=location_filter,
            requirement_filter=requirement_filter,
            queue_index=queue_index,
            message=query.get("message", [""])[-1],
            error=query.get("error", [""])[-1],
        )
        body = page.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_run(self, form: dict[str, str]) -> None:
        brief_path = self.resolve_path(form.get("brief", ""), DEFAULT_BRIEF)
        output_path = DEFAULT_OUTPUT
        seed_path = self.resolve_path(form.get("seed", ""), DEFAULT_SEED) if form.get("use_seed") else None
        max_results = int(form.get("max_results", str(DEFAULT_MAX_RESULTS)))
        view_mode = form.get("view", "cards")
        status_filter = normalize_status_filter(form.get("status_filter", "all"))
        evidence_filter = normalize_evidence_filter(form.get("evidence_filter", "all"))
        location_filter = normalize_location_filter(form.get("location_filter", "all"))
        requirement_filter = normalize_requirement_filter(form.get("requirement_filter", "all"))
        queue_index = form.get("queue", "0")

        try:
            brief = load_role_brief(brief_path)
            cards = build_candidates(brief, max_results_per_query=max_results, seed_results_path=seed_path)
            save_cards(cards, output_path)
            export_csv(cards, DEFAULT_CSV)
            params = {
                "brief": str(brief_path.relative_to(ROOT)),
                "seed": str(seed_path.relative_to(ROOT)) if seed_path else "",
                "max_results": str(max_results),
                "load": "1",
                "view": view_mode,
                "status": status_filter,
                "evidence": evidence_filter,
                "location": location_filter,
                "requirement": requirement_filter,
                "queue": queue_index,
                "message": f"Generated {len(cards)} candidate cards.",
            }
            self.redirect("/", params)
        except Exception as exc:  # pragma: no cover - surfaced in UI
            self.redirect(
                "/",
                {
                    "brief": str(brief_path.relative_to(ROOT)),
                    "seed": str(seed_path.relative_to(ROOT)) if seed_path else "",
                    "max_results": str(max_results),
                    "load": "1",
                    "view": view_mode,
                    "status": status_filter,
                    "evidence": evidence_filter,
                    "location": location_filter,
                    "requirement": requirement_filter,
                    "queue": queue_index,
                    "error": str(exc),
                },
            )

    def handle_review(self, form: dict[str, str]) -> None:
        brief_path = self.resolve_path(form.get("brief", ""), DEFAULT_BRIEF)
        seed_path = self.resolve_path(form.get("seed", ""), DEFAULT_SEED) if form.get("seed") else None
        max_results = form.get("max_results", str(DEFAULT_MAX_RESULTS))
        view_mode = form.get("view", "cards")
        status_filter = normalize_status_filter(form.get("status_filter", "all"))
        evidence_filter = normalize_evidence_filter(form.get("evidence_filter", "all"))
        location_filter = normalize_location_filter(form.get("location_filter", "all"))
        requirement_filter = normalize_requirement_filter(form.get("requirement_filter", "all"))
        queue_index = int(form.get("queue_index", "0"))
        try:
            cards = load_cards(DEFAULT_OUTPUT)
            update_card_status(cards, form["candidate_id"], form["status"])
            save_cards(cards, DEFAULT_OUTPUT)
            export_csv(cards, DEFAULT_CSV)
            self.redirect(
                "/",
                {
                    "brief": str(brief_path.relative_to(ROOT)),
                    "seed": str(seed_path.relative_to(ROOT)) if seed_path else "",
                    "max_results": max_results,
                    "load": "1",
                    "view": view_mode,
                    "status": status_filter,
                    "evidence": evidence_filter,
                    "location": location_filter,
                    "requirement": requirement_filter,
                    "queue": str(queue_index + 1 if view_mode == "queue" else queue_index),
                    "message": f"Updated {form['candidate_id']} to {form['status']}.",
                },
            )
        except Exception as exc:  # pragma: no cover - surfaced in UI
            self.redirect(
                "/",
                {
                    "brief": str(brief_path.relative_to(ROOT)),
                    "seed": str(seed_path.relative_to(ROOT)) if seed_path else "",
                    "max_results": max_results,
                    "load": "1",
                    "view": view_mode,
                    "status": status_filter,
                    "evidence": evidence_filter,
                    "location": location_filter,
                    "requirement": requirement_filter,
                    "queue": str(queue_index),
                    "error": str(exc),
                },
            )

    def handle_export(self, query: dict[str, list[str]]) -> None:
        output_path = self.resolve_path(query.get("output", [str(DEFAULT_OUTPUT.relative_to(ROOT))])[-1], DEFAULT_OUTPUT)
        cards = load_cards(output_path)
        csv_text = cards_to_csv_text(cards).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="candidates.csv"')
        self.send_header("Content-Length", str(len(csv_text)))
        self.end_headers()
        self.wfile.write(csv_text)

    def resolve_path(self, raw_value: str, default: Path) -> Path:
        if not raw_value:
            return default
        candidate = (ROOT / raw_value).resolve()
        try:
            candidate.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError("Path must stay within the project directory") from exc
        return candidate

    def redirect(self, path: str, params: dict[str, str]) -> None:
        cleaned = {key: value for key, value in params.items() if value}
        location = path if not cleaned else f"{path}?{urlencode(cleaned, quote_via=quote)}"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local recruiting shortlist web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    server = ThreadingHTTPServer((args.host, args.port), RecruitingHandler)
    print(f"Serving recruiting tool at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
