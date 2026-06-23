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
                         Telethon + Tesseract OCR + local embeddings
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
5. The worker generates embeddings locally (fastembed / ONNX, on CPU) for new or changed posts.
6. Supabase `pgvector` finds posts above the configured similarity threshold.
7. Matches are delivered through the Telegram Bot API.

The current beta uses threshold-only semantic matching. This keeps the system simple and cheap to run, but the threshold will need tuning against real source channels.

## Current Limits

- Near-Live is polling-based, not instant. The worker is scheduled through GitHub Actions, so runs may be delayed.
- Public channels and public supergroups are supported. Private sources are not.
- Past Search is queued. It is processed by worker runs, not immediately by the webhook.
- Large Past Searches may take multiple worker runs while posts are indexed. There are no embedding rate limits, but a run can be interrupted (or the dyno restarted) and the still-unindexed posts resume on the next pass.
- Scraping walks backward until the lookback boundary; `SCRAPE_MAX_MESSAGES` is an optional emergency cap and defaults to unlimited. The scraper processes posts in small batches so it does not keep the whole channel window in memory.
- Post embedding runs locally with no rate limit. It streams the backlog in DB pages and embeds in small batches (`EMBEDDING_DB_PAGE`, `EMBEDDING_BATCH`), writing each vector back immediately, so peak memory stays flat regardless of backlog size — important under the worker dyno's RAM cap.
- Jobs left `running` by canceled worker runs are retried after `JOB_STALE_MINUTES`.
- OCR is English by default through Tesseract, configurable with `OCR_LANGS`. It is bounded by image count, file size, pixel count, download timeout, and Tesseract timeout so a single image cannot monopolize the worker.
- The beta cap defaults to 5 users.
- Live integration behavior depends on Telegram, Supabase, Vercel, and worker (Heroku) free/low-tier constraints — notably the worker dyno's RAM cap, which bounds the local embedding model.

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
```

Embeddings run locally on the worker (no API key, no network at runtime); the model
is baked into the Docker image at build time.

Useful optional GitHub Actions variables:

```text
SIMILARITY_THRESHOLD
SCRAPE_MAX_MESSAGES
SCRAPE_PROGRESS_EVERY
SCRAPE_GROUP_BATCH_SIZE
PAST_SEARCH_JOBS_PER_RUN
WORKER_RUN_TIMEOUT_SECONDS
WORKER_STAGE_TIMEOUT_SECONDS
SUPABASE_DB_RETRIES
SUPABASE_DB_RETRY_BACKOFF_SECONDS
SUPABASE_DB_RETRY_JOB_MINUTES
EMBEDDING_MODEL
EMBEDDING_DIM
EMBEDDING_QUERY_PREFIX
EMBEDDING_DOCUMENT_PREFIX
EMBEDDING_BATCH
EMBEDDING_DB_PAGE
EMBEDDING_THREADS
EMBEDDING_BACKLOG_RETRY_MINUTES
OCR_ENABLED
OCR_LANGS
OCR_TIMEOUT_SECONDS
OCR_DOWNLOAD_TIMEOUT_SECONDS
OCR_MAX_IMAGES_PER_POST
OCR_MAX_IMAGE_MB
OCR_MAX_IMAGE_PIXELS
OCR_MAX_IMAGE_DIMENSION
OCR_MAX_TEXT_CHARS
OCR_SKIP_WHEN_CAPTION_PRESENT
OCR_THREAD_LIMIT
OCR_TESSERACT_CONFIG
```

Embeddings run on CPU inside the worker, so memory is the constraint, not a rate limit. Lower `EMBEDDING_BATCH` (and optionally set `EMBEDDING_THREADS=1`) if the worker dyno approaches its RAM cap. Switching to a smaller model means changing `EMBEDDING_MODEL`/`EMBEDDING_DIM` (and its prefixes), altering the `vector(...)` column to the new dimension, and running `scripts/reset_embeddings.sql`. `SIMILARITY_THRESHOLD` must be re-tuned per model.

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
