#!/usr/bin/env python3
"""Lightweight AI-assisted recruiting tool for generating candidate shortlists."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import ssl
from io import StringIO
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

NEGATION_PREFIXES = (
    "limited",
    "limited recent",
    "no",
    "not",
    "without",
    "lacks",
    "lack of",
)
SKILL_ALIASES = {
    "typescript": ["typescript", "ts"],
    "react": ["react", "reactjs", "react.js"],
    "frontend": ["frontend", "front-end", "ui", "react", "nextjs", "next.js", "tailwind", "html canvas"],
    "backend": ["backend", "back-end", "node", "nodejs", "node.js", "api", "server", "postgres", "postgresql"],
    "performance": ["performance", "performant", "optimization", "optimiz", "scalability", "scale"],
    "data structures": ["data structures", "algorithms"],
    "storage systems": ["storage systems", "databases", "database", "postgres", "postgresql", "redis"],
    "rust": ["rust"],
    "effect": ["effect"],
    "html canvas": ["html canvas", "canvas"],
    "graphics": ["graphics", "rendering", "visualization"],
    "animation": ["animation", "motion"],
    "material ui": ["material ui", "mui"],
    "base ui": ["base ui"],
    "ariakit": ["ariakit"],
    "panda": ["panda", "panda css", "pandacss"],
    "open source": ["open source", "oss"],
}
GITHUB_PROFILE_RE = re.compile(r"^https?://github\.com/([^/?#]+?)/?$", flags=re.I)
GITHUB_RESERVED_PATHS = {
    "about",
    "account",
    "apps",
    "collections",
    "contact",
    "customer-stories",
    "enterprise",
    "events",
    "explore",
    "features",
    "gist",
    "git-guides",
    "images",
    "issues",
    "login",
    "marketplace",
    "notifications",
    "orgs",
    "organizations",
    "pricing",
    "pulls",
    "readme",
    "search",
    "security",
    "sessions",
    "settings",
    "site",
    "sponsors",
    "team",
    "teams",
    "topics",
    "trending",
    "users",
}


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
    must_have_score: float
    nice_to_have_score: float
    rationale: str
    status: str
    location_hits: list[str]
    location_eligible: bool
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


def _build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ssl_context = _build_ssl_context()
    try:
        with urlopen(req, timeout=20, context=ssl_context) as response:  # nosec B310
            return response.read().decode("utf-8", errors="ignore")
    except HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError(
                "GitHub is rate-limiting the live search right now. Wait a minute and rerun, "
                "or use the seed dataset for the UI demo."
            ) from exc
        raise
    except URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise RuntimeError(
                "HTTPS certificate verification failed while requesting a public page. "
                "Install the project dependencies with `python3 -m pip install -r requirements.txt` "
                "to use the bundled CA certificates, then rerun the search."
            ) from exc
        raise


def github_user_search(query: str, max_results: int = 20) -> list[dict]:
    """GitHub user search scraping from embedded JSON results."""
    results: list[dict] = []
    page = 1

    while len(results) < max_results:
        search_url = f"https://github.com/search?q={quote_plus(query)}&type=users&p={page}"
        html = _fetch_html(search_url)
        payload = _extract_github_search_payload(html)
        page_results = payload.get("results", [])
        if not page_results:
            break

        for row in page_results:
            login = row.get("login")
            if not login:
                continue
            profile_url = f"https://github.com/{login}"
            name = row.get("name") or login
            bio = row.get("profile_bio") or ""
            location = row.get("location") or ""
            repos = row.get("repos")
            followers = row.get("followers")
            snippet_parts = [bio, location]
            if repos is not None:
                snippet_parts.append(f"{repos} public repos")
            if followers is not None:
                snippet_parts.append(f"{followers} followers")
            results.append(
                {
                    "title": f"{name} ({login}) · GitHub",
                    "url": profile_url,
                    "snippet": " | ".join(part for part in snippet_parts if part),
                    "profile_name": name,
                    "profile_location": location,
                    "headline_override": bio or f"{location} GitHub profile" if location else "GitHub profile",
                    "evidence_links": [profile_url, search_url],
                }
            )
            if len(results) >= max_results:
                break

        if len(page_results) < 10:
            break
        page += 1

    return results


def _extract_github_search_payload(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" data-target="react-app\.embeddedData">(\{.*?\})</script>',
        html,
        flags=re.S,
    )
    if not match:
        return {}
    data = json.loads(unescape(match.group(1)))
    return data.get("payload", {})


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.S)
    return match.group(1).strip() if match else ""


def is_github_profile_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return False
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 1:
        return False
    username = path_parts[0].lower()
    if username in GITHUB_RESERVED_PATHS:
        return False
    return True


def enrich_github_result(item: dict) -> dict:
    if not is_github_profile_url(item["url"]):
        return item

    html = _fetch_html(item["url"])
    full_name = _strip_tags(_first_match(r'<span class="p-name[^"]*"[^>]*>(.*?)</span>', html))
    username = _strip_tags(_first_match(r'<span class="p-nickname[^"]*"[^>]*>(.*?)</span>', html))
    bio = _strip_tags(_first_match(r'<div class="p-note[^"]*"[^>]*>(.*?)</div>', html))
    company = _strip_tags(_first_match(r'itemprop="worksFor"[^>]*aria-label="Organization:\s*([^"]+)"', html))
    location = _strip_tags(_first_match(r'itemprop="homeLocation"[^>]*aria-label="Home location:\s*([^"]+)"', html))
    website = _first_match(r'itemprop="url"[^>]*href="([^"]+)"', html)
    profile_meta = unescape(_first_match(r'<meta name="description" content="([^"]+)"', html))
    pinned_repos = _extract_pinned_repos(html, item["url"])

    display_name = full_name or username or item["title"]
    headline_parts = [part for part in (bio, company, location) if part]
    headline = " | ".join(headline_parts) if headline_parts else item["title"]

    text_parts = [item["title"], item.get("snippet", ""), full_name, username, bio, company, location, profile_meta]
    text_parts.extend(repo["text"] for repo in pinned_repos)
    enriched_item = dict(item)
    enriched_item["title"] = f"{display_name} ({username}) · GitHub" if full_name and username else item["title"]
    enriched_item["snippet"] = " ".join(part for part in text_parts if part)
    enriched_item["headline_override"] = headline
    evidence_links = [item["url"]]
    if website and website not in evidence_links:
        evidence_links.append(website)
    for repo in pinned_repos[:3]:
        if repo["url"] not in evidence_links:
            evidence_links.append(repo["url"])
    enriched_item["evidence_links"] = evidence_links
    enriched_item["profile_name"] = display_name
    enriched_item["profile_location"] = location
    enriched_item["pinned_repos"] = pinned_repos
    return enriched_item


def _extract_pinned_repos(html: str, profile_url: str) -> list[dict]:
    results: list[dict] = []
    owner = urlparse(profile_url).path.strip("/")
    for href, name, body in re.findall(
        r'class="Link mr-1 text-bold wb-break-word"[^>]*href="(/[^"]+)"[^>]*><span class="repo">(.*?)</span></a>(.*?)</article>',
        html,
        flags=re.S,
    ):
        repo_url = f"https://github.com{href}"
        description = _strip_tags(_first_match(r'<p class="pinned-item-desc[^"]*"[^>]*>(.*?)</p>', body))
        language = _strip_tags(_first_match(r'<span itemprop="programmingLanguage">(.*?)</span>', body))
        repo_text_parts = [name, description, language, owner]
        results.append(
            {
                "url": repo_url,
                "name": name,
                "text": " ".join(part for part in repo_text_parts if part),
            }
        )
    return results


def extract_name(title: str) -> str:
    github_match = re.match(r"^(.*?)\s*(?:\([^)]*\))?\s*·\s*GitHub$", title)
    if github_match:
        return github_match.group(1).strip()
    for separator in (" - ", " | ", " — "):
        if separator in title:
            first, second = title.split(separator, 1)
            if 1 <= len(first.split()) <= 4:
                return first.strip()
            if 1 <= len(second.split()) <= 4:
                return second.strip()
    return title[:80]


def score_candidate(text: str, must_haves: Iterable[str], nice_to_haves: Iterable[str]) -> tuple[list[str], list[str], float, float, float]:
    must_haves = list(must_haves)
    nice_to_haves = list(nice_to_haves)
    normalized = normalize_text(text)
    must_hits = [item for item in must_haves if _has_positive_signal(normalized, item)]
    nice_hits = [item for item in nice_to_haves if _has_positive_signal(normalized, item)]

    must_score = len(must_hits) / max(1, len(must_haves))
    nice_score = len(nice_hits) / max(1, len(nice_to_haves))
    fit_score = (0.75 * must_score) + (0.25 * nice_score)
    return must_hits, nice_hits, round(fit_score, 3), round(must_score, 3), round(nice_score, 3)


def _has_positive_signal(normalized_text: str, keyword: str) -> bool:
    alias_terms = SKILL_ALIASES.get(normalize_text(keyword), [keyword])
    for term in alias_terms:
        normalized_keyword = normalize_text(term)
        if normalized_keyword not in normalized_text:
            continue

        for match in re.finditer(re.escape(normalized_keyword), normalized_text):
            prefix = normalized_text[max(0, match.start() - 24):match.start()].strip()
            if any(prefix.endswith(negation) for negation in NEGATION_PREFIXES):
                continue
            return True
    return False


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


def score_location(text: str, location_targets: Iterable[str]) -> list[str]:
    normalized = normalize_text(text)
    return [target for target in location_targets if _has_positive_signal(normalized, target)]


def to_status(score: float, location_eligible: bool = True) -> str:
    if not location_eligible:
        return "reject"
    if score >= 0.55:
        return "shortlist"
    if score >= 0.25:
        return "hold"
    return "reject"


def _cards_from_results(role_brief: dict, results: list[dict], enrich_profiles: bool = True) -> list[CandidateCard]:
    dedup: dict[str, CandidateCard] = {}
    location_targets = role_brief.get("location_targets", [])
    for item in results:
        enriched_item = enrich_github_result(item) if enrich_profiles else dict(item)
        text_blob = f"{enriched_item['title']} {enriched_item.get('snippet', '')}"
        must_hits, nice_hits, score, must_score, nice_score = score_candidate(
            text_blob,
            role_brief["must_haves"],
            role_brief["nice_to_haves"],
        )
        location_hits = score_location(text_blob, location_targets)
        location_eligible = bool(location_hits) if location_targets else True
        name = enriched_item.get("profile_name") or extract_name(enriched_item["title"])
        identity = hashlib.sha1(f"{name}|{enriched_item['url']}".encode()).hexdigest()[:8]
        rationale = (
            f"Matched must-haves: {must_hits if must_hits else 'none'}; "
            f"nice-to-haves: {nice_hits if nice_hits else 'none'}; "
            f"must-have score: {must_score:.3f}; "
            f"nice-to-have score: {nice_score:.3f}; "
            f"location: {location_hits if location_hits else 'no London/Berlin evidence'}."
        )
        candidate = CandidateCard(
            id=identity,
            name=name,
            headline=enriched_item.get("headline_override") or enriched_item["title"],
            source_url=enriched_item["url"],
            evidence_links=enriched_item.get("evidence_links", [enriched_item["url"]]),
            must_have_hits=must_hits,
            nice_to_have_hits=nice_hits,
            fit_score=score,
            must_have_score=must_score,
            nice_to_have_score=nice_score,
            rationale=rationale,
            status=to_status(score, location_eligible=location_eligible),
            location_hits=location_hits,
            location_eligible=location_eligible,
            outreach_draft=generate_outreach(name, role_brief, must_hits, nice_hits, enriched_item["url"]),
        )
        if identity not in dedup or dedup[identity].fit_score < candidate.fit_score:
            dedup[identity] = candidate
    return sorted(dedup.values(), key=lambda c: c.fit_score, reverse=True)


def build_candidates(role_brief: dict, max_results_per_query: int = 20, seed_results_path: Path | None = None) -> list[CandidateCard]:
    if seed_results_path:
        raw = json.loads(seed_results_path.read_text())
        return _cards_from_results(role_brief, raw, enrich_profiles=False)

    merged_results: list[dict] = []
    for query in role_brief["queries"]:
        merged_results.extend(github_user_search(query, max_results=max_results_per_query))
    return _cards_from_results(role_brief, merged_results, enrich_profiles=True)


def save_cards(cards: list[CandidateCard], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([asdict(c) for c in cards], indent=2))


def load_cards(path: Path) -> list[CandidateCard]:
    raw = json.loads(path.read_text())
    cards: list[CandidateCard] = []
    for row in raw:
        normalized = dict(row)
        normalized.setdefault("evidence_links", [normalized.get("source_url", "")] if normalized.get("source_url") else [])
        normalized.setdefault("must_have_hits", [])
        normalized.setdefault("nice_to_have_hits", [])
        normalized.setdefault("must_have_score", 0.0)
        normalized.setdefault("nice_to_have_score", 0.0)
        normalized.setdefault("location_hits", [])
        normalized.setdefault("location_eligible", True)
        cards.append(CandidateCard(**normalized))
    return cards


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
            "must_have_score",
            "nice_to_have_score",
            "location_hits",
            "location_eligible",
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
        row["location_hits"] = "; ".join(card.location_hits)
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
    run.add_argument("--max-results-per-query", type=int, default=20)
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
