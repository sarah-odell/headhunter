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
from typing import Any, Iterable
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
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", flags=re.I)
SOURCE_STRENGTHS = {
    "search": 0.3,
    "web": 0.45,
    "profile": 0.6,
    "repo": 1.0,
    "website": 0.55,
}
SOURCE_LABELS = {
    "search": "search",
    "web": "public web",
    "profile": "profile",
    "repo": "repo",
    "website": "linked site",
}
DEFAULT_MUST_CATEGORY_WEIGHT = 0.85
DEFAULT_NICE_CATEGORY_WEIGHT = 0.15
HTML_CACHE: dict[str, str] = {}
JSON_CACHE: dict[str, dict | list] = {}


@dataclass
class CandidateCard:
    id: str
    name: str
    headline: str
    source_url: str
    email: str
    found_via: list[str]
    evidence_links: list[str]
    evidence_records: list[dict[str, Any]]
    evidence_count: int
    evidence_density: str
    must_have_hits: list[str]
    nice_to_have_hits: list[str]
    fit_score: float
    must_have_score: float
    nice_to_have_score: float
    confidence_score: float
    rationale: str
    status: str
    location_hits: list[str]
    location_eligible: bool
    eligibility_reason: str
    requirement_scores: dict[str, float]
    requirement_sources: dict[str, str]
    requirement_evidence: dict[str, str]
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
    if url in HTML_CACHE:
        return HTML_CACHE[url]
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ssl_context = _build_ssl_context()
    try:
        with urlopen(req, timeout=20, context=ssl_context) as response:  # nosec B310
            html = response.read().decode("utf-8", errors="ignore")
            HTML_CACHE[url] = html
            return html
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


def _fetch_json(url: str) -> dict | list:
    if url in JSON_CACHE:
        return JSON_CACHE[url]
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    ssl_context = _build_ssl_context()
    try:
        with urlopen(req, timeout=20, context=ssl_context) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))
            JSON_CACHE[url] = payload
            return payload
    except Exception:
        return {}


def _make_evidence_record(
    source_type: str,
    url: str,
    label: str,
    snippet: str,
    text: str,
    matched_requirements: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "url": url,
        "label": label,
        "snippet": snippet.strip(),
        "text": text.strip(),
        "matched_requirements": matched_requirements or [],
        "strength": SOURCE_STRENGTHS.get(source_type, 0.0),
    }


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
                    "found_via": ["GitHub user search"],
                    "evidence_records": [
                        _make_evidence_record(
                            "search",
                            search_url,
                            "GitHub user search",
                            " | ".join(part for part in snippet_parts if part),
                            " ".join(part for part in (name, login, bio, location, " ".join(snippet_parts)) if part),
                        )
                    ],
                    "search_text": " ".join(part for part in (name, login, bio, location, " ".join(snippet_parts)) if part),
                }
            )
            if len(results) >= max_results:
                break

        if len(page_results) < 10:
            break
        page += 1

    return results


def duckduckgo_profile_search(query: str, max_results: int = 10) -> list[dict]:
    html = _fetch_html(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
    results: list[dict] = []
    for href, title, snippet in re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__url"[^>]*>.*?</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        html,
        flags=re.S,
    ):
        target = _unwrap_duckduckgo_url(unescape(href))
        if not is_github_profile_url(target):
            continue
        clean_title = _strip_tags(unescape(title))
        clean_snippet = _strip_tags(unescape(snippet))
        results.append(
            {
                "title": clean_title or f"{target} · GitHub",
                "url": target,
                "snippet": clean_snippet,
                "profile_name": extract_name(clean_title) if clean_title else urlparse(target).path.strip("/"),
                "profile_location": "",
                "headline_override": clean_snippet or "GitHub profile",
                "evidence_links": [target],
                "found_via": ["DuckDuckGo"],
                "evidence_records": [
                    _make_evidence_record(
                        "web",
                        "https://html.duckduckgo.com/html/",
                        "DuckDuckGo public web search",
                        clean_snippet,
                        " ".join(part for part in (clean_title, clean_snippet) if part),
                    )
                ],
                "search_text": " ".join(part for part in (clean_title, clean_snippet) if part),
            }
        )
        if len(results) >= max_results:
            break
    return results


