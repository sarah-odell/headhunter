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


def render_page(
    brief_path: Path,
    output_path: Path,
    csv_path: Path,
    seed_path: Path | None,
    max_results: int,
    cards: list,
    message: str = "",
    error: str = "",
) -> str:
    brief = load_role_brief(brief_path)
    counts = status_counts(cards)
    role_options = []
    for option in available_briefs():
        selected = " selected" if option == brief_path else ""
        role_options.append(
            f'<option value="{escape(str(option.relative_to(ROOT)))}"{selected}>{escape(option.stem.replace("_", " ").title())}</option>'
        )

    summary_cards = "".join(
        f"""
        <div class="metric">
          <span class="metric-label">{status.title()}</span>
          <strong>{counts.get(status, 0)}</strong>
        </div>
        """
        for status in STATUS_OPTIONS
    )

    candidate_cards = []
    for card in cards:
        must_hits = "".join(f'<li>{escape(hit)}</li>' for hit in card.must_have_hits) or "<li>None</li>"
        nice_hits = "".join(f'<li>{escape(hit)}</li>' for hit in card.nice_to_have_hits) or "<li>None</li>"
        evidence_links = "".join(
            f'<li><a href="{escape(link)}" target="_blank" rel="noreferrer">{escape(link)}</a></li>'
            for link in card.evidence_links
        )
        status_form = "".join(
            f"""
            <button type="submit" name="status" value="{status}" class="status-button {'active' if card.status == status else ''}">
              {status.title()}
            </button>
            """
            for status in STATUS_OPTIONS
        )
        candidate_cards.append(
            f"""
            <article class="candidate">
              <div class="candidate-header">
                <div>
                  <h3>{escape(card.name)}</h3>
                  <p class="headline">{escape(card.headline)}</p>
                </div>
                <div class="score-pill">{card.fit_score:.3f}</div>
              </div>
              <div class="meta-row">
                <span class="badge">{escape(card.status.title())}</span>
                <a href="{escape(card.source_url)}" target="_blank" rel="noreferrer">Source</a>
              </div>
              <p class="rationale">{escape(card.rationale)}</p>
              <div class="grid">
                <section>
                  <h4>Must-have hits</h4>
                  <ul>{must_hits}</ul>
                </section>
                <section>
                  <h4>Nice-to-have hits</h4>
                  <ul>{nice_hits}</ul>
                </section>
                <section>
                  <h4>Evidence</h4>
                  <ul>{evidence_links}</ul>
                </section>
              </div>
              <section>
                <h4>Outreach draft</h4>
                <pre>{escape(card.outreach_draft)}</pre>
              </section>
              <form method="post" action="/review" class="review-form">
                <input type="hidden" name="candidate_id" value="{escape(card.id)}">
                <input type="hidden" name="brief" value="{escape(str(brief_path.relative_to(ROOT)))}">
                <input type="hidden" name="seed" value="{escape(str(seed_path.relative_to(ROOT))) if seed_path else ''}">
                <input type="hidden" name="max_results" value="{max_results}">
                {status_form}
              </form>
            </article>
            """
        )

    message_html = f'<div class="banner success">{escape(message)}</div>' if message else ""
    error_html = f'<div class="banner error">{escape(error)}</div>' if error else ""
    seed_value = escape(str(seed_path.relative_to(ROOT))) if seed_path else ""
    seed_checked = " checked" if seed_path else ""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HASH Recruiting Tool</title>
  <style>
    :root {{
      --paper: #f7f2e8;
      --ink: #182028;
      --accent: #b64d2e;
      --accent-2: #1f6b5d;
      --card: #fffdf8;
      --border: #d7c8ae;
      --muted: #675f54;
      --shadow: 0 16px 40px rgba(24, 32, 40, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(182, 77, 46, 0.15), transparent 30%),
        linear-gradient(180deg, #efe4cf 0%, var(--paper) 36%, #fdfaf4 100%);
    }}
    a {{ color: var(--accent-2); }}
    .shell {{
      max-width: 1160px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .hero {{
      display: grid;
      gap: 20px;
      grid-template-columns: 1.2fr 0.8fr;
      align-items: start;
      margin-bottom: 28px;
    }}
    .panel, .candidate {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: var(--shadow);
    }}
    .hero-copy {{
      padding: 26px;
    }}
    .eyebrow {{
      text-transform: uppercase;
      letter-spacing: 0.14em;
      font-size: 12px;
      color: var(--accent);
      margin: 0 0 10px;
    }}
    h1, h2, h3, h4 {{
      margin: 0 0 10px;
      line-height: 1.1;
    }}
    h1 {{ font-size: clamp(2.2rem, 4vw, 4.4rem); max-width: 10ch; }}
    .lede, .headline, .rationale, li, p, label {{
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      line-height: 1.5;
    }}
    .lede {{ color: var(--muted); max-width: 62ch; }}
    .control-panel {{
      padding: 22px;
    }}
    .form-grid {{
      display: grid;
      gap: 14px;
    }}
    label {{ display: grid; gap: 6px; font-size: 14px; color: var(--muted); }}
    select, input[type="number"], input[type="text"] {{
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: #fff;
      font-size: 15px;
    }}
    .checkbox {{
      display: flex;
      gap: 10px;
      align-items: center;
    }}
    .checkbox input {{ width: auto; }}
    button {{
      cursor: pointer;
      border: 0;
      border-radius: 999px;
      padding: 12px 16px;
      font-weight: 600;
    }}
    .primary {{
      background: var(--ink);
      color: #fff;
    }}
    .secondary {{
      background: transparent;
      border: 1px solid var(--border);
      color: var(--ink);
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin: 18px 0 28px;
    }}
    .metric {{
      padding: 18px;
      background: rgba(255,255,255,0.72);
      border: 1px solid var(--border);
      border-radius: 14px;
      text-align: center;
    }}
    .metric-label {{
      display: block;
      margin-bottom: 8px;
      color: var(--muted);
      font: 600 12px/1.3 "Helvetica Neue", Helvetica, Arial, sans-serif;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .candidate-list {{
      display: grid;
      gap: 18px;
    }}
    .candidate {{
      padding: 22px;
    }}
    .candidate-header, .meta-row {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
    }}
    .score-pill, .badge {{
      border-radius: 999px;
      padding: 8px 12px;
      font: 700 13px/1 "Helvetica Neue", Helvetica, Arial, sans-serif;
    }}
    .score-pill {{
      background: #1f6b5d;
      color: #fff;
      min-width: 72px;
      text-align: center;
    }}
    .badge {{
      background: #efe4cf;
      color: var(--ink);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 18px;
      margin: 18px 0;
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
    }}
    pre {{
      white-space: pre-wrap;
      background: #f8f4ea;
      padding: 14px;
      border-radius: 14px;
      border: 1px solid var(--border);
      font: 14px/1.55 "SFMono-Regular", Menlo, monospace;
      margin: 0;
    }}
    .review-form {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }}
    .status-button {{
      background: #f1ecdf;
      color: var(--ink);
    }}
    .status-button.active {{
      background: var(--accent);
      color: #fff;
    }}
    .toolbar {{
      display: flex;
      gap: 12px;
      align-items: center;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }}
    .banner {{
      margin-bottom: 16px;
      padding: 12px 14px;
      border-radius: 12px;
      font: 600 14px/1.4 "Helvetica Neue", Helvetica, Arial, sans-serif;
    }}
    .success {{ background: #deefe8; color: #134d41; }}
    .error {{ background: #fbe2dc; color: #7a2410; }}
    @media (max-width: 920px) {{
      .hero, .grid, .summary {{
        grid-template-columns: 1fr;
      }}
      .candidate-header, .meta-row {{
        flex-direction: column;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    {message_html}
    {error_html}
    <section class="hero">
      <div class="panel hero-copy">
        <p class="eyebrow">Founder’s Associate Challenge</p>
        <h1>Recruiting Shortlist Workbench</h1>
        <p class="lede">
          Turn a HASH role brief into structured candidate cards with evidence, fit scoring,
          outreach drafts, and a review workflow. This local app runs on one machine and keeps
          the sourcing logic transparent enough to audit.
        </p>
      </div>
      <form class="panel control-panel" method="post" action="/run">
        <div class="form-grid">
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
          <button class="primary" type="submit">Run sourcing</button>
        </div>
      </form>
    </section>

    <section class="toolbar">
      <div><strong>{escape(brief['company'])}</strong> / {escape(brief['role_name'])}</div>
      <div>{len(cards)} candidates</div>
      <a class="secondary" href="/export?{urlencode({'output': str(output_path.relative_to(ROOT)), 'csv': str(csv_path.relative_to(ROOT))})}">Download CSV</a>
    </section>

    <section class="summary">
      {summary_cards}
      <div class="metric">
        <span class="metric-label">Top Score</span>
        <strong>{max((card.fit_score for card in cards), default=0):.3f}</strong>
      </div>
    </section>

    <section class="candidate-list">
      {''.join(candidate_cards) if candidate_cards else '<div class="panel hero-copy"><p class="lede">No candidate cards yet. Run sourcing to populate the shortlist.</p></div>'}
    </section>
  </main>
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
        max_results = int(query.get("max_results", ["10"])[-1])
        cards = load_cards(output_path) if output_path.exists() else []
        page = render_page(
            brief_path=brief_path,
            output_path=output_path,
            csv_path=csv_path,
            seed_path=seed_path,
            max_results=max_results,
            cards=cards,
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
        max_results = int(form.get("max_results", "10"))

        try:
            brief = load_role_brief(brief_path)
            cards = build_candidates(brief, max_results_per_query=max_results, seed_results_path=seed_path)
            save_cards(cards, output_path)
            export_csv(cards, DEFAULT_CSV)
            params = {
                "brief": str(brief_path.relative_to(ROOT)),
                "seed": str(seed_path.relative_to(ROOT)) if seed_path else "",
                "max_results": str(max_results),
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
                    "error": str(exc),
                },
            )

    def handle_review(self, form: dict[str, str]) -> None:
        brief_path = self.resolve_path(form.get("brief", ""), DEFAULT_BRIEF)
        seed_path = self.resolve_path(form.get("seed", ""), DEFAULT_SEED) if form.get("seed") else None
        max_results = form.get("max_results", "10")
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
