# iScraper

A Telegram bot for finding relevant internship and job posts across public Telegram channels and groups.

Users tell iScraper what they are looking for, add public Telegram sources, and receive matching posts through one-time Past Searches or ongoing alerts. It is built for communities where opportunities are scattered across Telegram channels, groups, image posters, and short application-link posts.

## What It Does

- Watches public Telegram channels and public supergroups selected by each user.
- Runs semantic matching against a short user-defined match profile.
- Supports one-time Past Search over recent history, up to the configured lookback limit.
- Supports ongoing alerts in two modes: Every N days and Near-Live Every N minutes.
- Extracts text from image-based posts with offline OCR before embedding and matching.
- Groups Telegram albums into one logical post so captions and poster images stay together.
- Delivers results to direct messages or to a registered Telegram group.
- Keeps bot interaction stateless by storing all durable state in Supabase.

## Why It Exists

Many internship and job opportunities on Telegram are not posted in a clean, searchable format. Some are just a link with the actual role hidden inside attached images. Others are posted across several source channels with inconsistent wording.

iScraper turns that stream into a personal filter. A user can say something like:

```text
Computer Science or Software Developer internships, including backend, frontend, full-stack, mobile, ML, and general SWE roles.
```

Then iScraper searches selected sources and sends back posts that are semantically close enough to that intent.

## Product Surface

The bot supports:

- `/start` - onboarding
- `/settings` - match profile, sources, delivery, timezone, defaults
- `/search_past` - queue a one-time historical search
- `/search_status` - inspect the latest Past Search job
- `/alerts` - configure ongoing alert delivery
- `/help` and `/cancel`

Source inputs currently support public Telegram usernames and links:

```text
@channelname
https://t.me/channelname
@publicgroupname
https://t.me/publicgroupname
```

Private invite links such as `t.me/+...` and `t.me/joinchat/...` are intentionally unsupported in the beta.

## Architecture

```text
Telegram Bot API
      |
      v
Vercel Python webhook  <---->  Supabase Postgres + pgvector
      ^                                ^
      |                                |
      |                         GitHub Actions worker
      |                                |
      +------------------------- delivers matches
                                       |
                                       v
                         Telethon + Tesseract OCR + Gemini embeddings
```

The system is split into three deployable surfaces:

- `api/` and `bot/` handle the interactive Telegram bot on Vercel.
- `scraper/` and `jobs/` run as a scheduled GitHub Actions worker.
- `core/` contains shared configuration, database access, Telegram API helpers, OCR, embeddings, parsing, and time utilities.

Supabase is the single source of truth for users, source lists, conversation state, scraped posts, match records, and queued jobs.

## Matching Pipeline

1. A user adds public Telegram sources and a match profile.
2. The worker scrapes source history with Telethon.
3. Telegram albums are grouped into one logical post.
4. Captions and OCR text are combined into one searchable content field.
5. Gemini generates embeddings for new or changed posts.
6. Supabase `pgvector` finds posts above the configured similarity threshold.
7. Matches are delivered through the Telegram Bot API.

The current beta uses threshold-only semantic matching. This keeps the system simple and cheap to run, but the threshold will need tuning against real source channels.

## Current Limits

- Near-Live is polling-based, not instant. The worker is scheduled through GitHub Actions, so runs may be delayed.
- Public channels and public supergroups are supported. Private sources are not.
- Past Search is queued. It is processed by worker runs, not immediately by the webhook.
- Large Past Searches may take multiple worker runs while posts are indexed under Gemini quota limits.
- Scraping walks backward until the lookback boundary; `SCRAPE_MAX_MESSAGES` is an optional emergency cap and defaults to unlimited.
- Post embedding is paced below Gemini free limits: 100 RPM, 30k input TPM, and 1,000 RPD. Defaults use 80 RPM, 24k TPM, and 900 post embeddings per run.
- Jobs left `running` by canceled worker runs are retried after `JOB_STALE_MINUTES`.
- OCR is English by default through Tesseract, configurable with `OCR_LANGS`.
- The beta cap defaults to 5 users.
- Live integration behavior depends on Telegram, Supabase, Gemini, Vercel, and GitHub Actions free-tier constraints.

## Running Locally

Install worker dependencies when running scripts or the scraper locally:

```bash
pip install -r requirements-worker.txt
cp .env.example .env
```

Common local commands:

```bash
python scripts/login_telethon.py
python scripts/set_webhook.py https://your-vercel-app.vercel.app
python -m scraper.runner
```

The lightweight Vercel webhook dependencies live in `requirements.txt`; worker and script dependencies live in `requirements-worker.txt`.

## Deployment Notes

Database setup starts with:

```text
scripts/init_db.sql
```

Required Vercel environment variables:

```text
BOT_TOKEN
WEBHOOK_SECRET
SUPABASE_URL
SUPABASE_SERVICE_KEY
```

Required GitHub Actions secrets:

```text
BOT_TOKEN
TELEGRAM_API_ID
TELEGRAM_API_HASH
TELETHON_SESSION
SUPABASE_URL
SUPABASE_SERVICE_KEY
GEMINI_API_KEY
```

Useful optional GitHub Actions variables:

```text
SIMILARITY_THRESHOLD
SCRAPE_MAX_MESSAGES
SCRAPE_PROGRESS_EVERY
EMBEDDING_REQUESTS_PER_MINUTE
EMBEDDING_INPUT_TOKENS_PER_MINUTE
EMBEDDING_REQUESTS_PER_DAY
EMBEDDING_DAILY_REQUEST_RESERVE
EMBEDDING_MAX_PER_RUN
EMBEDDING_QUOTA_RETRY_MINUTES
EMBEDDING_BACKLOG_RETRY_MINUTES
OCR_LANGS
OCR_TIMEOUT_SECONDS
```

For the current Gemini free embedding quota, keep `EMBEDDING_REQUESTS_PER_MINUTE` at or below 100 and `EMBEDDING_INPUT_TOKENS_PER_MINUTE` at or below 30000. Defaults stay under those limits and reserve daily calls for query embeddings, retries, and alerts.

The scraper should use a real Telegram user session for `TELETHON_SESSION`, not the bot token, because the worker needs to read public Telegram history with Telethon.

## Project Layout

```text
api/webhook.py              Vercel serverless webhook
bot/                        Telegram bot router, flows, copy, keyboards
core/                       shared config, db, embeddings, OCR, Telegram helpers
scraper/                    Telethon scraping and worker runner
jobs/                       Past Search, interval alerts, near-live alerts
scripts/init_db.sql         Supabase schema
scripts/login_telethon.py   one-time Telethon session generation
scripts/set_webhook.py      webhook and Telegram command menu setup
.github/workflows/iscraper-worker.yml  scheduled worker
```

## Status

iScraper is a working beta. It is intentionally small: one Telegram bot, one database, one scheduled worker, and one shared Python core.