def _unwrap_duckduckgo_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) if target else url
    return url


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
    email = _extract_public_email(html)
    github_username = username or urlparse(item["url"]).path.strip("/")
    api_profile = _fetch_github_user_profile(github_username) if github_username else {}
    api_email = _normalize_email(api_profile.get("email", "")) if isinstance(api_profile, dict) else ""
    api_blog = _normalize_optional_text(api_profile.get("blog", "")) if isinstance(api_profile, dict) else ""
    if not website and api_blog:
        website = api_blog
    profile_meta = unescape(_first_match(r'<meta name="description" content="([^"]+)"', html))
    pinned_repos = _extract_pinned_repos(html, item["url"])
    listed_repos = _extract_repository_list(item["url"], limit=8)
    public_web_results = _search_public_web_context(full_name or username or item.get("profile_name", ""), github_username)
    website_text = ""

    display_name = full_name or username or item["title"]
    headline_parts = [part for part in (bio, company, location) if part]
    headline = " | ".join(headline_parts) if headline_parts else item["title"]

    text_parts = [item["title"], item.get("snippet", ""), full_name, username, bio, company, location, email, profile_meta]
    text_parts.extend(repo["text"] for repo in pinned_repos)
    text_parts.extend(repo["text"] for repo in listed_repos)
    text_parts.extend(result["text"] for result in public_web_results)
    enriched_item = dict(item)
    enriched_item["title"] = f"{display_name} ({username}) · GitHub" if full_name and username else item["title"]
    enriched_item["snippet"] = " ".join(part for part in text_parts if part)
    enriched_item["headline_override"] = headline
    evidence_links = [item["url"]]
    if website and website not in evidence_links:
        evidence_links.append(website)
    if not email:
        email = _choose_preferred_email(
            [
                email,
                api_email,
                _extract_email_from_github_repo_pages([repo["url"] for repo in [*pinned_repos, *listed_repos]]),
                _extract_email_from_url(website) if website else "",
            ]
        )
    if website:
        website_text = _extract_website_text(website)
    for repo in pinned_repos[:3]:
        if repo["url"] not in evidence_links:
            evidence_links.append(repo["url"])
    for repo in listed_repos[:3]:
        if repo["url"] not in evidence_links:
            evidence_links.append(repo["url"])
    for result in public_web_results[:2]:
        if result["url"] not in evidence_links:
            evidence_links.append(result["url"])
    evidence_records = list(item.get("evidence_records", []))
    profile_text = " ".join(part for part in (full_name, username, bio, company, location, profile_meta, email) if part)
    evidence_records.append(
        _make_evidence_record(
            "profile",
            item["url"],
            "GitHub profile",
            " | ".join(part for part in (bio, company, location) if part),
            profile_text,
        )
    )
    if website_text:
        evidence_records.append(
            _make_evidence_record(
                "website",
                website,
                "Linked website",
                website_text[:220],
                website_text,
            )
        )
    evidence_records.extend(repo["evidence_record"] for repo in pinned_repos[:3] if repo.get("evidence_record"))
    evidence_records.extend(repo["evidence_record"] for repo in listed_repos[:3] if repo.get("evidence_record"))
    evidence_records.extend(
        _make_evidence_record("web", result["url"], result["label"], result["snippet"], result["text"])
        for result in public_web_results[:2]
    )
    enriched_item["evidence_links"] = evidence_links
    enriched_item["found_via"] = sorted({*(item.get("found_via") or []), *(["Linked website"] if website else []), *(["Public web"] if public_web_results else [])})
    enriched_item["evidence_records"] = evidence_records
    enriched_item["profile_name"] = display_name
    enriched_item["profile_location"] = location
    enriched_item["public_email"] = email
    enriched_item["pinned_repos"] = pinned_repos
    enriched_item["listed_repos"] = listed_repos
    enriched_item["source_texts"] = {
        "search": item.get("search_text", item.get("snippet", "")),
        "profile": " ".join(part for part in (full_name, username, bio, company, location, profile_meta) if part),
        "repo": " ".join(repo["text"] for repo in [*pinned_repos, *listed_repos]),
        "website": website_text,
        "web": " ".join(result["text"] for result in public_web_results),
    }
    return enriched_item


