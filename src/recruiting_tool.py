#!/usr/bin/env python3
"""Lightweight AI-assisted recruiting tool for generating candidate shortlists."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / ".cache"
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
    "typescript": ["typescript"],
    "react": ["react", "reactjs", "react.js"],
    "frontend": ["frontend", "front-end", "react", "nextjs", "next.js", "tailwind", "html canvas"],
    "backend": ["backend", "back-end", "node", "nodejs", "node.js", "server", "postgres", "postgresql"],
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
    location_state: str
    eligibility_reason: str
    review_state: str
    reviewer_note: str
    why_summary: str
    review_tags: list[str]
    contact_source: str
    requirement_scores: dict[str, float]
    requirement_judgments: dict[str, str]
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
    return unescape(re.sub(r"<[^>]+>", "", value))


def _build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _cache_path(namespace: str, key: str) -> Path:
    digest = hashlib.sha1(key.encode()).hexdigest()
    return CACHE_DIR / namespace / f"{digest}.cache"


def _read_cache_text(namespace: str, key: str) -> str:
    path = _cache_path(namespace, key)
    if not path.exists():
        return ""
    try:
        return path.read_text()
    except Exception:
        return ""


def _write_cache_text(namespace: str, key: str, value: str) -> None:
    path = _cache_path(namespace, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(value)
    except Exception:
        return


def _fetch_html(url: str) -> str:
    if url in HTML_CACHE:
        return HTML_CACHE[url]
    cached = _read_cache_text("html", url)
    if cached:
        HTML_CACHE[url] = cached
        return cached
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ssl_context = _build_ssl_context()
    try:
        with urlopen(req, timeout=20, context=ssl_context) as response:  # nosec B310
            html = response.read().decode("utf-8", errors="ignore")
            HTML_CACHE[url] = html
            _write_cache_text("html", url, html)
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
    cached = _read_cache_text("json", url)
    if cached:
        try:
            payload = json.loads(cached)
            JSON_CACHE[url] = payload
            return payload
        except Exception:
            pass
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
            _write_cache_text("json", url, json.dumps(payload))
            return payload
    except Exception:
        return {}


def _make_evidence_record(
    source_type: str,
    url: str,
    label: str,
    snippet: str,
    text: str,
    provenance: str = "",
    matched_requirements: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "url": url,
        "label": label,
        "snippet": snippet.strip(),
        "text": text.strip(),
        "provenance": provenance or label,
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
                        provenance="GitHub user search result",
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
    try:
        html = _fetch_html(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
    except Exception:
        return []
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
                        provenance="DuckDuckGo result snippet",
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
    return re.sub(r"\s+", " ", unescape(value).lower()).strip()


def _term_patterns(term: str) -> list[re.Pattern[str]]:
    normalized = normalize_text(term)
    escaped = re.escape(normalized)
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\-", r"[-\s]?")
    return [re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", flags=re.I)]


def _has_negation(normalized_text: str, start_index: int) -> bool:
    prefix = normalized_text[max(0, start_index - 32):start_index].strip()
    return any(prefix.endswith(negation) for negation in NEGATION_PREFIXES)


def _find_positive_excerpt(text: str, keyword: str) -> tuple[bool, str]:
    normalized_text = normalize_text(text)
    for term in SKILL_ALIASES.get(normalize_text(keyword), [keyword]):
        for pattern in _term_patterns(term):
            for match in pattern.finditer(normalized_text):
                if _has_negation(normalized_text, match.start()):
                    continue
                original_match = re.search(pattern.pattern, text, flags=re.I)
                if original_match:
                    start = max(0, original_match.start() - 80)
                    end = min(len(text), original_match.end() + 80)
                    return True, re.sub(r"\s+", " ", text[start:end]).strip(" |")
                start = max(0, match.start() - 80)
                end = min(len(normalized_text), match.end() + 80)
                return True, normalized_text[start:end].strip(" |")
    return False, ""


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
    listed_repos = _extract_repository_list(item["url"], limit=4)
    public_web_results: list[dict[str, str]] = []
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
            provenance="GitHub profile bio",
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
                provenance="Linked personal website",
            )
        )
    evidence_records.extend(repo["evidence_record"] for repo in pinned_repos[:3] if repo.get("evidence_record"))
    evidence_records.extend(repo["evidence_record"] for repo in listed_repos[:3] if repo.get("evidence_record"))
    evidence_records.extend(
        _make_evidence_record("web", result["url"], result["label"], result["snippet"], result["text"], provenance="Public web result")
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
        "web": "",
    }
    return enriched_item


def _extract_public_email(html: str) -> str:
    candidate_patterns = [
        r'href="mailto:([^"]+)"',
        r'itemprop="email"[^>]*href="mailto:([^"]+)"',
        r'itemprop="email"[^>]*>\s*([^<@\s]+@[^<\s]+)\s*<',
        r'class="[^"]*\bu-email\b[^"]*"[^>]*>\s*([^<@\s]+@[^<\s]+)\s*<',
        r'data-test-selector="profile-email"[^>]*>\s*([^<@\s]+@[^<\s]+)\s*<',
        r'aria-label="Email:\s*([^"]+)"',
    ]
    for pattern in candidate_patterns:
        match = _first_match(pattern, html)
        if match:
            normalized = _normalize_email(unquote(match))
            if normalized:
                return normalized
    visible_match = EMAIL_RE.search(_strip_tags(html))
    return _normalize_email(visible_match.group(0) if visible_match else "")


def _normalize_email(value: str) -> str:
    value = _normalize_optional_text(value)
    if value.startswith("mailto:"):
        value = value[7:]
    return value if EMAIL_RE.fullmatch(value) else ""


def _normalize_optional_text(value: object) -> str:
    return unescape(value).strip() if isinstance(value, str) else ""


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


def _github_repo_slug(repo_url: str) -> tuple[str, str] | None:
    parsed = urlparse(repo_url)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _fetch_github_repo_api(repo_url: str) -> dict:
    slug = _github_repo_slug(repo_url)
    if not slug:
        return {}
    owner, repo = slug
    response = _fetch_json(f"https://api.github.com/repos/{quote_plus(owner)}/{quote_plus(repo)}")
    return response if isinstance(response, dict) else {}


def _fetch_github_repo_file(repo_url: str, path: str) -> str:
    slug = _github_repo_slug(repo_url)
    if not slug:
        return ""
    owner, repo = slug
    response = _fetch_json(f"https://api.github.com/repos/{quote_plus(owner)}/{quote_plus(repo)}/contents/{path}")
    if not isinstance(response, dict):
        return ""
    encoded = response.get("content", "")
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _repo_artifact_signals(repo_url: str) -> dict[str, str]:
    cached = _read_cache_text("repo-signals", repo_url)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass

    repo_meta = _fetch_github_repo_api(repo_url)
    languages = _fetch_json(repo_meta.get("languages_url", "")) if repo_meta.get("languages_url") else {}
    package_text = _fetch_github_repo_file(repo_url, "package.json")
    tsconfig_text = _fetch_github_repo_file(repo_url, "tsconfig.json")
    package_data = {}
    if package_text:
        try:
            package_data = json.loads(package_text)
        except Exception:
            package_data = {}

    dependencies = {
        **(package_data.get("dependencies") or {}),
        **(package_data.get("devDependencies") or {}),
    }
    dep_names = " ".join(dependencies.keys())
    language_names = " ".join((languages or {}).keys()) if isinstance(languages, dict) else ""
    artifact_markers: list[str] = []
    depth_score = 0.0

    if "typescript" in normalize_text(language_names) or "typescript" in normalize_text(dep_names) or tsconfig_text:
        artifact_markers.append("TypeScript project with typed build configuration")
        depth_score += 0.2
    if any(dep in dependencies for dep in ("react", "react-dom", "next", "nextjs", "vite")):
        artifact_markers.append("React/frontend application dependencies")
        depth_score += 0.2
    if any(dep in dependencies for dep in ("express", "fastify", "@nestjs/core", "koa", "hono", "prisma", "pg", "postgres", "trpc")):
        artifact_markers.append("Backend/server-side stack dependencies")
        depth_score += 0.2
    if any(dep in dependencies for dep in ("framer-motion", "gsap", "three", "@react-three/fiber")):
        artifact_markers.append("Animation/graphics libraries")
        depth_score += 0.1
    if any(dep in dependencies for dep in ("@mui/material", "@material-ui/core", "ariakit", "@ariakit/react", "@base-ui-components/react", "@pandacss/dev")):
        artifact_markers.append("Component system and styling libraries")
        depth_score += 0.1
    scripts = package_data.get("scripts") or {}
    if any(key in scripts for key in ("test", "test:unit", "test:e2e", "lint", "build")):
        artifact_markers.append("Build/test scripts present")
        depth_score += 0.1
    if tsconfig_text and package_text:
        depth_score += 0.1
    if repo_meta.get("stargazers_count", 0) >= 5:
        depth_score += 0.1

    result = {
        "languages": language_names,
        "dependency_names": dep_names,
        "artifact_markers": " | ".join(artifact_markers),
        "depth_score": str(round(min(depth_score, 1.0), 3)),
    }
    _write_cache_text("repo-signals", repo_url, json.dumps(result))
    return result


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
    try:
        html = _fetch_html(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
    except Exception:
        return []
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
    for index, (href, name, body) in enumerate(re.findall(
        r'class="Link mr-1 text-bold wb-break-word"[^>]*href="(/[^"]+)"[^>]*><span class="repo">(.*?)</span></a>(.*?)</article>',
        html,
        flags=re.S,
    )):
        repo_url = f"https://github.com{href}"
        description = _strip_tags(_first_match(r'<p class="pinned-item-desc[^"]*"[^>]*>(.*?)</p>', body))
        language = _strip_tags(_first_match(r'<span itemprop="programmingLanguage">(.*?)</span>', body))
        readme_summary = _extract_repo_page_summary(repo_url) if index == 0 else ""
        artifact_signals = _repo_artifact_signals(repo_url) if index == 0 else {"languages": language, "dependency_names": "", "artifact_markers": ""}
        repo_text_parts = [name, description, language, owner, readme_summary, artifact_signals.get("languages", ""), artifact_signals.get("dependency_names", ""), artifact_signals.get("artifact_markers", "")]
        results.append(
            {
                "url": repo_url,
                "name": name,
                "text": " ".join(part for part in repo_text_parts if part),
                "evidence_record": _make_evidence_record(
                    "repo",
                    repo_url,
                    f"Repo: {name}",
                    " | ".join(part for part in (description, language, artifact_signals.get("artifact_markers", ""), readme_summary[:140]) if part),
                    " ".join(part for part in repo_text_parts if part),
                    provenance="Pinned repository summary",
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

    for index, body in enumerate(entries[:limit]):
        href = _first_match(r'<a href="(/[^"]+)" itemprop="name codeRepository"', body)
        name = _strip_tags(_first_match(r'itemprop="name codeRepository"[^>]*>\s*(.*?)</a>', body))
        if not href or not name:
            continue
        repo_url = f"https://github.com{href}"
        description = _strip_tags(_first_match(r'itemprop="description">\s*(.*?)\s*</p>', body))
        language = _strip_tags(_first_match(r'<span itemprop="programmingLanguage">(.*?)</span>', body))
        topics = re.findall(r'class="topic-tag[^"]*"[^>]*>\s*(.*?)\s*</a>', body, flags=re.S)
        topic_text = " ".join(_strip_tags(topic) for topic in topics)
        readme_summary = ""
        artifact_signals = _repo_artifact_signals(repo_url) if index == 0 else {"languages": language, "dependency_names": "", "artifact_markers": ""}
        repo_text_parts = [name, description, language, topic_text, owner, readme_summary, artifact_signals.get("languages", ""), artifact_signals.get("dependency_names", ""), artifact_signals.get("artifact_markers", "")]
        results.append(
            {
                "url": repo_url,
                "name": name,
                "text": " ".join(part for part in repo_text_parts if part),
                "evidence_record": _make_evidence_record(
                    "repo",
                    repo_url,
                    f"Repo: {name}",
                    " | ".join(part for part in (description, language, artifact_signals.get("artifact_markers", ""), readme_summary[:140]) if part),
                    " ".join(part for part in repo_text_parts if part),
                    provenance="Repository list summary",
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
    repo_markers: list[str] = []
    marker_patterns = {
        "typescript": r"tsconfig\.json|TypeScript",
        "react": r"React|react-dom|next\.config|Next\.js|vite",
        "backend": r"express|fastify|nestjs|api|server|postgres|prisma",
        "performance": r"performance|optimization|benchmark|latency|scalability",
        "open source": r"MIT License|Apache License|Contributing|pull request|issues",
    }
    for label, pattern in marker_patterns.items():
        if re.search(pattern, html, flags=re.I):
            repo_markers.append(label)
    return " ".join(part for part in (meta_description, readme[:800], " ".join(repo_markers)) if part)


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
) -> tuple[float, str, str, str]:
    best_score = 0.0
    best_source = ""
    best_excerpt = ""
    best_provenance = ""
    for record in evidence_records:
        text = record.get("text", "")
        if not text:
            continue
        matched, excerpt = _find_positive_excerpt(text, keyword)
        if not matched:
            continue
        strength = float(record.get("strength", SOURCE_STRENGTHS.get(record.get("source_type", ""), 0.0)))
        if strength > best_score:
            best_score = strength
            best_source = SOURCE_LABELS.get(record.get("source_type", ""), record.get("source_type", ""))
            best_excerpt = excerpt[:220]
            best_provenance = record.get("provenance", record.get("label", ""))
    return best_score, best_source, best_excerpt, best_provenance


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
) -> tuple[list[str], float, dict[str, float], dict[str, str], dict[str, str], dict[str, str]]:
    hits: list[str] = []
    scores: dict[str, float] = {}
    sources: dict[str, str] = {}
    evidence_snippets: dict[str, str] = {}
    evidence_provenance: dict[str, str] = {}
    requirements = list(requirements)
    total_weight = sum(weights.get(item, 1.0) for item in requirements) or 1.0
    weighted_sum = 0.0

    for requirement in requirements:
        score, source, excerpt, provenance = _match_requirement_from_records(evidence_records, requirement)
        scores[requirement] = round(score, 3)
        sources[requirement] = source
        evidence_snippets[requirement] = excerpt
        evidence_provenance[requirement] = provenance
        weighted_sum += weights.get(requirement, 1.0) * score
        if score > 0:
            hits.append(requirement)

    return hits, round(weighted_sum / total_weight, 3), scores, sources, evidence_snippets, evidence_provenance


def score_candidate_with_evidence(
    role_brief: dict,
    source_texts: dict[str, str],
    evidence_records: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str], float, float, float, float, dict[str, float], dict[str, str], dict[str, str], dict[str, str], list[str]]:
    must_weights = role_brief.get("must_have_weights", {})
    nice_weights = role_brief.get("nice_to_have_weights", {})
    if evidence_records:
        must_hits, must_score, must_requirement_scores, must_sources, must_evidence, must_provenance = _weighted_score_from_records(
            role_brief["must_haves"],
            must_weights,
            evidence_records,
        )
        nice_hits, nice_score, nice_requirement_scores, nice_sources, nice_evidence, nice_provenance = _weighted_score_from_records(
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
        must_provenance = {requirement: "" for requirement in role_brief["must_haves"]}
        nice_provenance = {requirement: "" for requirement in role_brief["nice_to_haves"]}
    fit_weight_must = float(role_brief.get("must_have_category_weight", DEFAULT_MUST_CATEGORY_WEIGHT))
    fit_weight_nice = float(role_brief.get("nice_to_have_category_weight", DEFAULT_NICE_CATEGORY_WEIGHT))
    fit_score = round((fit_weight_must * must_score) + (fit_weight_nice * nice_score), 3)
    requirement_scores = {**must_requirement_scores, **nice_requirement_scores}
    requirement_sources = {**must_sources, **nice_sources}
    requirement_evidence = {**must_evidence, **nice_evidence}
    requirement_provenance = {**must_provenance, **nice_provenance}
    confidence = compute_confidence(source_texts, requirement_scores)
    uncertainty_flags = summarize_uncertainty(role_brief, requirement_scores, source_texts)
    return must_hits, nice_hits, fit_score, must_score, nice_score, confidence, requirement_scores, requirement_sources, requirement_evidence, requirement_provenance, uncertainty_flags


def _has_positive_signal(normalized_text: str, keyword: str) -> bool:
    for term in SKILL_ALIASES.get(normalize_text(keyword), [keyword]):
        for pattern in _term_patterns(term):
            for match in pattern.finditer(normalized_text):
                if _has_negation(normalized_text, match.start()):
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


def summarize_uncertainty(role_brief: dict, requirement_scores: dict[str, float], source_texts: dict[str, str]) -> list[str]:
    flags: list[str] = []
    missing_core = [req for req in role_brief.get("must_haves", []) if requirement_scores.get(req, 0.0) <= 0]
    weak_core = [req for req in role_brief.get("must_haves", []) if 0 < requirement_scores.get(req, 0.0) < 0.55]
    if missing_core:
        flags.append(f"No public evidence for: {', '.join(missing_core[:3])}")
    if weak_core:
        flags.append(f"Weak support for: {', '.join(weak_core[:3])}")
    if not source_texts.get("repo"):
        flags.append("No repo-backed evidence collected")
    return flags


def requirement_judgment(score: float) -> str:
    if score >= 0.85:
        return "confirmed"
    if score > 0:
        return "partial"
    return "unknown"


def build_requirement_judgments(requirement_scores: dict[str, float]) -> dict[str, str]:
    return {requirement: requirement_judgment(score) for requirement, score in requirement_scores.items()}


def build_review_tags(
    role_brief: dict,
    requirement_scores: dict[str, float],
    location_state: str,
    contact_source: str,
) -> list[str]:
    tags: list[str] = []
    if requirement_scores.get("TypeScript", 0.0) >= 0.85 and requirement_scores.get("React", 0.0) >= 0.85:
        tags.append("strong core stack")
    if requirement_scores.get("frontend", 0.0) >= 0.85 and requirement_scores.get("backend", 0.0) < 0.55:
        tags.append("frontend-leaning")
    if requirement_scores.get("backend", 0.0) >= 0.85 and requirement_scores.get("frontend", 0.0) < 0.55:
        tags.append("backend-leaning")
    if location_state == "inferred":
        tags.append("location inferred")
    if location_state == "unknown":
        tags.append("location unconfirmed")
    if not contact_source:
        tags.append("no public contact")
    elif "website" in contact_source.lower():
        tags.append("contact via linked site")
    weak_nice = [req for req in role_brief.get("nice_to_haves", []) if 0 < requirement_scores.get(req, 0.0) < 0.55]
    if weak_nice:
        tags.append("bonus signals are weak")
    return tags[:4]


def build_why_summary(
    requirement_judgments: dict[str, str],
    location_state: str,
    review_tags: list[str],
) -> str:
    strong = [req for req in ("TypeScript", "React", "frontend", "backend") if requirement_judgments.get(req) == "confirmed"]
    partial = [req for req in ("TypeScript", "React", "frontend", "backend") if requirement_judgments.get(req) == "partial"]
    parts: list[str] = []
    if strong:
        parts.append(f"Confirmed {', '.join(strong[:2])} evidence")
    if partial:
        parts.append(f"partial support for {', '.join(partial[:2])}")
    if location_state == "confirmed":
        parts.append("location publicly confirmed")
    elif location_state == "inferred":
        parts.append("location inferred from public sources")
    else:
        parts.append("location not publicly confirmed")
    if review_tags:
        parts.append(f"tags: {', '.join(review_tags[:2])}")
    return ". ".join(parts) + "."


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
    proof_requirement = strongest[0] if strongest else (supporting[0] if supporting else "")
    proof_excerpt = requirement_evidence.get(proof_requirement, "")
    location_line = f" and your {', '.join(location_hits)} location" if location_hits else ""
    role_summary = (
        "At HASH, full-stack engineers build most user-facing features across both frontend and backend, "
        "ship quickly, work heavily in TypeScript and React, and contribute to an open-source product around "
        "knowledge graphs, simulation, and automation."
    )
    return (
        f"Hi {candidate_name},\n\n"
        f"I came across your profile while sourcing for {role_brief['company']}'s {role_brief['role_name']} role. "
        f"Your public work looks relevant for the team because of {proof}{location_line}.\n\n"
        f"At HASH, this role focuses on building user-facing features across frontend and backend, "
        f"moving quickly in TypeScript and React, and contributing to an open-source platform for knowledge graphs, "
        f"simulation, and automation. {role_summary}\n\n"
        f"If that overlaps with the kind of full-stack work you want to do next, I'd be glad to share more about the role and team at {role_brief['company']}.\n\n"
        "Best,\nHASH Recruiting Team"
    )


def score_location(text: str, location_targets: Iterable[str]) -> list[str]:
    normalized = normalize_text(text)
    return [target for target in location_targets if _has_positive_signal(normalized, target)]


def location_state_for_hits(location_hits: list[str]) -> str:
    return "confirmed" if location_hits else "unknown"


def score_location_from_records(evidence_records: list[dict[str, Any]], location_targets: Iterable[str]) -> tuple[list[str], str]:
    confirmed_hits: list[str] = []
    inferred_hits: list[str] = []
    for record in evidence_records:
        text = record.get("text", "")
        if not text:
            continue
        hits = score_location(text, location_targets)
        if not hits:
            continue
        source_type = record.get("source_type", "")
        if source_type in {"profile", "website"}:
            confirmed_hits.extend(hits)
        else:
            inferred_hits.extend(hits)
    if confirmed_hits:
        return sorted(dict.fromkeys(confirmed_hits)), "confirmed"
    if inferred_hits:
        return sorted(dict.fromkeys(inferred_hits)), "inferred"
    return [], "unknown"


def contact_source_label(email: str, evidence_records: list[dict[str, Any]]) -> str:
    if not email:
        return ""
    for record in evidence_records:
        text = record.get("text", "")
        snippet = record.get("snippet", "")
        if email in text or email in snippet:
            provenance = record.get("provenance", record.get("label", "public source"))
            return provenance
    return "Public GitHub or linked website"


def review_state_for_candidate(location_eligible: bool, uncertainty_flags: list[str], fit_score: float, requirement_judgments: dict[str, str], location_state: str) -> tuple[str, str]:
    if not location_eligible:
        return "insufficient_evidence", "Missing public London/Berlin evidence."
    if location_state == "inferred":
        return "needs_review", "Location is inferred from public sources and should be verified."
    backend_judgment = requirement_judgments.get("backend", "unknown")
    frontend_judgment = requirement_judgments.get("frontend", "unknown")
    if frontend_judgment == "confirmed" and backend_judgment != "confirmed":
        return "needs_review", "Strong frontend evidence, limited backend proof."
    if backend_judgment == "confirmed" and frontend_judgment != "confirmed":
        return "needs_review", "Strong backend evidence, limited frontend proof."
    if uncertainty_flags:
        return "needs_review", uncertainty_flags[0]
    if fit_score >= 0.55:
        return "ready", "Strong public evidence across the core role requirements."
    return "needs_review", "Some public evidence exists, but the profile needs manual review."


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
    if enrich_profiles:
        enriched_results: list[dict] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_map = {executor.submit(enrich_github_result, item): item for item in results}
            for future in as_completed(future_map):
                original = future_map[future]
                try:
                    enriched_results.append(future.result())
                except Exception:
                    enriched_results.append(dict(original))
    else:
        enriched_results = [dict(item) for item in results]

    for enriched_item in enriched_results:
        evidence_records = list(enriched_item.get("evidence_records", []))
        source_texts = enriched_item.get("source_texts") or {
            "search": enriched_item.get("search_text", enriched_item.get("snippet", "")),
            "profile": f"{enriched_item['title']} {enriched_item.get('snippet', '')}",
            "repo": "",
            "website": "",
            "web": "",
        }
        must_hits, nice_hits, score, must_score, nice_score, confidence, requirement_scores, requirement_sources, requirement_evidence, requirement_provenance, uncertainty_flags = score_candidate_with_evidence(
            role_brief,
            source_texts,
            evidence_records=evidence_records,
        )
        location_hits, location_state = score_location_from_records(evidence_records, location_targets)
        if not location_hits:
            text_blob = " ".join(part for part in source_texts.values() if part)
            location_hits = score_location(text_blob, location_targets)
            if location_hits and location_state == "unknown":
                location_state = "inferred"
        location_eligible = bool(location_hits) if location_targets else True
        name = enriched_item.get("profile_name") or extract_name(enriched_item["title"])
        identity = hashlib.sha1(f"{name}|{enriched_item['url']}".encode()).hexdigest()[:8]
        requirement_judgments = build_requirement_judgments(requirement_scores)
        contact_source = contact_source_label(enriched_item.get("public_email", ""), evidence_records)
        review_tags = build_review_tags(role_brief, requirement_scores, location_state, contact_source)
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
            f"Public location evidence aligns with {', '.join(location_hits)} ({location_state})."
            if location_eligible
            else "Ineligible: no public London/Berlin evidence found."
        )
        review_state, reviewer_note = review_state_for_candidate(location_eligible, uncertainty_flags, score, requirement_judgments, location_state)
        why_summary = build_why_summary(requirement_judgments, location_state, review_tags)
        rationale = (
            f"Must-have evidence: {must_breakdown}; "
            f"nice-to-have evidence: {nice_breakdown}; "
            f"must-have score: {must_score:.3f}; "
            f"nice-to-have score: {nice_score:.3f}; "
            f"confidence: {confidence:.3f}; "
            f"location: {location_hits if location_hits else 'no London/Berlin evidence'}; "
            f"top evidence: {' | '.join(record.get('snippet', '') for record in strongest_evidence) if strongest_evidence else 'limited public evidence'}; "
            f"uncertainty: {' | '.join(uncertainty_flags) if uncertainty_flags else 'none'}."
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
            location_state=location_state,
            eligibility_reason=eligibility_reason,
            review_state=review_state,
            reviewer_note=reviewer_note,
            why_summary=why_summary,
            review_tags=review_tags,
            contact_source=contact_source,
            requirement_scores=requirement_scores,
            requirement_judgments=requirement_judgments,
            requirement_sources={
                key: f"{requirement_sources.get(key, '')}{f' · {requirement_provenance.get(key, '')}' if requirement_provenance.get(key) else ''}".strip(" ·")
                for key in requirement_scores
            },
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
    deduped_results: list[dict] = []
    seen_urls: set[str] = set()
    for item in merged_results:
        url = item.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped_results.append(item)
    return _cards_from_results(role_brief, deduped_results, enrich_profiles=True)


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
        normalized.setdefault("location_state", "unknown")
        normalized.setdefault("eligibility_reason", "")
        normalized.setdefault("review_state", "needs_review")
        normalized.setdefault("reviewer_note", "")
        normalized.setdefault("why_summary", "")
        normalized.setdefault("review_tags", [])
        normalized.setdefault("contact_source", "")
        normalized.setdefault("requirement_scores", {})
        normalized.setdefault("requirement_judgments", {})
        normalized.setdefault("requirement_sources", {})
        normalized.setdefault("requirement_evidence", {})
        cards.append(CandidateCard(**normalized))
    return cards


def cards_to_csv_text(cards: list[CandidateCard]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "rank",
            "name",
            "id",
            "headline",
            "source_url",
            "email",
            "contact_source",
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
            "location_state",
            "eligibility_reason",
            "review_state",
            "reviewer_note",
            "why_summary",
            "review_tags",
            "fit_score",
            "status",
            "rationale",
            "outreach_draft",
        ],
    )
    writer.writeheader()
    ranked_cards = sorted(
        cards,
        key=lambda card: (card.fit_score, card.must_have_score, card.nice_to_have_score, card.confidence_score),
        reverse=True,
    )
    for rank, card in enumerate(ranked_cards, start=1):
        row = asdict(card)
        row["rank"] = rank
        row["found_via"] = "; ".join(card.found_via)
        row["review_tags"] = "; ".join(card.review_tags)
        row["must_have_hits"] = "; ".join(card.must_have_hits)
        row["nice_to_have_hits"] = "; ".join(card.nice_to_have_hits)
        row["location_hits"] = "; ".join(card.location_hits)
        row.pop("evidence_links", None)
        row.pop("evidence_records", None)
        row.pop("requirement_scores", None)
        row.pop("requirement_judgments", None)
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
