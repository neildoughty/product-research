"""
BBC World Service Research Intelligence Tool
Weekly digest of news org product/engineering activity.

Flow:
  1. Fetch RSS articles + GitHub activity + HN discussions
  2. Deduplicate against seen.json
  3. Keyword pre-filter (free)
  4. Claude Haiku relevance filter (cheap — one API call)
  5. Claude Opus summarise + WS relevance note (per article)
  6. Build HTML email and send via Resend
  7. Commit updated seen.json back to repo
"""

import logging

import yaml

from ai import filter_articles, summarise_article
from digest import build_and_send
from seen import load_seen, save_seen
from sources import fetch_github_activity, fetch_hn_items, fetch_rss_articles

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def keyword_match(article: dict, keywords: dict) -> bool:
    """Cheap pre-filter: check if any keyword appears in title or summary."""
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    for kws in keywords.values():
        for kw in kws:
            if kw in text:
                return True
    return False


def main():
    config = load_config()
    logger.info("WS Research Tool starting")

    seen = load_seen()
    logger.info(f"Loaded {len(seen)} previously seen URLs")

    # ── Fetch ────────────────────────────────────────────────────────────────
    rss_articles = fetch_rss_articles(config.get("rss_feeds", []))
    logger.info(f"RSS: {len(rss_articles)} articles fetched")

    github_items = fetch_github_activity(config.get("github_orgs", []))
    logger.info(f"GitHub: {len(github_items)} items fetched")

    hn_items = fetch_hn_items(config.get("hn_domains", []))
    logger.info(f"HN: {len(hn_items)} items fetched")

    # ── Deduplicate ──────────────────────────────────────────────────────────
    all_candidates = rss_articles + hn_items
    new_articles = [a for a in all_candidates if a.get("url") and a["url"] not in seen]
    new_github = [g for g in github_items if g.get("url") and g["url"] not in seen]
    logger.info(f"{len(new_articles)} new articles, {len(new_github)} new GitHub items after dedup")

    # ── Keyword pre-filter ───────────────────────────────────────────────────
    keywords = config.get("keywords", {})
    keyword_matched = [a for a in new_articles if keyword_match(a, keywords)]
    logger.info(f"{len(keyword_matched)} articles pass keyword filter")

    # ── Claude filter (Haiku — one call) ────────────────────────────────────
    relevant = filter_articles(keyword_matched)

    # ── Claude summarise (Opus — one call per article) ───────────────────────
    processed = []
    for article in relevant:
        result = summarise_article(article)
        article.update(result)
        processed.append(article)
    logger.info(f"{len(processed)} articles summarised")

    # ── Build and send digest ─────────────────────────────────────────────────
    build_and_send(processed, new_github, config)

    # ── Save seen URLs ────────────────────────────────────────────────────────
    new_seen = (
        seen
        | {a["url"] for a in new_articles if a.get("url")}
        | {g["url"] for g in new_github if g.get("url")}
    )
    save_seen(new_seen)
    logger.info(f"Saved {len(new_seen)} seen URLs")


if __name__ == "__main__":
    main()