def _extract_public_email(html: str) -> str:
    mailto = _first_match(r'href="mailto:([^"]+)"', html)
    if mailto:
        return _normalize_email(unquote(mailto))
    text_match = _first_match(r'aria-label="Email:\s*([^"]+)"', html)
    if text_match:
        return _normalize_email(unquote(text_match))
    visible_match = EMAIL_RE.search(_strip_tags(html))
    return _normalize_email(visible_match.group(0) if visible_match else "")


def _normalize_email(value: str) -> str:
    value = _normalize_optional_text(value)
    if value.startswith("mailto:"):
        value = value[7:]
    return value if EMAIL_RE.fullmatch(value) else ""


def _normalize_optional_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_noreply_email(value: str) -> bool:
    email = value.lower()
    return email.endswith("@users.noreply.github.com") or "noreply" in email


def _choose_preferred_email(candidates: Iterable[str]) -> str:
    normalized = [_normalize_email(candidate) for candidate in candidates if candidate]
    preferred = next((email for email in normalized if email and not _is_noreply_email(email)), "")
    return preferred or next((email for email in normalized if email), "")


def _fetch_github_user_profile(username: str) -> dict:
    response = _fetch_json(f"https://api.github.com/users/{quote_plus(username)}")
    return response if isinstance(response, dict) else {}


def _extract_email_from_url(url: str) -> str:
    try:
        html = _fetch_html(url)
    except Exception:
        return ""
    return _extract_public_email(html)


def _extract_website_text(url: str) -> str:
    try:
        html = _fetch_html(url)
    except Exception:
        return ""
    title = _strip_tags(_first_match(r"<title>(.*?)</title>", html))
    meta_description = unescape(_first_match(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html))
    body_text = re.sub(r"\s+", " ", _strip_tags(html)).strip()
    return " ".join(part for part in (title, meta_description, body_text[:2500]) if part)


def _extract_email_from_github_repo_pages(repo_urls: Iterable[str], limit: int = 2) -> str:
    emails: list[str] = []
    seen: set[str] = set()
    for repo_url in repo_urls:
        if repo_url in seen:
            continue
        seen.add(repo_url)
        try:
            html = _fetch_html(repo_url)
        except Exception:
            continue
        email = _extract_public_email(html)
        if email:
            emails.append(email)
        if len(seen) >= limit:
            break
    return _choose_preferred_email(emails)


def _search_public_web_context(name: str, username: str, limit: int = 2) -> list[dict[str, str]]:
    if not (name or username):
        return []
    query = " ".join(part for part in (name, username, "engineer") if part)
    html = _fetch_html(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
    results: list[dict[str, str]] = []
    for href, title, snippet in re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        html,
        flags=re.S,
    ):
        target = _unwrap_duckduckgo_url(unescape(href))
        if not target or "github.com" in urlparse(target).netloc.lower():
            continue
        clean_title = _strip_tags(unescape(title))
        clean_snippet = _strip_tags(unescape(snippet))
        combined = normalize_text(" ".join(part for part in (clean_title, clean_snippet) if part))
        if username and normalize_text(username) not in combined and name and normalize_text(name).split(" ")[0] not in combined:
            continue
        results.append(
            {
                "url": target,
                "label": "Public web result",
                "snippet": clean_snippet,
                "text": " ".join(part for part in (clean_title, clean_snippet) if part),
            }
        )
        if len(results) >= limit:
            break
    return results


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
        readme_summary = _extract_repo_page_summary(repo_url)
        repo_text_parts = [name, description, language, owner, readme_summary]
        results.append(
            {
                "url": repo_url,
                "name": name,
                "text": " ".join(part for part in repo_text_parts if part),
                "evidence_record": _make_evidence_record(
                    "repo",
                    repo_url,
                    f"Repo: {name}",
                    " | ".join(part for part in (description, language, readme_summary[:140]) if part),
                    " ".join(part for part in repo_text_parts if part),
                ),
            }
        )
    return results


