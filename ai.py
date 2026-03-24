"""
Claude API calls for filtering and summarising articles.

Two-pass approach:
  Pass 1 — Haiku: filter all keyword-matched articles in a single call (cheap)
  Pass 2 — Opus:  summarise each relevant article individually (quality)
"""

import json
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

FILTER_MODEL = "claude-haiku-4-5"
SUMMARISE_MODEL = "claude-opus-4-6"

CATEGORY_DESCRIPTIONS = {
    "live_news": "live/rolling news products, live blogs, breaking news UX",
    "audio": "podcasts, synthetic voice, text-to-speech, audio innovation",
    "ai_journalism": "AI in journalism, generative AI, editorial automation",
    "product_engineering": "product decisions, new features, UX improvements, engineering architecture at news organisations",
}


def filter_articles(articles: list) -> list:
    """
    Send all keyword-matched articles to Haiku in a single call.
    Returns the subset that are relevant, with 'categories' key set on each.
    """
    if not articles:
        return []

    # Build numbered list for the prompt
    lines = []
    for i, a in enumerate(articles, 1):
        title = a.get("title", "")
        summary = a.get("summary", "")[:200]
        org = a.get("org", "")
        lines.append(f"{i}. [{org}] {title} | {summary}")
    article_list = "\n".join(lines)

    prompt = f"""You are filtering articles for a BBC World Service product manager.

Relevant topics:
- live_news: {CATEGORY_DESCRIPTIONS['live_news']}
- audio: {CATEGORY_DESCRIPTIONS['audio']}
- ai_journalism: {CATEGORY_DESCRIPTIONS['ai_journalism']}
- product_engineering: {CATEGORY_DESCRIPTIONS['product_engineering']}

Articles to assess:
{article_list}

Return a JSON array of ONLY the relevant articles. Skip general news, politics, sport, celebrity, finance.
Format: [{{"index": 1, "categories": ["live_news", "product_engineering"]}}, ...]
If nothing is relevant, return an empty array: []"""

    try:
        response = client.messages.create(
            model=FILTER_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "[]")
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            results = json.loads(text[start:end])
            relevant = []
            for r in results:
                idx = r.get("index", 0) - 1
                if 0 <= idx < len(articles):
                    articles[idx]["categories"] = r.get("categories", [])
                    relevant.append(articles[idx])
            logger.info(f"Filter: {len(relevant)}/{len(articles)} articles relevant")
            return relevant
    except json.JSONDecodeError as e:
        logger.warning(f"Filter JSON parse error: {e}")
    except Exception as e:
        logger.warning(f"Filter API call failed: {e}")

    return []


def summarise_article(article: dict) -> dict:
    """
    Use Opus to write a 3–5 sentence summary and a BBC World Service relevance note.
    Returns dict with 'summary' and 'relevance_note' keys.
    """
    categories = article.get("categories", [])
    cat_labels = [CATEGORY_DESCRIPTIONS.get(c, c) for c in categories]
    cat_str = "; ".join(cat_labels) if cat_labels else "product/engineering"

    prompt = f"""You are writing a research digest for a BBC World Service product manager.

BBC World Service context: international news, multilingual audiences (40+ languages),
live news products, audio innovation including synthetic voice, AI in journalism.

Article:
Organisation: {article.get('org', 'Unknown')}
Title: {article.get('title', '')}
Content: {article.get('summary', '')[:1500]}
URL: {article.get('url', '')}
Relevant to: {cat_str}

Write two things:
1. A 3–5 sentence summary of what was reported (factual, no waffle)
2. A 1–2 sentence note explaining why this matters specifically to BBC World Service

Return JSON only:
{{"summary": "...", "relevance_note": "..."}}"""

    try:
        response = client.messages.create(
            model=SUMMARISE_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "{}")
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
            return {
                "summary": result.get("summary", ""),
                "relevance_note": result.get("relevance_note", ""),
            }
    except json.JSONDecodeError as e:
        logger.warning(f"Summarise JSON parse error for '{article.get('title', '')}': {e}")
    except Exception as e:
        logger.warning(f"Summarise API call failed for '{article.get('title', '')}': {e}")

    # Fallback: use the raw feed summary
    return {
        "summary": article.get("summary", "")[:400],
        "relevance_note": "",
    }
