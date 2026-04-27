"""
Groq (free tier) calls for filtering and summarising articles.

Two-pass approach:
  Pass 1 — single call to classify and filter all candidates
  Pass 2 — one call per article to write a focused summary + WS relevance note

Groq free tier: ~6,000 requests/day, no card needed. Sign up at console.groq.com.
"""

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"

CATEGORY_DESCRIPTIONS = {
    "live_news": "live/rolling news products, live blogs, breaking news UX, real-time coverage tools",
    "audio": "podcasts, synthetic voice, text-to-speech, audio innovation, voice products",
    "ai_journalism": "how news organisations are using AI — tools, workflows, editorial automation, AI-assisted reporting, generative AI in the newsroom",
    "product_engineering": "new digital products or features at news orgs, new content formats and how they're performing, where journalism output meets product and engineering decisions, CMS, audience tools, platform strategy",
    "circumvention": "how news organisations reach audiences in restricted or censored markets — anti-censorship tools, mirror sites, VPNs, secure distribution, working around government internet blocks or app store bans, digital repression countermeasures, secure communications for journalists and audiences in authoritarian contexts",
}

_INCLUDE = "\n".join(f"- {k}: {v}" for k, v in CATEGORY_DESCRIPTIONS.items())

_EXCLUDE = (
    "actual news stories (politics, world events, crime, sport — we want news ABOUT the news industry, not news itself); "
    "news diaries, editorial calendars, programme schedules; "
    "advertising revenue, ad deals, commercial partnerships, sponsorship; "
    "new business ventures, company launches, acquisitions, investments (unless the story is specifically about a digital product or technology); "
    "job cuts, layoffs, redundancies, headcount announcements; "
    "executive appointments, editor promotions, named hires; "
    "obituaries; "
    "earnings reports, revenue figures, quarterly/annual financial results; "
    "awards and rankings; "
    "legal/regulatory disputes (unless they directly affect a product)"
)


def _call_groq(prompt: str) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not set")
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def filter_articles(articles: list) -> list:
    """
    Send all candidate articles to Groq in a single call.
    Returns only those about product/technology developments, with categories set.
    """
    if not articles:
        return []

    lines = []
    for i, a in enumerate(articles, 1):
        title = a.get("title", "")
        snippet = a.get("summary", "")[:200]
        org = a.get("org", "")
        lines.append(f"{i}. [{org}] {title} | {snippet}")
    article_list = "\n".join(lines)

    prompt = f"""You are filtering articles for a BBC World Service product manager. They want to read news ABOUT the news industry — how news organisations are building products, using AI, experimenting with formats, and making engineering decisions. They do NOT want to read the news itself.

INCLUDE if the article is about any of:
{_INCLUDE}

Good examples to INCLUDE:
- "Cleveland Plain Dealer uses AI to generate video content" → yes, AI in journalism
- "AI in journalism: tracker of scandals and mistakes" → yes, industry AI overview
- "Publishers urge CMA to act on Google's AI search impact" → yes, platform strategy
- "Does short-form video help podcasts grow?" → yes, content formats
- "Newsroom leaders struggle with AI adoption" → yes, AI in journalism
- "Readly app hit with complaints after merger" → yes, digital product issue
- "BBC Persian launches Telegram channel to bypass Iranian blocks" → yes, circumvention
- "How RFE/RL reaches audiences behind the Great Firewall" → yes, circumvention
- "Signal adds new feature for journalists in high-risk countries" → yes, circumvention

EXCLUDE if the article is about:
{_EXCLUDE}

Bad examples to EXCLUDE:
- "White House correspondents' dinner guest list" → actual news/politics
- "Judge dismisses Kash Patel defamation lawsuit" → legal news
- "Former editor dies aged 86" → obituary
- "STV journalists strike over salary freeze" → labour dispute

Articles:
{article_list}

Return a JSON array of the articles worth including. When in doubt, include it — the reader can skim.
Format: [{{"index": 1, "categories": ["product_engineering"]}}]
If nothing qualifies, return: []
Return JSON only, no explanation."""

    try:
        text = _call_groq(prompt).strip()
        # Strip markdown fences if the model adds them
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
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
            logger.info(f"AI filter: {len(relevant)}/{len(articles)} articles kept")
            return relevant
    except json.JSONDecodeError as e:
        logger.warning(f"Filter JSON parse error: {e}")
    except Exception as e:
        logger.warning(f"Filter API call failed: {e}")

    return []


def summarise_article(article: dict) -> dict:
    """
    Write a focused summary and BBC World Service relevance note for one article.
    """
    categories = article.get("categories", [])
    cat_labels = [CATEGORY_DESCRIPTIONS.get(c, c) for c in categories]
    cat_str = "; ".join(cat_labels) if cat_labels else "product/engineering"

    prompt = f"""You are writing a research digest for a BBC World Service product manager. Their team publishes a fortnightly email about product developments and AI in digital news media — the intersection of journalism and product/engineering.

BBC World Service context: international news, 40+ language services across Africa, Asia, Latin America, Europe and the Middle East, live news products, audio innovation including synthetic voice, AI in journalism, and reaching audiences in restricted or censored markets.

Article:
Organisation: {article.get("org", "Unknown")}
Title: {article.get("title", "")}
Content: {article.get("summary", "")[:1500]}
Relevant to: {cat_str}

Write:
1. A 3-4 sentence summary focused on what was built, launched, or decided and why it matters — skip background, be specific
2. A 1-2 sentence note on why this is relevant to BBC World Service specifically

Return JSON only, no explanation, no markdown fences:
{{"summary": "...", "relevance_note": "..."}}"""

    try:
        text = _call_groq(prompt).strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
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

    return {
        "summary": article.get("summary", "")[:400],
        "relevance_note": "",
    }