def _extract_repository_list(profile_url: str, limit: int = 8) -> list[dict]:
    try:
        html = _fetch_html(f"{profile_url}?tab=repositories")
    except Exception:
        return []

    results: list[dict] = []
    owner = urlparse(profile_url).path.strip("/")
    entries = re.findall(
        r'<li class="[^"]*?public[^"]*?" itemprop="owns"[^>]*>(.*?)</li>\s*</li>?',
        html,
        flags=re.S,
    )
    if not entries:
        entries = re.findall(
            r'<li class="[^"]*?public[^"]*?" itemprop="owns"[^>]*>(.*?)</li>',
            html,
            flags=re.S,
        )

    for body in entries[:limit]:
        href = _first_match(r'<a href="(/[^"]+)" itemprop="name codeRepository"', body)
        name = _strip_tags(_first_match(r'itemprop="name codeRepository"[^>]*>\s*(.*?)</a>', body))
        if not href or not name:
            continue
        repo_url = f"https://github.com{href}"
        description = _strip_tags(_first_match(r'itemprop="description">\s*(.*?)\s*</p>', body))
        language = _strip_tags(_first_match(r'<span itemprop="programmingLanguage">(.*?)</span>', body))
        topics = re.findall(r'class="topic-tag[^"]*"[^>]*>\s*(.*?)\s*</a>', body, flags=re.S)
        topic_text = " ".join(_strip_tags(topic) for topic in topics)
        readme_summary = _extract_repo_page_summary(repo_url)
        repo_text_parts = [name, description, language, topic_text, owner, readme_summary]
        results.append(
            {
                "url": repo_url,
                "name": name,
                "text": " ".join(part for part in repo_text_parts if part),
                "evidence_record": _make_evidence_record(
                    "repo",
                    repo_url,
                    f"Repo: {name}",
                    " | ".join(part for part in (description, language, readme_summary[:140]) if part),
                    " ".join(part for part in repo_text_parts if part),
                ),
            }
        )
    return results


def _extract_repo_page_summary(repo_url: str) -> str:
    try:
        html = _fetch_html(repo_url)
    except Exception:
        return ""
    meta_description = unescape(_first_match(r'<meta name="description" content="([^"]+)"', html))
    readme = _strip_tags(_first_match(r'<article class="markdown-body[^"]*"[^>]*>(.*?)</article>', html))
    return " ".join(part for part in (meta_description, readme[:800]) if part)


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


def _match_source_strength(source_texts: dict[str, str], keyword: str) -> tuple[float, str]:
    best_score = 0.0
    best_source = ""
    for source_name, text in source_texts.items():
        if not text:
            continue
        normalized_text = normalize_text(text)
        if not _has_positive_signal(normalized_text, keyword):
            continue
        strength = SOURCE_STRENGTHS[source_name]
        if strength > best_score:
            best_score = strength
            best_source = SOURCE_LABELS[source_name]
    return best_score, best_source


def _match_requirement_from_records(
    evidence_records: list[dict[str, Any]],
    keyword: str,
) -> tuple[float, str, str]:
    best_score = 0.0
    best_source = ""
    best_snippet = ""
    for record in evidence_records:
        text = record.get("text", "")
        if not text:
            continue
        normalized_text = normalize_text(text)
        if not _has_positive_signal(normalized_text, keyword):
            continue
        strength = float(record.get("strength", SOURCE_STRENGTHS.get(record.get("source_type", ""), 0.0)))
        if strength > best_score:
            best_score = strength
            best_source = SOURCE_LABELS.get(record.get("source_type", ""), record.get("source_type", ""))
            best_snippet = record.get("snippet", "")[:220]
    return best_score, best_source, best_snippet


def _weighted_score(
    requirements: Iterable[str],
    weights: dict[str, float],
    source_texts: dict[str, str],
) -> tuple[list[str], float, dict[str, float], dict[str, str]]:
    hits: list[str] = []
    scores: dict[str, float] = {}
    sources: dict[str, str] = {}
    requirements = list(requirements)
    total_weight = sum(weights.get(item, 1.0) for item in requirements) or 1.0
    weighted_sum = 0.0

    for requirement in requirements:
        score, source = _match_source_strength(source_texts, requirement)
        scores[requirement] = round(score, 3)
        sources[requirement] = source
        weighted_sum += weights.get(requirement, 1.0) * score
        if score > 0:
            hits.append(requirement)

    return hits, round(weighted_sum / total_weight, 3), scores, sources


