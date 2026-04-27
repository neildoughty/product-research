"""
Monthly source discovery — finds new RSS feeds worth monitoring.

Triggered via: python3 main.py --discover
Runs as a separate monthly GitHub Actions job.

Process:
  1. Search HN for stories about journalism/news product from unknown domains
  2. Try to find an RSS feed on each candidate domain
  3. Sample recent articles from the feed
  4. Ask Groq whether the source regularly covers digital news products or AI in journalism
  5. Save validated sources to auto_sources.yaml (never modifies config.yaml)
"""

import logging
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml

logger = logging.getLogger(__name__)

AUTO_SOURCES_PATH = "auto_sources.yaml"

# Search terms designed to surface relevant stories from domains we don't yet know about
_SEARCH_TERMS = [
    "newsroom AI product",
    "journalism technology",
    "news engineering",
    "media product strategy",
    "digital news platform",
    "publisher product team",
    "audio journalism",
    "news app product",
]

# Domains that will never be useful as RSS sources
_SKIP_DOMAINS = {
    "github.com", "twitter.com", "x.com", "youtube.com", "linkedin.com",
    "facebook.com", "reddit.com", "wikipedia.org", "arxiv.org",
    "docs.google.com", "google.com", "apple.com", "microsoft.com",
    "techcrunch.com", "theverge.com", "wired.com",  # too generic
}

# Common RSS feed path patterns to try
_RSS_PATHS = [
    "/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml",
    "/blog/feed", "/blog/rss", "/blog/feed.xml", "/blog/rss.xml",
    "/index.xml", "/feed/", "/feeds/posts/default", "/rss/",
]

MAX_NEW_PER_RUN = 6


def _load_auto() -> dict:
    try:
        with open(AUTO_SOURCES_PATH) as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {"rss_feeds": []}
    except FileNotFoundError:
        return {"rss_feeds": []}


def _save_auto(data: dict):
    with open(AUTO_SOURCES_PATH, "w") as f:
        f.write("# Auto-discovered RSS sources\n")
        f.write("# Managed by discovery.py — do not edit manually\n")
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _known_domains(config: dict, auto: dict) -> set:
    domains = set()
    for feed in config.get("rss_feeds", []) + auto.get("rss_feeds", []):
        try:
            netloc = urlparse(feed["url"]).netloc.lower().lstrip("www.")
            domains.add(netloc)
        except Exception:
            pass
    for d in config.get("hn_domains", []):
        domains.add(d.lower())
    return domains


def _find_rss(domain: str) -> Optional[str]:
    """Try to find an RSS feed for a domain via HTML autodiscovery then common paths."""
    base = f"https://{domain}"
    headers = {"User-Agent": "WS-Research-Bot/1.0"}

    # HTML autodiscovery first — most reliable when it works
    try:
        resp = requests.get(base, timeout=8, headers=headers)
        if resp.status_code == 200:
            matches = re.findall(
                r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)["\']',
                resp.text, re.IGNORECASE,
            )
            if not matches:
                # Also try reversed attribute order
                matches = re.findall(
                    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\']',
                    resp.text, re.IGNORECASE,
                )
            if matches:
                return urljoin(base, matches[0])
    except Exception:
        pass

    # Fall back to common paths
    for path in _RSS_PATHS:
        url = base + path
        try:
            resp = requests.get(url, timeout=6, headers=headers, allow_redirects=True)
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and any(x in ct for x in ("xml", "rss", "atom")):
                return url
        except Exception:
            pass

    return None


def _sample_titles(feed_url: str) -> list:
    """Return up to 5 recent article titles from a feed."""
    try:
        resp = requests.get(feed_url, timeout=10, headers={"User-Agent": "WS-Research-Bot/1.0"})
        parsed = feedparser.parse(resp.content)
        return [e.get("title", "") for e in parsed.entries[:5] if e.get("title")]
    except Exception:
        return []


def _is_relevant(domain: str, titles: list) -> bool:
    """Ask Groq whether this source is worth monitoring."""
    from ai import _call_groq
    sample = "\n".join(f"- {t}" for t in titles) if titles else "(no sample available)"
    prompt = f"""A BBC World Service product manager monitors sources about: digital news products, AI in journalism, news org engineering decisions, audio/podcast innovation, content format experiments.

Should this source be added to their monitoring list?

Domain: {domain}
Recent article titles:
{sample}

Reply YES or NO only. YES if this source regularly covers digital news products, journalism technology, AI in media, or engineering at news organisations. NO if it's a general news outlet, unrelated to media/journalism, or only covers business/finance."""
    try:
        result = _call_groq(prompt).strip().upper()
        return result.startswith("YES")
    except Exception:
        return False


def run(config: dict) -> int:
    """
    Main discovery entry point. Returns number of new sources added.
    """
    auto = _load_auto()
    known = _known_domains(config, auto)
    existing_urls = {f["url"] for f in auto.get("rss_feeds", [])}

    # Search HN for candidate domains
    candidates = {}
    for term in _SEARCH_TERMS:
        try:
            resp = requests.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": term, "tags": "story", "hitsPerPage": 20},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            for hit in resp.json().get("hits", []):
                url = hit.get("url") or ""
                if not url:
                    continue
                domain = urlparse(url).netloc.lower().lstrip("www.")
                if domain and domain not in known and domain not in _SKIP_DOMAINS and "." in domain:
                    candidates[domain] = candidates.get(domain, 0) + 1
        except Exception as e:
            logger.warning(f"Discovery search failed for '{term}': {e}")
        time.sleep(0.5)

    # Rank by how many times each domain appeared across search terms
    ranked = sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:20]
    logger.info(f"Discovery: {len(ranked)} candidate domains to evaluate")

    added = 0
    for domain, hits in ranked:
        if added >= MAX_NEW_PER_RUN:
            break

        logger.info(f"Discovery: checking {domain} ({hits} HN mentions)")

        feed_url = _find_rss(domain)
        if not feed_url:
            logger.info(f"Discovery: no RSS found for {domain}")
            continue

        if feed_url in existing_urls:
            continue

        titles = _sample_titles(feed_url)
        if not _is_relevant(domain, titles):
            logger.info(f"Discovery: Groq rejected {domain}")
            continue

        auto["rss_feeds"].append({
            "name": domain,
            "url": feed_url,
            "org": domain,
            "tier": "trade",
        })
        existing_urls.add(feed_url)
        known.add(domain)
        added += 1
        logger.info(f"Discovery: added {domain} — {feed_url}")
        time.sleep(1)

    _save_auto(auto)

    if added:
        logger.info(f"Discovery complete: {added} new source(s) added to {AUTO_SOURCES_PATH}")
    else:
        logger.info("Discovery complete: no new sources found this run")

    return added
