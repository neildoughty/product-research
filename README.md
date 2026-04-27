# BBC World Service Research Digest

A weekly email digest of product and engineering activity across news organisations. Delivered every Monday at 07:00 UTC.

It monitors RSS feeds, GitHub orgs, and Hacker News for stories about how news organisations are building digital products, using AI, and making engineering decisions — then uses an AI layer to filter and summarise the relevant ones before emailing them.

---

## What it covers

**Included:**
- How news orgs are using AI (tools, workflows, editorial automation)
- New digital products, features, and platform changes at news orgs
- New content formats and how they're performing
- Where journalism output meets product and engineering decisions

**Excluded:**
- Actual news stories (politics, sport, world events)
- Job cuts, appointments, and staff news
- Ad revenue, commercial deals, financial results
- News diaries and editorial calendars
- Press freedom and journalist safety stories

---

## How it works

1. **Fetch** — pulls articles from RSS feeds, GitHub org activity, and Hacker News discussions
2. **Deduplicate** — skips anything already seen in a previous week's run (tracked in `seen.json`)
3. **Negative keyword filter** — cheaply drops obvious noise (job cuts, obituaries, etc.) before any AI call
4. **AI filter** — sends remaining articles to Groq (free tier) in a single call; keeps only product/tech/AI stories
5. **AI summarise** — writes a 3–4 sentence summary and a BBC World Service relevance note for each article
6. **Email** — builds an HTML email and sends it via Gmail

---

## Sources

Configured in `config.yaml`:

- **RSS feeds** — engineering blogs (NYT, Guardian, FT, BBC, Spotify) and trade publications (Nieman Lab, Press Gazette, Poynter, WNIP, INMA, Reuters Institute, Simon Owens)
- **GitHub orgs** — NYT, Guardian, FT, BBC, Washington Post, Reuters, AP, Politico, Deutsche Welle
- **Hacker News** — domain searches for engineering blog URLs

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/neildoughty/product-research.git
cd product-research
pip3 install -r requirements.txt
```

### 2. Get a Groq API key (free)

Go to [console.groq.com](https://console.groq.com), sign up (no card needed), and create an API key.

### 3. Set up Gmail sending

You need a Gmail account and an **App Password** (not your regular password):

1. Go to your Google Account → Security → 2-Step Verification (must be enabled)
2. Search for "App passwords" → create one for "Mail"
3. Copy the 16-character password

The `digest_from` address in `config.yaml` must match the Gmail account you use here.

### 4. Configure `config.yaml`

```yaml
digest_from: "your.gmail@gmail.com"   # sends from here
digest_to: "recipient@example.com"    # delivers to here
```

### 5. Set environment variables

Add these to your `~/.zshrc` (or `~/.bashrc`):

```bash
export GROQ_API_KEY=your_groq_key_here
export GMAIL_APP_PASSWORD=your_app_password_here
```

Then run `source ~/.zshrc`.

---

## Running locally

```bash
# Test run — ignores seen.json, won't mark articles as seen
python3 main.py --test && open digest_output.html

# Production run — updates seen.json, sends email
python3 main.py
```

The test run writes `digest_output.html` and opens it in your browser. No email is sent unless `GMAIL_APP_PASSWORD` is set.

---

## GitHub Actions (automated weekly run)

The workflow in `.github/workflows/weekly-digest.yml` runs every Monday at 07:00 UTC.

**Required GitHub secrets** (Settings → Secrets and variables → Actions):

| Secret name | Value |
|---|---|
| `GROQSECRET` | Your Groq API key |
| `GMAIL_APP_PASSWORD` | Your Gmail app password |

`GITHUB_TOKEN` is provided automatically by Actions — no setup needed.

After each run, the workflow commits the updated `seen.json` back to the repo so the following week's run knows what's already been sent.

---

## Customising sources and filters

Everything is in `config.yaml`:

- **`rss_feeds`** — add or remove feeds; set `tier: "engineering"` for high-signal blogs (no keyword filtering) or `tier: "trade"` for publications (keyword filtered)
- **`negative_keywords`** — strings that immediately disqualify an article before the AI sees it
- **`keywords`** — positive keyword categories used for pre-filtering trade feeds

The AI filter prompt is in `ai.py` — edit `_INCLUDE` and `_EXCLUDE` to adjust what the AI considers relevant.