def _weighted_score_from_records(
    requirements: Iterable[str],
    weights: dict[str, float],
    evidence_records: list[dict[str, Any]],
) -> tuple[list[str], float, dict[str, float], dict[str, str], dict[str, str]]:
    hits: list[str] = []
    scores: dict[str, float] = {}
    sources: dict[str, str] = {}
    evidence_snippets: dict[str, str] = {}
    requirements = list(requirements)
    total_weight = sum(weights.get(item, 1.0) for item in requirements) or 1.0
    weighted_sum = 0.0

    for requirement in requirements:
        score, source, snippet = _match_requirement_from_records(evidence_records, requirement)
        scores[requirement] = round(score, 3)
        sources[requirement] = source
        evidence_snippets[requirement] = snippet
        weighted_sum += weights.get(requirement, 1.0) * score
        if score > 0:
            hits.append(requirement)

    return hits, round(weighted_sum / total_weight, 3), scores, sources, evidence_snippets


def score_candidate_with_evidence(
    role_brief: dict,
    source_texts: dict[str, str],
    evidence_records: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str], float, float, float, float, dict[str, float], dict[str, str], dict[str, str]]:
    must_weights = role_brief.get("must_have_weights", {})
    nice_weights = role_brief.get("nice_to_have_weights", {})
    if evidence_records:
        must_hits, must_score, must_requirement_scores, must_sources, must_evidence = _weighted_score_from_records(
            role_brief["must_haves"],
            must_weights,
            evidence_records,
        )
        nice_hits, nice_score, nice_requirement_scores, nice_sources, nice_evidence = _weighted_score_from_records(
            role_brief["nice_to_haves"],
            nice_weights,
            evidence_records,
        )
    else:
        must_hits, must_score, must_requirement_scores, must_sources = _weighted_score(
            role_brief["must_haves"],
            must_weights,
            source_texts,
        )
        nice_hits, nice_score, nice_requirement_scores, nice_sources = _weighted_score(
            role_brief["nice_to_haves"],
            nice_weights,
            source_texts,
        )
        must_evidence = {requirement: "" for requirement in role_brief["must_haves"]}
        nice_evidence = {requirement: "" for requirement in role_brief["nice_to_haves"]}
    fit_weight_must = float(role_brief.get("must_have_category_weight", DEFAULT_MUST_CATEGORY_WEIGHT))
    fit_weight_nice = float(role_brief.get("nice_to_have_category_weight", DEFAULT_NICE_CATEGORY_WEIGHT))
    fit_score = round((fit_weight_must * must_score) + (fit_weight_nice * nice_score), 3)
    requirement_scores = {**must_requirement_scores, **nice_requirement_scores}
    requirement_sources = {**must_sources, **nice_sources}
    requirement_evidence = {**must_evidence, **nice_evidence}
    confidence = compute_confidence(source_texts, requirement_scores)
    return must_hits, nice_hits, fit_score, must_score, nice_score, confidence, requirement_scores, requirement_sources, requirement_evidence


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


def compute_confidence(source_texts: dict[str, str], requirement_scores: dict[str, float]) -> float:
    confidence = 0.0
    if source_texts.get("profile"):
        confidence += 0.25
    if source_texts.get("repo"):
        confidence += 0.45
    if source_texts.get("website"):
        confidence += 0.15
    if source_texts.get("search"):
        confidence += 0.05
    strong_matches = sum(1 for score in requirement_scores.values() if score >= 1.0)
    medium_matches = sum(1 for score in requirement_scores.values() if 0.55 <= score < 1.0)
    confidence += min(0.1, strong_matches * 0.03)
    confidence += min(0.1, medium_matches * 0.02)
    return round(min(confidence, 1.0), 3)


