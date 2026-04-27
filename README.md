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

**Weekly digest (every Monday 07:00 UTC):**

1. **Fetch** — pulls articles from RSS feeds, GitHub org activity, and Hacker News discussions
2. **Deduplicate** — skips anything already seen in a previous week's run (tracked in `seen.json`)
3. **Negative keyword filter** — cheaply drops obvious noise (job cuts, obituaries, etc.) before any AI call
4. **AI filter** — sends remaining articles to Groq (free tier) in a single call; keeps only product/tech/AI stories
5. **AI summarise** — writes a 3–4 sentence summary and a BBC World Service relevance note for each article
6. **Email** — builds an HTML email and sends it via Gmail

**Monthly source discovery (1st of each month):**

1. Searches HN for relevant stories from domains not already in the source list
2. Checks each candidate domain for an RSS feed
3. Asks Groq whether the source regularly covers digital news products or AI in journalism
4. Saves validated sources to `auto_sources.yaml`, which is merged in automatically on every weekly run

This means the source list grows over time without any manual intervention.

---

## Sources

**Curated in `config.yaml`:**

- **Engineering blogs** — NYT, Guardian, FT, BBC, Spotify, ProPublica, The Pudding
- **Trade publications** — Nieman Lab, Press Gazette, Poynter, WNIP, INMA, Reuters Institute, Digiday, The Markup, Rest of World, CJR
- **Newsletters** — Simon Owens, Hot Pod, The Fix, Media Voices, Podnews
- **GitHub orgs** — NYT, Guardian, FT, BBC, Washington Post, Reuters, AP, Politico, Deutsche Welle
- **Hacker News** — domain searches for engineering blog URLs

**Auto-discovered in `auto_sources.yaml`:**

Sources found by the monthly discovery job are stored here and merged in at runtime. This file is machine-managed — don't edit it manually.

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

# Run source discovery manually
python3 main.py --discover
```

The test run writes `digest_output.html` and opens it in your browser. No email is sent unless `GMAIL_APP_PASSWORD` is set.

---

## GitHub Actions (automated runs)

**Weekly digest** — `.github/workflows/weekly-digest.yml` — runs every Monday at 07:00 UTC.

**Monthly discovery** — `.github/workflows/monthly-discovery.yml` — runs on the 1st of each month, finds new sources and commits `auto_sources.yaml`.

**Required GitHub secrets** (Settings → Secrets and variables → Actions):

| Secret name | Value |
|---|---|
| `GROQSECRET` | Your Groq API key |
| `GMAIL_APP_PASSWORD` | Your Gmail app password |

`GITHUB_TOKEN` is provided automatically by Actions — no setup needed.

After each weekly run, `seen.json` is committed back to the repo. After each discovery run, `auto_sources.yaml` is committed back.

---

## Customising sources and filters

**To add a source manually** — add an entry to the `rss_feeds` list in `config.yaml`. Set `tier: "engineering"` for dedicated engineering/product blogs (everything passes through) or `tier: "trade"` for general publications (keyword filtered first).

**To block a type of story** — add a phrase to `negative_keywords` in `config.yaml`. Any article whose title or summary contains that phrase is dropped before the AI sees it.

**To adjust what the AI considers relevant** — edit `_INCLUDE` and `_EXCLUDE` in `ai.py`.

**To run source discovery immediately** — run `python3 main.py --discover` locally or trigger the monthly-discovery workflow manually in GitHub Actions. New sources are saved to `auto_sources.yaml` and picked up automatically.
