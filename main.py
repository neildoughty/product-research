"""
BBC World Service Research Intelligence Tool
Weekly digest of news org product/engineering activity.

Flow:
  1. Fetch RSS articles + GitHub activity + HN discussions
  2. Deduplicate against seen.json
  3. Negative keyword pre-filter (free — drops staff/financial noise)
  4. Gemini Flash relevance filter (one API call — keeps only product/tech stories)
  5. Gemini Flash summarise + WS relevance note (one call per article)
  6. Build HTML email and send via Gmail
  7. Commit updated seen.json back to repo
"""

import logging
import sys
import time

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


def _passes_negative_filter(article: dict, negative_keywords: list) -> bool:
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    return not any(kw.lower() in text for kw in negative_keywords)


def main():
    test_mode = "--test" in sys.argv
    config = load_config()
    negative_keywords = config.get("negative_keywords", [])
    logger.info(f"WS Research Tool starting {'[TEST MODE]' if test_mode else ''}")

    seen = set() if test_mode else load_seen()
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

    # ── Negative keyword pre-filter ───────────────────────────────────────────
    if negative_keywords:
        before = len(new_articles)
        new_articles = [a for a in new_articles if _passes_negative_filter(a, negative_keywords)]
        logger.info(f"Negative filter: {before - len(new_articles)} articles dropped, {len(new_articles)} remain")

    # ── Cap before sending to AI ─────────────────────────────────────────────
    candidates = sorted(new_articles, key=lambda a: a.get("published", ""), reverse=True)[:40]

    # ── AI relevance filter (Gemini Flash — one call) ─────────────────────────
    filtered = filter_articles(candidates)
    logger.info(f"{len(filtered)} articles after AI filter")

    # ── AI summarise (Gemini Flash — one call per article) ────────────────────
    for article in filtered:
        ai = summarise_article(article)
        if ai.get("summary"):
            article["summary"] = ai["summary"]
        article["relevance_note"] = ai.get("relevance_note", "")
        time.sleep(1)  # stay well within free-tier rate limit (15 RPM)

    logger.info(f"{len(filtered)} articles going to digest")

    # ── Build and send digest ─────────────────────────────────────────────────
    build_and_send(filtered, new_github, config)

    # ── Save seen URLs (skipped in test mode) ────────────────────────────────
    if not test_mode:
        new_seen = (
            seen
            | {a["url"] for a in new_articles if a.get("url")}
            | {g["url"] for g in new_github if g.get("url")}
        )
        save_seen(new_seen)
        logger.info(f"Saved {len(new_seen)} seen URLs")
    else:
        logger.info("Test mode — seen.json not updated")


if __name__ == "__main__":
    main()