def evidence_density_label(evidence_records: list[dict[str, Any]]) -> str:
    count = len(evidence_records)
    if count >= 7:
        return "high"
    if count >= 4:
        return "medium"
    return "low"


def generate_outreach(
    candidate_name: str,
    role_brief: dict,
    must_hits: list[str],
    nice_hits: list[str],
    source_url: str,
    requirement_evidence: dict[str, str],
    location_hits: list[str],
) -> str:
    strongest = must_hits[:2]
    supporting = nice_hits[:1]
    proof = ", ".join(strongest + supporting) if (strongest or supporting) else "your background"
    evidence_line = next((requirement_evidence.get(item, "") for item in strongest + supporting if requirement_evidence.get(item)), "")
    location_line = f" and your {', '.join(location_hits)} location" if location_hits else ""
    return (
        f"Hi {candidate_name},\n\n"
        f"I came across your profile while sourcing for {role_brief['company']}'s {role_brief['role_name']} role. "
        f"Your public work suggests a strong match on {proof}{location_line}.\n\n"
        f"What stood out was the evidence around {proof}: {evidence_line or source_url}.\n\n"
        f"If you're open to it, I'd be glad to share why this role could be a particularly strong fit at {role_brief['company']}.\n\n"
        "If you're open to a brief intro conversation, I'd love to share more context on the role and team.\n\n"
        "Best,\nHASH Recruiting Team"
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
        evidence_records = list(enriched_item.get("evidence_records", []))
        source_texts = enriched_item.get("source_texts") or {
            "search": enriched_item.get("search_text", enriched_item.get("snippet", "")),
            "profile": f"{enriched_item['title']} {enriched_item.get('snippet', '')}",
            "repo": "",
            "website": "",
            "web": "",
        }
        must_hits, nice_hits, score, must_score, nice_score, confidence, requirement_scores, requirement_sources, requirement_evidence = score_candidate_with_evidence(
            role_brief,
            source_texts,
            evidence_records=evidence_records,
        )
        text_blob = " ".join(part for part in source_texts.values() if part)
        location_hits = score_location(text_blob, location_targets)
        location_eligible = bool(location_hits) if location_targets else True
        name = enriched_item.get("profile_name") or extract_name(enriched_item["title"])
        identity = hashlib.sha1(f"{name}|{enriched_item['url']}".encode()).hexdigest()[:8]
        must_breakdown = ", ".join(
            f"{item} {requirement_scores.get(item, 0):.2f} via {requirement_sources.get(item, 'none')}"
            for item in role_brief["must_haves"]
            if requirement_scores.get(item, 0) > 0
        ) or "none"
        nice_breakdown = ", ".join(
            f"{item} {requirement_scores.get(item, 0):.2f} via {requirement_sources.get(item, 'none')}"
            for item in role_brief["nice_to_haves"]
            if requirement_scores.get(item, 0) > 0
        ) or "none"
        strongest_evidence = [
            record for record in evidence_records
            if record.get("snippet")
        ][:3]
        eligibility_reason = (
            f"Eligible: public evidence aligns with {', '.join(location_hits)}."
            if location_eligible
            else "Ineligible: no public London/Berlin evidence found."
        )
        rationale = (
            f"Must-have evidence: {must_breakdown}; "
            f"nice-to-have evidence: {nice_breakdown}; "
            f"must-have score: {must_score:.3f}; "
            f"nice-to-have score: {nice_score:.3f}; "
            f"confidence: {confidence:.3f}; "
            f"location: {location_hits if location_hits else 'no London/Berlin evidence'}; "
            f"top evidence: {' | '.join(record.get('snippet', '') for record in strongest_evidence) if strongest_evidence else 'limited public evidence'}."
        )
        candidate = CandidateCard(
            id=identity,
            name=name,
            headline=enriched_item.get("headline_override") or enriched_item["title"],
            source_url=enriched_item["url"],
            email=enriched_item.get("public_email", ""),
            found_via=enriched_item.get("found_via", ["GitHub user search"]),
            evidence_links=enriched_item.get("evidence_links", [enriched_item["url"]]),
            evidence_records=evidence_records,
            evidence_count=len(evidence_records),
            evidence_density=evidence_density_label(evidence_records),
            must_have_hits=must_hits,
            nice_to_have_hits=nice_hits,
            fit_score=score,
            must_have_score=must_score,
            nice_to_have_score=nice_score,
            confidence_score=confidence,
            rationale=rationale,
            status=to_status(score, location_eligible=location_eligible),
            location_hits=location_hits,
            location_eligible=location_eligible,
            eligibility_reason=eligibility_reason,
            requirement_scores=requirement_scores,
            requirement_sources=requirement_sources,
            requirement_evidence=requirement_evidence,
            outreach_draft=generate_outreach(
                name,
                role_brief,
                must_hits,
                nice_hits,
                enriched_item["url"],
                requirement_evidence,
                location_hits,
            ),
        )
        existing = dedup.get(identity)
        if existing:
            existing.found_via = sorted({*existing.found_via, *candidate.found_via})
            existing.evidence_links = list(dict.fromkeys([*existing.evidence_links, *candidate.evidence_links]))
            existing.evidence_records = [*existing.evidence_records, *candidate.evidence_records]
            existing.evidence_count = len(existing.evidence_records)
            existing.evidence_density = evidence_density_label(existing.evidence_records)
            if candidate.fit_score > existing.fit_score:
                dedup[identity] = candidate
                dedup[identity].found_via = existing.found_via
                dedup[identity].evidence_links = existing.evidence_links
                dedup[identity].evidence_records = existing.evidence_records
                dedup[identity].evidence_count = existing.evidence_count
                dedup[identity].evidence_density = existing.evidence_density
        else:
            dedup[identity] = candidate
    return sorted(dedup.values(), key=lambda c: c.fit_score, reverse=True)


def build_candidates(role_brief: dict, max_results_per_query: int = 20, seed_results_path: Path | None = None) -> list[CandidateCard]:
    if seed_results_path:
        raw = json.loads(seed_results_path.read_text())
        return _cards_from_results(role_brief, raw, enrich_profiles=False)

    merged_results: list[dict] = []
    for query in role_brief["queries"]:
        merged_results.extend(github_user_search(query, max_results=max_results_per_query))
        merged_results.extend(duckduckgo_profile_search(query, max_results=max(3, max_results_per_query // 2)))
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
        normalized.setdefault("email", "")
        normalized.setdefault("found_via", ["GitHub user search"])
        normalized.setdefault("must_have_hits", [])
        normalized.setdefault("nice_to_have_hits", [])
        normalized.setdefault("evidence_records", [])
        normalized.setdefault("evidence_count", len(normalized.get("evidence_records", [])))
        normalized.setdefault("evidence_density", evidence_density_label(normalized.get("evidence_records", [])))
        normalized.setdefault("must_have_score", 0.0)
        normalized.setdefault("nice_to_have_score", 0.0)
        normalized.setdefault("confidence_score", 0.0)
        normalized.setdefault("location_hits", [])
        normalized.setdefault("location_eligible", True)
        normalized.setdefault("eligibility_reason", "")
        normalized.setdefault("requirement_scores", {})
        normalized.setdefault("requirement_sources", {})
        normalized.setdefault("requirement_evidence", {})
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
            "email",
            "found_via",
            "evidence_count",
            "evidence_density",
            "must_have_hits",
            "nice_to_have_hits",
            "must_have_score",
            "nice_to_have_score",
            "confidence_score",
            "location_hits",
            "location_eligible",
            "eligibility_reason",
            "fit_score",
            "status",
            "rationale",
            "outreach_draft",
        ],
    )
    writer.writeheader()
    for card in cards:
        row = asdict(card)
        row["found_via"] = "; ".join(card.found_via)
        row["must_have_hits"] = "; ".join(card.must_have_hits)
        row["nice_to_have_hits"] = "; ".join(card.nice_to_have_hits)
        row["location_hits"] = "; ".join(card.location_hits)
        row.pop("evidence_links", None)
        row.pop("evidence_records", None)
        row.pop("requirement_scores", None)
        row.pop("requirement_sources", None)
        row.pop("requirement_evidence", None)
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
