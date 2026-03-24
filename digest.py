"""
Build HTML email digest and send via Resend.

From address: uses onboarding@resend.dev (Resend's shared sender).
This works for sending to your own verified Resend account email without
needing a custom domain. To send to any address, verify a domain at resend.com/domains.
"""

import datetime
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SECTIONS = [
    ("live_news",          "🔴 Live & Rolling News Products"),
    ("audio",              "🎙️ Audio & Synthetic Voice"),
    ("ai_journalism",      "🤖 AI in Journalism"),
    ("product_engineering","⚙️ Product & Engineering"),
]


def build_and_send(articles: list, github_items: list, config: dict):
    html = _build_html(articles, github_items)
    _send(html, config)


# ── HTML builder ─────────────────────────────────────────────────────────────

def _build_html(articles: list, github_items: list) -> str:
    week = datetime.date.today().strftime("%-d %B %Y")

    # Group articles by primary category (first matching in SECTIONS order)
    section_keys = [k for k, _ in SECTIONS]
    buckets = {k: [] for k in section_keys}
    uncategorised = []
    for a in articles:
        cats = a.get("categories", [])
        placed = False
        for k in section_keys:
            if k in cats:
                buckets[k].append(a)
                placed = True
                break
        if not placed:
            uncategorised.append(a)
    if uncategorised:
        buckets["product_engineering"].extend(uncategorised)

    total_articles = len(articles)
    total_github = len(github_items)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: Georgia, 'Times New Roman', serif; max-width: 680px; margin: 0 auto; padding: 24px 16px; color: #222; background: #fff; }}
  h1 {{ color: #1a1a1a; border-bottom: 3px solid #b00; padding-bottom: 12px; margin-bottom: 6px; font-size: 1.6em; }}
  .meta {{ color: #777; font-size: 0.85em; margin-bottom: 28px; }}
  h2 {{ color: #b00; margin-top: 36px; margin-bottom: 16px; font-size: 1.15em; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
  .item {{ margin-bottom: 28px; }}
  .item-title {{ margin: 0 0 4px; font-size: 1em; font-weight: bold; }}
  .item-title a {{ color: #1a1a1a; text-decoration: none; }}
  .item-title a:hover {{ text-decoration: underline; color: #b00; }}
  .item-meta {{ color: #888; font-size: 0.8em; margin-bottom: 8px; font-family: Arial, sans-serif; }}
  .item-summary {{ margin: 0 0 8px; line-height: 1.6; font-size: 0.95em; }}
  .relevance {{ background: #fffbf0; border-left: 3px solid #e8a020; padding: 7px 10px; font-size: 0.88em; color: #555; font-family: Arial, sans-serif; margin-top: 6px; }}
  .relevance strong {{ color: #c07010; }}
  .github-section {{ margin-top: 36px; }}
  .github-item {{ margin-bottom: 10px; font-family: Arial, sans-serif; font-size: 0.9em; }}
  .github-item a {{ color: #1a1a1a; }}
  .github-org {{ color: #888; }}
  .github-desc {{ color: #666; font-size: 0.85em; }}
  .footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid #eee; font-size: 0.78em; color: #aaa; font-family: Arial, sans-serif; }}
</style>
</head>
<body>
<h1>BBC World Service Research Digest</h1>
<div class="meta">Week of {week} &bull; {total_articles} articles &bull; {total_github} GitHub updates</div>
"""

    # Article sections
    for key, label in SECTIONS:
        items = buckets.get(key, [])
        if not items:
            continue
        html += f"<h2>{label}</h2>\n"
        for a in items:
            html += _render_article(a)

    # GitHub section
    if github_items:
        html += '<div class="github-section">\n'
        html += '<h2>💻 GitHub Activity</h2>\n'
        for g in github_items:
            title = _esc(g.get("title", ""))
            url = g.get("url", "#")
            org = _esc(g.get("org", ""))
            desc = _esc((g.get("summary", "") or "")[:200])
            html += f"""<div class="github-item">
  <strong><a href="{url}">{title}</a></strong> <span class="github-org">&mdash; {org}</span>
  {f'<div class="github-desc">{desc}</div>' if desc else ''}
</div>
"""
        html += "</div>\n"

    html += f"""<div class="footer">
  Generated automatically &bull; {total_articles + total_github} items this week<br>
  <a href="https://github.com" style="color:#aaa">Edit sources in config.yaml</a>
</div>
</body>
</html>"""

    return html


def _render_article(a: dict) -> str:
    title = _esc(a.get("title", ""))
    url = a.get("url", "#")
    org = _esc(a.get("org", ""))
    feed_name = _esc(a.get("feed_name", a.get("source", "")))
    summary = _esc(a.get("summary", ""))
    relevance = _esc(a.get("relevance_note", ""))

    meta = f"{org}"
    if feed_name and feed_name != org:
        meta += f" &bull; {feed_name}"

    html = f"""<div class="item">
  <div class="item-title"><a href="{url}">{title}</a></div>
  <div class="item-meta">{meta}</div>
  <p class="item-summary">{summary}</p>"""
    if relevance:
        html += f'\n  <div class="relevance"><strong>WS relevance:</strong> {relevance}</div>'
    html += "\n</div>\n"
    return html


def _esc(s: str) -> str:
    """Basic HTML escaping."""
    return (s
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ── email sender ─────────────────────────────────────────────────────────────

def _send(html: str, config: dict):
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not app_password:
        logger.warning("GMAIL_APP_PASSWORD not set — writing digest to digest_output.html")
        with open("digest_output.html", "w") as f:
            f.write(html)
        return

    from_addr = config.get("digest_from", "neil.doughty@gmail.com")
    to_addr = config.get("digest_to", from_addr)
    if not to_addr:
        logger.error("digest_to not configured in config.yaml")
        return

    week = datetime.date.today().strftime("%-d %B %Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"BBC World Service Research Digest — {week}"
    msg["From"] = f"WS Research Digest <{from_addr}>"
    msg["To"] = to_addr
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_addr, app_password)
            server.sendmail(from_addr, to_addr, msg.as_string())
        logger.info(f"Email sent to {to_addr}")
    except Exception as e:
        logger.error(f"Gmail send failed: {e}")
        with open("digest_output.html", "w") as f:
            f.write(html)
        logger.info("Digest saved to digest_output.html as fallback")
