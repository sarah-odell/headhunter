#!/usr/bin/env python3
"""Lightweight AI-assisted recruiting tool for generating candidate shortlists."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from io import StringIO
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


@dataclass
class CandidateCard:
    id: str
    name: str
    headline: str
    source_url: str
    evidence_links: list[str]
    must_have_hits: list[str]
    nice_to_have_hits: list[str]
    fit_score: float
    rationale: str
    status: str
    outreach_draft: str


def load_role_brief(path: Path) -> dict:
    data = json.loads(path.read_text())
    required = {"role_name", "company", "must_haves", "nice_to_haves", "queries", "outreach_context"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"Role brief missing keys: {sorted(missing)}")
    return data


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def ddg_search(query: str, max_results: int = 10) -> list[dict]:
    """DuckDuckGo HTML scraping without third-party dependencies."""
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=20) as response:  # nosec B310
        html = response.read().decode("utf-8", errors="ignore")

    links = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.S)
    snippets = re.findall(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', html, flags=re.S)

    results: list[dict] = []
    for idx, (href, title_html) in enumerate(links[:max_results]):
        title = unescape(_strip_tags(title_html)).strip()
        snippet_raw = snippets[idx] if idx < len(snippets) else ""
        snippet = unescape(_strip_tags(snippet_raw)).strip()
        results.append({"title": title, "url": href, "snippet": snippet})
    return results


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def extract_name(title: str) -> str:
    for separator in (" - ", " | ", " — "):
        if separator in title:
            first, second = title.split(separator, 1)
            if 1 <= len(first.split()) <= 4:
                return first.strip()
            if 1 <= len(second.split()) <= 4:
                return second.strip()
    return title[:80]


def score_candidate(text: str, must_haves: Iterable[str], nice_to_haves: Iterable[str]) -> tuple[list[str], list[str], float]:
    must_haves = list(must_haves)
    nice_to_haves = list(nice_to_haves)
    normalized = normalize_text(text)
    must_hits = [item for item in must_haves if normalize_text(item) in normalized]
    nice_hits = [item for item in nice_to_haves if normalize_text(item) in normalized]

    must_score = len(must_hits) / max(1, len(must_haves))
    nice_score = len(nice_hits) / max(1, len(nice_to_haves))
    fit_score = (0.75 * must_score) + (0.25 * nice_score)
    return must_hits, nice_hits, round(fit_score, 3)


def generate_outreach(candidate_name: str, role_brief: dict, must_hits: list[str], nice_hits: list[str], source_url: str) -> str:
    strongest = must_hits[:2] + nice_hits[:1]
    proof = ", ".join(strongest) if strongest else "your background"
    return (
        f"Hi {candidate_name},\n\n"
        f"I came across your profile while sourcing for {role_brief['company']}'s {role_brief['role_name']} role. "
        f"Your experience with {proof} looked highly relevant, especially based on what I saw here: {source_url}.\n\n"
        "If you're open to a brief intro conversation, I'd love to share more context on the role and team.\n\n"
        "Best,\nRecruiting Team"
    )


def to_status(score: float) -> str:
    if score >= 0.65:
        return "shortlist"
    if score >= 0.40:
        return "hold"
    return "reject"


def _cards_from_results(role_brief: dict, results: list[dict]) -> list[CandidateCard]:
    dedup: dict[str, CandidateCard] = {}
    for item in results:
        text_blob = f"{item['title']} {item.get('snippet', '')}"
        must_hits, nice_hits, score = score_candidate(text_blob, role_brief["must_haves"], role_brief["nice_to_haves"])
        name = extract_name(item["title"])
        identity = hashlib.sha1(f"{name}|{item['url']}".encode()).hexdigest()[:8]
        rationale = (
            f"Matched must-haves: {must_hits if must_hits else 'none'}; "
            f"nice-to-haves: {nice_hits if nice_hits else 'none'}."
        )
        candidate = CandidateCard(
            id=identity,
            name=name,
            headline=item["title"],
            source_url=item["url"],
            evidence_links=[item["url"]],
            must_have_hits=must_hits,
            nice_to_have_hits=nice_hits,
            fit_score=score,
            rationale=rationale,
            status=to_status(score),
            outreach_draft=generate_outreach(name, role_brief, must_hits, nice_hits, item["url"]),
        )
        if identity not in dedup or dedup[identity].fit_score < candidate.fit_score:
            dedup[identity] = candidate
    return sorted(dedup.values(), key=lambda c: c.fit_score, reverse=True)


def build_candidates(role_brief: dict, max_results_per_query: int = 10, seed_results_path: Path | None = None) -> list[CandidateCard]:
    if seed_results_path:
        raw = json.loads(seed_results_path.read_text())
        return _cards_from_results(role_brief, raw)

    merged_results: list[dict] = []
    for query in role_brief["queries"]:
        merged_results.extend(ddg_search(query, max_results=max_results_per_query))
    return _cards_from_results(role_brief, merged_results)


def save_cards(cards: list[CandidateCard], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([asdict(c) for c in cards], indent=2))


def load_cards(path: Path) -> list[CandidateCard]:
    raw = json.loads(path.read_text())
    return [CandidateCard(**row) for row in raw]


def cards_to_csv_text(cards: list[CandidateCard]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "id",
            "name",
            "headline",
            "source_url",
            "must_have_hits",
            "nice_to_have_hits",
            "fit_score",
            "status",
            "rationale",
            "outreach_draft",
        ],
    )
    writer.writeheader()
    for card in cards:
        row = asdict(card)
        row["must_have_hits"] = "; ".join(card.must_have_hits)
        row["nice_to_have_hits"] = "; ".join(card.nice_to_have_hits)
        row.pop("evidence_links", None)
        writer.writerow(row)
    return buffer.getvalue()


def export_csv(cards: list[CandidateCard], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(cards_to_csv_text(cards))


def update_card_status(cards: list[CandidateCard], candidate_id: str, status: str) -> None:
    valid = {"shortlist", "hold", "reject"}
    if status not in valid:
        raise ValueError(f"Status must be one of {sorted(valid)}")

    for card in cards:
        if card.id == candidate_id:
            card.status = status
            return

    raise ValueError(f"Candidate id {candidate_id} not found")


def run_cmd(args: argparse.Namespace) -> None:
    role_brief = load_role_brief(Path(args.brief))
    seed_path = Path(args.seed_results) if args.seed_results else None
    cards = build_candidates(
        role_brief,
        max_results_per_query=args.max_results_per_query,
        seed_results_path=seed_path,
    )
    save_cards(cards, Path(args.output))
    print(f"Generated {len(cards)} candidate cards in {args.output}")


def review_cmd(args: argparse.Namespace) -> None:
    output = Path(args.output)
    cards = load_cards(output)
    update_card_status(cards, args.id, args.status)
    save_cards(cards, output)
    print(f"Updated {args.id} => {args.status}")


def export_cmd(args: argparse.Namespace) -> None:
    cards = load_cards(Path(args.output))
    export_csv(cards, Path(args.csv))
    print(f"Exported {len(cards)} candidates to {args.csv}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recruiting shortlist tool")
    sub = parser.add_subparsers(required=True)

    run = sub.add_parser("run", help="Search and score candidate leads")
    run.add_argument("--brief", required=True, help="Path to role brief JSON")
    run.add_argument("--output", default="data/candidates.json", help="Output JSON path")
    run.add_argument("--max-results-per-query", type=int, default=10)
    run.add_argument("--seed-results", help="Optional path to local JSON search results")
    run.set_defaults(func=run_cmd)

    review = sub.add_parser("review", help="Update shortlist/hold/reject status")
    review.add_argument("--output", default="data/candidates.json")
    review.add_argument("--id", required=True)
    review.add_argument("--status", required=True)
    review.set_defaults(func=review_cmd)

    exp = sub.add_parser("export", help="Export cards to CSV")
    exp.add_argument("--output", default="data/candidates.json")
    exp.add_argument("--csv", default="data/candidates.csv")
    exp.set_defaults(func=export_cmd)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
