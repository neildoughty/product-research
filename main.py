"""
BBC World Service Research Intelligence Tool
Weekly digest of news org product/engineering activity.

Flow:
  1. Fetch RSS articles + GitHub activity + HN discussions
  2. Deduplicate against seen.json
  3. Negative keyword pre-filter (free — drops staff/financial noise)
  4. Groq relevance filter (one API call, strict JSON schema, retried)
  5. Groq summarise + WS relevance note (one call per article)
  6. Build HTML email and send via Gmail
  7. Mark as seen ONLY the URLs that were in the email that was sent
     (the workflow then commits seen.json back to the repo)

Safety rules:
  - If the AI filter fails after retries, the negative-filtered articles are
    sent marked UNFILTERED and the run exits non-zero so it shows red.
  - If the email isn't sent, nothing is marked as seen.
  - A digest with 0 articles says why in a warning banner.
"""

import logging
import sys
import time

import yaml

from ai import FilterFailed, filter_articles, summarise_article
from digest import build_and_send
from seen import load_seen, save_seen
from sources import LOOKBACK_DAYS, fetch_github_activity, fetch_hn_items, fetch_rss_articles

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        config = yaml.safe_load(f)
    # Merge in auto-discovered sources if they exist
    try:
        with open("auto_sources.yaml") as f:
            auto = yaml.safe_load(f) or {}
        extra = auto.get("rss_feeds", [])
        if extra:
            config["rss_feeds"] = config.get("rss_feeds", []) + extra
            logger.info(f"Loaded {len(extra)} auto-discovered sources")
    except FileNotFoundError:
        pass
    return config


def _passes_negative_filter(article: dict, negative_keywords: list) -> bool:
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    return not any(kw.lower() in text for kw in negative_keywords)


def main():
    test_mode = "--test" in sys.argv
    discover_mode = "--discover" in sys.argv
    config = load_config()

    if discover_mode:
        from discovery import run as discover
        discover(config)
        return

    negative_keywords = config.get("negative_keywords", [])
    logger.info(f"WS Research Tool starting {'[TEST MODE]' if test_mode else ''} (lookback {LOOKBACK_DAYS} days)")

    seen = set() if test_mode else load_seen()
    logger.info(f"Loaded {len(seen)} previously seen URLs")

    # ── Fetch ────────────────────────────────────────────────────────────────
    rss_articles = fetch_rss_articles(config.get("rss_feeds", []))
    logger.info(f"RSS: {len(rss_articles)} articles fetched")

    github_items = fetch_github_activity(config.get("github_orgs", []))
    logger.info(f"GitHub: {len(github_items)} items fetched")

    hn_items = fetch_hn_items(config.get("hn_domains", []))
    logger.info(f"HN: {len(hn_items)} items fetched")

    # ── Deduplicate (against seen.json and within this run) ────────────────────
    all_candidates = rss_articles + hn_items
    new_articles, this_run = [], set()
    for a in all_candidates:
        url = a.get("url")
        if url and url not in seen and url not in this_run:
            this_run.add(url)
            new_articles.append(a)
    new_github = [g for g in github_items if g.get("url") and g["url"] not in seen]
    logger.info(f"{len(new_articles)} new articles, {len(new_github)} new GitHub items after dedup")

    # ── Negative keyword pre-filter ───────────────────────────────────────────
    if negative_keywords:
        before = len(new_articles)
        new_articles = [a for a in new_articles if _passes_negative_filter(a, negative_keywords)]
        logger.info(f"Negative filter: {before - len(new_articles)} articles dropped, {len(new_articles)} remain")

    # ── Cap before sending to AI ─────────────────────────────────────────────
    candidates = sorted(new_articles, key=lambda a: a.get("published", ""), reverse=True)[:40]
    if len(new_articles) > len(candidates):
        logger.info(f"Capped at {len(candidates)} candidates; the other "
                    f"{len(new_articles) - len(candidates)} stay unseen for a later run")

    # ── AI relevance filter (Groq — one call, retried) ────────────────────────
    notices = []
    unfiltered = False
    try:
        filtered = filter_articles(candidates)
        logger.info(f"{len(filtered)} articles after AI filter")
    except FilterFailed as e:
        logger.error(str(e))
        unfiltered = True
        filtered = candidates
        for a in filtered:
            a["categories"] = []
        notices.append(
            f"The AI relevance filter failed this week ({e}). The {len(filtered)} articles below "
            "passed only the keyword filter and have NOT been screened for relevance."
        )

    # ── AI summarise (Groq — one call per article) ────────────────────────────
    for article in filtered:
        ai = summarise_article(article)
        if ai.get("summary"):
            article["summary"] = ai["summary"]
        article["relevance_note"] = ai.get("relevance_note", "")
        time.sleep(1)  # stay well within free-tier rate limit

    logger.info(f"{len(filtered)} articles going to digest")

    # ── Explain an empty digest instead of sending a blank one ────────────────
    if not filtered:
        if not all_candidates:
            reason = (f"No articles were fetched from any source in the last {LOOKBACK_DAYS} days "
                      "— check the Action log for feed errors.")
        elif not new_articles and not candidates:
            reason = (f"{len(all_candidates)} articles were fetched but all had already been sent in "
                      "an earlier digest or were removed by the keyword filter.")
        else:
            reason = (f"The AI filter reviewed {len(candidates)} new articles and judged none of them "
                      "relevant this week.")
        notices.append(f"This digest contains 0 articles. {reason}")

    # ── Build and send digest ─────────────────────────────────────────────────
    sent = build_and_send(filtered, new_github, config, notices=notices, unfiltered=unfiltered)

    # ── Save seen URLs — only what was actually in a sent email ───────────────
    if test_mode:
        logger.info("Test mode — seen.json not updated")
    elif not sent:
        logger.error("Digest was NOT sent — seen.json left unchanged so these articles are retried next run")
    else:
        delivered = {a["url"] for a in filtered if a.get("url")} | {g["url"] for g in new_github if g.get("url")}
        save_seen(seen | delivered)
        logger.info(f"Marked {len(delivered)} delivered URLs as seen ({len(seen | delivered)} total)")

    mode = "UNFILTERED" if unfiltered else "filtered"
    logger.info(f"RESULT: sent={sent} mode={mode} articles={len(filtered)} github={len(new_github)}")

    # Make problems visible as a red run in GitHub Actions
    if not sent and not test_mode:
        print("::error::Digest email was not sent")
        sys.exit(1)
    if unfiltered:
        print("::error::AI filter failed — an UNFILTERED digest was sent")
        sys.exit(2)


if __name__ == "__main__":
    main()
