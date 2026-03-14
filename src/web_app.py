#!/usr/bin/env python3
"""Local web UI for the recruiting shortlist tool."""

from __future__ import annotations

import argparse
import json
from html import escape
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
STATUS_OPTIONS = ("shortlist", "hold", "reject")


def available_briefs() -> list[Path]:
    return sorted((ROOT / "role_briefs").glob("*.json"))


def status_counts(cards: list) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_OPTIONS}
    for card in cards:
        counts[card.status] = counts.get(card.status, 0) + 1
    return counts


def normalize_status_filter(raw_value: str) -> str:
    return raw_value if raw_value in {"all", *STATUS_OPTIONS} else "all"


def render_page(
    brief_path: Path,
    output_path: Path,
    csv_path: Path,
    seed_path: Path | None,
    max_results: int,
    cards: list,
    view_mode: str = "cards",
    status_filter: str = "all",
    message: str = "",
    error: str = "",
) -> str:
    brief = load_role_brief(brief_path)
    counts = status_counts(cards)
    filtered_cards = [card for card in cards if status_filter == "all" or card.status == status_filter]
    location_targets = brief.get("location_targets", [])
    base_query = {
        "brief": str(brief_path.relative_to(ROOT)),
        "seed": str(seed_path.relative_to(ROOT)) if seed_path else "",
        "max_results": str(max_results),
    }
    role_options = []
    for option in available_briefs():
        selected = " selected" if option == brief_path else ""
        role_options.append(
            f'<option value="{escape(str(option.relative_to(ROOT)))}"{selected}>{escape(option.stem.replace("_", " ").title())}</option>'
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
    for card in filtered_cards:
        must_hits = "".join(f'<li>{escape(hit)}</li>' for hit in card.must_have_hits) or "<li>None</li>"
        nice_hits = "".join(f'<li>{escape(hit)}</li>' for hit in card.nice_to_have_hits) or "<li>None</li>"
        location_text = ", ".join(card.location_hits) if card.location_hits else "No London/Berlin evidence"
        evidence_links = "".join(
            f'<li><a href="{escape(link)}" target="_blank" rel="noreferrer">{escape(link)}</a></li>'
            for link in card.evidence_links
        )
        must_badges = "".join(f'<span class="match-badge">{escape(hit)}</span>' for hit in card.must_have_hits) or '<span class="match-empty">No must-have evidence</span>'
        nice_badges = "".join(f'<span class="match-badge subdued">{escape(hit)}</span>' for hit in card.nice_to_have_hits) or '<span class="match-empty">No supporting evidence</span>'
        status_form = "".join(
            f"""
            <button type="submit" name="status" value="{status}" class="status-button {'active' if card.status == status else ''}">
              {status.title()}
            </button>
            """
            for status in STATUS_OPTIONS
        )
        location_state = "Eligible" if card.location_eligible else "Outside target locations"
        location_class = "eligible" if card.location_eligible else "ineligible"
        candidate_cards.append(
            f"""
            <article class="candidate-card spotlight-card reveal">
              <div class="candidate-head">
                <div>
                  <div class="candidate-meta">
                    <span class="pill pill-status">{escape(card.status.title())}</span>
                    <span class="pill pill-location {location_class}">{escape(location_state)}</span>
                  </div>
                  <h3>{escape(card.name)}</h3>
                  <p class="headline">{escape(card.headline)}</p>
                </div>
                <div class="score-stack">
                  <span class="eyebrow-label">Fit score</span>
                  <div class="score-pill">{card.fit_score:.3f}</div>
                </div>
              </div>
              <div class="candidate-topline">
                <a class="ghost-link" href="{escape(card.source_url)}" target="_blank" rel="noreferrer">Open GitHub profile</a>
                <span class="candidate-location-note">{escape(location_text)}</span>
              </div>
              <div class="score-breakdown">
                <div class="breakdown-item">
                  <span class="eyebrow-label">Must-haves</span>
                  <strong>{card.must_have_score:.3f}</strong>
                </div>
                <div class="breakdown-item">
                  <span class="eyebrow-label">Nice-to-haves</span>
                  <strong>{card.nice_to_have_score:.3f}</strong>
                </div>
                <div class="breakdown-item">
                  <span class="eyebrow-label">Weighted total</span>
                  <strong>{card.fit_score:.3f}</strong>
                </div>
              </div>
              <div class="match-grid">
                <section class="match-panel">
                  <h4>Must-have matches</h4>
                  <div class="match-list">{must_badges}</div>
                </section>
                <section class="match-panel">
                  <h4>Nice-to-have signals</h4>
                  <div class="match-list">{nice_badges}</div>
                </section>
              </div>
              <details class="detail-panel">
                <summary>Evidence links</summary>
                <div class="detail-body">
                  <ul>{evidence_links}</ul>
                </div>
              </details>
              <details class="detail-panel">
                <summary>Outreach draft</summary>
                <div class="detail-body">
                  <pre>{escape(card.outreach_draft)}</pre>
                </div>
              </details>
              <form method="post" action="/review" class="review-form">
                <input type="hidden" name="candidate_id" value="{escape(card.id)}">
                <input type="hidden" name="brief" value="{escape(str(brief_path.relative_to(ROOT)))}">
                <input type="hidden" name="seed" value="{escape(str(seed_path.relative_to(ROOT))) if seed_path else ''}">
                <input type="hidden" name="max_results" value="{max_results}">
                <input type="hidden" name="view" value="{escape(view_mode)}">
                <input type="hidden" name="status_filter" value="{escape(status_filter)}">
                {status_form}
              </form>
            </article>
            """
        )
        candidate_rows.append(
            f"""
            <tr>
              <td>
                <div class="table-name">{escape(card.name)}</div>
                <a class="table-link" href="{escape(card.source_url)}" target="_blank" rel="noreferrer">GitHub</a>
              </td>
              <td>{escape(card.status.title())}</td>
              <td>{card.fit_score:.3f}</td>
              <td>{card.must_have_score:.3f}</td>
              <td>{card.nice_to_have_score:.3f}</td>
              <td>{escape(", ".join(card.location_hits) if card.location_hits else "No match")}</td>
              <td>{escape(", ".join(card.must_have_hits) if card.must_have_hits else "None")}</td>
              <td>{escape(", ".join(card.nice_to_have_hits) if card.nice_to_have_hits else "None")}</td>
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
    filter_label = "All candidates" if status_filter == "all" else f"{status_filter.title()} only"
    view_query = urlencode(base_query | {"status": status_filter})
    cards_view_html = (
        ''.join(candidate_cards)
        if candidate_cards
        else f'<div class="candidate-card empty-state spotlight-card"><p class="lede">No candidates in the {escape(filter_label.lower())} view yet.</p></div>'
    )
    table_view_html = (
        f"""
        <section class="table-shell spotlight-card reveal">
          <div class="section-head">
            <div>
              <p class="eyebrow">Candidate table</p>
              <h3>All candidates</h3>
            </div>
            <span class="pill">{len(filtered_cards)} rows</span>
          </div>
          <div class="table-wrap">
            <table class="candidate-table">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Status</th>
                  <th>Score</th>
                  <th>Must</th>
                  <th>Nice</th>
                  <th>Location</th>
                  <th>Must-haves</th>
                  <th>Nice-to-haves</th>
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
        else f'<div class="candidate-card empty-state spotlight-card"><p class="lede">No candidates available for the {escape(filter_label.lower())} table view yet.</p></div>'
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
    .hero {{
      position: relative;
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) minmax(360px, 0.7fr);
      gap: 24px;
      align-items: stretch;
      margin-bottom: 28px;
    }}
    .hero-copy,
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
    .hero-copy {{
      padding: 26px;
      min-height: 300px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .hero-copy h1 {{
      margin: 0 0 12px;
      font-size: clamp(2.4rem, 5vw, 4.5rem);
      line-height: 0.98;
      letter-spacing: -0.04em;
      font-weight: 600;
      max-width: 10ch;
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
      font-size: clamp(0.95rem, 1.1vw, 1.05rem);
      line-height: 1.6;
      color: var(--foreground-muted);
      max-width: 48ch;
      margin: 0 0 20px;
    }}
    .hero-stack {{
      display: grid;
      gap: 10px;
    }}
    .hero-stats {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .hero-stat {{
      padding: 12px 14px;
      border-radius: var(--radius-md);
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--border-default);
    }}
    .hero-stat strong {{
      display: block;
      margin-top: 6px;
      font-size: 1.15rem;
      font-weight: 600;
    }}
    .hero-stat span {{
      color: var(--foreground-muted);
      font-size: 12px;
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
    }}
    .summary-card {{
      padding: 20px;
      min-height: 128px;
      display: grid;
      gap: 8px;
      align-content: start;
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
    .view-tab.active {{
      background: rgba(94,106,210,0.18);
      color: var(--foreground);
      box-shadow: inset 0 0 0 1px rgba(94,106,210,0.25);
    }}
    .candidate-list {{
      display: grid;
      gap: 18px;
    }}
    .candidate-card {{
      padding: 24px;
    }}
    .candidate-head {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: start;
      margin-bottom: 14px;
    }}
    .candidate-head h3 {{
      font-size: clamp(1.35rem, 2vw, 1.7rem);
      margin-top: 12px;
      margin-bottom: 10px;
    }}
    .score-stack {{
      display: grid;
      justify-items: end;
      gap: 8px;
    }}
    .score-pill {{
      min-width: 84px;
      padding: 14px 16px;
      border-radius: 14px;
      text-align: center;
      background: linear-gradient(to bottom, rgba(94,106,210,0.9), rgba(94,106,210,0.7));
      color: #fff;
      font-size: 1.3rem;
      font-weight: 700;
      box-shadow: var(--shadow-accent);
    }}
    .candidate-topline {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
      flex-wrap: wrap;
    }}
    .candidate-location-note {{
      color: var(--foreground-muted);
      font-size: 14px;
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
    .match-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .match-badge,
    .match-empty {{
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 0 10px;
      border-radius: 999px;
      background: rgba(94,106,210,0.12);
      border: 1px solid rgba(94,106,210,0.22);
      color: var(--foreground);
      font-size: 13px;
    }}
    .match-badge.subdued {{
      background: rgba(255,255,255,0.05);
      border-color: var(--border-default);
      color: var(--foreground-muted);
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
    }}
    .table-shell {{
      padding: 20px;
    }}
    .table-wrap {{
      overflow-x: auto;
      border-radius: 14px;
      border: 1px solid var(--border-default);
      background: rgba(8, 8, 12, 0.72);
    }}
    .candidate-table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 920px;
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
    ul {{
      margin: 0;
      padding-left: 18px;
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
    }}
    .status-button.active {{
      background: var(--accent);
      color: #fff;
      box-shadow: var(--shadow-accent);
    }}
    .banner {{
      margin-bottom: 16px;
      padding: 12px 14px;
      border-radius: 12px;
      font: 600 14px/1.4 "Inter", system-ui, sans-serif;
      border: 1px solid transparent;
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
      .hero-copy,
      .control-panel,
      .toolbar,
      .brief-card,
      .candidate-card {{
        padding: 20px;
      }}
      .hero-copy h1 {{
        font-size: clamp(2.6rem, 16vw, 4.2rem);
      }}
      .hero-stats,
      .summary-grid,
      .score-breakdown,
      .match-grid {{
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

    <section class="hero" id="hero">
      <div class="hero-copy spotlight-card">
        <p class="eyebrow">Founder’s Associate Challenge</p>
        <div class="hero-stack">
          <h1>Recruiting <span class="hero-gradient">Shortlist</span></h1>
          <p class="lede">
            GitHub-first sourcing for HASH engineering candidates, with weighted fit scoring and a London/Berlin gate.
          </p>
        </div>
        <div class="hero-stats">
          <article class="hero-stat">
            <span>Source</span>
            <strong>GitHub</strong>
          </article>
          <article class="hero-stat">
            <span>Location gate</span>
            <strong>{escape(location_label)}</strong>
          </article>
        </div>
      </div>
      <form class="control-panel spotlight-card" method="post" action="/run">
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
          <label>
            Role brief
            <select name="brief">{''.join(role_options)}</select>
          </label>
          <label>
            Max results per query
            <input type="number" name="max_results" min="1" max="25" value="{max_results}">
          </label>
          <label class="checkbox">
            <input type="checkbox" name="use_seed"{seed_checked}>
            Use local seed results for deterministic demo data
          </label>
          <label>
            Seed results path
            <input type="text" name="seed" value="{seed_value}" placeholder="data/sample_search_results.json">
          </label>
          <div class="button-row">
            <button class="primary" type="submit">Run GitHub sourcing</button>
            <a class="secondary" href="/export?{urlencode({'output': str(output_path.relative_to(ROOT)), 'csv': str(csv_path.relative_to(ROOT))})}">Export current CSV</a>
          </div>
        </div>
      </form>
    </section>

    <section class="workspace">
      <section class="toolbar spotlight-card">
        <div class="toolbar-copy">
          <p class="eyebrow">Role scope</p>
          <h2>{escape(brief['company'])} / {escape(brief['role_name'])}</h2>
          <p>GitHub-first sourcing with weighted scoring and a hard London/Berlin screen.</p>
        </div>
        <div class="toolbar-side">
          <div class="toolbar-pills">
            <span class="pill">{len(filtered_cards)} visible / {len(cards)} total</span>
            <span class="pill">{escape(location_label)}</span>
            <span class="pill">{escape(filter_label)}</span>
          </div>
          <nav class="view-switcher" aria-label="Candidate views">
            <a class="view-tab {cards_tab_class}" href="/?{view_query}&view=cards">Cards</a>
            <a class="view-tab {table_tab_class}" href="/?{view_query}&view=table">Table</a>
          </nav>
          <p class="toolbar-note">Must-haves drive score. Missing London/Berlin evidence rejects.</p>
        </div>
      </section>

      <section class="summary-grid">
        {summary_cards}
        <a class="summary-card spotlight-card workflow-filter {'active' if status_filter == 'all' else ''}" href="/?{urlencode(base_query | {'view': view_mode, 'status': 'all'})}">
          <span class="eyebrow-label">Top score</span>
          <strong>{max((card.fit_score for card in filtered_cards), default=0):.3f}</strong>
          <span class="summary-caption">Showing {escape(filter_label.lower())}</span>
        </a>
      </section>

      <section class="workspace-grid">
        <section class="candidate-list">
          {cards_view_html if view_mode == "cards" else table_view_html}
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
        max_results = int(query.get("max_results", ["20"])[-1])
        view_mode = query.get("view", ["cards"])[-1]
        status_filter = normalize_status_filter(query.get("status", ["all"])[-1])
        cards = load_cards(output_path) if output_path.exists() else []
        page = render_page(
            brief_path=brief_path,
            output_path=output_path,
            csv_path=csv_path,
            seed_path=seed_path,
            max_results=max_results,
            cards=cards,
            view_mode=view_mode if view_mode in {"cards", "table"} else "cards",
            status_filter=status_filter,
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
        max_results = int(form.get("max_results", "20"))
        view_mode = form.get("view", "cards")
        status_filter = normalize_status_filter(form.get("status_filter", "all"))

        try:
            brief = load_role_brief(brief_path)
            cards = build_candidates(brief, max_results_per_query=max_results, seed_results_path=seed_path)
            save_cards(cards, output_path)
            export_csv(cards, DEFAULT_CSV)
            params = {
                "brief": str(brief_path.relative_to(ROOT)),
                "seed": str(seed_path.relative_to(ROOT)) if seed_path else "",
                "max_results": str(max_results),
                "view": view_mode,
                "status": status_filter,
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
                    "view": view_mode,
                    "status": status_filter,
                    "error": str(exc),
                },
            )

    def handle_review(self, form: dict[str, str]) -> None:
        brief_path = self.resolve_path(form.get("brief", ""), DEFAULT_BRIEF)
        seed_path = self.resolve_path(form.get("seed", ""), DEFAULT_SEED) if form.get("seed") else None
        max_results = form.get("max_results", "20")
        view_mode = form.get("view", "cards")
        status_filter = normalize_status_filter(form.get("status_filter", "all"))
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
                    "view": view_mode,
                    "status": status_filter,
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
                    "view": view_mode,
                    "status": status_filter,
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
