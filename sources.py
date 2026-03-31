"""
Fetch articles and activity from three sources:
  - RSS feeds (engineering blogs + trade pubs)
  - GitHub orgs (new repos and releases)
  - Hacker News (discussion threads about these domains)
"""

import datetime
import logging
import os
import time

import feedparser
import requests

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 7
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def _github_headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def fetch_rss_articles(feeds: list) -> list:
    """
    Fetch articles from all configured RSS feeds published in the last LOOKBACK_DAYS.
    Returns list of article dicts.
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=LOOKBACK_DAYS)
    articles = []

    for feed_config in feeds:
        url = feed_config["url"]
        try:
            parsed = feedparser.parse(url, request_headers={"User-Agent": "WS-Research-Bot/1.0"})
            for entry in parsed.entries:
                pub_date = _parse_feed_date(entry)
                if pub_date and pub_date < cutoff:
                    continue
                article_url = entry.get("link", "")
                if not article_url:
                    continue
                articles.append({
                    "source": "rss",
                    "org": feed_config.get("org", ""),
                    "feed_name": feed_config.get("name", ""),
                    "tier": feed_config.get("tier", "trade"),
                    "title": entry.get("title", "").strip(),
                    "url": article_url,
                    "summary": _extract_summary(entry),
                    "published": pub_date.isoformat() if pub_date else "",
                })
        except Exception as e:
            logger.warning(f"RSS fetch failed for {url}: {e}")

    return articles


_FRONTEND_LANGS = {"javascript", "typescript", "css", "html", "vue", "svelte"}
_FRONTEND_NAME_KEYWORDS = {
    "frontend", "front-end", "web", "app", "ui", "ux", "reader", "player",
    "site", "dotcom", "design", "interactive", "cms", "editorial", "publish",
    "mobile", "amp", "pwa", "component", "template", "theme",
}


def _is_frontend_repo(repo: dict) -> bool:
    lang = (repo.get("language") or "").lower()
    name = repo.get("name", "").lower()
    desc = (repo.get("description") or "").lower()
    if lang in _FRONTEND_LANGS:
        return True
    return any(kw in name or kw in desc for kw in _FRONTEND_NAME_KEYWORDS)


def fetch_github_activity(orgs: list) -> list:
    """
    Fetch new repos (created this week) and new releases from GitHub orgs.
    Uses the Events API for releases and the Repos API for new repos.
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=LOOKBACK_DAYS)
    items = []

    for org_config in orgs:
        org = org_config["name"]
        display = org_config.get("display", org)

        # New repos created this week
        try:
            resp = requests.get(
                f"https://api.github.com/orgs/{org}/repos",
                headers=_github_headers(),
                params={"sort": "created", "direction": "desc", "per_page": 10},
                timeout=10,
            )
            if resp.status_code == 200:
                for repo in resp.json():
                    if repo.get("fork", False) or not _is_frontend_repo(repo):
                        continue
                    created = _parse_github_date(repo.get("created_at", ""))
                    if created and created > cutoff:
                        items.append({
                            "source": "github",
                            "type": "new_repo",
                            "org": display,
                            "title": f"New repo: {repo['name']}",
                            "url": repo["html_url"],
                            "summary": repo.get("description", "") or "",
                            "published": repo.get("created_at", ""),
                        })
            elif resp.status_code == 404:
                logger.warning(f"GitHub org not found: {org}")
            elif resp.status_code == 403:
                logger.warning(f"GitHub rate limited — consider adding GITHUB_TOKEN")
        except Exception as e:
            logger.warning(f"GitHub repos fetch failed for {org}: {e}")

        # Releases via events API
        try:
            resp = requests.get(
                f"https://api.github.com/orgs/{org}/events",
                headers=_github_headers(),
                params={"per_page": 30},
                timeout=10,
            )
            if resp.status_code == 200:
                for event in resp.json():
                    if event.get("type") != "ReleaseEvent":
                        continue
                    created = _parse_github_date(event.get("created_at", ""))
                    if not created or created < cutoff:
                        continue
                    payload = event.get("payload", {})
                    release = payload.get("release", {})
                    if release.get("draft") or release.get("prerelease"):
                        continue
                    repo_name = event.get("repo", {}).get("name", org)
                    if not _is_frontend_repo({"name": repo_name.split("/")[-1], "description": release.get("body", "")}):
                        continue
                    items.append({
                        "source": "github",
                        "type": "release",
                        "org": display,
                        "title": f"Release: {repo_name} {release.get('tag_name', '')}",
                        "url": release.get("html_url", ""),
                        "summary": (release.get("body", "") or "")[:400],
                        "published": event.get("created_at", ""),
                    })
        except Exception as e:
            logger.warning(f"GitHub events fetch failed for {org}: {e}")

        time.sleep(0.3)  # gentle rate limiting across 13 orgs

    return items


def fetch_hn_items(domains: list) -> list:
    """
    Search Hacker News (via Algolia API) for recent stories linked to these domains.
    Surfaces engineer/PM discussion threads that don't appear in RSS feeds.
    """
    cutoff_ts = int(
        (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=LOOKBACK_DAYS)).timestamp()
    )
    items = []

    for domain in domains:
        try:
            resp = requests.get(
                "https://hn.algolia.com/api/v1/search",
                params={
                    "query": f"site:{domain}",
                    "tags": "story",
                    "numericFilters": f"created_at_i>{cutoff_ts}",
                    "hitsPerPage": 5,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                for hit in resp.json().get("hits", []):
                    url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                    points = hit.get("points", 0)
                    comments = hit.get("num_comments", 0)
                    items.append({
                        "source": "hn",
                        "org": domain,
                        "feed_name": "Hacker News",
                        "tier": "hn",
                        "title": hit.get("title", ""),
                        "url": url,
                        "summary": f"{points} points · {comments} comments on Hacker News",
                        "published": "",
                    })
        except Exception as e:
            logger.warning(f"HN search failed for {domain}: {e}")

    return items


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_feed_date(entry):
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc)
            except Exception:
                pass
    return None


def _parse_github_date(s: str):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_summary(entry) -> str:
    """Pull summary/description text from a feed entry, stripping HTML tags."""
    raw = entry.get("summary") or entry.get("description") or ""
    # Simple tag strip — feedparser usually provides plain text but not always
    import re
    clean = re.sub(r"<[^>]+>", " ", raw)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:800]
