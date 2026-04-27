"""
Monthly source discovery — finds new RSS feeds worth monitoring.

Triggered via: python3 main.py --discover
Runs as a separate monthly GitHub Actions job.

Strategy:
  Primary — link extraction from trusted sources. If Nieman Lab, Reuters Institute,
  CJR, GIJN, or Press Gazette keep citing a domain we don't monitor, that's a strong
  editorial signal it's on-brief. This naturally surfaces global sources that never
  appear on Hacker News.

  Fallback — if primary finds fewer than MIN_CANDIDATES candidates, supplement with
  an HN keyword search.

Process for each candidate domain:
  1. Try to find an RSS feed
  2. Sample recent article titles
  3. Ask Groq whether it's relevant to digital news products / AI in journalism
  4. Save validated sources to auto_sources.yaml
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

# Trusted sources we mine for outbound links.
# These are editorially aligned with the brief and have global scope.
_LINK_SOURCES = [
    "https://www.niemanlab.org/feed/",
    "https://reutersinstitute.politics.ox.ac.uk/rss.xml",
    "https://www.cjr.org/feed/",
    "https://gijn.org/feed/",
    "https://pressgazette.co.uk/feed/",
    "https://www.inma.org/blogs/rss.cfm",
    "https://ijnet.org/en/rss.xml",
]

# Min citations from distinct trusted sources before we investigate a domain
CITATION_THRESHOLD = 2

# HN fallback search terms (only used if link extraction is sparse)
_HN_TERMS = [
    "newsroom AI journalism",
    "news engineering product",
    "digital journalism platform",
    "media circumvention censorship",
]

MIN_LINK_CANDIDATES = 5  # below this, supplement with HN

# Domains to always skip
_SKIP_DOMAINS = {
    "github.com", "twitter.com", "x.com", "youtube.com", "linkedin.com",
    "facebook.com", "reddit.com", "wikipedia.org", "arxiv.org",
    "docs.google.com", "google.com", "apple.com", "microsoft.com",
    "nytimes.com", "theguardian.com", "bbc.co.uk", "bbc.com",
    "ft.com", "reuters.com", "washingtonpost.com",  # news sites, not eng blogs
}

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


def _extract_linked_domains(known: set) -> dict:
    """
    Fetch recent articles from trusted sources and count how many distinct
    trusted sources link to each unknown domain.
    Returns {domain: count_of_distinct_linking_sources}.
    """
    domain_sources = {}  # domain -> set of source URLs that linked to it
    headers = {"User-Agent": "WS-Research-Bot/1.0"}

    for feed_url in _LINK_SOURCES:
        try:
            resp = requests.get(feed_url, timeout=10, headers=headers)
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:30]:
                # Pull HTML from content or summary
                content_list = entry.get("content", [])
                html = content_list[0].get("value", "") if content_list else ""
                if not html:
                    html = entry.get("summary", "")

                for href in re.findall(r'href=["\']([^"\']+)["\']', html):
                    try:
                        domain = urlparse(href).netloc.lower().lstrip("www.")
                        if (domain and "." in domain
                                and domain not in known
                                and domain not in _SKIP_DOMAINS):
                            if domain not in domain_sources:
                                domain_sources[domain] = set()
                            domain_sources[domain].add(feed_url)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Link extraction failed for {feed_url}: {e}")
        time.sleep(0.3)

    return {d: len(sources) for d, sources in domain_sources.items()
            if len(sources) >= CITATION_THRESHOLD}


def _hn_candidates(known: set) -> dict:
    """Fallback: search HN for relevant terms, return {domain: hit_count}."""
    candidates = {}
    for term in _HN_TERMS:
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
                domain = urlparse(url).netloc.lower().lstrip("www.")
                if domain and domain not in known and domain not in _SKIP_DOMAINS and "." in domain:
                    candidates[domain] = candidates.get(domain, 0) + 1
        except Exception as e:
            logger.warning(f"HN fallback search failed for '{term}': {e}")
        time.sleep(0.5)
    return candidates


def _find_rss(domain: str) -> Optional[str]:
    base = f"https://{domain}"
    headers = {"User-Agent": "WS-Research-Bot/1.0"}

    # HTML autodiscovery
    try:
        resp = requests.get(base, timeout=8, headers=headers)
        if resp.status_code == 200:
            for pattern in [
                r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)["\']',
                r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\']',
            ]:
                matches = re.findall(pattern, resp.text, re.IGNORECASE)
                if matches:
                    return urljoin(base, matches[0])
    except Exception:
        pass

    # Common paths
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
    try:
        resp = requests.get(feed_url, timeout=10, headers={"User-Agent": "WS-Research-Bot/1.0"})
        parsed = feedparser.parse(resp.content)
        return [e.get("title", "") for e in parsed.entries[:5] if e.get("title")]
    except Exception:
        return []


def _is_relevant(domain: str, titles: list) -> bool:
    from ai import _call_groq
    sample = "\n".join(f"- {t}" for t in titles) if titles else "(no sample available)"
    prompt = f"""A BBC World Service product manager monitors sources about: digital news products, AI in journalism, news org engineering decisions, audio innovation, circumvention of internet censorship, and global media development — with particular interest in Africa, Asia, Latin America, and Europe.

Should this source be added to their monitoring list?

Domain: {domain}
Recent article titles:
{sample}

Reply YES or NO only."""
    try:
        result = _call_groq(prompt).strip().upper()
        return result.startswith("YES")
    except Exception:
        return False


def run(config: dict) -> int:
    auto = _load_auto()
    known = _known_domains(config, auto)
    existing_urls = {f["url"] for f in auto.get("rss_feeds", [])}

    # Primary: link extraction from trusted sources
    logger.info("Discovery: extracting links from trusted sources...")
    candidates = _extract_linked_domains(known)
    logger.info(f"Discovery: {len(candidates)} domains cited {CITATION_THRESHOLD}+ times by trusted sources")

    # Fallback: HN search if we didn't find enough candidates
    if len(candidates) < MIN_LINK_CANDIDATES:
        logger.info("Discovery: supplementing with HN search...")
        hn = _hn_candidates(known)
        for domain, count in hn.items():
            if domain not in candidates:
                candidates[domain] = count

    ranked = sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:20]
    logger.info(f"Discovery: evaluating {len(ranked)} candidate domains")

    added = 0
    for domain, score in ranked:
        if added >= MAX_NEW_PER_RUN:
            break

        logger.info(f"Discovery: checking {domain} (score {score})")

        feed_url = _find_rss(domain)
        if not feed_url or feed_url in existing_urls:
            logger.info(f"Discovery: no RSS found for {domain}")
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
